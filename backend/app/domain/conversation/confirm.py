"""Confirm creates the order; the customer may edit or cancel it while NEW (docs/backend.md#confirmation,
#edits-while-new). An order is created only by `confirm:{cart_version}` (or `confirm` on an unchanged cart).

Confirm, in order: version matches and cart not empty → not paused or closed → availability re-checked →
table session → ONE transaction: order + items + event + cleared cart + step (+ the "received" reply step).
A second tap hits the unique idempotency key → "Already placed — order #N".
"""

from typing import Any

import asyncpg

from app.core.errors import InvalidTransition, VersionConflict
from app.db.repositories import customers as customers_repo
from app.db.repositories import menu as menu_repo
from app.db.repositories import order_writes
from app.db.repositories import orders as orders_repo
from app.db.repositories import tables as tables_repo
from app.db.repositories.orders import Order, OrderLine
from app.db.tx import conn
from app.domain import cart as cart_mod
from app.domain import hours
from app.domain.conversation import flows, render
from app.domain.conversation.turn import Reply, TurnContext, TurnResult
from app.domain.orders import CUSTOMER, transition
from app.domain.orders.service import after_status_change
from app.integrations.whatsapp import messages as m


def idempotency_key(conversation_id: str, cart_version: int) -> str:
    return f"{conversation_id}:{cart_version}"


async def confirm(ctx: TurnContext, state: dict[str, Any], version: int) -> TurnResult:
    key = idempotency_key(ctx.conversation.id, version)
    async with conn() as c:
        placed = await orders_repo.by_key(c, ctx.cafe.id, key)
    if placed:
        return TurnResult(state=state, replies=[Reply(render.already_placed(placed.number))])

    cart = state["cart"]
    if version != int(cart.get("version", -1)) or not cart.get("items"):
        replies = [Reply(render.outdated_summary())] if cart.get("items") else []
        return TurnResult(state=state, replies=replies + await flows.summary(ctx, state))

    blocked = flows.can_order(ctx)
    if blocked:
        return TurnResult(state=state, replies=blocked)

    async with conn() as c:
        items = await menu_repo.items_by_ids(c, ctx.cafe.id, cart_mod.item_ids(cart))
    priced = cart_mod.price(cart, items)
    if priced.dropped:  # sold out since the summary: no order; say what went, show the new summary
        state["cart"] = priced.cart
        notice = Reply(render.dropped([d.name for d in priced.dropped]))
        return TurnResult(state=state, replies=[notice] + await flows.summary(ctx, state))

    if not ctx.conversation.table_session_id:
        state["awaiting"] = "table"
        state["confirm_after_table"] = version
        async with conn() as c:
            tables = await tables_repo.all_tables(c, ctx.cafe.id)
        return TurnResult(state=state, replies=[Reply(render.ask_table(tables))])

    near_closing = hours.status(ctx.cafe).near_closing
    lines = [OrderLine(ln.item.id, ln.item.name, ln.item.price_paise, ln.qty, ln.note) for ln in priced.lines]
    allergy_note = state.get("allergy_note")  # captured now: state is reset for the next visit below
    asked_name = state.get("awaiting") == "name"
    created_box: dict[str, Order] = {}

    async def commit(c: asyncpg.Connection) -> list[Reply]:
        order, created = await orders_repo.create(
            c, ctx.cafe.id, customer_id=ctx.customer.id, table_session_id=ctx.conversation.table_session_id,
            lines=lines, expected_minutes=priced.expected_minutes, allergy_note=allergy_note,
            source="qr", idempotency_key=key, status="NEW", flags=["near_closing"] if near_closing else [],
        )
        if not created:
            return [Reply(render.already_placed(order.number))]
        await order_writes.insert_event(c, ctx.cafe.id, order.id, None, "NEW", "customer", None, None)
        await customers_repo.touch_visit(c, ctx.cafe.id, ctx.customer.id)
        created_box["order"] = order
        replies = [Reply(render.order_received(order.number, order.expected_minutes, order.id, order.version))]
        if asked_name or not ctx.customer.name:
            replies.append(Reply(render.ask_name_after_order()))
        return replies

    async def after() -> None:
        if "order" in created_box:
            await after_status_change(created_box["order"], message=False)

    state["cart"] = cart_mod.clear(cart)
    state["step"] = "order_placed"
    state["allergy_note"] = None
    state.pop("confirm_after_table", None)
    if not ctx.customer.name:
        state["awaiting"] = "name"  # name asked after the order
    return TurnResult(state=state, commit=[commit], after=[after])


# ── edits and cancels while NEW ───────────────────────────────────────────

def _not_new(order: Order) -> list[Reply]:
    return [Reply(m.Buttons(f"The kitchen has already accepted #{order.number}, so I can't change it.",
                            [render.CALL_WAITER]))]


async def _own_order(ctx: TurnContext, order_id: str) -> Order | None:
    async with conn() as c:
        order = await orders_repo.get(c, ctx.cafe.id, order_id)
    return order if order and order.customer_id == ctx.customer.id else None


async def edit_summary(ctx: TurnContext, state: dict[str, Any]) -> list[Reply]:
    edit = state["order_edit"]
    async with conn() as c:
        items = await menu_repo.items_by_ids(c, ctx.cafe.id, cart_mod.item_ids(edit["cart"]))
    priced = cart_mod.price(edit["cart"], items)
    edit["cart"] = priced.cart
    lines = [f"Updated order #{edit['number']}"]
    for ln in priced.lines:
        lines.append(f"{ln.qty} × {ln.item.name} {render.inr(ln.line_paise)}" + (f"\n   ↳ {ln.note}" if ln.note else ""))
    lines.append(f"Total (incl. GST) {render.inr(priced.total_paise)}")
    if priced.is_empty:
        lines.append("(empty — tap Cancel order to cancel it instead)")
    buttons = [m.Button(f"apply_edit:{edit['order_id']}:{edit['order_version']}", "Save changes"),
               m.Button(f"keep_order:{edit['order_id']}", "Keep order")]
    return [Reply(m.Buttons("\n".join(lines), buttons))]


async def start_edit(ctx: TurnContext, state: dict[str, Any], order_id: str) -> TurnResult:
    order = await _own_order(ctx, order_id)
    if order is None:
        return TurnResult(state=state, replies=await flows.menu_categories(ctx))
    if order.status != "NEW":
        return TurnResult(state=state, replies=_not_new(order))
    async with conn() as c:
        lines = await orders_repo.lines(c, ctx.cafe.id, order_id)
    edit_cart = {"items": [{"item_id": ln.item_id, "qty": ln.qty, "note": ln.note} for ln in lines], "version": 0}
    state["order_edit"] = {"order_id": order.id, "order_version": order.version, "number": order.number, "cart": edit_cart}
    intro = Reply(m.Buttons(f"Editing order #{order.number}. Type what to change — like “add a brownie” or "
                            "“make it 2 cold coffee” — or add from the menu.", [render.MENU]))
    return TurnResult(state=state, replies=[intro] + await edit_summary(ctx, state))


async def apply_edit(ctx: TurnContext, state: dict[str, Any], order_id: str, order_version: int) -> TurnResult:
    edit = state.get("order_edit") or {}
    order = await _own_order(ctx, order_id)
    if order is None or edit.get("order_id") != order_id:
        return TurnResult(state=state, replies=await flows.menu_categories(ctx))
    if order.status != "NEW":
        state.pop("order_edit", None)
        return TurnResult(state=state, replies=_not_new(order))
    async with conn() as c:
        items = await menu_repo.items_by_ids(c, ctx.cafe.id, cart_mod.item_ids(edit["cart"]))
    priced = cart_mod.price(edit["cart"], items)
    if priced.is_empty:
        state["order_edit"]["cart"] = priced.cart
        return TurnResult(state=state, replies=await edit_summary(ctx, state))
    lines = [OrderLine(ln.item.id, ln.item.name, ln.item.price_paise, ln.qty, ln.note) for ln in priced.lines]
    outcome: dict[str, Any] = {}

    async def commit(c: asyncpg.Connection) -> list[Reply]:
        try:
            flags = sorted(set(order.flags) | {"edited"})
            updated = await transition(c, ctx.cafe.id, order_id, "NEW", CUSTOMER, order_version, reason="edited",
                                       extra={"flags": flags, "expected_minutes": priced.expected_minutes,
                                              "total_paise": priced.total_paise})
        except (VersionConflict, InvalidTransition):
            outcome["race"] = True
            current = await orders_repo.get(c, ctx.cafe.id, order_id)
            return _not_new(current) if current and current.status != "NEW" else [Reply(render.fallback())]
        await orders_repo.replace_items(c, ctx.cafe.id, order_id, lines)
        return [Reply(render.order_updated(updated.number, updated.expected_minutes, updated.id, updated.version))]

    state.pop("order_edit", None)
    return TurnResult(state=state, commit=[commit])


async def keep_order(ctx: TurnContext, state: dict[str, Any], order_id: str) -> TurnResult:
    edit = state.pop("order_edit", None)
    number = edit["number"] if edit else None
    text = f"No changes — order #{number} stays as it was." if number else "No changes made."
    return TurnResult(state=state, replies=[Reply(m.Text(text))])


async def cancel_order(ctx: TurnContext, state: dict[str, Any], order_id: str, version: int) -> TurnResult:
    order = await _own_order(ctx, order_id)
    if order is None:
        return TurnResult(state=state, replies=await flows.menu_categories(ctx))
    if order.status != "NEW":
        return TurnResult(state=state, replies=_not_new(order))

    async def commit(c: asyncpg.Connection) -> list[Reply]:
        current = await orders_repo.get(c, ctx.cafe.id, order_id)
        if current is None or current.status != "NEW":
            return _not_new(current) if current else []
        await transition(c, ctx.cafe.id, order_id, "CANCELLED", CUSTOMER, current.version, reason="customer",
                         extra={"cancel_reason_code": "customer"})
        return [Reply(m.Buttons(f"Order #{order.number} cancelled.", [render.MENU]))]

    state.pop("order_edit", None)
    state["step"] = "browsing"
    return TurnResult(state=state, commit=[commit])
