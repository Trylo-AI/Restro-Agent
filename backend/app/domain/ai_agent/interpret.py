"""One free-text turn → a validated Interpretation. The model interprets; code checks every field.

Never states a price, creates an order, changes status or records consent (docs/rules.md: domain/ai_agent).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db.repositories import menu as menu_repo
from app.db.repositories import orders as orders_repo
from app.db.repositories import profiles
from app.db.repositories.menu import MenuItem
from app.db.tx import conn
from app.domain import hours as hours_mod
from app.domain import recommend as rec_mod
from app.domain.ai_agent import calls, guardrails, prompt
from app.domain.conversation.turn import Turn, TurnContext
from app.ops import tracing

INTENTS = {"browse", "ask_question", "add_items", "modify_cart", "confirm", "cancel", "order_status", "human",
           "smalltalk", "unclear", "recommend"}
HUMAN_REASONS = {"asked", "frustration", "complaint", "allergen_unknown", "repeated_unclear"}
MAX_OUTPUT_TOKENS = 600


@dataclass
class Op:
    op: str
    item: MenuItem
    qty: int
    note: str | None


@dataclass
class Rejection:
    kind: str                 # sold_out · not_on_menu · not_in_cart
    words: str
    item: MenuItem | None = None
    alternatives: list[MenuItem] = field(default_factory=list)


@dataclass
class AllergenAnswer:
    kind: str                 # contains · free_from · unknown · list · none_found · unknown_allergen
    allergen: str | None = None        # label, e.g. "Tree nuts"
    item: MenuItem | None = None
    alternatives: list[MenuItem] = field(default_factory=list)


@dataclass
class RecPick:
    item: MenuItem                     # available, resolved from the turn view
    why: str | None                    # passed clean_why, else None (code writes one)


@dataclass
class RecommendAsk:
    mood: str | None                   # sanitise_mood(model summary)
    picks: list[RecPick] = field(default_factory=list)


@dataclass
class Interpretation:
    intent: str
    ops: list[Op] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    allergen: AllergenAnswer | None = None
    needs_human: str | None = None
    clarify: str | None = None
    reply: str | None = None
    recommend: RecommendAsk | None = None
    guardrails: list[str] = field(default_factory=list)
    call_id: str | None = None


async def interpret(ctx: TurnContext, state: dict[str, Any], turn: Turn) -> Interpretation | None:
    """None = the LLM failed twice (the caller hands off with system_error)."""
    view = await prompt.menu_view(ctx.cafe)
    async with conn() as c:
        summary = await profiles.summary(c, ctx.cafe.id, ctx.customer.id)
        chat = await profiles.recent_chat(c, ctx.cafe.id, ctx.conversation.id, ctx.customer.id,
                                          [m.id for m in turn.messages])
        open_orders = await orders_repo.open_for_customer(c, ctx.cafe.id, ctx.customer.id)
        cart_items = await menu_repo.items_by_ids(c, ctx.cafe.id, _cart_ids(state))
    h = hours_mod.status(ctx.cafe)
    edit = state.get("order_edit")
    cart = edit["cart"] if edit else state["cart"]
    cart_lines = []
    for line in cart.get("items", []):
        it = cart_items.get(line["item_id"])
        if it:
            ref = view.short_of.get(it.id, "sold out")
            cart_lines.append(f"{ref} {it.name} ×{line['qty']}" + (f" ({line['note']})" if line.get("note") else ""))
    text = prompt.suffix(
        summary=profiles.as_dict(summary), hours={"open": h.open, "paused": h.paused, "near_closing": h.near_closing},
        step=str(state.get("step")), cart_lines=cart_lines,
        editing=f"order #{edit['number']}" if edit else None,
        open_orders=[f"#{o.number} {o.status}" for o in open_orders], chat=chat, text=turn.text,
        local_time=datetime.now(ZoneInfo(ctx.cafe.timezone or "Asia/Kolkata")).strftime("%a %H:%M"),
    )
    attempted = await calls.call(
        cafe_id=ctx.cafe.id, cafe_model=ctx.cafe.settings.get("model"), purpose="turn", system=view.system, prompt=text,
        schema_name="turn", max_output_tokens=MAX_OUTPUT_TOKENS, conversation_id=ctx.conversation.id,
        message_id=turn.first_id, trace_suffix=text,
    )
    if attempted is None:
        return None
    out = attempted.result.data or {}
    intent = out.get("intent")
    if intent not in INTENTS:
        return None
    result = Interpretation(intent=str(intent), call_id=attempted.call_id)

    nh = out.get("needs_human") or {}
    if intent == "human" or nh.get("flag"):
        reason = nh.get("reason")
        result.needs_human = reason if reason in HUMAN_REASONS else "asked"

    await _resolve_ops(ctx, out.get("cart_ops") or [], view, cart, cart_items, result)
    await _answer_allergen(ctx, out.get("allergen_question"), view, result)
    if intent == "recommend":
        result.recommend = _resolve_recommend(out.get("recommend"), view, result)  # a missing key counts as null

    for key in ("reply", "clarify"):
        raw = out.get(key)
        cleaned = guardrails.clean_reply(raw if isinstance(raw, str) else None)
        if raw and cleaned is None and guardrails.has_amount(raw):
            result.guardrails.append(f"{key}_amount_dropped")
            tracing.degraded("guardrail_reply_dropped", field=key)
        setattr(result, key, cleaned)
    if result.rejections and result.reply:
        result.reply = None  # any rejected op discards the model's reply; code says what happened
        result.guardrails.append("reply_dropped_rejected_ops")
    await calls.add_guardrails_to_trace(ctx.cafe.id, attempted.call_id, result.guardrails)
    return result


def _cart_ids(state: dict[str, Any]) -> list[str]:
    edit = state.get("order_edit")
    cart = edit["cart"] if edit else state.get("cart", {})
    return [line["item_id"] for line in cart.get("items", [])]


async def _all_items(cafe_id: str) -> list[MenuItem]:
    async with conn() as c:
        return await menu_repo.all_items(c, cafe_id)


async def _resolve_ops(ctx: TurnContext, raw_ops: list[Any], view: prompt.MenuView, cart: dict[str, Any],
                       cart_items: dict[str, MenuItem], result: Interpretation) -> None:
    everything: list[MenuItem] | None = None
    in_cart = [cart_items[line["item_id"]] for line in cart.get("items", []) if line["item_id"] in cart_items]
    for raw in raw_ops[:20]:
        if not isinstance(raw, dict) or raw.get("op") not in ("add", "remove", "set_qty", "set_note"):
            continue
        ref = str(raw.get("item_ref") or "").strip()[:80]
        note = raw.get("note") if isinstance(raw.get("note"), str) else None
        if note and guardrails.has_amount(note):
            note = None
            result.guardrails.append("note_amount_dropped")
        qty = raw.get("qty") if isinstance(raw.get("qty"), int) else 1
        item = view.by_short.get(ref)
        if raw["op"] == "add":
            if item is None:
                everything = everything or await _all_items(ctx.cafe.id)
                by_name = guardrails.match_by_name(ref, everything)
                if by_name and not by_name.available:
                    alts = [i for i in view.items if i.category == by_name.category][:3]
                    result.rejections.append(Rejection("sold_out", ref, by_name, alts))
                elif by_name and by_name.available:
                    result.ops.append(Op("add", by_name, max(1, min(qty or 1, 20)), note))
                    result.guardrails.append("ref_resolved_by_name")
                else:
                    result.rejections.append(Rejection("not_on_menu", ref))
                continue
            result.ops.append(Op("add", item, max(1, min(qty or 1, 20)), note))
            continue
        target = item if item and item in in_cart else guardrails.match_by_name(ref, in_cart)
        if target is None:
            result.rejections.append(Rejection("not_in_cart", ref))
            continue
        result.ops.append(Op(raw["op"], target, max(0, min(qty if qty is not None else 1, 20)), note))


def _resolve_recommend(raw: Any, view: prompt.MenuView, result: Interpretation) -> RecommendAsk:
    """Picks resolved to available dishes on the turn view; unresolved ones dropped; every `why` through clean_why
    (the turn prompt shows prices, so a why with any digit goes and code writes one), and dropped when it names a dish
    that is not a pick."""
    raw = raw if isinstance(raw, dict) else {}
    ask = RecommendAsk(mood=rec_mod.sanitise_mood(raw.get("mood") if isinstance(raw.get("mood"), str) else None))
    seen: set[str] = set()
    for p in raw.get("picks") or []:
        if not isinstance(p, dict):
            continue
        item = view.by_short.get(str(p.get("item_ref") or "").strip()[:80])
        if item is None:
            result.guardrails.append("rec_ref_unresolved")
            continue
        if item.id in seen or len(ask.picks) >= rec_mod.MAX_PICKS:
            continue
        seen.add(item.id)
        why_raw = p.get("why") if isinstance(p.get("why"), str) else None
        why = guardrails.clean_why(why_raw)
        if why_raw and why is None:
            result.guardrails.append("rec_why_dropped")
        ask.picks.append(RecPick(item, why))
    picked = [p.item for p in ask.picks]
    for pick in ask.picks:
        if rec_mod.names_other_dish(pick.why, view.items, picked):
            pick.why = None  # names a dish that is not a pick (maybe filtered out): code writes the why
            result.guardrails.append("rec_why_names_dish")
    return ask


async def _answer_allergen(ctx: TurnContext, q: Any, view: prompt.MenuView, result: Interpretation) -> None:
    if not isinstance(q, dict) or not q.get("allergen"):
        return
    norm = guardrails.normalise_allergen(str(q.get("allergen")))
    if norm is None:
        result.allergen = AllergenAnswer("unknown_allergen")
        return
    allergen_id, label = norm
    ref = str(q.get("item_ref") or "")
    item = view.by_short.get(ref) or (guardrails.match_by_name(ref, view.items) if ref else None)
    async with conn() as c:
        if item is not None:
            states = await menu_repo.allergens(c, ctx.cafe.id, [i.id for i in view.items if i.category == item.category])
        else:
            states = await menu_repo.allergens(c, ctx.cafe.id, [i.id for i in view.items])
    if item is None:
        free = [i for i in view.items if states.get(i.id, {}).get(allergen_id) == "free_from"][:5]
        result.allergen = AllergenAnswer("list" if free else "none_found", label, None, free)
        return
    st = states.get(item.id, {}).get(allergen_id, "unknown")  # no row = unknown
    alternatives = [i for i in view.items if i.category == item.category and i.id != item.id
                    and states.get(i.id, {}).get(allergen_id) == "free_from"][:3]
    result.allergen = AllergenAnswer(st, label, item, alternatives if st == "contains" else [])
