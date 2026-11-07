import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.signature import sign
from app.db.repositories.cafes import Cafe
from app.integrations.whatsapp import messages as m
from app.integrations.whatsapp import simulated
from app.integrations.whatsapp.client import MetaTransport
from app.integrations.whatsapp.errors import SendClass, SendError
from app.integrations.whatsapp.parser import parse
from app.main_api import create_app

PNID = "100000000000001"


# ── parser ────────────────────────────────────────────────────────────────

def test_parse_text_tap_list_and_media() -> None:
    text, _ = simulated.message_payload(PNID, "919999000001", "Riya", "text", text="2 cold coffee")
    tap, _ = simulated.message_payload(PNID, "919999000001", None, "interactive", reply_id="confirm:3", reply_title="Confirm")
    lst, _ = simulated.message_payload(PNID, "919999000001", None, "interactive", reply_id="item:abc",
                                       reply_title="Cold Coffee", list_reply=True)
    img, _ = simulated.message_payload(PNID, "919999000001", None, "image")
    events = [parse(p).messages[0] for p in (text, tap, lst, img)]
    assert events[0].type == "text" and events[0].text == "2 cold coffee" and not events[0].is_tap
    assert events[1].reply_id == "confirm:3" and events[1].is_tap
    assert events[2].reply_id == "item:abc" and events[2].reply_title == "Cold Coffee"
    assert events[3].type == "image" and events[3].text is None
    assert all(e.phone_number_id == PNID and e.wa_id == "919999000001" for e in events)


def test_parse_multi_entry_batch_and_statuses() -> None:
    a, _ = simulated.message_payload(PNID, "919999000001", None, "text", text="hi")
    b, _ = simulated.message_payload("other-pnid", "919999000002", None, "text", text="hello")
    st = simulated.status_payload(PNID, "wamid.X", "delivered", "919999000001", "row-1", "utility", True)
    body = {"object": "whatsapp_business_account", "entry": a["entry"] + b["entry"] + st["entry"]
            + [{"id": "W", "changes": [{"field": "message_template_status_update", "value": {"event": "APPROVED"}}]}]}
    batch = parse(body)
    assert [e.wa_id for e in batch.messages] == ["919999000001", "919999000002"]
    assert batch.statuses[0].status == "delivered" and batch.statuses[0].callback_data == "row-1"
    assert batch.statuses[0].pricing_category == "utility" and batch.statuses[0].billable is True
    assert batch.other[0].field == "message_template_status_update"


def test_parse_ignores_malformed_entries() -> None:
    assert parse({"entry": [{"changes": [{"field": "messages", "value": {"messages": [{"type": "text"}]}}]}]}).messages == []
    assert parse({}).messages == []


# ── builders and limits ───────────────────────────────────────────────────

def test_buttons_are_clipped_to_meta_limits() -> None:
    msg = m.Buttons("Your order", [m.Button(f"b{i}", "A very long button title indeed") for i in range(5)])
    p = m.payload(msg)
    assert len(p["interactive"]["action"]["buttons"]) == 3
    assert all(len(b["reply"]["title"]) <= 20 for b in p["interactive"]["action"]["buttons"])
    assert m.limit_violations(p) == []


def test_list_keeps_ten_rows_and_clips_titles() -> None:
    rows = [m.Row(f"item:{i}", f"Extremely long dish name number {i}", "₹ 190 · veg · " + "x" * 100) for i in range(14)]
    p = m.payload(m.ListMessage("Menu", "See the menu please now", [m.Section("Cold coffee drinks and more", rows)]))
    out_rows = p["interactive"]["action"]["sections"][0]["rows"]
    assert len(out_rows) == 10
    assert m.limit_violations(p) == []


def test_recommendation_list_of_ten_rows_is_within_meta_limits() -> None:
    from app.domain.conversation import render

    picks = [(f"id-{i}", f"A dish with a long long name {i}", 1234500, i % 2 == 0, "Why " * 30) for i in range(3)]
    p = m.payload(render.recommendations(picks, "Intro " * 40, allergy=True, editing=1048))
    rows = [r for s in p["interactive"]["action"]["sections"] for r in s["rows"]]
    assert len(rows) == 10 and m.limit_violations(p) == []


def test_limit_checker_flags_a_raw_violation() -> None:
    bad = {"type": "interactive", "interactive": {"type": "button", "body": {"text": "x"},
           "action": {"buttons": [{"type": "reply", "reply": {"id": "a", "title": "x" * 25}}]}}}
    assert m.limit_violations(bad) == ["button title > 20"]


def test_template_payload() -> None:
    p = m.payload(m.Template("order_update", ["1048", "being made"]))
    assert p["template"]["name"] == "order_update"
    assert [x["text"] for x in p["template"]["components"][0]["parameters"]] == ["1048", "being made"]


# ── Meta transport classification ─────────────────────────────────────────

CAFE = Cafe(id="c", name="Kaapi", status="live", phone_number_id=PNID, display_phone_number=None,
            token_ref="env:WA_TEST_TOKEN", timezone="Asia/Kolkata", opening_hours={}, cutoff_minutes=30, settings={})


async def _send_with(handler, monkeypatch: pytest.MonkeyPatch) -> str:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WA_TEST_TOKEN", "test-token")
    return await MetaTransport(httpx.MockTransport(handler)).send(CAFE, "919999000001", {"type": "text"}, "row-1")


async def test_meta_success_returns_wamid(monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["biz_opaque_callback_data"] == "row-1" and body["to"] == "919999000001"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]})
    assert await _send_with(ok, monkeypatch) == "wamid.OK"


@pytest.mark.parametrize("response,expected", [
    (httpx.Response(429, json={"error": {"code": 130429}}), SendClass.NOT_SENT_RETRY),
    (httpx.Response(500, json={}), SendClass.AMBIGUOUS),
    (httpx.Response(401, json={"error": {"code": 190}}), SendClass.TOKEN_INVALID),
    (httpx.Response(400, json={"error": {"code": 131026}}), SendClass.FAILED),
])
async def test_meta_error_classes(response: httpx.Response, expected: SendClass, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SendError) as err:
        await _send_with(lambda _: response, monkeypatch)
    assert err.value.cls is expected


async def test_connect_error_is_not_sent_and_timeout_is_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SendError) as e1:
        await _send_with(refuse, monkeypatch)
    with pytest.raises(SendError) as e2:
        await _send_with(hang, monkeypatch)
    assert e1.value.cls is SendClass.NOT_SENT_RETRY and e2.value.cls is SendClass.AMBIGUOUS


# ── webhook gate (no database needed: a bad signature never reaches it) ───

def test_tampered_signature_is_401_and_touches_nothing() -> None:
    body, _ = simulated.message_payload(PNID, "919999000001", None, "text", text="hi")
    raw = json.dumps(body).encode()
    header = sign(raw, "test-app-secret")
    client = TestClient(create_app(), raise_server_exceptions=True)
    r = client.post("/webhooks/whatsapp", content=raw + b" ", headers={"X-Hub-Signature-256": header})
    assert r.status_code == 401
    r = client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": sign(raw, "wrong")})
    assert r.status_code == 401
    r = client.post("/webhooks/whatsapp", content=raw)
    assert r.status_code == 401


def test_verify_handshake() -> None:
    client = TestClient(create_app())
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token",
                                                  "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                   "hub.challenge": "12345"})
    assert bad.status_code == 403


def test_dev_router_needs_the_sim_token() -> None:
    client = TestClient(create_app())
    assert client.get("/dev/sim/faults").status_code == 401
    assert client.get("/dev/sim/faults", headers={"X-Sim-Token": "test-sim-token"}).status_code == 200
