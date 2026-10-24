"""One conversation, one turn at a time (CLAUDE.md invariant 4, docs/architecture.md#message-pipeline).

signal → claim lease (held → exit) → wait until pending_work is due → claim the row → read unprocessed
messages → split into turns → for each turn: handler (LLM call, no transaction open) → ONE fenced transaction:
state + extra writes + outbound step rows + processed_at → send steps (no transaction open) → after-hooks
→ row done → loop while due work remains → release.

Failures retry from the pending_work row (2 / 4 / 8 s), then dead-letter: the customer is told how to reach
a person and a human is alerted. A lost lease hands the row back; the new holder reruns it.
"""

import asyncio
import time
from datetime import UTC, datetime

from app.core import devhooks
from app.core.logging import get_logger
from app.db.repositories import conversations, inbound
from app.db.repositories import pending_work as pw
from app.db.repositories.inbound import InboundMessage
from app.db.repositories.wa_messages import Step
from app.db.tx import conn, tx
from app.domain import outbound, timers
from app.domain.conversation import lease as lease_mod
from app.domain.conversation.turn import Handler, Turn, TurnContext
from app.domain.outbound import Purpose, Recipient
from app.integrations import tasks
from app.integrations.whatsapp import messages as m

log = get_logger(__name__)

MAX_WAIT_SLICE = 2.0

_handler: Handler | None = None
_dead_letter_hooks: list = []  # type: ignore[type-arg]


def set_handler(handler: Handler) -> None:
    global _handler
    _handler = handler


def on_dead_letter(hook) -> None:  # type: ignore[no-untyped-def]
    """Extra dead-letter actions (help request + human mode, docs/backend.md#human-help); registered in S7+."""
    _dead_letter_hooks.append(hook)


def split_turns(messages: list[InboundMessage]) -> list[Turn]:
    """Each tap is its own turn; each non-text is its own turn; consecutive texts join into one turn."""
    turns: list[Turn] = []
    for msg in messages:
        is_tap = msg.type in ("interactive", "button")
        if msg.type == "text" and turns and turns[-1].kind == "text":
            turns[-1].messages.append(msg)
        else:
            turns.append(Turn(kind="tap" if is_tap else "text" if msg.type == "text" else "other", messages=[msg]))
    return turns


async def run(cafe_id: str, conversation_id: str) -> None:
    async with lease_mod.hold(cafe_id, conversation_id) as lease:
        if lease is None:
            return  # the holder re-reads pending work before it releases
        while True:
            async with conn() as c:
                work = await pw.next_pending(c, cafe_id, conversation_id)
            if work is None:
                return
            wait = (work.next_attempt_at - datetime.now(UTC)).total_seconds()
            if wait > 0:
                await asyncio.sleep(min(wait, MAX_WAIT_SLICE))  # a new text may push the due time again
                continue
            async with conn() as c:
                claimed = await pw.claim_due(c, cafe_id, work.id)
            if claimed is None:
                continue
            try:
                await _process(lease, claimed.id)
            except lease_mod.LeaseLost:
                async with tx() as c:
                    await pw.requeue(c, cafe_id, claimed.id)
                log.warning("turn_lease_lost", cafe_id=cafe_id, conversation_id=conversation_id)
                return
            except Exception as exc:
                await _failed(cafe_id, conversation_id, claimed.id, exc)
                continue
            async with conn() as c:
                await pw.mark_done(c, cafe_id, claimed.id)


async def _process(lease: lease_mod.Lease, work_id: str) -> None:
    cafe_id, conversation_id = lease.cafe_id, lease.conversation_id
    async with conn() as c:
        msgs = await inbound.unprocessed(c, cafe_id, conversation_id)
    if not msgs:
        return
    fault = await devhooks.consume_fault("turn")
    if fault == "turn_crash":
        raise RuntimeError("simulated turn crash")
    if _handler is None:
        raise RuntimeError("no turn handler registered")

    for turn in split_turns(msgs):
        started = time.perf_counter()
        async with conn() as c:
            loaded = await conversations.turn_context(c, cafe_id, conversation_id)
        if loaded is None:
            raise RuntimeError("conversation vanished")
        cafe, convo, customer = loaded
        ctx = TurnContext(cafe=cafe, conversation=convo, customer=customer)
        result = await _handler(ctx, turn)  # may call the LLM: no transaction is open here

        steps: list[Step] = []
        async with tx() as c:
            await lease_mod.fence(c, lease)
            if result.state is not None:
                await conversations.save_state(c, cafe_id, conversation_id, result.state)
            for fn in result.commit:
                await fn(c)
            for i, reply in enumerate(result.replies):
                step = await outbound.prepare(
                    c, cafe_id, Recipient(customer_id=customer.id), reply.message, f"turn:{turn.first_id}:{i}",
                    reply.purpose, fallback_template=reply.fallback_template, customer=customer,
                )
                if step is not None:
                    steps.append(step)
            await inbound.mark_processed(c, cafe_id, [msg.id for msg in turn.messages])

        for step in steps:  # no transaction open; each delivery claims its row atomically
            if step.status == "pending" and step.attempted_at is None:
                await outbound.deliver(cafe_id, step.id)
        for after in result.after:
            await after()
        await devhooks.push_event(customer.wa_id, "turn", turn_kind=turn.kind, replies=len(steps),
                                  ms=round((time.perf_counter() - started) * 1000))


async def _failed(cafe_id: str, conversation_id: str, work_id: str, exc: Exception) -> None:
    error = type(exc).__name__
    log.error("turn_failed", cafe_id=cafe_id, conversation_id=conversation_id, error_class=error)
    async with tx() as c:
        result = await pw.fail(c, cafe_id, work_id, error)
    if result.dead_lettered:
        await dead_letter(cafe_id, conversation_id, work_id)
    elif result.retry_in is not None:
        await timers.schedule(cafe_id, "work_retry", conversation_id, result.retry_in,
                              dedupe=f"work_retry:{work_id}:{result.attempts}")


async def dead_letter(cafe_id: str, conversation_id: str, work_id: str) -> None:
    """After 3 retries: the standard message + Call waiter, and an alert for the team (docs/backend.md#handoff)."""
    log.error("turn_dead_lettered", cafe_id=cafe_id, conversation_id=conversation_id, work_id=work_id)
    async with conn() as c:
        convo = await conversations.get(c, cafe_id, conversation_id)
    if convo is None:
        return
    message = m.Buttons("Sorry, something went wrong on our side. Tap below to talk to a person.",
                        [m.Button("help", "Call waiter")])
    await outbound.send(cafe_id, Recipient(customer_id=convo.customer_id), message, f"dead_letter:{work_id}",
                        Purpose.REPLY)
    for hook in _dead_letter_hooks:
        await hook(cafe_id, conversation_id)
    async with conn() as c:  # its messages won't be retried; don't leave them for the stuck sweep
        msgs = await inbound.unprocessed(c, cafe_id, conversation_id)
        await inbound.mark_processed(c, cafe_id, [x.id for x in msgs])


@timers.handler("work_retry")
async def work_retry(cafe_id: str, conversation_id: str, _: dict[str, object]) -> None:
    await tasks.signal().publish(cafe_id, conversation_id)
