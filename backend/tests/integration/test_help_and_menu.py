"""Human help (S10) and menu availability (S11) through the pipeline and the REST API."""

from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from app.domain import timers
from app.domain.ai_agent import prompt
from app.domain.conversation import pipeline
from app.integrations import llm
from app.integrations.llm import StubLlm
from tests.integration.conftest import CafeFixture, drop_test_cafe
from tests.integration.conversation_kit import (
    Kitchen,
    RecordingTransport,
    TaskRecorder,
    build_kitchen,
    create_staff,
    drop_staff,
    install,
    uninstall,
)
from tests.integration.test_staff_api import client_as

pytestmark = pytest.mark.integration
WA = "919999730001"


@pytest.fixture
async def k(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Kitchen]:
    async with db.acquire() as c:
        items, tables = await build_kitchen(c, cafe.cafe_id)
        staff = await create_staff(c, cafe.cafe_id)
        pnid = await c.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id)
    transport, rec = RecordingTransport(), TaskRecorder()
    install(transport, rec)
    prompt._cache.clear()
    try:
        yield Kitchen(db, cafe.cafe_id, pnid, items, tables, transport, rec, staff)
    finally:
        uninstall()
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


async def conv(k: Kitchen) -> asyncpg.Record:
    return await k.db.fetchrow(
        """select cv.id::text, cv.mode, cv.assigned_to::text, cv.table_session_id::text from conversations cv
           join customers c on c.id = cv.customer_id where cv.cafe_id = $1 and c.wa_id = $2""", k.cafe_id, WA)


async def test_call_waiter_ack_resolve_hands_back(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, "Aman")
    await k.say(WA, "call waiter")
    req = await k.db.fetchrow("select id::text, status from requests where cafe_id = $1 and kind = 'help'", k.cafe_id)
    assert req["status"] == "open" and k.rec.timers("help_escalation")
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/requests/{req['id']}/ack")
        assert r.status_code == 200 and r.json()["status"] == "acknowledged" and r.json()["acknowledged_by"] == k.staff["shift_manager"]
        assert (await api.post(f"/requests/{req['id']}/ack")).status_code == 200  # no-op
        r = await api.post(f"/requests/{req['id']}/resolve")
        assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert (await conv(k))["mode"] == "bot"
    actions = [a["action"] for a in await k.db.fetch("select action from audit_log where cafe_id = $1", k.cafe_id)]
    assert actions.count("request.ack") == 1 and actions.count("request.resolve") == 1
    back = await k.say(WA, "menu")
    assert back and "cat:" in " ".join(back[0].ids)  # the bot answers again


async def test_staff_reply_takes_over_and_customer_messages_stay_with_staff(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    c = await conv(k)
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/conversations/{c['id']}/reply", json={"text": "Coming to your table now"})
        assert r.status_code == 200 and r.json()["mode"] == "human" and r.json()["assigned_to"] == k.staff["shift_manager"]
    assert k.transport.sent[-1].text == "Coming to your table now"
    before = len(k.transport.sent)
    assert await k.say(WA, "thanks!") == [] and len(k.transport.sent) == before
    assert k.rec.timers("idle_handback")
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/conversations/{c['id']}/handback")
        assert r.status_code == 200 and r.json()["mode"] == "bot"
    audit = [a["action"] for a in await k.db.fetch("select action, change from audit_log where cafe_id = $1", k.cafe_id)]
    assert {"chat.takeover", "chat.reply", "chat.handback"} <= set(audit)
    change = await k.db.fetchval("select change from audit_log where cafe_id = $1 and action = 'chat.reply'", k.cafe_id)
    assert "Coming" not in str(change)  # message text never in the audit log


async def test_reply_after_the_window_closed_is_409(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    c = await conv(k)
    await k.db.execute("update customers set last_inbound_at = now() - interval '25 hours' where cafe_id = $1 and wa_id = $2",
                       k.cafe_id, WA)
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/conversations/{c['id']}/reply", json={"text": "Hello?"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "window_closed"


async def test_idle_handback_only_when_nothing_is_unanswered(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    c = await conv(k)
    async with client_as(k, "shift_manager") as api:
        await api.post(f"/conversations/{c['id']}/takeover")
    await k.say(WA, "hello? anyone?")  # unanswered
    await k.db.execute("update inbound_messages set received_at = received_at - interval '20 minutes' where conversation_id = $1", c["id"])
    await k.db.execute("update wa_messages set created_at = created_at - interval '30 minutes' where cafe_id = $1", k.cafe_id)
    await timers.dispatch({"kind": "idle_handback", "cafe_id": k.cafe_id, "subject_id": c["id"]})
    assert (await conv(k))["mode"] == "human"  # the customer is still waiting for an answer
    async with client_as(k, "shift_manager") as api:
        await api.post(f"/conversations/{c['id']}/reply", json={"text": "Here you go"})
    await k.db.execute("update wa_messages set created_at = created_at - interval '20 minutes' where cafe_id = $1", k.cafe_id)
    await timers.dispatch({"kind": "idle_handback", "cafe_id": k.cafe_id, "subject_id": c["id"]})
    assert (await conv(k))["mode"] == "bot"


async def test_help_escalation_at_two_minutes(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, "call waiter")
    body = k.rec.timers("help_escalation")[-1]
    await timers.dispatch(body)
    assert await k.db.fetchval("select count(*) from escalations where cafe_id = $1 and subject_type = 'request'", k.cafe_id) == 1
    alerts = [s for s in k.transport.sent if s.body.get("type") == "template"]
    assert {a.to for a in alerts} == {"919999000900", "919999000802"}
    assert k.transport.sent[-1].text == "Sorry, the team is busy — someone is coming to you."
    sent = len(k.transport.sent)
    await timers.dispatch(body)  # twice: nothing more
    assert len(k.transport.sent) == sent


async def test_bill_request_and_delivery(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, "Aman")
    await k.say(WA, tap=f"item:{k.items['Masala Chai']}")
    version = (await k.state(WA))["cart"]["version"]
    await k.say(WA, tap=f"confirm:{version}")
    await k.say(WA, "bill please")
    c = await conv(k)
    assert c["mode"] == "bot"
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/bills/{c['table_session_id']}/deliver")
        assert r.status_code == 200 and r.json()["total_paise"] == 9000 and len(r.json()["orders"]) == 1
    assert await k.db.fetchval("select closed_at is not null from table_sessions where id = $1", c["table_session_id"])
    assert await k.db.fetchval("select status from requests where cafe_id = $1 and kind = 'bill'", k.cafe_id) == "resolved"


async def test_message_a_table(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    c = await conv(k)
    async with client_as(k, "shift_manager") as api:
        r = await api.post("/conversations/open", json={"table_session_id": c["table_session_id"]})
        assert r.status_code == 200 and r.json()["mode"] == "human"
        empty = await k.db.fetchval("insert into table_sessions (cafe_id, table_id) values ($1, $2) returning id::text",
                                    k.cafe_id, k.tables["T2"])
        r = await api.post("/conversations/open", json={"table_session_id": empty})
        assert r.status_code == 422


async def test_dead_letter_opens_help_and_human_mode(k: Kitchen) -> None:
    from app.domain.handoff import service  # noqa: F401  (registers the hook)

    await k.say(WA, "Hi, I'm at Table 5")
    c = await conv(k)
    await pipeline.dead_letter(k.cafe_id, c["id"], "work-x")
    assert "something went wrong" in k.transport.sent[-1].text
    assert (await conv(k))["mode"] == "human"
    assert await k.db.fetchval("select reason_tag from requests where conversation_id = $1", c["id"]) == "system_error"


async def test_notifications_and_telemetry(k: Kitchen) -> None:
    async with k.db.acquire() as c:
        await c.execute("""insert into notifications (cafe_id, user_id, kind, subject_type, subject_id, payload)
                           values ($1, $2, 'order_cancelled', 'order', gen_random_uuid(), '{}')""", k.cafe_id, k.staff["manager"])
    nid = await k.db.fetchval("select id::text from notifications where user_id = $1", k.staff["manager"])
    async with client_as(k, "shift_manager") as api:
        assert (await api.post(f"/notifications/{nid}/ack")).status_code in (401, 403)  # manager+ only
        r = await api.post("/telemetry/console", json={"events": [{"kind": "realtime_disconnect", "at": "2026-10-24T12:00:00Z"}]})
        assert r.status_code == 204
    async with client_as(k, "manager") as api:
        assert (await api.post(f"/notifications/{nid}/ack")).json() == {"read": 1}
        assert (await api.post("/notifications/ack-all")).json() == {"read": 0}


async def test_sold_out_toggle_reaches_prompt_carts_and_open_orders(k: Kitchen) -> None:
    systems: list[str] = []

    def respond(system: str, user: str) -> dict[str, Any]:
        systems.append(system)
        return {"intent": "browse", "cart_ops": [], "allergen_question": None,
                "needs_human": {"flag": False, "reason": None}, "clarify": None, "reply": "What would you like?"}
    llm.set_client(StubLlm(respond))
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, "Aman")
    await k.say(WA, tap=f"item:{k.items['Avocado Toast']}")
    await k.say(WA, "what's good?")
    assert "Avocado Toast" in systems[-1]
    # Another customer's open order with the toast:
    other = "919999730002"
    await k.say(other, "Hi, I'm at Table 2")
    await k.say(other, "Bea")
    await k.say(other, tap=f"item:{k.items['Avocado Toast']}")
    v = (await k.state(other))["cart"]["version"]
    await k.say(other, tap=f"confirm:{v}")

    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/menu/{k.items['Avocado Toast']}/availability", json={"available": False})
        assert r.status_code == 200 and r.json()["available"] is False
        assert (await api.post(f"/menu/{k.items['Avocado Toast']}/availability", json={"available": False})).status_code == 200
    assert [a["action"] for a in await k.db.fetch("select action from audit_log where cafe_id = $1", k.cafe_id)].count("menu.availability") == 1
    assert "item_sold_out" in (await k.orders())[0]["flags"]

    await k.say(WA, "anything else?")
    assert "Avocado Toast" not in systems[-1]  # the very next message
    summary = await k.say(WA, tap=f"item:{k.items['Masala Chai']}")
    assert any("Avocado Toast just sold out" in s.text for s in summary)
