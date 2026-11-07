"""Order use cases for the console API and the timers. Each runs one short transaction, then schedules
timers and messages after commit."""

from datetime import UTC, datetime
from typing import Any

from app.core.auth import Principal
from app.core.errors import BadRequest, InvalidTransition, NotFound
from app.core.logging import get_logger
from app.db.repositories import cafes as cafes_repo
from app.db.repositories import memberships, order_writes
from app.db.repositories import menu as menu_repo
from app.db.repositories import orders as repo
from app.db.repositories import tables as tables_repo
from app.db.repositories.orders import Order, OrderLine
from app.db.tx import conn, tx
from app.domain import audit, outbound, timers
from app.domain.conversation import render
from app.domain.orders import (
    AUTO_SERVED_AFTER,
    STAFF_REASONS,
    STATUS_MESSAGE_DELAY,
    SYSTEM,
    OrderActor,
    check_cancel_role,
    due_at,
    transition,
    undo,
)
from app.domain.outbound import Purpose, Recipient
from app.integrations.whatsapp import messages as m

log = get_logger(__name__)

ESCALATION_1_AFTER = 60
ESCALATION_2_AFTER = 120


def staff(p: Principal) -> OrderActor:
    return OrderActor("staff", p.user_id, p.role)


# ── after-commit effects ──────────────────────────────────────────────────

async def after_status_change(order: Order, *, message: bool) -> None:
    """Timers for the new status, and the +5 s customer message for staff moves."""
    if order.status == "NEW":
        await timers.schedule(order.cafe_id, "accept_escalation_1", order.id, ESCALATION_1_AFTER,
                              dedupe=f"esc1:{order.id}")
    elif order.status == "ACCEPTED":
        due = due_at(order)
        delay = max(0.0, (due - datetime.now(UTC)).total_seconds()) if due else 0.0
        await timers.schedule(order.cafe_id, "overdue", order.id, delay, extra={"version": order.version})
    elif order.status == "READY":
        await timers.schedule(order.cafe_id, "auto_served", order.id, AUTO_SERVED_AFTER, extra={"version": order.version})
    elif order.status == "SERVED" and order.customer_id:
        from app.domain import feedback  # the rating ask (docs/backend.md#feedback)

        async with conn() as c:
            cafe = await cafes_repo.get(c, order.cafe_id)
        await feedback.schedule(order.cafe_id, order.id, int((cafe.settings if cafe else {}).get("feedback_delay_minutes") or 30))
    if message and order.customer_id and order.status in ("ACCEPTED", "READY", "CANCELLED"):
        await timers.schedule(order.cafe_id, "status_message", order.id, STATUS_MESSAGE_DELAY,
                              extra={"to": order.status, "version": order.version},
                              dedupe=f"status:{order.id}:{order.version}")


# ── staff actions (API) ───────────────────────────────────────────────────

async def staff_transition(p: Principal, order_id: str, to: str, expected_version: int) -> Order:
    if to not in ("ACCEPTED", "READY", "SERVED"):
        raise BadRequest()
    async with tx() as c:
        order = await transition(c, p.cafe_id, order_id, to, staff(p), expected_version)
    await after_status_change(order, message=True)
    return order


async def staff_cancel(p: Principal, order_id: str, reason_code: str, reason_text: str | None,
                       expected_version: int) -> Order:
    if reason_code not in STAFF_REASONS:
        raise BadRequest()
    text = " ".join((reason_text or "").split()) or None
    if reason_code == "other" and not text:
        raise BadRequest("Please add a reason.")
    if text and len(text) > 120:
        raise BadRequest()
    async with tx() as c:
        current = await repo.get(c, p.cafe_id, order_id)
        if current is None:
            raise NotFound()
        check_cancel_role(current, p.role)
        order = await transition(c, p.cafe_id, order_id, "CANCELLED", staff(p), expected_version, reason=reason_code,
                                 extra={"cancel_reason_code": reason_code, "cancel_reason_text": text,
                                        "cancelled_by": p.user_id})
        table = await repo.table_label(c, p.cafe_id, order.table_session_id)
        recipients = await memberships.active_with_min_role(c, p.cafe_id, ("manager", "owner"))
        payload = {"order_number": order.number, "table": table, "reason_code": reason_code,
                   "reason_text": text, "actor_name": p.display_name}
        await order_writes.insert_notifications(c, p.cafe_id, [r.user_id for r in recipients], "order_cancelled",
                                                "order", order.id, payload)
    await after_status_change(order, message=True)
    return order


async def staff_undo(p: Principal, order_id: str) -> Order:
    async with tx() as c:
        order = await undo(c, p.cafe_id, order_id, staff(p))
    await after_status_change(order, message=False)
    return order


async def staff_delay(p: Principal, order_id: str, minutes: int, note: str | None) -> Order:
    note = " ".join((note or "").split()) or None
    if not 1 <= minutes <= 99 or (note and len(note) > 120):
        raise BadRequest()
    async with tx() as c:
        delayed = await order_writes.apply_delay(c, p.cafe_id, order_id, minutes)
        if delayed is None:
            if await repo.get(c, p.cafe_id, order_id) is None:
                raise NotFound()
            raise InvalidTransition()
        order = delayed
        change: dict[str, Any] = {"order_id": order_id, "number": order.number, "minutes": minutes, "has_note": bool(note)}
        if order.customer_id:
            change["customer_id"] = order.customer_id
        await audit.write(c, p.cafe_id, staff(p).audit_actor, "order.delay", change)  # never the note itself
    if order.status == "ACCEPTED":
        await after_status_change(order, message=False)  # re-creates `overdue` at the new due time
    if order.customer_id:
        body = f"Sorry for the wait — your order needs about {minutes} more minutes." + (f" {note}" if note else "")
        await outbound.send(p.cafe_id, Recipient(order.customer_id), m.Text(body),
                            f"order:{order.id}:delay:{order.version}", Purpose.ORDER_STATUS,
                            fallback_template=render.order_status_template(order.number, f"needs about {minutes} more minutes"))
    return order


async def manual_order(p: Principal, table_id: str, items: list[dict[str, Any]], allergy_note: str | None,
                       idempotency_key: str) -> Order:
    """Offline fallback: priced by code, created ACCEPTED (open-decision default), no customer messages."""
    async with tx() as c:
        table = await tables_repo.get(c, p.cafe_id, table_id)
        if table is None:
            raise NotFound()
        menu = await menu_repo.items_by_ids(c, p.cafe_id, [str(i["item_id"]) for i in items])
        lines = []
        for i in items:
            item = menu.get(str(i["item_id"]))
            if item is None or not item.available:
                raise BadRequest(f"{item.name if item else 'An item'} isn't available.")
            lines.append(OrderLine(item.id, item.name, item.price_paise, int(i["qty"]), i.get("note") or None))
        session_id = await tables_repo.open_session(c, p.cafe_id, table_id)
        order, created = await repo.create(
            c, p.cafe_id, customer_id=None, table_session_id=session_id, lines=lines,
            expected_minutes=max(menu[ln.item_id].prep_minutes for ln in lines),
            allergy_note=" ".join((allergy_note or "").split()) or None, source="manual",
            idempotency_key=f"manual:{p.cafe_id}:{idempotency_key}", status="ACCEPTED", flags=[],
        )
        if created:
            await order_writes.insert_event(c, p.cafe_id, order.id, None, "ACCEPTED", "staff", p.user_id, "manual")
            await audit.write(c, p.cafe_id, staff(p).audit_actor, "order.manual",
                              {"order_id": order.id, "number": order.number, "table": table[1],
                               "lines": [{"item_id": ln.item_id, "name": ln.name, "qty": ln.qty, "price_paise": ln.price_paise}
                                         for ln in lines],
                               "total_paise": order.total_paise, "has_allergy_note": bool(order.allergy_note)})
    if created:
        await after_status_change(order, message=False)
    return order


# ── timers ────────────────────────────────────────────────────────────────

async def _alert(cafe_id: str, order: Order, level: int) -> None:
    """new_order_alert to the café alert number + managers (level 1) or the owner (level 2)."""
    async with conn() as c:
        table = await repo.table_label(c, cafe_id, order.table_session_id) or "?"
        roles = ("manager",) if level == 1 else ("owner",)
        refs = (["cafe_alert"] if level == 1 else []) + await memberships.alert_refs(c, cafe_id, roles)
    template = m.Template("new_order_alert", [str(order.number), str(table)])
    for ref in refs:
        await outbound.send(cafe_id, Recipient(to_ref=ref), template, f"alert:{order.id}:{level}:{ref}", Purpose.ALERT)


@timers.handler("accept_escalation_1")
async def accept_escalation_1(cafe_id: str, order_id: str, _: dict[str, object]) -> None:
    async with tx() as c:
        order = await repo.get(c, cafe_id, order_id)
        if order is None or order.status != "NEW":
            return
        inserted = await order_writes.insert_escalation(c, cafe_id, "order", order_id, 1, "managers")
        await repo.add_flag(c, cafe_id, order_id, "escalated")
    if inserted:
        log.warning("order_escalated", cafe_id=cafe_id, order_id=order_id, level=1)
        await _alert(cafe_id, order, 1)
        await timers.schedule(cafe_id, "accept_escalation_2", order_id, ESCALATION_2_AFTER, dedupe=f"esc2:{order_id}")


@timers.handler("accept_escalation_2")
async def accept_escalation_2(cafe_id: str, order_id: str, _: dict[str, object]) -> None:
    async with tx() as c:
        order = await repo.get(c, cafe_id, order_id)
        if order is None or order.status != "NEW":
            return
        inserted = await order_writes.insert_escalation(c, cafe_id, "order", order_id, 2, "owner")
    if inserted:
        log.warning("order_escalated", cafe_id=cafe_id, order_id=order_id, level=2)
        await _alert(cafe_id, order, 2)


@timers.handler("overdue")
async def overdue(cafe_id: str, order_id: str, _: dict[str, object]) -> None:
    async with conn() as c:
        order = await repo.get(c, cafe_id, order_id)
        due = due_at(order) if order else None
        if order and order.status == "ACCEPTED" and due and due <= datetime.now(UTC):
            await repo.add_flag(c, cafe_id, order_id, "overdue")


@timers.handler("auto_served")
async def auto_served(cafe_id: str, order_id: str, _: dict[str, object]) -> None:
    async with tx() as c:
        order = await repo.get(c, cafe_id, order_id)
        if order is None or order.status != "READY" or order.ready_at is None:
            return
        if (datetime.now(UTC) - order.ready_at).total_seconds() < AUTO_SERVED_AFTER - 1:
            return
        served = await transition(c, cafe_id, order_id, "SERVED", SYSTEM, order.version, reason="auto_served")
    await after_status_change(served, message=False)


@timers.handler("status_message")
async def status_message(cafe_id: str, order_id: str, body: dict[str, object]) -> None:
    """Sent 5 s after a staff move unless it was undone or overtaken (status and version re-checked)."""
    async with conn() as c:
        order = await repo.get(c, cafe_id, order_id)
    if order is None or order.customer_id is None:
        return
    if order.status != body.get("to") or order.version != body.get("version"):
        return
    if order.status == "ACCEPTED":
        msg: m.Message = m.Text(render.ACCEPTED_TEXT)
        template = render.order_status_template(order.number, "being made")
    elif order.status == "READY":
        msg = m.Text(render.READY_TEXT)
        template = render.order_status_template(order.number, "ready and on its way")
    elif order.status == "CANCELLED":
        from app.domain.ai_agent import cancel_draft

        msg = await cancel_draft.message(cafe_id, order)
        template = render.order_status_template(order.number, "cancelled")
    else:
        return
    await outbound.send(cafe_id, Recipient(order.customer_id), msg, f"order:{order.id}:{order.status}:{order.version}",
                        Purpose.ORDER_STATUS, fallback_template=template)
