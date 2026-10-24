"""Routing order (docs/backend.md#routing-order). First match wins:

1. `optout` button or STOP → opt out, in any step and mode.
2. mode = human → store only (shown in Chats); no bot reply.
3. QR text → join or open the table session → greeting.
4. Interactive reply → handler by reply id.
5. Non-text → "please type" + Menu · Call waiter.
6. `awaiting` set → save the answer.
7. Keyword (call waiter, bill, status, menu) → deterministic handler.
8. Paused or closed → fixed reply.
9. Free text → one LLM call (domain/ai_agent).

Keywords match the whole normalised message against short per-language lists, never a substring.
"""

import re
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg

from app.db.repositories import conversations as convo_repo
from app.db.repositories import customers as customers_repo
from app.db.repositories import menu as menu_repo
from app.db.repositories import tables as tables_repo
from app.db.tx import conn
from app.domain import cart as cart_mod
from app.domain import consent
from app.domain.conversation import confirm, flows, render
from app.domain.conversation.turn import Reply, Turn, TurnContext, TurnResult
from app.integrations.whatsapp.messages import Text

KEYWORDS: dict[str, set[str]] = {
    "stop": {"stop", "unsubscribe", "stop offers", "stop messages", "band karo"},
    "help": {"call waiter", "waiter", "waiter please", "help", "human", "person", "talk to a person", "staff",
             "call staff", "waiter bulao", "waiter ko bulao", "bhaiya", "bhaiya please", "manager", "speak to someone",
             "वेटर", "वेटर बुलाओ"},
    "bill": {"bill", "bill please", "the bill", "check please", "bill do", "bill de do", "bill dena", "bill lao",
             "बिल", "बिल दो"},
    "status": {"status", "order status", "where is my order", "my order", "kitna time", "kitni der", "kab aayega",
               "how long", "how much longer"},
    "menu": {"menu", "show menu", "menu please", "menu dikhao", "the menu", "मेनू"},
    "greeting": {"hi", "hii", "hiii", "hello", "hey", "helo", "namaste", "namaskar", "hola", "good morning",
                 "good evening", "good afternoon", "yo", "नमस्ते"},
}

FreeTextHandler = Callable[[TurnContext, dict[str, Any], Turn], Awaitable[TurnResult]]


async def _fallback_free_text(ctx: TurnContext, state: dict[str, Any], turn: Turn) -> TurnResult:
    """Until the AI agent is registered (S9): greet or point to the menu."""
    return TurnResult(state=state, replies=[Reply(render.fallback())])


_free_text: FreeTextHandler = _fallback_free_text


def set_free_text_handler(handler: FreeTextHandler) -> None:
    global _free_text
    _free_text = handler


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def keyword(text: str) -> str | None:
    norm = normalise(text)
    for name, words in KEYWORDS.items():
        if norm in words:
            return str(name)
    return None


def _looks_like_name(text: str, dish_words: set[str]) -> str | None:
    words = re.findall(r"[^\W\d_]+", text)
    if not 1 <= len(words) <= 3 or len(text) > 40:
        return None
    lowered = {w.lower() for w in words}
    if lowered & dish_words or keyword(text):
        return None
    if len(words) >= 2 and words[0].lower() in {"i", "im", "i'm", "my", "this", "mera", "main"}:
        words = words[-1:]  # "I'm Riya" · "my name is Riya" → Riya
    return str(words[0]).capitalize()


async def _dish_words(cafe_id: str) -> set[str]:
    async with conn() as c:
        items = await menu_repo.available_items(c, cafe_id)
    return {w.lower() for it in items for w in re.findall(r"[^\W\d_]+", it.name) if len(w) > 2}


async def route(ctx: TurnContext, turn: Turn) -> TurnResult:
    now = datetime.now(UTC)
    state = flows.fresh_state(ctx.conversation.state)
    text = turn.text if turn.kind == "text" else ""
    kw = keyword(text) if text else None

    # 1 · STOP / opt-out, any step and mode
    if turn.reply_id == "optout" or kw == "stop":
        source = "button" if turn.reply_id == "optout" else "keyword"

        async def opt_out(c: asyncpg.Connection) -> list[Reply]:
            await consent.opt_out(c, ctx.cafe.id, ctx.customer.id, source)
            return [Reply(render.opted_out(ctx.cafe.name))]
        return TurnResult(commit=[opt_out])

    # 2 · human mode: the bot is silent
    if ctx.conversation.mode == "human":
        return TurnResult()

    # 3 · table QR
    if text:
        async with conn() as c:
            table = await tables_repo.by_qr_text(c, ctx.cafe.id, text)
        if table:
            return await _join_table(ctx, state, now, table[0])

    if flows.is_stale_visit(state, now):
        state = flows.new_visit(state, now)
    state["visit_at"] = now.isoformat()

    # 4 · taps
    if turn.kind == "tap":
        return await _tap(ctx, state, turn.reply_id or "")

    # 5 · non-text
    if turn.kind == "other":
        return TurnResult(state=state, replies=[Reply(render.non_text())])

    # 6 · awaiting an answer
    if state.get("awaiting") == "name":
        name = _looks_like_name(text, await _dish_words(ctx.cafe.id))
        if name:
            state["awaiting"] = None
            if state.get("step") == "greeting":
                state["step"] = "browsing"

            async def save_name(c: asyncpg.Connection) -> None:
                await customers_repo.set_name_if_missing(c, ctx.cafe.id, ctx.customer.id, name)
            if state.get("step") == "order_placed":
                replies = [Reply(Text(f"Thanks, {name}!"))]
            else:
                replies = [Reply(render.name_saved(name))]
            return TurnResult(state=state, commit=[save_name], replies=replies)
        state["awaiting"] = None  # an order instead of a name: ask again after the order
    elif state.get("awaiting") == "table":
        async with conn() as c:
            tables = await tables_repo.all_tables(c, ctx.cafe.id)
        wanted = normalise(text).removeprefix("table ").removeprefix("t").strip()
        match = next((tid for tid, label in tables if normalise(label).removeprefix("t") == wanted), None)
        if match:
            return await _set_table(ctx, state, match)
        return TurnResult(state=state, replies=[Reply(render.ask_table(tables))])

    # 7 · keywords
    if kw == "help":
        result = flows.call_waiter(ctx)
        result.state = state
        return result
    if kw == "bill":
        result = flows.bill(ctx)
        result.state = state
        return result
    if kw == "status":
        return TurnResult(state=state, replies=await flows.status(ctx))
    if kw == "menu" or (kw == "greeting" and state.get("step") != "greeting"):
        blocked = flows.can_order(ctx)
        return TurnResult(state=state, replies=blocked or await flows.menu_categories(ctx))
    if kw == "greeting":
        return TurnResult(state=state, replies=await flows.greeting(ctx, state))

    # 8 · paused or closed
    blocked = flows.can_order(ctx)
    if blocked:
        return TurnResult(state=state, replies=blocked)

    # 9 · free text → the AI agent
    return await _free_text(ctx, state, turn)


async def _join_table(ctx: TurnContext, state: dict[str, Any], now: datetime, table_id: str) -> TurnResult:
    """QR scan: a new visit at this table (shared table sessions join the open one)."""
    keep_cart = state.get("cart") if state.get("step") in ("building_cart", "confirming") else None
    state = flows.new_visit(state, now)
    if keep_cart:
        state["cart"] = keep_cart
    async with conn() as c:
        session_id = await tables_repo.open_session(c, ctx.cafe.id, table_id)

    async def set_session(c: asyncpg.Connection) -> None:
        await convo_repo.set_table_session(c, ctx.cafe.id, ctx.conversation.id, session_id)

    ctx.conversation.table_session_id = session_id
    replies = await flows.greeting(ctx, state)
    blocked = flows.can_order(ctx)
    return TurnResult(state=state, commit=[set_session], replies=replies + (blocked or []))


async def _set_table(ctx: TurnContext, state: dict[str, Any], table_id: str) -> TurnResult:
    async with conn() as c:
        if await tables_repo.get(c, ctx.cafe.id, table_id) is None:
            tables = await tables_repo.all_tables(c, ctx.cafe.id)
            return TurnResult(state=state, replies=[Reply(render.ask_table(tables))])
        session_id = await tables_repo.open_session(c, ctx.cafe.id, table_id)

    async def set_session(c: asyncpg.Connection) -> None:
        await convo_repo.set_table_session(c, ctx.cafe.id, ctx.conversation.id, session_id)

    ctx.conversation.table_session_id = session_id
    state["awaiting"] = None
    pending = state.pop("confirm_after_table", None)
    if pending is not None:
        result = await confirm.confirm(ctx, state, int(pending))
        result.commit.insert(0, set_session)  # the session must exist before the order points at it
        return result
    return TurnResult(state=state, commit=[set_session], replies=await flows.summary(ctx, state))


def _int(value: str | None, default: int = -1) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


async def _tap(ctx: TurnContext, state: dict[str, Any], reply_id: str) -> TurnResult:
    action, *args = reply_id.split(":")
    arg = args[0] if args else None

    if action == "help":
        result = flows.call_waiter(ctx)
        result.state = state
        return result
    if action == "bill":
        result = flows.bill(ctx)
        result.state = state
        return result
    if action == "status":
        return TurnResult(state=state, replies=await flows.status(ctx))
    if action in ("edit_order", "cancel_order", "apply_edit", "keep_order") and arg:
        if action == "edit_order":
            return await confirm.start_edit(ctx, state, arg)
        if action == "cancel_order":
            return await confirm.cancel_order(ctx, state, arg, _int(args[1] if len(args) > 1 else None))
        if action == "apply_edit":
            return await confirm.apply_edit(ctx, state, arg, _int(args[1] if len(args) > 1 else None))
        return await confirm.keep_order(ctx, state, arg)

    # Everything below is ordering: paused or closed gets the fixed reply.
    blocked = flows.can_order(ctx)
    if blocked:
        return TurnResult(state=state, replies=blocked)

    if action in ("menu", "recommend"):
        return TurnResult(state=state, replies=await flows.menu_categories(ctx))
    if action == "cat" and arg:
        return TurnResult(state=state, replies=await flows.category_page(ctx, arg, _int(args[1] if len(args) > 1 else None, 0)))
    if action == "item" and arg:
        if state.get("order_edit"):
            return await _add_to_edit(ctx, state, arg)
        return TurnResult(state=state, replies=await flows.add_item(ctx, state, arg))
    if action == "reorder" and arg:
        return TurnResult(state=state, replies=await flows.reorder(ctx, state, arg))
    if action == "confirm":
        return await confirm.confirm(ctx, state, _int(arg))
    if action == "edit":
        state["step"] = "building_cart"
        return TurnResult(state=state, replies=[Reply(render.edit_prompt())])
    if action == "cancel":
        if _int(arg) != int(state["cart"].get("version", -1)):
            return TurnResult(state=state, replies=await flows.summary(ctx, state))
        state["cart"] = cart_mod.clear(state["cart"])
        state["step"] = "browsing"
        return TurnResult(state=state, replies=[Reply(render.cart_cleared())])
    if action == "table" and arg:
        return await _set_table(ctx, state, arg)
    return TurnResult(state=state, replies=await flows.menu_categories(ctx))


async def _add_to_edit(ctx: TurnContext, state: dict[str, Any], item_id: str) -> TurnResult:
    async with conn() as c:
        item = await menu_repo.get(c, ctx.cafe.id, item_id)
    if item is None or not item.available:
        return TurnResult(state=state, replies=[Reply(render.sold_out(item.name))] if item else [])
    try:
        state["order_edit"]["cart"] = cart_mod.add(state["order_edit"]["cart"], item, 1)
    except cart_mod.CartError:
        pass
    return TurnResult(state=state, replies=await confirm.edit_summary(ctx, state))
