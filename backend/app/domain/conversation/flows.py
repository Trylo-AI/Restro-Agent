"""Deterministic flows (docs/backend.md#deterministic-flows). Code renders every reply; ids are validated
against this café and conversation; handlers act on reply ids, never titles."""

import copy
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from app.db.repositories import menu as menu_repo
from app.db.repositories import orders as orders_repo
from app.db.repositories.conversations import DEFAULT_STATE
from app.db.repositories.menu import MenuItem
from app.db.tx import conn
from app.domain import cart as cart_mod
from app.domain import handoff, hours
from app.domain.conversation import render
from app.domain.conversation.turn import Reply, TurnContext, TurnResult
from app.domain.orders import due_at
from app.ops import tracing

PAGE_SIZE = 8
VISIT_GAP = timedelta(hours=6)


def slug(category: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", category.lower()).strip("-")[:30]


def fresh_state(state: dict[str, Any] | None) -> dict[str, Any]:
    s = copy.deepcopy(DEFAULT_STATE)
    s.update(copy.deepcopy(state or {}))
    s.setdefault("cart", cart_mod.empty())
    return s


def new_visit(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Next visit: back to greeting, cart and allergy note cleared (they belong to one visit)."""
    s = fresh_state(None)
    s["cart"] = {"items": [], "version": int(state.get("cart", {}).get("version", 0)) + 1}
    s["visit_at"] = now.isoformat()
    return s


def is_stale_visit(state: dict[str, Any], now: datetime) -> bool:
    last = state.get("visit_at")
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last) > VISIT_GAP
    except ValueError:
        return True


def can_order(ctx: TurnContext) -> list[Reply] | None:
    """Paused or closed → the fixed reply (status, help, bill and STOP still work)."""
    h = hours.status(ctx.cafe)
    if h.paused:
        return [Reply(render.paused())]
    if not h.open:
        return [Reply(render.closed(h.opens_at))]
    return None


# ── menu ──────────────────────────────────────────────────────────────────

async def available(cafe_id: str) -> list[MenuItem]:
    async with conn() as c:
        return await menu_repo.available_items(c, cafe_id)


async def menu_categories(ctx: TurnContext) -> list[Reply]:
    items = await available(ctx.cafe.id)
    counts: dict[str, int] = {}
    for it in items:
        counts[it.category] = counts.get(it.category, 0) + 1
    if not counts:
        return [Reply(render.paused())]
    cats = [(slug(cat), cat, n) for cat, n in counts.items()]
    return [Reply(render.categories(cats[:10]))]


async def category_page(ctx: TurnContext, cat_slug: str, page: int) -> list[Reply]:
    items = [it for it in await available(ctx.cafe.id) if slug(it.category) == cat_slug]
    if not items:
        return await menu_categories(ctx)
    page = max(page, 0)
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    if not chunk:
        return await menu_categories(ctx)
    has_more = len(items) > (page + 1) * PAGE_SIZE
    rows = [(it.id, it.name, it.price_paise, it.veg, it.spice) for it in chunk]
    return [Reply(render.category_items(items[0].category, cat_slug, rows, page, has_more))]


# ── cart summary ──────────────────────────────────────────────────────────

async def summary(ctx: TurnContext, state: dict[str, Any]) -> list[Reply]:
    """Re-reads prices and availability; drops lines that sold out (version + 1) and says so."""
    async with conn() as c:
        items = await menu_repo.items_by_ids(c, ctx.cafe.id, cart_mod.item_ids(state["cart"]))
        table = await orders_repo.table_label(c, ctx.cafe.id, ctx.conversation.table_session_id)
    priced = cart_mod.price(state["cart"], items)
    replies: list[Reply] = []
    if priced.dropped:
        replies.append(Reply(render.dropped([d.name for d in priced.dropped])))
        tracing.degraded("sold_out_dropped", dishes=len(priced.dropped), at="summary")
    state["cart"] = priced.cart
    if priced.is_empty:
        state["step"] = "browsing"
        return replies + [Reply(render.empty_cart())]
    state["step"] = "confirming"
    return replies + [Reply(render.summary(priced, table, state.get("allergy_note"), priced.cart["version"]))]


async def add_item(ctx: TurnContext, state: dict[str, Any], item_id: str) -> list[Reply]:
    async with conn() as c:
        item = await menu_repo.get(c, ctx.cafe.id, item_id)
    if item is None:
        return await menu_categories(ctx)
    if not item.available:
        return [Reply(render.sold_out(item.name))]
    try:
        state["cart"] = cart_mod.add(state["cart"], item, 1)
    except cart_mod.CartError:
        return await summary(ctx, state)
    state["step"] = "building_cart"
    return await summary(ctx, state)


# ── greeting ──────────────────────────────────────────────────────────────

async def greeting(ctx: TurnContext, state: dict[str, Any]) -> list[Reply]:
    tone = str(ctx.cafe.settings.get("tone") or "warm")
    name = (ctx.customer.name or "").split(" ")[0]
    if not name:
        state["awaiting"] = "name"
        state["step"] = "greeting"
        return [Reply(render.greeting_new(ctx.cafe.name, tone))]
    async with conn() as c:
        last = await orders_repo.last_served(c, ctx.cafe.id, ctx.customer.id)
        lines = await orders_repo.lines(c, ctx.cafe.id, last.id) if last else []
    state["step"] = "browsing"
    names = [f"{ln.qty} × {ln.name}" if ln.qty > 1 else ln.name for ln in lines][:3]
    return [Reply(render.greeting_returning(name, names, last.id if last else None, tone))]


async def reorder(ctx: TurnContext, state: dict[str, Any], order_id: str) -> list[Reply]:
    async with conn() as c:
        order = await orders_repo.get(c, ctx.cafe.id, order_id)
        if order is None or order.customer_id != ctx.customer.id:
            return await menu_categories(ctx)
        lines = await orders_repo.lines(c, ctx.cafe.id, order_id)
        items = await menu_repo.items_by_ids(c, ctx.cafe.id, [ln.item_id for ln in lines])
    sold_out = []
    for ln in lines:
        item = items.get(ln.item_id)
        try:
            state["cart"] = cart_mod.add(state["cart"], item, ln.qty, ln.note)
        except cart_mod.CartError:
            sold_out.append(item.name if item else ln.name)
    replies = [Reply(render.dropped(sold_out))] if sold_out else []
    return replies + await summary(ctx, state)


# ── status, help, bill ────────────────────────────────────────────────────

async def status(ctx: TurnContext) -> list[Reply]:
    async with conn() as c:
        open_orders = await orders_repo.open_for_customer(c, ctx.cafe.id, ctx.customer.id)
    if not open_orders:
        return [Reply(render.no_open_orders())]
    now = datetime.now(UTC)
    lines = []
    for o in open_orders:
        due = due_at(o)
        left = int((due - now).total_seconds() // 60) if due else None
        lines.append((o.number, o.status, left))
    return [Reply(render.status(lines))]


def call_waiter(ctx: TurnContext, reason_tag: str = "asked") -> TurnResult:
    """Opens a help request and hands the chat to staff. Never blocked by pause, closed or guardrails."""
    box: dict[str, str] = {}

    async def commit(c: asyncpg.Connection) -> list[Reply]:
        opened = await handoff.handoff(c, ctx.cafe.id, ctx.conversation.id, reason_tag,
                                       table_session_id=ctx.conversation.table_session_id)
        if opened.created:
            box["request_id"] = opened.request_id
        return [Reply(render.help_opened() if opened.created else render.already_on_way())]

    async def after() -> None:
        if "request_id" in box:
            from app.domain.handoff.service import schedule_help_escalation

            await schedule_help_escalation(ctx.cafe.id, box["request_id"])
    return TurnResult(commit=[commit], after=[after])


def bill(ctx: TurnContext) -> TurnResult:
    """A bill request never stops the bot."""
    async def commit(c: asyncpg.Connection) -> list[Reply]:
        opened = await handoff.open_request(c, ctx.cafe.id, ctx.conversation.id, "bill",
                                            table_session_id=ctx.conversation.table_session_id)
        return [Reply(render.bill_opened() if opened.created else render.already_on_way())]
    return TurnResult(commit=[commit])
