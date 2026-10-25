"""Feedback after SERVED and the marketing opt-in (docs/backend.md#feedback, #consent).

Ask: the `feedback` timer, the café's delay after SERVED (15 / 30 / 60 min, default 30), re-checks the order, then
sends a 1–5 list inside the 24-hour window or the `feedback_request` template outside it. Nothing if it was rated
or is no longer SERVED. The list's reply ids carry the order (`rate:<order_id>:<n>`), so a tap needs no state; a
typed answer counts while the ask is recent and unrated (leading digit 1–5, the rest kept as text).

Replies: 4–5★ "Thank you!" · 3★ "Thanks! Anything we could do better?" · 1–2★ "Sorry about that — what went
wrong? We've passed this on to the team." + a `low_rating` request (Calls, manager+). After 1–3★ the next text is
saved as the feedback text unless it is clearly an order (a keyword or a dish name), which is a normal turn.

Opt-in: asked next if eligible, whatever the rating (after the text for 1–3★). Yes / No buttons with fixed text and
`WORDING_VERSION`. Never after an opt-out (STOP, "Stop offers", error 131050: only the customer opts back in);
after a "No" not within 30 days. The AI never asks for, reads or records consent. Counter QR: the text
`COUNTER_QR_TEXT` asks the same question with source `counter_qr`.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from app.core.logging import get_logger
from app.db.repositories import consents, conversations
from app.db.repositories import feedback as feedback_repo
from app.db.repositories import orders as orders_repo
from app.db.tx import conn
from app.domain import outbound, timers
from app.domain.conversation.turn import Reply, TurnContext, TurnResult
from app.domain.handoff import open_request
from app.integrations.whatsapp import messages as m
from app.ops import tracing

log = get_logger(__name__)

WORDING_VERSION = 1
OPT_IN_TEXT = "Would you like occasional offers from {cafe} on WhatsApp? You can reply STOP anytime."
REASK_AFTER = timedelta(days=30)
COUNTER_QR_TEXT = "Hi, send me offers"
CUSTOMER_OPT_OUTS = ("keyword", "button", "meta_block")
DEFAULT_DELAY_MIN = 30
LEADING_DIGIT = re.compile(r"^\s*([1-5])(?:\s*(?:★|stars?|/\s*5))?(?:[\s.,:;)\-–—]+(.*))?$", re.S | re.I)

STARS = {5: "Loved it", 4: "Good", 3: "Okay", 2: "Not great", 1: "Bad"}


def rating_list(number: int, order_id: str) -> m.ListMessage:
    rows = [m.Row(f"rate:{order_id}:{n}", f"{n} {'★' * n}", STARS[n]) for n in (5, 4, 3, 2, 1)]
    return m.ListMessage(f"How was your order #{number}? Rate 1–5.", "Rate", [m.Section("Your rating", rows)])


def opt_in_ask(cafe_name: str, source: str) -> m.Buttons:
    return m.Buttons(OPT_IN_TEXT.format(cafe=cafe_name),
                     [m.Button(f"optin:yes:{source}", "Yes, send offers"), m.Button(f"optin:no:{source}", "No thanks")])


def parse_typed(text: str) -> tuple[int, str | None] | None:
    """"2. Food took 25 minutes" → (2, "Food took 25 minutes"); "5" → (5, None); anything else → None."""
    match = LEADING_DIGIT.match(text)
    if not match:
        return None
    rest = (match.group(2) or "").strip()
    return int(match.group(1)), rest[:500] or None


async def eligible(c: asyncpg.Connection, cafe_id: str, customer_id: str) -> bool:
    row = await c.fetchrow(
        """select status, source, created_at from consents where cafe_id = $1 and customer_id = $2
           order by created_at desc limit 1""", cafe_id, customer_id)
    if row is None:
        return True
    if row["status"] == "opted_in" or row["source"] in CUSTOMER_OPT_OUTS:
        return False
    return bool(datetime.now(UTC) - row["created_at"] >= REASK_AFTER)  # a "No" from an ask


# ── the ask (timer) ───────────────────────────────────────────────────────

async def schedule(cafe_id: str, order_id: str, delay_minutes: int | None) -> None:
    minutes = delay_minutes if delay_minutes in (15, 30, 60) else DEFAULT_DELAY_MIN
    await timers.schedule(cafe_id, "feedback", order_id, minutes * 60, dedupe=f"feedback:{order_id}")


@timers.handler("feedback")
async def ask(cafe_id: str, order_id: str, _: dict[str, object]) -> None:
    async with conn() as c:
        order = await orders_repo.get(c, cafe_id, order_id)
        if order is None or order.status != "SERVED" or order.customer_id is None:
            return
        if await feedback_repo.exists(c, cafe_id, order_id):
            return
        convo = await conversations.by_customer(c, cafe_id, order.customer_id)
    if convo is not None and convo.mode == "human":
        tracing.count(skipped_human_mode=1)
        return  # staff are talking to them; don't interrupt with a survey
    await outbound.send(cafe_id, outbound.Recipient(customer_id=order.customer_id), rating_list(order.number, order.id),
                        f"feedback:{order.id}", outbound.Purpose.FEEDBACK,
                        fallback_template=m.Template("feedback_request", [str(order.number)]))


# ── answers (inside a turn) ───────────────────────────────────────────────

def _thanks(rating: int) -> str:
    if rating >= 4:
        return "Thank you!"
    if rating == 3:
        return "Thanks! Anything we could do better?"
    return "Sorry about that — what went wrong? We've passed this on to the team."


def rate(ctx: TurnContext, state: dict[str, Any], order_id: str, rating: int, text: str | None = None) -> TurnResult:
    """A rating for an order of this customer. Opens `low_rating` for 1–2★; asks for text after 1–3★."""
    async def commit(c: asyncpg.Connection) -> list[Reply]:
        order = await orders_repo.get(c, ctx.cafe.id, order_id)
        if order is None or order.customer_id != ctx.customer.id:
            return [Reply(m.Text("Thanks!"))]
        if not await feedback_repo.insert(c, ctx.cafe.id, order_id, rating, text):
            return [Reply(m.Text(f"We already have your rating for #{order.number}. Thank you!"))]
        if rating <= 2:
            await open_request(c, ctx.cafe.id, ctx.conversation.id, "low_rating", order_id=order_id,
                               table_session_id=order.table_session_id)
        replies = [Reply(m.Text(_thanks(rating) if not (text and rating <= 3) else "Thank you — we've passed this on to the team."))]
        if (rating >= 4 or text) and await eligible(c, ctx.cafe.id, ctx.customer.id):
            replies.append(Reply(opt_in_ask(ctx.cafe.name, "feedback_flow")))
        return replies

    if rating <= 3 and not text:
        state["awaiting"] = "feedback_text"
        state["feedback_order"] = order_id
    return TurnResult(state=state, commit=[commit])


def save_text(ctx: TurnContext, state: dict[str, Any], text: str) -> TurnResult:
    order_id = str(state.pop("feedback_order", "") or "")
    state["awaiting"] = None

    async def commit(c: asyncpg.Connection) -> list[Reply]:
        if not order_id or await feedback_repo.set_text(c, ctx.cafe.id, order_id, text[:500]) is None:
            return []
        replies = [Reply(m.Text("Thank you — we've passed this on to the team."))]
        if await eligible(c, ctx.cafe.id, ctx.customer.id):
            replies.append(Reply(opt_in_ask(ctx.cafe.name, "feedback_flow")))
        return replies
    return TurnResult(state=state, commit=[commit])


def answer_opt_in(ctx: TurnContext, state: dict[str, Any], yes: bool, source: str) -> TurnResult:
    if source not in ("feedback_flow", "counter_qr", "order_flow"):
        source = "feedback_flow"

    async def commit(c: asyncpg.Connection) -> list[Reply]:
        latest = await consents.latest_status(c, ctx.cafe.id, ctx.customer.id)
        status = "opted_in" if yes else "opted_out"
        if latest != status:
            await consents.append(c, ctx.cafe.id, ctx.customer.id, status, source, WORDING_VERSION)
        if yes:
            return [Reply(m.Text(f"Done — you'll get occasional offers from {ctx.cafe.name}. Reply STOP anytime."))]
        return [Reply(m.Text("No problem. Ordering works just the same."))]
    return TurnResult(state=state, commit=[commit])


def counter_qr(ctx: TurnContext, state: dict[str, Any]) -> TurnResult:
    """The counter QR asks directly: the customer chose to scan it, so a past opt-out doesn't block the question."""
    async def commit(c: asyncpg.Connection) -> list[Reply]:
        if await consents.latest_status(c, ctx.cafe.id, ctx.customer.id) == "opted_in":
            return [Reply(m.Text(f"You're already getting offers from {ctx.cafe.name}. Reply STOP anytime."))]
        return [Reply(opt_in_ask(ctx.cafe.name, "counter_qr"))]
    return TurnResult(state=state, commit=[commit])


async def open_ask_for(cafe_id: str, customer_id: str) -> str | None:
    async with conn() as c:
        return await feedback_repo.open_ask(c, cafe_id, customer_id)

