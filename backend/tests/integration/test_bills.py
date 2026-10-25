"""Bill sheet and code redemption (docs/backend.md#bill-sheet, #redemption) through the API."""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.domain.ai_agent import prompt
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

pytestmark = pytest.mark.integration


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


async def order_at(k: Kitchen, wa: str, table: int, dishes: list[str]) -> str:
    await k.say(wa, f"Hi, I'm at Table {table}")
    await k.say(wa, "Asha")
    for d in dishes:
        await k.say(wa, tap=f"item:{k.items[d]}")
    v = (await k.state(wa))["cart"]["version"]
    await k.say(wa, tap=f"confirm:{v}")
    return str(await k.db.fetchval(
        """select o.table_session_id::text from orders o join customers c on c.id = o.customer_id
           where c.cafe_id = $1 and c.wa_id = $2""", k.cafe_id, wa))


async def a_code(db: asyncpg.Pool, cafe_id: str, customer_wa: str, code: str, *, percent: int = 20,
                 valid: str = "now() + interval '7 days'", status: str = "sent") -> None:
    async with db.acquire() as c:
        campaign = await c.fetchval(
            f"""insert into campaigns (cafe_id, name, segment, template_name, discount_percent, code_prefix, valid_until, status)
                values ($1, 'Back soon', 'all_opted_in', 'weekly_offer', $2, 'BACK', {valid}, 'sending') returning id""",  # noqa: S608
            cafe_id, percent)
        customer = await c.fetchval("insert into customers (cafe_id, wa_id, name) values ($1, $2, 'Old') "
                                    "on conflict (cafe_id, wa_id) do update set name = customers.name returning id",
                                    cafe_id, customer_wa)
        await c.execute("insert into campaign_recipients (cafe_id, campaign_id, customer_id, code, status) values ($1, $2, $3, $4, $5)",
                        cafe_id, campaign, customer, code, status)


async def test_bill_lines_code_discount_and_delivery(k: Kitchen) -> None:
    session = await order_at(k, "919999760001", 5, ["Cold Coffee", "Masala Chai"])
    await a_code(k.db, k.cafe_id, "919999760099", "BACK-7KQ")
    async with client_as(k, "shift_manager") as api:
        bill = (await api.get(f"/bills/{session}")).json()
        assert bill["subtotal_paise"] == bill["total_paise"] > 0 and bill["discount_paise"] == 0 and bill["code"] is None
        assert {i["name_snapshot"] for o in bill["orders"] for i in o["items"]} == {"Cold Coffee", "Masala Chai"}
        assert sum(i["line_paise"] for o in bill["orders"] for i in o["items"]) == bill["subtotal_paise"]

        bad = await api.post(f"/bills/{session}/apply-code", json={"code": "NOPE-123"})
        assert bad.status_code == 422 and bad.json()["error"]["message"] == "This isn't a valid code, or it has expired."
        r = await api.post(f"/bills/{session}/apply-code", json={"code": "back-7kq"})  # case doesn't matter
        assert r.status_code == 200, r.text
        applied = r.json()
        assert applied["code"] == "BACK-7KQ"
        assert applied["discount_paise"] == bill["subtotal_paise"] * 20 // 100
        assert applied["total_paise"] == bill["subtotal_paise"] - applied["discount_paise"]
        assert (await api.post(f"/bills/{session}/apply-code", json={"code": "BACK-7KQ"})).json() == applied  # no-op

        delivered = (await api.post(f"/bills/{session}/deliver")).json()
        assert delivered["total_paise"] == applied["total_paise"] and delivered["discount_paise"] == applied["discount_paise"]
    order = await k.db.fetchrow("select source, campaign_recipient_id from orders where table_session_id = $1", session)
    assert order["source"] == "campaign" and order["campaign_recipient_id"] is not None
    assert await k.db.fetchval("select count(*) from audit_log where cafe_id = $1 and action = 'offer.redeem'", k.cafe_id) == 1


async def test_used_expired_second_code_and_other_cafe(k: Kitchen, db: asyncpg.Pool) -> None:
    first = await order_at(k, "919999760002", 1, ["Cold Coffee"])
    second = await order_at(k, "919999760003", 2, ["Masala Chai"])
    await a_code(k.db, k.cafe_id, "919999760098", "BACK-AAA")
    await a_code(k.db, k.cafe_id, "919999760097", "BACK-BBB")
    await a_code(k.db, k.cafe_id, "919999760096", "BACK-OLD", valid="now() - interval '1 day'")
    await a_code(k.db, k.cafe_id, "919999760095", "BACK-QUE", status="queued")  # never sent: not valid yet
    async with db.acquire() as c:
        other = await create_test_cafe(c)
    try:
        await a_code(db, other.cafe_id, "919999760094", "ELSE-123")
        async with client_as(k, "shift_manager") as api:
            assert (await api.post(f"/bills/{first}/apply-code", json={"code": "BACK-AAA"})).status_code == 200
            used = await api.post(f"/bills/{second}/apply-code", json={"code": "BACK-AAA"})
            number = await k.db.fetchval("select number from orders where table_session_id = $1", first)
            assert used.status_code == 409 and used.json()["error"]["message"] == f"This code was already used today on #{number}."
            two = await api.post(f"/bills/{first}/apply-code", json={"code": "BACK-BBB"})
            assert two.status_code == 400 and "One code per bill" in two.json()["error"]["message"]
            for code in ("BACK-OLD", "BACK-QUE", "ELSE-123"):
                assert (await api.post(f"/bills/{second}/apply-code", json={"code": code})).status_code == 422, code
            assert (await api.get(f"/bills/{other.conversation_id}")).status_code == 404
    finally:
        async with db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)
