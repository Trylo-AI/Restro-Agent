"""The only path to WhatsApp (CLAUDE.md invariant 10, docs/backend.md#sending).

Two halves so a turn can commit its state and its outbound steps together, then send with no transaction open:

- `prepare(conn, ...)` inside the caller's transaction: 24-hour window check → one `wa_messages` step row
  (`pending`, unique idempotency key). An existing key returns the existing row: re-runs never add a step.
- `deliver(cafe_id, step_id)` after commit: claim `attempted_at` atomically → send → `sent` / `ambiguous`
  (send_check at 60 s) / `failed`; retry only what certainly didn't go (connection refused, 429), 3 attempts.

`send()` does both for callers outside a turn (timers, staff replies).
Dish names and prices in any message come from the DB, rendered by the caller's code, never by the LLM.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from app.core.errors import WindowClosed
from app.core.logging import get_logger
from app.db.repositories import cafes, customers, memberships
from app.db.repositories import wa_messages as steps_repo
from app.db.repositories.wa_messages import Step
from app.db.tx import conn as db_conn
from app.db.tx import tx
from app.domain import timers
from app.integrations.whatsapp import client
from app.integrations.whatsapp.errors import SendClass, SendError
from app.integrations.whatsapp.messages import Message, Template, payload
from app.ops import tracing

log = get_logger(__name__)

WINDOW = timedelta(hours=24)
RETRY_DELAYS = (0.5, 1.5, 3.0)


class Purpose:
    REPLY = "reply"                # bot reply in a turn: outside the window → dropped and logged
    ORDER_STATUS = "order_status"  # outside the window → `order_update` template
    STAFF_REPLY = "staff_reply"    # outside the window → WindowClosed (Chats disables the composer)
    ALERT = "alert"                # `new_order_alert` template to a staff alert number (no customer)


@dataclass(frozen=True)
class Recipient:
    customer_id: str | None = None
    to_ref: str | None = None      # alerts: "cafe_alert" or "membership:<id>"; never a phone number in the row


def in_window(last_inbound_at: datetime | None, now: datetime | None = None) -> bool:
    return last_inbound_at is not None and (now or datetime.now(UTC)) - last_inbound_at < WINDOW


async def prepare(conn: asyncpg.Connection, cafe_id: str, recipient: Recipient, message: Message, key: str,
                  purpose: str, *, fallback_template: Template | None = None,
                  customer: customers.Customer | None = None) -> Step | None:
    """Window check + step row, inside the caller's transaction. None = deliberately not sent.

    Pass `customer` when the caller already loaded it this turn (saves a round trip).
    """
    if purpose == Purpose.ALERT:
        if not isinstance(message, Template):
            raise ValueError("alerts are templates")
        body = {**payload(message), "_to_ref": recipient.to_ref}
        step, _ = await steps_repo.insert_step(conn, cafe_id, None, key, "template", body, message.name)
        return step

    if recipient.customer_id is None:
        raise ValueError("customer message without a customer")
    if customer is None or customer.id != recipient.customer_id or customer.cafe_id != cafe_id:
        customer = await customers.get(conn, cafe_id, recipient.customer_id)
    if customer is None:
        raise ValueError("unknown customer for this café")
    chosen: Message = message
    if not isinstance(message, Template) and not in_window(customer.last_inbound_at):
        if purpose == Purpose.ORDER_STATUS and fallback_template is not None:
            chosen = fallback_template
            tracing.degraded("send_template_fallback", template=fallback_template.name)
        elif purpose == Purpose.STAFF_REPLY:
            raise WindowClosed()
        else:
            log.info("outbound_dropped_window_closed", cafe_id=cafe_id, key=key)
            return None
    template_name = chosen.name if isinstance(chosen, Template) else None
    step, _ = await steps_repo.insert_step(conn, cafe_id, customer.id, key, chosen.kind, payload(chosen), template_name)
    return step


async def _alert_address(c: asyncpg.Connection, step: Step, cafe: cafes.Cafe) -> str | None:
    to_ref = str(step.payload.get("_to_ref") or "")
    if to_ref == "cafe_alert":
        return str(cafe.settings.get("alert_phone") or "") or None
    if to_ref.startswith("membership:"):
        return await memberships.alert_phone(c, step.cafe_id, to_ref.removeprefix("membership:"))
    return None


def _meta_body(step: Step) -> dict[str, Any]:
    return {k: v for k, v in step.payload.items() if not k.startswith("_")}


async def deliver(cafe_id: str, step_id: str) -> str:
    """Send one prepared step. Returns the final status. Safe to call twice: only one call ever sends."""
    with tracing.span("outbound.deliver") as sp:
        status = await _deliver(cafe_id, step_id)
        sp.annotate(status=status)
        if status == "failed":
            sp.fail("send failed")
        tracing.count(**{f"sends_{status}": 1})
        return status


async def _deliver(cafe_id: str, step_id: str) -> str:
    for attempt in range(len(RETRY_DELAYS) + 1):
        async with db_conn() as c:
            claimed = await steps_repo.claim_for_delivery(c, cafe_id, step_id)
            if claimed is None:
                current = await steps_repo.get(c, cafe_id, step_id)
                return current.status if current else "missing"
            step, row = claimed
            cafe = cafes.Cafe(
                id=step.cafe_id, name=row["cafe_name"], status=row["cafe_status"], phone_number_id=row["phone_number_id"],
                display_phone_number=row["display_phone_number"], token_ref=row["token_ref"], timezone=row["timezone"],
                opening_hours=row["opening_hours"], cutoff_minutes=row["cutoff_minutes"], settings=row["settings"],
            )
            to = row["wa_id"] if step.customer_id else await _alert_address(c, step, cafe)
        if not to or cafe.status == "disconnected":
            async with db_conn() as c:
                await steps_repo.mark_failed(c, cafe_id, step_id, "no_recipient" if not to else "disconnected")
            return "failed"
        try:
            with tracing.span("wa.send", target=client.transport().__class__.__name__, step_id=step.id,
                              attempt=attempt + 1) as sp:
                try:
                    wamid = await client.transport().send(cafe, to, _meta_body(step), callback_data=step.id)
                except SendError as err:
                    sp.fail(f"{err.cls.value}:{err.code}")
                    sp.annotate(send_class=err.cls.value, error_code=err.code)
                    raise
        except SendError as err:
            async with db_conn() as c:
                if err.cls is SendClass.NOT_SENT_RETRY and attempt < len(RETRY_DELAYS):
                    await steps_repo.release_attempt(c, cafe_id, step_id, err.code)
                    tracing.degraded("send_retry_429", error_code=err.code)
                elif err.cls is SendClass.AMBIGUOUS:
                    tracing.degraded("send_ambiguous", step_id=step_id)
                    await steps_repo.mark_ambiguous(c, cafe_id, step_id, err.code)
                    await timers.schedule(cafe_id, "send_check", step_id, 60, dedupe=f"send_check:{step_id}")
                    log.warning("send_ambiguous", cafe_id=cafe_id, step_id=step_id)
                    return "ambiguous"
                else:
                    await steps_repo.mark_failed(c, cafe_id, step_id, err.code)
                    if err.cls is SendClass.TOKEN_INVALID:
                        await cafes.set_status(c, cafe_id, "disconnected")
                        log.error("whatsapp_token_invalid", cafe_id=cafe_id)
                    log.error("send_failed", cafe_id=cafe_id, step_id=step_id, error_code=err.code)
                    return "failed"
            await asyncio.sleep(RETRY_DELAYS[attempt])
            continue
        async with db_conn() as c:
            await steps_repo.mark_sent(c, cafe_id, step_id, wamid)
        return "sent"
    return "failed"


async def send(cafe_id: str, recipient: Recipient, message: Message, key: str, purpose: str, *,
               fallback_template: Template | None = None) -> str | None:
    async with tx() as c:
        step = await prepare(c, cafe_id, recipient, message, key, purpose, fallback_template=fallback_template)
    if step is None:
        return None
    if step.status != "pending" or step.attempted_at is not None:
        return step.status  # already handled by an earlier run
    return await deliver(cafe_id, step.id)


@timers.handler("send_check")
async def send_check(cafe_id: str, step_id: str, _: dict[str, object]) -> None:
    """60 s after an ambiguous send: a status webhook naming the row would have made it `sent`."""
    async with db_conn() as c:
        step = await steps_repo.get(c, cafe_id, step_id)
        if step and step.status == "ambiguous":
            await steps_repo.mark_failed(c, cafe_id, step_id, "unconfirmed")
            log.error("send_unconfirmed", cafe_id=cafe_id, step_id=step_id)
