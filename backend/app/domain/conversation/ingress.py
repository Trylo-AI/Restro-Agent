"""Webhook ingest (docs/architecture.md#message-pipeline): store, queue, signal. Nothing slow here.

Per message, one transaction: customer upsert (24-hour window clock) + conversation + inbound_messages
(unique wamid; a duplicate stops here) + pending_work (text debounces 1 s, taps due now). After commit: publish
the wake-up signal and enqueue wa-ack. If the publish fails the work is still in pending_work and
stuck_inbound_check signals it within a minute.
"""

import uuid

from app.core.logging import get_logger
from app.db.repositories import cafes, conversations, customers, inbound, pending_work
from app.db.repositories import wa_messages as steps_repo
from app.db.tx import tx
from app.integrations import tasks
from app.integrations.whatsapp import pricing
from app.integrations.whatsapp.parser import InboundEvent, StatusEvent
from app.ops import tracing

log = get_logger(__name__)


async def ingest_message(ev: InboundEvent) -> bool:
    """Returns True when the message was new and queued."""
    with tracing.span("webhook.store", type=ev.type):
        async with tx() as c:
            cafe = await cafes.by_phone_number_id(c, ev.phone_number_id)
            if cafe is None:
                log.warning("webhook_unknown_phone_number_id")
                return False
            tracing.tenant(cafe.id)
            customer_id = await customers.upsert_on_inbound(c, cafe.id, ev.wa_id, ev.timestamp)
            conversation_id = await conversations.get_or_create(c, cafe.id, customer_id)
            if not await inbound.insert(c, cafe.id, conversation_id, ev.wamid, ev.type, ev.raw, ev.timestamp):
                log.info("inbound_duplicate", cafe_id=cafe.id, wamid=ev.wamid)
                tracing.count(duplicate=1)
                return False
            await pending_work.upsert_turn(c, cafe.id, conversation_id, debounce=not ev.is_tap and ev.type == "text")
    tracing.subject("inbound", ev.wamid)  # the unit is the message; the conversation is in the span
    tracing.annotate(conversation_id=conversation_id)
    log.info("inbound_stored", cafe_id=cafe.id, conversation_id=conversation_id, wamid=ev.wamid, type=ev.type)
    with tracing.span("webhook.signal"):
        try:
            await tasks.signal().publish(cafe.id, conversation_id)
            await tasks.tasks().enqueue("wa-ack", {"cafe_id": cafe.id, "conversation_id": conversation_id,
                                                   "wamid": ev.wamid}, name=f"ack:{ev.wamid}")
        except Exception as exc:  # the work is durable; the sweep re-signals
            log.error("signal_failed", cafe_id=cafe.id, conversation_id=conversation_id, error_class=type(exc).__name__)
            tracing.degraded("publish_failed_sweep", error_class=type(exc).__name__)
    return True


def _uuid_or_none(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(value)) if value else None
    except ValueError:
        return None


async def ingest_status(ev: StatusEvent) -> bool:
    async with tx() as c:
        cafe = await cafes.by_phone_number_id(c, ev.phone_number_id)
        if cafe is None:
            return False
        tracing.tenant(cafe.id)
        cost, currency, version = pricing.estimate(ev.pricing_category, ev.billable)
        return await steps_repo.apply_status(
            c, cafe.id, wamid=ev.wamid, step_id=_uuid_or_none(ev.callback_data), status=ev.status,
            error_code=ev.error_code, pricing_category=ev.pricing_category, billable=ev.billable,
            cost=cost, currency=currency, pricing_version=version,
        )
