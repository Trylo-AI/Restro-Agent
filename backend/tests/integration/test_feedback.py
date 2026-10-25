"""Feedback after SERVED and the opt-in (docs/backend.md#feedback, #consent), through the real pipeline."""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.domain import feedback
from app.domain.ai_agent import prompt
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


@pytest.fixture
async def k(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Kitchen]:
    async with db.acquire() as c:
        items, tables = await build_kitchen(c, cafe.cafe_id)
        staff = await create_staff(c, cafe.cafe_id)
        pnid = await c.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id)
        await c.execute("""update cafes set settings = settings || '{"feedback_delay_minutes": 15}' where id = $1""", cafe.cafe_id)
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


async def served_order(k: Kitchen, wa: str) -> str:
    await k.say(wa, "Hi, I'm at Table 5")
    await k.say(wa, "Asha")
    await k.say(wa, tap=f"item:{k.items['Cold Coffee']}")
    v = (await k.state(wa))["cart"]["version"]
    await k.say(wa, tap=f"confirm:{v}")
    order = await k.db.fetchrow("""select o.id::text, o.version from orders o join customers c on c.id = o.customer_id
                                   where c.cafe_id = $1 and c.wa_id = $2""", k.cafe_id, wa)
    async with client_as(k, "shift_manager") as api:
        version = order["version"]
        for to in ("ACCEPTED", "READY", "SERVED"):
            r = await api.post(f"/orders/{order['id']}/transition", json={"to": to, "expected_version": version})
            assert r.status_code == 200, r.text
            version = r.json()["version"]
    return str(order["id"])


async def ask_now(k: Kitchen, order_id: str) -> list[str]:
    timer = next(t for t in k.rec.timers("feedback") if t["subject_id"] == order_id)
    before = len(k.transport.sent)
    await feedback.ask(k.cafe_id, order_id, timer)
    return [s.text for s in k.transport.sent[before:]]


async def consent_rows(k: Kitchen, wa: str) -> list[asyncpg.Record]:
    return await k.db.fetch("""select status, source, wording_version from consents s join customers c on c.id = s.customer_id
                               where c.cafe_id = $1 and c.wa_id = $2 order by s.created_at""", k.cafe_id, wa)


async def test_five_stars_then_opt_in_yes(k: Kitchen) -> None:
    wa = "919999750001"
    order_id = await served_order(k, wa)
    delays = [d for q, b, d in k.rec.tasks if q == "timers" and b.get("kind") == "feedback"]
    assert delays == [15 * 60]  # the café's delay
    assert any("Rate 1–5" in t for t in await ask_now(k, order_id))
    reply = await k.say(wa, tap=f"rate:{order_id}:5")
    assert [s.text for s in reply][0] == "Thank you!"
    assert any(f"optin:yes:feedback_flow" in s.ids for s in reply)
    again = await k.say(wa, tap=f"rate:{order_id}:4")
    assert "already have your rating" in again[0].text
    done = await k.say(wa, tap="optin:yes:feedback_flow")
    assert "you'll get occasional offers" in done[0].text
    assert [tuple(r) for r in await consent_rows(k, wa)] == [("opted_in", "feedback_flow", 1)]
    assert await k.db.fetchval("select rating from feedback where order_id = $1", order_id) == 5
    assert await ask_now(k, order_id) == []  # rated: the timer does nothing


async def test_typed_two_stars_opens_low_rating_and_keeps_the_text(k: Kitchen) -> None:
    wa = "919999750002"
    order_id = await served_order(k, wa)
    await ask_now(k, order_id)
    reply = await k.say(wa, "2. Food took 25 minutes")
    assert reply[0].text == "Thank you — we've passed this on to the team."
    row = await k.db.fetchrow("select rating, text from feedback where order_id = $1", order_id)
    assert dict(row) == {"rating": 2, "text": "Food took 25 minutes"}
    req = await k.db.fetchrow("select kind, status, order_id::text from requests where cafe_id = $1 and kind = 'low_rating'", k.cafe_id)
    assert dict(req) == {"kind": "low_rating", "status": "open", "order_id": order_id}
    assert (await k.db.fetchval("select mode from conversations c join customers u on u.id = c.customer_id where u.wa_id = $1", wa)) == "bot"


async def test_three_stars_asks_for_text_then_opt_in_no_and_no_reask_within_30_days(k: Kitchen) -> None:
    wa = "919999750003"
    order_id = await served_order(k, wa)
    await ask_now(k, order_id)
    reply = await k.say(wa, tap=f"rate:{order_id}:3")
    assert reply[0].text == "Thanks! Anything we could do better?" and len(reply) == 1
    after = await k.say(wa, "Music was a bit loud")
    assert after[0].text == "Thank you — we've passed this on to the team." and "optin:no:feedback_flow" in after[1].ids
    assert await k.db.fetchval("select text from feedback where order_id = $1", order_id) == "Music was a bit loud"
    await k.say(wa, tap="optin:no:feedback_flow")
    assert [tuple(r)[:2] for r in await consent_rows(k, wa)] == [("opted_out", "feedback_flow")]
    async with k.db.acquire() as c:
        cid = await c.fetchval("select id from customers where cafe_id = $1 and wa_id = $2", k.cafe_id, wa)
        assert await feedback.eligible(c, k.cafe_id, str(cid)) is False  # "No" today: not again for 30 days
        async with c.transaction():  # test only: backdate the append-only row
            await c.execute('alter table consents disable trigger "consents_append_only"')
            await c.execute("update consents set created_at = now() - interval '31 days' where customer_id = $1", cid)
            await c.execute('alter table consents enable trigger "consents_append_only"')
        assert await feedback.eligible(c, k.cafe_id, str(cid)) is True


async def test_an_order_typed_instead_of_feedback_text_is_a_normal_turn(k: Kitchen) -> None:
    wa = "919999750004"
    order_id = await served_order(k, wa)
    await ask_now(k, order_id)
    await k.say(wa, tap=f"rate:{order_id}:1")
    reply = await k.say(wa, "menu")
    assert any(i.startswith("cat:") for s in reply for i in s.ids)
    assert await k.db.fetchval("select text from feedback where order_id = $1", order_id) is None


async def test_stop_means_never_asked_again_but_counter_qr_still_asks(k: Kitchen) -> None:
    wa = "919999750005"
    order_id = await served_order(k, wa)
    await k.say(wa, "STOP")
    await ask_now(k, order_id)
    reply = await k.say(wa, tap=f"rate:{order_id}:5")
    assert len(reply) == 1 and reply[0].text == "Thank you!"  # no opt-in ask after an opt-out
    qr = await k.say(wa, feedback.COUNTER_QR_TEXT)
    assert "optin:yes:counter_qr" in qr[0].ids
    await k.say(wa, tap="optin:yes:counter_qr")
    assert [tuple(r)[:2] for r in await consent_rows(k, wa)] == [("opted_out", "keyword"), ("opted_in", "counter_qr")]


def test_parse_typed() -> None:
    assert feedback.parse_typed("2. Food took 25 minutes") == (2, "Food took 25 minutes")
    assert feedback.parse_typed(" 5 ") == (5, None)
    assert feedback.parse_typed("4/5 good") == (4, "good")
    assert feedback.parse_typed("2 cold coffee") == (2, "cold coffee")  # only while an ask is open (checked in code)
    assert feedback.parse_typed("6") is None and feedback.parse_typed("10 stars") is None and feedback.parse_typed("hi") is None
