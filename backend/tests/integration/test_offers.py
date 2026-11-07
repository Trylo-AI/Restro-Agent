"""Counter offers (docs/backend.md#redemption, rules.md Redemption row): one verdict for both channels; one redemption
per code across channels; same key → one row; concurrent → one wins; one offer per visit; other café → not found;
lookup never returns a full number. Also the recipients CSV and the campaign insights view."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from app.core import auth
from app.domain.ai_agent import prompt
from app.main_api import create_app
from app.ops.tracing import recorder
from app.ops.tracing.trace import Run
from tests.integration.conftest import CafeFixture, create_test_cafe, drop_test_cafe
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
from tests.unit.test_auth import FakeJwks, make_token

pytestmark = pytest.mark.integration

CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
OFFER_CHECK_KEYS = {"recipient_id", "code", "campaign_id", "campaign_name", "offer_text", "discount_percent",
                    "valid_until", "sent_at", "recipient_status", "verdict", "reason", "warning", "matched",
                    "redemption"}
OFFER_CUSTOMER_KEYS = {"customer_id", "name", "first_name", "masked_phone", "deleted", "visits", "last_visit_at",
                       "open_bill", "offers"}
COUNTER_KEYS = {"redemption_id", "code", "channel", "campaign_id", "campaign_name", "offer_text", "discount_percent",
                "customer_id", "first_name", "bill_paise", "discount_paise", "total_paise", "redeemed_at",
                "redeemed_by_name"}
D28_KEYS = {"table_session_id", "order_id", "order_number", "campaign_id", "recipient_id", "customer_id", "code",
            "channel", "discount_percent", "bill_paise", "discount_paise"}


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


# ── helpers ───────────────────────────────────────────────────────────────

def wa() -> str:
    return "9199997" + "".join(secrets.choice("0123456789") for _ in range(5))


async def customer(k: Kitchen, name: str | None, wa_id: str | None = None, cafe_id: str | None = None) -> str:
    return str(await k.db.fetchval(
        "insert into customers (cafe_id, wa_id, name, last_visit_at) values ($1, $2, $3, now() - interval '9 days') "
        "returning id::text", cafe_id or k.cafe_id, wa_id or wa(), name))


async def offer(k: Kitchen, customer_id: str, *, percent: int | None = 20, valid: datetime | None = None,
                status: str = "delivered", campaign_status: str = "sending", age_days: int = 2,
                cafe_id: str | None = None, offer_text: str | None = "20% off your next visit",
                name: str = "Weekend comeback") -> dict[str, str]:
    prefix = "T" + secrets.token_hex(2).upper()
    code = f"{prefix}-{''.join(secrets.choice(CHARS) for _ in range(3))}"
    cafe = cafe_id or k.cafe_id
    campaign = await k.db.fetchval(
        """insert into campaigns (cafe_id, name, segment, template_name, offer_text, discount_percent, code_prefix,
                                  valid_until, status, scheduled_at)
           values ($1, $2, 'all_opted_in', 'weekly_offer', $3, $4, $5, $6, $7, now() - make_interval(days => $8))
           returning id::text""",
        cafe, name, offer_text, percent, prefix, valid or datetime.now(UTC) + timedelta(days=7), campaign_status,
        age_days)
    recipient = await k.db.fetchval(
        """insert into campaign_recipients (cafe_id, campaign_id, customer_id, code, status, created_at, updated_at)
           values ($1, $2, $3, $4, $5, now() - make_interval(days => $6), now() - make_interval(days => $6))
           returning id::text""", cafe, campaign, customer_id, code, status, age_days)
    return {"code": code, "campaign_id": campaign, "recipient_id": recipient}


async def seat(k: Kitchen, customer_id: str, total: int = 64000) -> dict[str, Any]:
    """An open table bill with one order for the customer, at a table of its own."""
    label = "X" + secrets.token_hex(3).upper()
    table = await k.db.fetchval("insert into tables (cafe_id, label, qr_text) values ($1, $2, $3) returning id",
                                k.cafe_id, label, f"Hi, I'm at Table {label}")
    session = await k.db.fetchval("insert into table_sessions (cafe_id, table_id) values ($1, $2) returning id::text",
                                  k.cafe_id, table)
    number = await k.db.fetchval("select next_order_number($1)", k.cafe_id)
    await k.db.execute(
        """insert into orders (cafe_id, customer_id, table_session_id, number, status, expected_minutes, total_paise,
                               source, idempotency_key)
           values ($1, $2, $3, $4, 'NEW', 5, $5, 'qr', $6)""",
        k.cafe_id, customer_id, session, number, total, f"offers-test-{secrets.token_hex(6)}")
    return {"session": session, "label": label, "number": number, "total": total}


async def redeem_direct(k: Kitchen, o: dict[str, str], *, bill: int = 124000, discount: int = 24800,
                        ago_minutes: int = 0, channel: str = "counter", session: str | None = None) -> None:
    await k.db.execute(
        """insert into redemptions (cafe_id, campaign_recipient_id, code, discount_paise, redeemed_by, channel,
                                    bill_paise, table_session_id, redeemed_at)
           values ($1, $2, $3, $4, $5, $6, $7, $8, now() - make_interval(mins => $9))""",
        k.cafe_id, o["recipient_id"], o["code"], discount, k.staff["shift_manager"], channel, bill, session,
        ago_minutes)


async def lookup(api: httpx.AsyncClient, q: str) -> dict[str, Any]:
    r = await api.post("/offers/lookup", json={"q": q})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def redeem(api: httpx.AsyncClient, code: str, bill: int, key: str | None = None) -> Any:
    headers = {"Idempotency-Key": key or f"counter-{secrets.token_hex(6)}"}
    return api.post("/offers/redeem", json={"code": code, "bill_paise": bill}, headers=headers)


async def count(k: Kitchen, sql: str, *args: Any) -> int:
    return int(await k.db.fetchval(sql, k.cafe_id, *args))


# ── lookup ────────────────────────────────────────────────────────────────

async def test_lookup_by_name_lists_offers_with_verdicts_and_reasons(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    await customer(k, "Arjun Mehta")
    eligible = await offer(k, riya, age_days=1)
    used = await offer(k, riya, age_days=2)
    await redeem_direct(k, used, ago_minutes=90)
    expired = await offer(k, riya, age_days=3, valid=datetime.now(UTC) - timedelta(days=3))
    cancelled = await offer(k, riya, age_days=4, campaign_status="cancelled")
    queued = await offer(k, riya, age_days=5, status="queued")
    failed = await offer(k, riya, age_days=6, status="failed")
    async with client_as(k, "shift_manager") as api:
        out = await lookup(api, "riya")
        assert out["query_kind"] == "name" and out["truncated"] is False and len(out["customers"]) == 1
        cu = out["customers"][0]
        assert set(cu) == OFFER_CUSTOMER_KEYS
        assert cu["first_name"] == "Riya" and cu["name"] == "Riya Sharma" and cu["deleted"] is False
        offers = cu["offers"]
        assert all(set(o) == OFFER_CHECK_KEYS for o in offers)
        assert [o["code"] for o in offers] == [x["code"] for x in (eligible, used, expired, cancelled, queued, failed)]
        assert [o["verdict"] for o in offers] == ["eligible", "redeemed", "expired", "campaign_cancelled", "not_sent",
                                                  "not_delivered"]
        assert offers[0]["reason"].startswith("20 % off the bill · valid till ") and offers[0]["matched"] is False
        assert offers[1]["reason"].endswith("at the counter by Ravi") and offers[1]["redemption"]["channel"] == "counter"
        assert offers[1]["redemption"]["discount_paise"] == 24800 and offers[1]["redemption"]["bill_paise"] == 124000
        assert offers[2]["reason"].startswith("Expired on ")
        assert offers[3]["reason"] == "The café cancelled this offer."
        assert offers[4]["reason"] == "Not sent yet: the customer hasn't received this code."
        assert offers[5]["reason"] == "This message failed: the customer never got the code."
        assert (await lookup(api, "%%"))["customers"] == []  # LIKE wildcards are escaped
        assert (await lookup(api, "zzz-nobody"))["customers"] == []


async def test_lookup_by_last4_and_code_never_returns_full_number(k: Kitchen) -> None:
    number = "919999764821"
    meera = await customer(k, "Meera Iyer", number)
    o = await offer(k, meera)
    async with client_as(k, "shift_manager") as api:
        for q, kind in (("4821", "phone_last4"), (o["code"].lower(), "code")):
            r = await api.post("/offers/lookup", json={"q": q})
            assert r.status_code == 200 and number not in r.text and "wa_id" not in r.text
            out = r.json()
            assert out["query_kind"] == kind and [c["customer_id"] for c in out["customers"]] == [meera]
            assert out["customers"][0]["masked_phone"] == "+91 99••• ••821"
        assert (await lookup(api, o["code"]))["customers"][0]["offers"][0]["matched"] is True


async def test_lookup_other_cafe_customer_and_code_not_found(k: Kitchen, db: asyncpg.Pool) -> None:
    async with db.acquire() as c:
        other = await create_test_cafe(c)
    try:
        zara = await customer(k, "Zara Other", "919999765555", cafe_id=other.cafe_id)
        theirs = await offer(k, zara, cafe_id=other.cafe_id)
        async with client_as(k, "shift_manager") as api:
            for q in ("Zara", "5555", theirs["code"]):
                assert (await lookup(api, q))["customers"] == [], q
            r = await redeem(api, theirs["code"], 50000)
            assert r.status_code == 422 and r.json()["error"]["details"] == {"verdict": "not_found"}
            assert r.json()["error"]["message"] == "This isn't a valid code, or it has expired."
    finally:
        async with db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)


async def test_lookup_deleted_customer_only_by_code(k: Kitchen) -> None:
    gone = await customer(k, "Kabir Deleted", "919999766543")
    o = await offer(k, gone)
    await k.db.execute("update customers set name = null, wa_id = '000123456786543' where id = $1", gone)
    async with client_as(k, "shift_manager") as api:
        assert (await lookup(api, "Kabir"))["customers"] == []
        assert (await lookup(api, "6543"))["customers"] == []
        cu = (await lookup(api, o["code"]))["customers"][0]
        assert cu["deleted"] is True and cu["name"] is None and cu["first_name"] is None and cu["masked_phone"] is None
        assert cu["offers"][0]["verdict"] == "eligible"
        r = await redeem(api, o["code"], 10000)  # a bearer code: still the café's promise
        assert r.status_code == 200 and r.json()["first_name"] is None


async def test_lookup_open_bill_and_code_on_it(k: Kitchen) -> None:
    asha = await customer(k, "Asha Rao")
    first, second = await offer(k, asha, age_days=2), await offer(k, asha, age_days=1)
    bill = await seat(k, asha, 64000)
    async with client_as(k, "shift_manager") as api:
        cu = (await lookup(api, "Asha"))["customers"][0]
        assert cu["open_bill"] == {"table_session_id": bill["session"], "table_label": bill["label"],
                                   "subtotal_paise": 64000, "code": None}
        applied = await api.post(f"/bills/{bill['session']}/apply-code", json={"code": first["code"]})
        assert applied.status_code == 200, applied.text
        cu = (await lookup(api, "Asha"))["customers"][0]
        assert cu["open_bill"]["code"] == first["code"]
        by_code = {o["code"]: o for o in cu["offers"]}
        assert by_code[first["code"]]["verdict"] == "redeemed"
        assert by_code[first["code"]]["reason"].startswith("Used today at ") and \
            f"on the {bill['label']} bill (#{bill['number']}) by Ravi" in by_code[first["code"]]["reason"]
        assert by_code[second["code"]]["verdict"] == "eligible"
        assert by_code[second["code"]]["warning"].startswith(f"Asha used {first['code']} at ")
    await k.db.execute("update table_sessions set closed_at = now() where id = $1", bill["session"])
    async with client_as(k, "shift_manager") as api:
        assert (await lookup(api, "Asha"))["customers"][0]["open_bill"] is None


async def test_lookup_bad_digit_count_400(k: Kitchen) -> None:
    async with client_as(k, "shift_manager") as api:
        for q in ("123", "12345", "98765 43210"):
            r = await api.post("/offers/lookup", json={"q": q})
            assert r.status_code == 400 and r.json()["error"]["message"] == "Type the last 4 digits of the number.", q
        assert (await api.post("/offers/lookup", json={"q": "1"})).status_code == 400  # under 2 characters
        assert (await api.post("/offers/lookup", json={"q": "   "})).status_code == 400  # blank, never "everyone"
        assert (await api.post("/offers/lookup", json={"q": "Riya", "cafe_id": k.cafe_id})).status_code == 400


async def test_lookup_warns_recent_redemption_and_opt_out(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    a, b = await offer(k, riya, age_days=2), await offer(k, riya, age_days=1)
    sana = await customer(k, "Sana Khan")
    s = await offer(k, sana)
    await k.db.execute("insert into consents (cafe_id, customer_id, status, source) values ($1, $2, 'opted_out', 'keyword')",
                       k.cafe_id, sana)
    dev = await customer(k, "Dev Kapoor")
    await offer(k, dev, status="held")
    async with client_as(k, "shift_manager") as api:
        assert (await redeem(api, a["code"], 50000)).status_code == 200
        rows = {name: (await lookup(api, name))["customers"][0] for name in ("Riya", "Sana", "Dev")}
        riya_b = next(o for o in rows["Riya"]["offers"] if o["code"] == b["code"])
        assert riya_b["verdict"] == "eligible"
        assert riya_b["warning"].startswith(f"Riya used {a['code']} at ")
        assert riya_b["warning"].endswith("One offer per visit at the counter.")
        sana_s = rows["Sana"]["offers"][0]
        assert sana_s["code"] == s["code"] and sana_s["warning"] == "Opted out of offers since; this code still works."
        assert rows["Dev"]["offers"][0]["verdict"] == "eligible"
        assert rows["Dev"]["offers"][0]["warning"] == "WhatsApp held this message; the customer may not have seen it."


async def test_lookup_finds_code_typed_without_dash_or_lowercase(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    o = await offer(k, riya)
    async with client_as(k, "shift_manager") as api:
        dashless = await lookup(api, o["code"].replace("-", "").lower())
        assert dashless["query_kind"] == "name" and [c["customer_id"] for c in dashless["customers"]] == [riya]
        assert dashless["customers"][0]["offers"][0]["matched"] is True
        lower = await lookup(api, o["code"].lower())
        assert lower["query_kind"] == "code" and lower["customers"][0]["offers"][0]["matched"] is True
        r = await redeem(api, " " + o["code"].lower() + " ", 20000)
        assert r.status_code == 200 and r.json()["code"] == o["code"]


# ── counter redemption ────────────────────────────────────────────────────

async def test_counter_redeem_discount_audit_and_row_shape(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    o = await offer(k, riya, percent=20)
    async with client_as(k, "shift_manager") as api:
        r = await redeem(api, o["code"], 124000, "counter-shape-01")
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == COUNTER_KEYS
    assert (out["code"], out["channel"], out["bill_paise"], out["discount_paise"], out["total_paise"]) == \
        (o["code"], "counter", 124000, 24800, 99200)
    assert (out["campaign_id"], out["campaign_name"], out["customer_id"], out["first_name"], out["redeemed_by_name"],
            out["discount_percent"]) == (o["campaign_id"], "Weekend comeback", riya, "Riya", "Ravi", 20)
    row = await k.db.fetchrow("select * from redemptions where id = $1", out["redemption_id"])
    assert (row["channel"], row["table_session_id"], row["order_id"], row["bill_paise"], row["discount_paise"],
            row["idempotency_key"], str(row["redeemed_by"])) == \
        ("counter", None, None, 124000, 24800, "counter-shape-01", k.staff["shift_manager"])
    audit = await k.db.fetch("select actor_role, change from audit_log where cafe_id = $1 and action = 'offer.redeem'",
                             k.cafe_id)
    assert len(audit) == 1 and audit[0]["actor_role"] == "shift_manager"
    change = audit[0]["change"]
    assert set(change) == D28_KEYS
    assert change["channel"] == "counter" and change["code"] == o["code"] and change["customer_id"] == riya
    assert (change["bill_paise"], change["discount_paise"], change["discount_percent"]) == (124000, 24800, 20)
    assert change["table_session_id"] is None and change["order_id"] is None and change["order_number"] is None

    free = await offer(k, await customer(k, "Neel Shah"), percent=None, offer_text="a free cookie")
    async with client_as(k, "shift_manager") as api:
        r = await redeem(api, free["code"], 45000)
    assert r.status_code == 200 and r.json()["discount_paise"] == 0 and r.json()["total_paise"] == 45000


async def test_counter_redeem_same_key_returns_same_row_one_audit(k: Kitchen) -> None:
    o = await offer(k, await customer(k, "Riya Sharma"))
    async with client_as(k, "shift_manager") as api:
        first = await redeem(api, o["code"], 124000, "same-key-0001")
        again = await redeem(api, o["code"], 124000, "same-key-0001")
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 1
    assert await count(k, "select count(*) from audit_log where cafe_id = $1 and action = 'offer.redeem'") == 1


async def test_counter_redeem_same_key_concurrent_two_200s_one_row(k: Kitchen) -> None:
    o = await offer(k, await customer(k, "Riya Sharma"))
    async with client_as(k, "shift_manager") as api:
        a, b = await asyncio.gather(redeem(api, o["code"], 124000, "concurrent-key-1"),
                                    redeem(api, o["code"], 124000, "concurrent-key-1"))
    assert (a.status_code, b.status_code) == (200, 200), (a.text, b.text)
    assert a.json()["redemption_id"] == b.json()["redemption_id"]
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 1
    assert await count(k, "select count(*) from audit_log where cafe_id = $1 and action = 'offer.redeem'") == 1


async def test_counter_redeem_same_key_other_code_or_amount_400(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    o, other = await offer(k, riya), await offer(k, await customer(k, "Arjun Mehta"))
    async with client_as(k, "shift_manager") as api:
        assert (await redeem(api, o["code"], 124000, "reused-key-001")).status_code == 200
        amount = await redeem(api, o["code"], 125000, "reused-key-001")
        assert amount.status_code == 400
        assert amount.json()["error"]["message"] == "This request was already used for another code or amount."
        assert (await redeem(api, other["code"], 124000, "reused-key-001")).status_code == 400
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 1


async def test_counter_redeem_second_key_409_with_details(k: Kitchen) -> None:
    o = await offer(k, await customer(k, "Riya Sharma"))
    async with client_as(k, "shift_manager") as api:
        first = await redeem(api, o["code"], 124000)
        second = await redeem(api, o["code"], 124000)
    assert first.status_code == 200 and second.status_code == 409
    err = second.json()["error"]
    assert err["code"] == "code_already_used" and err["message"].startswith("Used today at ")
    assert err["message"].endswith("at the counter by Ravi")
    assert err["details"]["channel"] == "counter" and err["details"]["redeemed_by_name"] == "Ravi"
    assert err["details"]["redeemed_at"] == first.json()["redeemed_at"]
    assert err["details"]["order_number"] is None and err["details"]["table_label"] is None


async def test_counter_then_table_bill_409_and_table_then_counter_409(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    o = await offer(k, riya)
    bill = await seat(k, riya)
    arjun = await customer(k, "Arjun Mehta")
    p = await offer(k, arjun)
    arjun_bill = await seat(k, arjun)
    async with client_as(k, "shift_manager") as api:
        assert (await redeem(api, o["code"], 124000)).status_code == 200
        r = await api.post(f"/bills/{bill['session']}/apply-code", json={"code": o["code"]})
        assert r.status_code == 409 and r.json()["error"]["message"] == "This code was already used today at the counter."
        assert r.json()["error"]["details"]["channel"] == "counter"

        assert (await api.post(f"/bills/{arjun_bill['session']}/apply-code", json={"code": p["code"]})).status_code == 200
        r = await redeem(api, p["code"], 50000)
        assert r.status_code == 409 and r.json()["error"]["code"] == "code_already_used"
        details = r.json()["error"]["details"]
        assert (details["channel"], details["order_number"], details["table_label"]) == \
            ("table_bill", arjun_bill["number"], arjun_bill["label"])
        assert f"on the {arjun_bill['label']} bill (#{arjun_bill['number']})" in r.json()["error"]["message"]
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 2


async def test_concurrent_counter_redeems_one_wins(k: Kitchen) -> None:
    o = await offer(k, await customer(k, "Riya Sharma"))
    async with client_as(k, "shift_manager") as api:
        results = await asyncio.gather(redeem(api, o["code"], 124000, "till-one-0001"),
                                       redeem(api, o["code"], 124000, "till-two-0001"))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["error"]["code"] == "code_already_used"
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 1
    assert await count(k, "select count(*) from audit_log where cafe_id = $1 and action = 'offer.redeem'") == 1


async def test_concurrent_counter_and_table_bill_same_code_one_wins(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    o = await offer(k, riya)
    bill = await seat(k, riya)
    async with client_as(k, "shift_manager") as api:
        counter, table = await asyncio.gather(
            redeem(api, o["code"], 124000),
            api.post(f"/bills/{bill['session']}/apply-code", json={"code": o["code"]}))
    assert sorted((counter.status_code, table.status_code)) == [200, 409], (counter.text, table.text)
    loser = counter if counter.status_code == 409 else table
    assert loser.json()["error"]["code"] == "code_already_used"
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 1


async def test_one_offer_per_visit_409(k: Kitchen) -> None:
    riya = await customer(k, "Riya Sharma")
    a, b = await offer(k, riya, age_days=2), await offer(k, riya, age_days=1)
    arjun = await customer(k, "Arjun Mehta")
    c, d = await offer(k, arjun, age_days=2), await offer(k, arjun, age_days=1)
    arjun_bill = await seat(k, arjun)
    sana = await customer(k, "Sana Khan")
    e, f = await offer(k, sana, age_days=2), await offer(k, sana, age_days=1)
    sana_bill = await seat(k, sana)
    old = await customer(k, "Dev Kapoor")
    g, h = await offer(k, old, age_days=2), await offer(k, old, age_days=1)
    await redeem_direct(k, g, ago_minutes=61)
    async with client_as(k, "shift_manager") as api:
        assert (await redeem(api, a["code"], 50000)).status_code == 200
        r = await redeem(api, b["code"], 50000)
        assert r.status_code == 409 and r.json()["error"]["code"] == "already_redeemed_this_visit"
        msg = r.json()["error"]["message"]
        assert msg.startswith("Riya already used an offer at ") and msg.endswith(f"({a['code']}). One offer per visit.")

        # A table-bill redemption counts for the counter's 60 minutes …
        assert (await api.post(f"/bills/{arjun_bill['session']}/apply-code", json={"code": c["code"]})).status_code == 200
        r = await redeem(api, d["code"], 50000)
        assert r.status_code == 409 and r.json()["error"]["code"] == "already_redeemed_this_visit"
        # … but the table bill keeps "one code per bill" only (decision D10).
        assert (await redeem(api, e["code"], 50000)).status_code == 200
        assert (await api.post(f"/bills/{sana_bill['session']}/apply-code", json={"code": f["code"]})).status_code == 200
        # Over 60 minutes ago: a new visit.
        assert (await redeem(api, h["code"], 50000)).status_code == 200


async def test_redemption_errors_leave_no_name_or_code_in_the_trace(k: Kitchen,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The staff text (first name, code, staff name) goes in the response; the trace records the error code only."""
    runs: list[Run] = []
    monkeypatch.setattr(recorder, "finished", runs.append)
    monkeypatch.setattr(recorder, "started", lambda r: None)
    riya = await customer(k, "Riya Sharma")
    a, b = await offer(k, riya, age_days=2), await offer(k, riya, age_days=1)
    async with client_as(k, "shift_manager") as api:
        assert (await redeem(api, a["code"], 50000)).status_code == 200
        visit = await redeem(api, b["code"], 50000)
        used = await redeem(api, a["code"], 50000)
    assert visit.status_code == 409 and "Riya" in visit.json()["error"]["message"]
    assert used.status_code == 409 and "Ravi" in used.json()["error"]["message"]
    failed = {r.error_code: r for r in runs if r.error_code in ("already_redeemed_this_visit", "code_already_used")}
    assert set(failed) == {"already_redeemed_this_visit", "code_already_used"}
    for r in failed.values():
        reason = r.failure_reason or ""
        assert reason == f"{r.error_type}: {r.error_code}", reason
        assert all(s not in reason for s in ("Riya", "Ravi", a["code"], b["code"]))


async def test_counter_redeem_expiry_uses_cafe_local_day(k: Kitchen) -> None:
    ist = ZoneInfo("Asia/Kolkata")
    midnight = datetime.combine(datetime.now(ist).date(), time(0), tzinfo=ist)
    if datetime.now(UTC) - midnight < timedelta(seconds=5):
        pytest.skip("too close to café-local midnight")
    today = await offer(k, await customer(k, "Riya Sharma"), valid=midnight + timedelta(seconds=1))  # passed, same day
    yesterday = await offer(k, await customer(k, "Arjun Mehta"), valid=midnight - timedelta(seconds=1))
    async with client_as(k, "shift_manager") as api:
        ok = await redeem(api, today["code"], 50000)
        assert ok.status_code == 200, ok.text
        gone = await redeem(api, yesterday["code"], 50000)
        assert gone.status_code == 422 and gone.json()["error"]["details"] == {"verdict": "expired"}
        assert gone.json()["error"]["message"] == "This isn't a valid code, or it has expired."
        # The table bill reads the same date.
        bill = await seat(k, await customer(k, "Sana Khan"))
        assert (await api.post(f"/bills/{bill['session']}/apply-code", json={"code": yesterday["code"]})).status_code == 422


async def test_counter_redeem_ineligible_verdicts_422(k: Kitchen) -> None:
    cases = {
        "campaign_cancelled": ({"campaign_status": "cancelled"}, "The café cancelled this offer."),
        "not_sent": ({"status": "queued"}, "Not sent yet: the customer hasn't received this code."),
        "not_delivered": ({"status": "failed"}, "This message failed: the customer never got the code."),
        "expired": ({"valid": datetime.now(UTC) - timedelta(days=2)}, "This isn't a valid code, or it has expired."),
    }
    deferred = await offer(k, await customer(k, "Deferred Guest"), status="deferred")
    async with client_as(k, "shift_manager") as api:
        for verdict, (kw, message) in cases.items():
            o = await offer(k, await customer(k, f"Guest {verdict}"), **kw)
            r = await redeem(api, o["code"], 50000)
            assert r.status_code == 422, verdict
            assert r.json()["error"] == {"code": "invalid_code", "message": message, "details": {"verdict": verdict}}
        r = await redeem(api, deferred["code"], 50000)
        assert r.status_code == 422 and r.json()["error"]["details"] == {"verdict": "not_sent"}
        r = await redeem(api, "NOPE-ZZZ", 50000)
        assert r.status_code == 422 and r.json()["error"]["details"] == {"verdict": "not_found"}
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 0


async def test_counter_redeem_bill_bounds_and_missing_key_400(k: Kitchen) -> None:
    o = await offer(k, await customer(k, "Riya Sharma"))
    async with client_as(k, "shift_manager") as api:
        for bill in (0, 99, 10_000_001):
            r = await redeem(api, o["code"], bill)
            assert r.status_code == 400, bill
            assert r.json()["error"]["message"] == "Check the amount: between ₹ 1 and ₹ 1,00,000."
        r = await api.post("/offers/redeem", json={"code": o["code"], "bill_paise": 5000})
        assert r.status_code == 400 and r.json()["error"]["message"] == "Idempotency-Key header is required."
        assert (await redeem(api, o["code"], 5000, "short")).status_code == 400
        assert (await redeem(api, o["code"], 5000, "k" * 81)).status_code == 400
        extra = await api.post("/offers/redeem", json={"code": o["code"], "bill_paise": 5000, "discount_paise": 5000},
                               headers={"Idempotency-Key": "extra-field-01"})
        assert extra.status_code == 400
        floor = await redeem(api, o["code"], 100)
        assert floor.status_code == 200 and floor.json()["discount_paise"] == 20 and floor.json()["total_paise"] == 80
    assert await count(k, "select count(*) from redemptions where cafe_id = $1") == 1


def real_client(k: Kitchen, role: str, cafe_id: str | None = None) -> httpx.AsyncClient:
    """The real request check (JWT → membership → role), with a local key standing in for the JWKS."""
    token = make_token(sub=k.staff[role])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test",
                             headers={"X-Cafe-Id": cafe_id or k.cafe_id, "Authorization": f"Bearer {token}"})


async def test_counter_redeem_every_role_and_other_cafe_header_403(k: Kitchen, db: asyncpg.Pool,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_jwks", FakeJwks("http://jwks.test"))
    async with db.acquire() as c:
        other = await create_test_cafe(c)
    try:
        for role in ("shift_manager", "manager", "owner"):
            o = await offer(k, await customer(k, f"Guest {role}"))
            async with real_client(k, role) as api:
                assert (await api.post("/offers/lookup", json={"q": "Guest"})).status_code == 200, role
                r = await redeem(api, o["code"], 50000)
                assert r.status_code == 200, (role, r.text)
            async with real_client(k, role, other.cafe_id) as api:
                assert (await api.post("/offers/lookup", json={"q": "Guest"})).status_code == 403, role
                assert (await redeem(api, o["code"], 50000)).status_code == 403, role
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test",
                                     headers={"X-Cafe-Id": k.cafe_id}) as anon:
            assert (await anon.post("/offers/lookup", json={"q": "Guest"})).status_code == 401
    finally:
        async with db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)
    names = await k.db.fetch("select actor_role from audit_log where cafe_id = $1 and action = 'offer.redeem'", k.cafe_id)
    assert sorted(r["actor_role"] for r in names) == ["manager", "owner", "shift_manager"]


# ── owner: recipients CSV and insights ────────────────────────────────────

async def campaign_with_recipients(k: Kitchen) -> dict[str, Any]:
    """A campaign sent 5 days ago, valid until yesterday, with one recipient per outcome."""
    camp = await k.db.fetchval(
        """insert into campaigns (cafe_id, name, segment, template_name, offer_text, discount_percent, code_prefix,
                                  valid_until, status, scheduled_at)
           values ($1, 'Diwali', 'all_opted_in', 'weekly_offer', '20% off', 20, 'DIWALI', now() - interval '1 day',
                   'completed', now() - interval '5 days') returning id::text""", k.cafe_id)
    out: dict[str, Any] = {"campaign_id": camp}
    for key, name, number, status in (("ordered", "Asha Rao", "919999767001", "read"),
                                      ("redeemed", "Riya Sharma", "919999767002", "delivered"),
                                      ("failed", "Arjun Mehta", "919999767003", "failed"),
                                      ("opted_out", "Sana Khan", "919999767004", "delivered"),
                                      ("late", "Dev Kapoor", "919999767005", "sent")):
        cid = await customer(k, name, number)
        code = f"DIWALI-{''.join(secrets.choice(CHARS) for _ in range(3))}"
        rid = await k.db.fetchval(
            """insert into campaign_recipients (cafe_id, campaign_id, customer_id, code, status, created_at, updated_at)
               values ($1, $2, $3, $4, $5, now() - interval '5 days', now() - interval '5 days') returning id::text""",
            k.cafe_id, camp, cid, code, status)
        out[key] = {"customer_id": cid, "code": code, "recipient_id": rid, "wa_id": number}
    await k.db.execute(
        """insert into wa_messages (cafe_id, customer_id, idempotency_key, type, template_name, payload, status, sent_at,
                                    read_at, cost)
           values ($1, $2, $3, 'template', 'weekly_offer', '{}'::jsonb, 'read', now() - interval '5 days',
                   now() - interval '4 days', 0.88)""",
        k.cafe_id, out["ordered"]["customer_id"], f"campaign:{out['ordered']['recipient_id']}")
    conv = await k.db.fetchval("insert into conversations (cafe_id, customer_id) values ($1, $2) returning id",
                               k.cafe_id, out["ordered"]["customer_id"])
    await k.db.execute("""insert into inbound_messages (cafe_id, conversation_id, wamid, type, payload, received_at)
                          values ($1, $2, $3, 'text', '{}'::jsonb, now() - interval '4 days')""",
                       k.cafe_id, conv, f"wamid.offers-{secrets.token_hex(6)}")
    for key, total, ago in (("ordered", 50000, "2 days"), ("late", 30000, "12 hours")):
        await k.db.execute(
            f"""insert into orders (cafe_id, customer_id, number, status, expected_minutes, total_paise, source,
                                    idempotency_key, created_at)
                values ($1, $2, next_order_number($1), 'SERVED', 5, $3, 'qr', $4, now() - interval '{ago}')""",
            k.cafe_id, out[key]["customer_id"], total, f"offers-insights-{secrets.token_hex(6)}")
    await redeem_direct(k, out["redeemed"], bill=124000, discount=24800, ago_minutes=60 * 24 * 3)
    await k.db.execute("""insert into consents (cafe_id, customer_id, status, source, created_at)
                          values ($1, $2, 'opted_out', 'button', now() - interval '3 days')""",
                       k.cafe_id, out["opted_out"]["customer_id"])
    return out


async def test_recipients_export_owner_only_masked_and_audited(k: Kitchen, db: asyncpg.Pool) -> None:
    camp = await campaign_with_recipients(k)
    await k.db.execute("update customers set name = null, wa_id = '000111222333444' where id = $1",
                       camp["failed"]["customer_id"])
    async with client_as(k, "owner") as api:
        r = await api.get(f"/campaigns/{camp['campaign_id']}/recipients/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="campaign-recipients.csv"'
    lines = r.text.splitlines()
    assert lines[0] == ("first_name,masked_phone,code,status,sent_at,delivered_at,read_at,replied_at,orders_in_window,"
                        "spent_inr,redeemed_at,redemption_channel,bill_inr,discount_inr,redeemed_by,opted_out_at,"
                        "opted_out_source")
    assert len(lines) == 6
    for key in ("ordered", "redeemed", "opted_out", "late"):
        assert camp[key]["wa_id"] not in r.text
    assert "Sharma" not in r.text and "Rao" not in r.text  # first names only
    assert any(ln.startswith("Riya,'+91 99••• ••002,") and ",counter,1240.00,248.00,Ravi," in ln for ln in lines)
    assert any(ln.startswith("Asha,") and ",1,500.00," in ln for ln in lines)
    assert any(ln.startswith(",deleted,") for ln in lines)
    assert any(ln.startswith("Sana,") and ln.endswith(",button") for ln in lines)
    audit = await k.db.fetch("select change from audit_log where cafe_id = $1 and action = 'campaign.export'", k.cafe_id)
    assert [a["change"] for a in audit] == [{"campaign_id": camp["campaign_id"], "rows": 5}]
    for role in ("shift_manager", "manager"):
        async with client_as(k, role) as api:
            assert (await api.get(f"/campaigns/{camp['campaign_id']}/recipients/export")).status_code in (401, 403)
    async with db.acquire() as c:
        other = await create_test_cafe(c)
    try:
        theirs = await k.db.fetchval(
            """insert into campaigns (cafe_id, name, segment, template_name, status)
               values ($1, 'Theirs', 'all_opted_in', 'weekly_offer', 'draft') returning id::text""", other.cafe_id)
        async with client_as(k, "owner") as api:
            assert (await api.get(f"/campaigns/{theirs}/recipients/export")).status_code == 404
    finally:
        async with db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)
    assert await count(k, "select count(*) from audit_log where cafe_id = $1 and action = 'campaign.export'") == 1


async def test_campaign_insights_counts_and_money(k: Kitchen) -> None:
    camp = await campaign_with_recipients(k)
    row = await k.db.fetchrow("select * from v_campaign_insights where cafe_id = $1 and campaign_id = $2",
                              k.cafe_id, camp["campaign_id"])
    counts = {c: row[c] for c in ("recipients", "queued", "sent", "held", "delivered", "read", "failed", "replied",
                                  "ordered", "redeemed", "redeemed_counter", "redeemed_table_bill", "came_back",
                                  "opted_out")}
    assert counts == {"recipients": 5, "queued": 0, "sent": 4, "held": 0, "delivered": 3, "read": 1, "failed": 1,
                      "replied": 1, "ordered": 1, "redeemed": 1, "redeemed_counter": 1, "redeemed_table_bill": 0,
                      "came_back": 2, "opted_out": 1}
    assert (row["ordered_paise"], row["counter_bill_paise"], row["came_back_paise"], row["discount_paise"]) == \
        (50000, 124000, 174000, 24800)  # the order after valid_until isn't counted
    assert float(row["whatsapp_cost"]) == pytest.approx(0.88)
    recipients = {str(r["recipient_id"]): r for r in await k.db.fetch(
        "select * from v_campaign_recipients where cafe_id = $1 and campaign_id = $2",
        k.cafe_id, camp["campaign_id"])}
    late = recipients[camp["late"]["recipient_id"]]
    assert late["orders"] == 0 and late["came_back"] is False
    redeemed = recipients[camp["redeemed"]["recipient_id"]]
    assert redeemed["redemption_channel"] == "counter" and redeemed["redeemed_by_name"] == "Ravi"
    assert redeemed["bill_paise"] == 124000 and redeemed["came_back"] is True
