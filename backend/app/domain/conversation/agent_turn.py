"""Free-text turns (routing rule 9): the AI agent interprets, this module decides (docs/backend.md#ai-agent).

Cart ops apply to the cart, or to the NEW order being edited; rejected ops are explained by code; allergen
questions are answered from menu data only (`unknown` → a person); `confirm` acts only at `confirming` with an
unchanged cart; two unclear turns → handoff. Every message text here is code-rendered or a model reply that
passed the amount scan.
"""

from typing import Any

import asyncpg

from app.db.repositories import orders as orders_repo
from app.db.tx import conn
from app.domain import cart as cart_mod
from app.domain import handoff
from app.domain.ai_agent import calls, guardrails
from app.domain.ai_agent.interpret import Interpretation, Op, Rejection, interpret
from app.domain.conversation import confirm, flows, render
from app.domain.conversation.turn import Reply, Turn, TurnContext, TurnResult
from app.integrations.whatsapp import messages as m

MAX_STRIKES = 2
TROUBLE = "Sorry, I'm having trouble — a team member will help you."


def _handoff(ctx: TurnContext, state: dict[str, Any], reason: str, text: str | None = None) -> TurnResult:
    box: dict[str, str] = {}

    async def after() -> None:
        if "request_id" in box:
            from app.domain.handoff.service import schedule_help_escalation

            await schedule_help_escalation(ctx.cafe.id, box["request_id"])

    async def commit(c: asyncpg.Connection) -> list[Reply]:
        opened = await handoff.handoff(c, ctx.cafe.id, ctx.conversation.id, reason,
                                       table_session_id=ctx.conversation.table_session_id)
        if opened.created:
            box["request_id"] = opened.request_id
        if text:
            return [Reply(m.Text(text))]
        if reason == "allergen_unknown":
            return [Reply(render.allergy_check())]
        return [Reply(render.help_opened() if opened.created else render.already_on_way())]
    state["clarify_strikes"] = 0
    return TurnResult(state=state, commit=[commit], after=[after])


def _strike(ctx: TurnContext, state: dict[str, Any], replies: list[Reply]) -> TurnResult:
    state["clarify_strikes"] = int(state.get("clarify_strikes", 0)) + 1
    if state["clarify_strikes"] >= MAX_STRIKES:
        return _handoff(ctx, state, "repeated_unclear")
    return TurnResult(state=state, replies=replies)


def _rejection_lines(it: Interpretation) -> list[str]:
    lines = []
    for r in it.rejections:
        if r.kind == "sold_out" and r.item:
            alt = f" How about {', '.join(a.name for a in r.alternatives)}?" if r.alternatives else ""
            lines.append(f"{r.item.name} is sold out right now.{alt}")
        elif r.kind == "not_on_menu":
            lines.append(f"I couldn't find “{r.words[:40]}” on our menu.")
        elif r.kind == "not_in_cart":
            lines.append(f"“{r.words[:40]}” isn't in your order.")
    return lines


async def _apply_ops(ctx: TurnContext, state: dict[str, Any], it: Interpretation) -> TurnResult:
    editing = state.get("order_edit")
    target = editing["cart"] if editing else state["cart"]
    applied = 0
    for op in it.ops:
        try:
            if op.op == "add":
                target = cart_mod.add(target, op.item, op.qty, op.note)
            elif op.op == "remove":
                target = cart_mod.remove(target, op.item.id)
            elif op.op == "set_qty":
                target = cart_mod.set_qty(target, op.item.id, op.qty)
            elif op.op == "set_note":
                target = cart_mod.set_note(target, op.item.id, op.note)
            applied += 1
        except cart_mod.CartError:
            it.rejections.append(_cart_rejection(op))
    if editing:
        editing["cart"] = target
    else:
        state["cart"] = target
        if applied:
            state["step"] = "building_cart"

    lines = _rejection_lines(it)
    replies: list[Reply] = [Reply(m.Text("\n".join(lines)))] if lines else []
    not_on_menu = sum(1 for r in it.rejections if r.kind == "not_on_menu")
    if applied:
        state["clarify_strikes"] = 0
        if it.reply and not it.rejections:
            replies.append(Reply(m.Text(it.reply)))
        replies += await (confirm.edit_summary(ctx, state) if editing else flows.summary(ctx, state))
        return TurnResult(state=state, replies=replies)
    if not_on_menu:
        return _strike(ctx, state, replies + await flows.menu_categories(ctx))
    if target.get("items"):
        replies += await (confirm.edit_summary(ctx, state) if editing else flows.summary(ctx, state))
    else:
        replies += await flows.menu_categories(ctx)
    return TurnResult(state=state, replies=replies)


def _cart_rejection(op: Op) -> Rejection:
    return Rejection("not_in_cart" if op.op != "add" else "sold_out", op.item.name, op.item)


def _allergen_replies(ctx: TurnContext, state: dict[str, Any], it: Interpretation) -> TurnResult:
    a = it.allergen
    assert a is not None
    if a.kind in ("unknown", "unknown_allergen") or (a.kind == "none_found"):
        return _handoff(ctx, state, "allergen_unknown")
    if a.kind == "contains" and a.item:
        alt = f" These are marked free from it: {', '.join(x.name for x in a.alternatives)}." if a.alternatives else ""
        text = f"{a.item.name} contains {a.allergen.lower() if a.allergen else 'it'}.{alt}"
    elif a.kind == "free_from" and a.item:
        text = (f"{a.item.name} is marked free from {a.allergen.lower() if a.allergen else 'it'}. "
                "Please tell our team about severe allergies.")
    else:  # list
        names = ", ".join(x.name for x in a.alternatives)
        text = f"These are marked free from {a.allergen.lower() if a.allergen else 'it'}: {names}. Please tell our team about severe allergies."
    state["clarify_strikes"] = 0
    return TurnResult(state=state, replies=[Reply(m.Buttons(text, [render.MENU, render.CALL_WAITER]))])


async def handle_text(ctx: TurnContext, state: dict[str, Any], turn: Turn) -> TurnResult:
    mentioned = guardrails.allergens_in(turn.text)
    if mentioned:  # allergies stated in chat go to this visit's order, never inferred elsewhere
        prior = [x for x in (state.get("allergy_note") or "").split(", ") if x]
        state["allergy_note"] = ", ".join(dict.fromkeys(prior + mentioned))

    if calls.kill_switch() == "handoff":
        return _handoff(ctx, state, "system_error", TROUBLE)
    it = await interpret(ctx, state, turn)
    if it is None:
        return _handoff(ctx, state, "system_error", TROUBLE)

    if it.needs_human:
        return _handoff(ctx, state, it.needs_human)
    if it.allergen is not None:
        return _allergen_replies(ctx, state, it)
    if it.intent in ("add_items", "modify_cart") or it.ops or it.rejections:
        return await _apply_ops(ctx, state, it)
    if it.intent == "confirm":
        version = int(state["cart"].get("version", 0))
        if state.get("step") == "confirming" and state["cart"].get("items"):
            return await confirm.confirm(ctx, state, version)
        return TurnResult(state=state, replies=await flows.summary(ctx, state))
    if it.intent == "cancel":
        async with conn() as c:
            open_new = [o for o in await orders_repo.open_for_customer(c, ctx.cafe.id, ctx.customer.id) if o.status == "NEW"]
        if open_new:  # typed "cancel" gets the button; the tap does the cancelling
            o = open_new[-1]
            return TurnResult(state=state, replies=[Reply(m.Buttons(
                f"Do you want to cancel order #{o.number}?", [m.Button(f"cancel_order:{o.id}:{o.version}", "Cancel order"),
                                                             m.Button(f"keep_order:{o.id}", "Keep order")]))])
        state["cart"] = cart_mod.clear(state["cart"])
        state["step"] = "browsing"
        return TurnResult(state=state, replies=[Reply(render.cart_cleared())])
    if it.intent == "order_status":
        return TurnResult(state=state, replies=await flows.status(ctx))
    if it.intent == "unclear" or (it.clarify and it.intent not in ("browse", "ask_question", "smalltalk")):
        text = it.clarify or it.reply or "Sorry, could you say that another way?"
        return _strike(ctx, state, [Reply(m.Buttons(text, [render.MENU, render.CALL_WAITER]))])

    # browse · ask_question · smalltalk: one short line + the menu
    state["clarify_strikes"] = 0
    text = it.reply or "What would you like to order?"
    return TurnResult(state=state, replies=[Reply(m.Buttons(text, [render.MENU, render.CALL_WAITER]))])
