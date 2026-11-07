"""Scripted conversations through the real pipeline and router (docs/rules.md: Conversations, Confirm, Orders).

Free text here goes to the S7 fallback; S9 adds the AI agent tests.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.core.auth import Principal
from app.core.errors import Forbidden, InvalidTransition, VersionConflict
from app.domain import timers
from app.domain.orders import service
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

pytestmark = pytest.mark.integration
WA = "919999700001"


@pytest.fixture
async def k(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Kitchen]:
    async with db.acquire() as c:
        items, tables = await build_kitchen(c, cafe.cafe_id)
        staff = await create_staff(c, cafe.cafe_id)
        pnid = await c.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id)
    transport, rec = RecordingTransport(), TaskRecorder()
    install(transport, rec)
    try:
        yield Kitchen(db, cafe.cafe_id, pnid, items, tables, transport, rec, staff)
    finally:
        uninstall()
        async with db.acquire() as c:  # the café first (its orders point at the staff), then the users
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


def ids(sent: list) -> list[str]:  # type: ignore[type-arg]
    return [i for s in sent for i in s.ids]


def principal(k: Kitchen, role: str = "shift_manager", user: str | None = None) -> Principal:
    return Principal(user_id=user or k.staff[role], cafe_id=k.cafe_id, role=role, display_name="Ravi")


async def place_order(k: Kitchen, wa: str = WA) -> tuple[dict, list]:  # type: ignore[type-arg]
    await k.say(wa, "Hi, I'm at Table 5")
    await k.say(wa, "Riya")
    await k.say(wa, tap=f"item:{k.items['Cold Coffee']}")
    summary = await k.say(wa, tap=f"item:{k.items['Cold Coffee']}")
    version = (await k.state(wa))["cart"]["version"]
    received = await k.say(wa, tap=f"confirm:{version}")
    return {"summary": summary, "version": version}, received


async def test_taps_only_order_from_qr_to_confirm(k: Kitchen) -> None:
    greet = await k.say(WA, "Hi, I'm at Table 5")
    assert "What should we call you?" in greet[0].text and ids(greet) == ["menu", "recommend", "help"]
    named = await k.say(WA, "riya")
    assert "Nice to meet you, Riya" in named[0].text
    cats = await k.say(WA, tap="menu")
    assert ids(cats)[0] == "recommend"  # the "For you" row leads (≤ 9 categories)
    assert set(ids(cats)[1:]) == {"cat:cold-coffee", "cat:toasts", "cat:desserts", "cat:tea"}
    dishes = await k.say(WA, tap="cat:cold-coffee")
    assert f"item:{k.items['Cold Coffee']}" in ids(dishes)
    assert "₹190" in str(dishes[0].body)  # price rendered by code from the DB
    await k.say(WA, tap=f"item:{k.items['Cold Coffee']}")
    summary = await k.say(WA, tap=f"item:{k.items['Avocado Toast']}")
    assert "Your order · Table 5" in summary[0].text
    assert "1 × Cold Coffee ₹190" in summary[0].text and "Total (incl. GST) ₹510" in summary[0].text
    version = (await k.state(WA))["cart"]["version"]
    assert ids(summary) == [f"confirm:{version}", f"edit:{version}", f"cancel:{version}"]

    received = await k.say(WA, tap=f"confirm:{version}")
    orders = await k.orders()
    assert len(orders) == 1 and orders[0]["status"] == "NEW" and orders[0]["total_paise"] == 51000
    assert f"Order #{orders[0]['number']} received" in received[0].text
    assert k.rec.timers("accept_escalation_1")
    state = await k.state(WA)
    assert state["step"] == "order_placed" and state["cart"]["items"] == []

    again = await k.say(WA, tap=f"confirm:{version}")  # double tap
    assert len(await k.orders()) == 1 and "Already placed" in again[0].text
    events = await k.db.fetchval("select count(*) from order_events where cafe_id = $1", k.cafe_id)
    assert events == 1


async def test_stale_summary_never_orders(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, tap=f"item:{k.items['Cold Coffee']}")
    old = (await k.state(WA))["cart"]["version"]
    await k.say(WA, tap=f"item:{k.items['Masala Chai']}")
    reply = await k.say(WA, tap=f"confirm:{old}")
    assert await k.orders() == [] and "out of date" in reply[0].text


async def test_sold_out_at_confirm_drops_the_line_and_does_not_order(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, tap=f"item:{k.items['Cold Coffee']}")
    await k.say(WA, tap=f"item:{k.items['Avocado Toast']}")
    version = (await k.state(WA))["cart"]["version"]
    await k.db.execute("update menu_items set available = false where id = $1", k.items["Avocado Toast"])
    reply = await k.say(WA, tap=f"confirm:{version}")
    assert await k.orders() == []
    assert any("just sold out" in s.text for s in reply) and any("Total (incl. GST) ₹190" in s.text for s in reply)


async def test_confirm_without_a_table_asks_then_orders(k: Kitchen) -> None:
    await k.say(WA, tap="menu")
    await k.say(WA, tap=f"item:{k.items['Masala Chai']}")
    version = (await k.state(WA))["cart"]["version"]
    ask = await k.say(WA, tap=f"confirm:{version}")
    assert "Which table" in ask[0].text and await k.orders() == []
    done = await k.say(WA, tap=f"table:{k.tables['T2']}")
    assert any("received" in s.text for s in done)
    assert len(await k.orders()) == 1


async def test_foreign_or_unknown_ids_fall_back_to_the_menu(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    reply = await k.say(WA, tap="item:00000000-0000-0000-0000-000000000000")
    assert "cat:cold-coffee" in ids(reply)
    reply = await k.say(WA, tap="reorder:00000000-0000-0000-0000-000000000000")
    assert "cat:cold-coffee" in ids(reply)


async def test_non_text_gets_please_type(k: Kitchen) -> None:
    reply = await k.say(WA, kind="image")
    assert "only read text" in reply[0].text and ids(reply) == ["menu", "help"]


async def test_call_waiter_hands_off_and_bill_does_not(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    bill = await k.say(WA, "bill please")
    assert "bill is on its way" in bill[0].text
    conv = await k.conversation_id(WA)
    assert await k.db.fetchval("select mode from conversations where id = $1", conv) == "bot"
    help1 = await k.say(WA, "call waiter")
    assert "with you shortly" in help1[0].text
    assert await k.db.fetchval("select mode from conversations where id = $1", conv) == "human"
    silent = await k.say(WA, "hello?")  # human mode: the bot is silent
    assert silent == []
    stop = await k.say(WA, "STOP")  # STOP still works in human mode
    assert "won't get offers" in stop[0].text
    assert await k.db.fetchval("select status from consents where customer_id = (select customer_id from conversations where id = $1)", conv) == "opted_out"
    kinds = await k.db.fetch("select kind, reason_tag from requests where conversation_id = $1 order by kind", conv)
    assert [(r["kind"], r["reason_tag"]) for r in kinds] == [("bill", None), ("help", "asked")]


async def test_paused_blocks_ordering_but_not_status_or_help(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.db.execute("update cafes set settings = settings || '{\"paused\": true}'::jsonb where id = $1", k.cafe_id)
    menu = await k.say(WA, tap="menu")
    assert "not taking WhatsApp orders" in menu[0].text
    status = await k.say(WA, "status")
    assert "no open orders" in status[0].text


async def test_customer_cancel_while_new_and_refused_after_accept(k: Kitchen) -> None:
    _, received = await place_order(k)
    order = (await k.orders())[0]
    cancel_id = next(i for i in ids(received) if i.startswith("cancel_order:"))
    reply = await k.say(WA, tap=cancel_id)
    assert "cancelled" in reply[0].text and (await k.orders())[0]["status"] == "CANCELLED"

    await k.db.execute("update customers set name = null where wa_id = $1", "919999700002")
    _, received = await place_order(k, "919999700002")
    order = (await k.orders())[1]
    await service.staff_transition(principal(k), order["id"], "ACCEPTED", order["version"])
    cancel_id = next(i for i in ids(received) if i.startswith("cancel_order:"))
    refused = await k.say("919999700002", tap=cancel_id)
    assert "already accepted" in refused[0].text and (await k.orders())[1]["status"] == "ACCEPTED"


async def test_edit_while_new_and_the_edit_accept_race(k: Kitchen) -> None:
    _, received = await place_order(k)
    order = (await k.orders())[0]
    edit_id = next(i for i in ids(received) if i.startswith("edit_order:"))
    await k.say(WA, tap=edit_id)
    updated = await k.say(WA, tap=f"item:{k.items['Chocolate Brownie']}")
    assert "Chocolate Brownie" in updated[0].text
    save = next(i for i in ids(updated) if i.startswith("apply_edit:"))
    done = await k.say(WA, tap=save)
    row = (await k.orders())[0]
    assert "Updated order" in done[0].text and row["total_paise"] == 2 * 19000 + 18000 and "edited" in row["flags"]

    # Race: staff accepts with the old version → 409; customer edits after acceptance → refused.
    with pytest.raises(VersionConflict):
        await service.staff_transition(principal(k), order["id"], "ACCEPTED", order["version"])
    await service.staff_transition(principal(k), order["id"], "ACCEPTED", row["version"])
    again = await k.say(WA, tap=edit_id)
    assert "already accepted" in again[0].text


async def test_staff_flow_undo_and_status_messages(k: Kitchen) -> None:
    await place_order(k)
    order = (await k.orders())[0]
    accepted = await service.staff_transition(principal(k), order["id"], "ACCEPTED", order["version"])
    msg = k.rec.timers("status_message")[-1]
    assert msg["to"] == "ACCEPTED" and k.rec.timers("overdue")

    undone = await service.staff_undo(principal(k), order["id"])
    assert undone.status == "NEW"
    before = len(k.transport.sent)
    await timers.dispatch(msg)  # the +5 s message fires after the undo
    assert len(k.transport.sent) == before  # nothing sent

    with pytest.raises(InvalidTransition):
        await service.staff_undo(principal(k), order["id"])  # nothing left to undo
    with pytest.raises(InvalidTransition):
        await service.staff_transition(principal(k), order["id"], "SERVED", undone.version)  # no skipping

    accepted = await service.staff_transition(principal(k), order["id"], "ACCEPTED", undone.version)
    await timers.dispatch(k.rec.timers("status_message")[-1])
    assert k.transport.sent[-1].text == "Your order is being made."
    with pytest.raises(InvalidTransition):
        await service.staff_undo(principal(k, user=k.staff["owner"]), order["id"])  # other actor

    ready = await service.staff_transition(principal(k), order["id"], "READY", accepted.version)
    await timers.dispatch(k.rec.timers("status_message")[-1])
    assert k.transport.sent[-1].text == "Your order is ready and on its way."
    served = await service.staff_transition(principal(k), order["id"], "SERVED", ready.version)
    assert served.status == "SERVED"
    audit = await k.db.fetch("select action from audit_log where cafe_id = $1 order by at", k.cafe_id)
    assert [a["action"] for a in audit] == ["order.transition", "order.undo", "order.transition", "order.transition", "order.transition"]


async def test_cancel_roles_and_reasons(k: Kitchen) -> None:
    await place_order(k)
    order = (await k.orders())[0]
    accepted = await service.staff_transition(principal(k, "manager"), order["id"], "ACCEPTED", order["version"])
    with pytest.raises(Forbidden):
        await service.staff_cancel(principal(k), order["id"], "out_of_stock", None, accepted.version)
    cancelled = await service.staff_cancel(principal(k, "manager"), order["id"], "other", "Gas ran out", accepted.version)
    row = await k.db.fetchrow("select cancel_reason_code, cancel_reason_text, cancelled_by::text from orders where id = $1", order["id"])
    assert cancelled.status == "CANCELLED" and tuple(row) == ("other", "Gas ran out", k.staff["manager"])
    notified = await k.db.fetch("select user_id::text, payload from notifications where cafe_id = $1", k.cafe_id)
    assert {n["user_id"] for n in notified} == {k.staff["manager"], k.staff["owner"]}
    assert all(n["payload"]["order_number"] == order["number"] for n in notified)
    await timers.dispatch(k.rec.timers("status_message")[-1])
    assert "has been cancelled" in k.transport.sent[-1].text and "Gas ran out" not in k.transport.sent[-1].text


async def test_delay_messages_the_customer(k: Kitchen) -> None:
    await place_order(k)
    order = (await k.orders())[0]
    accepted = await service.staff_transition(principal(k), order["id"], "ACCEPTED", order["version"])
    delayed = await service.staff_delay(principal(k), order["id"], 5, "Oven is busy")
    assert delayed.delay_minutes == 5 and "delayed" in delayed.flags and delayed.version == accepted.version + 1
    assert k.transport.sent[-1].text == "Sorry for the wait — your order needs about 5 more minutes. Oven is busy"


async def test_manual_order_is_idempotent_and_silent(k: Kitchen) -> None:
    before = len(k.transport.sent)
    items = [{"item_id": k.items["Masala Chai"], "qty": 2}]
    a = await service.manual_order(principal(k), k.tables["T1"], items, None, "key-12345")
    b = await service.manual_order(principal(k), k.tables["T1"], items, None, "key-12345")
    assert a.id == b.id and a.status == "ACCEPTED" and a.source == "manual" and a.total_paise == 18000
    assert len(k.transport.sent) == before


async def test_escalation_alerts_and_auto_served(k: Kitchen) -> None:
    await place_order(k)
    order = (await k.orders())[0]
    await timers.dispatch(k.rec.timers("accept_escalation_1")[-1])
    assert "escalated" in (await k.orders())[0]["flags"]
    alerts = [s for s in k.transport.sent if s.body.get("type") == "template"]
    assert {a.to for a in alerts} == {"919999000900", "919999000802"}  # café alert number + the manager
    assert all(a.body["template"]["name"] == "new_order_alert" for a in alerts)
    await timers.dispatch(k.rec.timers("accept_escalation_1")[-1])  # twice: no second alert
    assert len([s for s in k.transport.sent if s.body.get("type") == "template"]) == 2

    accepted = await service.staff_transition(principal(k), order["id"], "ACCEPTED", (await k.orders())[0]["version"])
    ready = await service.staff_transition(principal(k), order["id"], "READY", accepted.version)
    await k.db.execute("update orders set ready_at = now() - interval '11 minutes' where id = $1", order["id"])
    await timers.dispatch(k.rec.timers("auto_served")[-1])
    assert (await k.orders())[0]["status"] == "SERVED"
    _ = ready
