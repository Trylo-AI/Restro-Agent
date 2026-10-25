"""Staff side of human help (docs/backend.md#human-help, docs/data-and-api.md#endpoints).

Set-state calls are no-ops when the value is already set: 200, no second audit row, no message.
Escalation: a help or allergy request still open at 2 min → managers (in-app + new_order_alert) and the customer
hears the team is busy. Human mode hands back after 15 min with no unanswered customer message.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.auth import Principal
from app.core.errors import Forbidden, InvalidTransition, NotFound
from app.core.logging import get_logger
from app.db.repositories import conversations, memberships, order_writes
from app.db.repositories import requests as requests_repo
from app.db.repositories import tables as tables_repo
from app.db.repositories.requests import Request
from app.db.tx import conn, tx
from app.domain import audit, outbound, timers
from app.domain.conversation import pipeline
from app.domain.handoff import handoff
from app.domain.outbound import Purpose, Recipient
from app.integrations.whatsapp import messages as m

log = get_logger(__name__)

HELP_ESCALATION_AFTER = 120
IDLE_HANDBACK_AFTER = 15 * 60
ROLE_RANK = {"shift_manager": 1, "manager": 2, "owner": 3}


def _actor(p: Principal) -> audit.Actor:
    return audit.Actor(p.user_id, p.role)


async def schedule_help_escalation(cafe_id: str, request_id: str) -> None:
    await timers.schedule(cafe_id, "help_escalation", request_id, HELP_ESCALATION_AFTER, dedupe=f"help_esc:{request_id}")


async def arm_idle_handback(cafe_id: str, conversation_id: str) -> None:
    """Re-armed on every message in human mode; the latest timer is the one that matters (it re-checks)."""
    await timers.schedule(cafe_id, "idle_handback", conversation_id, IDLE_HANDBACK_AFTER,
                          dedupe=f"idle:{conversation_id}:{secrets.token_hex(4)}")


# ── requests ──────────────────────────────────────────────────────────────

async def _own_request(c: Any, p: Principal, request_id: str) -> Request:
    req = await requests_repo.get(c, p.cafe_id, request_id)
    if req is None:
        raise NotFound()
    if req.kind == "low_rating" and ROLE_RANK[p.role] < ROLE_RANK["manager"]:
        raise Forbidden()
    return req


async def ack(p: Principal, request_id: str) -> Request:
    async with tx() as c:
        req = await _own_request(c, p, request_id)
        changed = await requests_repo.acknowledge(c, p.cafe_id, request_id, p.user_id)
        if changed:
            await audit.write(c, p.cafe_id, _actor(p), "request.ack", {"request_id": request_id, "kind": req.kind})
            return changed
    return req


async def resolve(p: Principal, request_id: str) -> Request:
    async with tx() as c:
        req = await _own_request(c, p, request_id)
        changed = await requests_repo.resolve(c, p.cafe_id, request_id)
        if not changed:
            return req
        await audit.write(c, p.cafe_id, _actor(p), "request.resolve", {"request_id": request_id, "kind": req.kind})
        if req.kind in ("help", "allergy") and req.conversation_id:
            still_open = await requests_repo.open_for_conversation(c, p.cafe_id, req.conversation_id, ("help", "allergy"))
            if not still_open:  # resolving the conversation's help hands the chat back to the bot
                await conversations.set_mode(c, p.cafe_id, req.conversation_id, "bot", None)
        return changed


# ── chats ─────────────────────────────────────────────────────────────────

async def _conversation(c: Any, p: Principal, conversation_id: str) -> conversations.Conversation:
    convo = await conversations.get(c, p.cafe_id, conversation_id)
    if convo is None:
        raise NotFound()
    return convo


async def takeover(p: Principal, conversation_id: str) -> conversations.Conversation:
    async with tx() as c:
        convo = await _conversation(c, p, conversation_id)
        if await conversations.set_mode(c, p.cafe_id, conversation_id, "human", p.user_id):
            await audit.write(c, p.cafe_id, _actor(p), "chat.takeover", {"conversation_id": conversation_id})
            convo.mode, convo.assigned_to = "human", p.user_id
    await arm_idle_handback(p.cafe_id, conversation_id)
    return convo


async def reply(p: Principal, conversation_id: str, text: str) -> conversations.Conversation:
    """Staff reply through the send layer; takes over from bot mode. Window closed → 409 window_closed."""
    async with conn() as c:
        convo = await _conversation(c, p, conversation_id)
    if convo.mode != "human" or convo.assigned_to != p.user_id:
        convo = await takeover(p, conversation_id)
    key = f"staff:{conversation_id}:{secrets.token_hex(8)}"
    await outbound.send(p.cafe_id, Recipient(customer_id=convo.customer_id), m.Text(text), key, Purpose.STAFF_REPLY)
    async with tx() as c:  # the reply text itself is in wa_messages, never in the audit log
        await audit.write(c, p.cafe_id, _actor(p), "chat.reply", {"conversation_id": conversation_id, "chars": len(text)})
    await arm_idle_handback(p.cafe_id, conversation_id)
    return convo


async def handback(p: Principal, conversation_id: str) -> conversations.Conversation:
    async with tx() as c:
        convo = await _conversation(c, p, conversation_id)
        if await conversations.set_mode(c, p.cafe_id, conversation_id, "bot", None):
            await audit.write(c, p.cafe_id, _actor(p), "chat.handback", {"conversation_id": conversation_id})
            convo.mode, convo.assigned_to = "bot", None
    return convo


async def open_for_table(p: Principal, table_session_id: str) -> conversations.Conversation:
    """Message a table: the session's chat in human mode, assigned to the caller."""
    async with conn() as c:
        if not await tables_repo.session_exists(c, p.cafe_id, table_session_id):
            raise NotFound()
        convo = await conversations.latest_in_session(c, p.cafe_id, table_session_id)
    if convo is None:
        raise InvalidTransition("Nobody at this table has messaged us on WhatsApp yet.")
    return await takeover(p, convo.id)


# ── bill ──────────────────────────────────────────────────────────────────

async def deliver_bill(p: Principal, table_session_id: str) -> dict[str, Any]:
    from app.domain import bills  # the one place a bill is computed (orders, code, discount, total)

    async with tx() as c:
        bill = await bills.compute(c, p.cafe_id, table_session_id)
        resolved = await requests_repo.resolve_bill_for_session(c, p.cafe_id, table_session_id)
        closed = await tables_repo.close_session(c, p.cafe_id, table_session_id)
        if closed or resolved:
            await audit.write(c, p.cafe_id, _actor(p), "bill.deliver",
                              {"table_session_id": table_session_id, "total_paise": bill["total_paise"]})
    return {**bill, "closed_at": bill["closed_at"] or datetime.now(UTC).isoformat()}


# ── notifications ─────────────────────────────────────────────────────────

async def ack_notification(p: Principal, notification_id: str | None) -> int:
    async with tx() as c:
        if notification_id and not await order_writes.notification_exists(c, p.cafe_id, p.user_id, notification_id):
            raise NotFound()
        changed = await order_writes.mark_notification_read(c, p.cafe_id, p.user_id, notification_id)
        if changed:
            await audit.write(c, p.cafe_id, _actor(p), "notification.ack", {"count": changed})
    return changed


# ── timers ────────────────────────────────────────────────────────────────

@timers.handler("help_escalation")
async def help_escalation(cafe_id: str, request_id: str, _: dict[str, object]) -> None:
    async with tx() as c:
        req = await requests_repo.get(c, cafe_id, request_id)
        if req is None or req.status != "open" or req.kind not in ("help", "allergy"):
            return
        if not await order_writes.insert_escalation(c, cafe_id, "request", request_id, 1, "managers"):
            return
        refs = ["cafe_alert"] + await memberships.alert_refs(c, cafe_id, ("manager",))
        convo = await conversations.get(c, cafe_id, req.conversation_id) if req.conversation_id else None
        table = None
        if req.table_session_id:
            from app.db.repositories import orders as orders_repo

            table = await orders_repo.table_label(c, cafe_id, req.table_session_id)
    log.warning("request_escalated", cafe_id=cafe_id, request_id=request_id, kind=req.kind)
    for ref in refs:
        await outbound.send(cafe_id, Recipient(to_ref=ref), m.Template("new_order_alert", [req.kind, table or "?"]),
                            f"alert:request:{request_id}:{ref}", Purpose.ALERT)
    if convo:
        await outbound.send(cafe_id, Recipient(customer_id=convo.customer_id),
                            m.Text("Sorry, the team is busy — someone is coming to you."),
                            f"request:{request_id}:busy", Purpose.REPLY)


@timers.handler("idle_handback")
async def idle_handback(cafe_id: str, conversation_id: str, _: dict[str, object]) -> None:
    """15 min with no unanswered customer message → back to the bot."""
    async with tx() as c:
        convo = await conversations.get(c, cafe_id, conversation_id, for_update=True)
        if convo is None or convo.mode != "human":
            return
        last_in, last_out = await conversations.last_activity(c, cafe_id, conversation_id, convo.customer_id)
        now = datetime.now(UTC)
        latest = max(x for x in (last_in, last_out) if x is not None) if (last_in or last_out) else None
        unanswered = last_in is not None and (last_out is None or last_in > last_out)
        if unanswered or (latest and now - latest < timedelta(seconds=IDLE_HANDBACK_AFTER - 5)):
            return
        await conversations.set_mode(c, cafe_id, conversation_id, "bot", None)
    log.info("idle_handback", cafe_id=cafe_id, conversation_id=conversation_id)


# ── dead letters: help request + human mode (open-decision default) ──────

async def _on_dead_letter(cafe_id: str, conversation_id: str) -> None:
    async with tx() as c:
        convo = await conversations.get(c, cafe_id, conversation_id)
        opened = await handoff(c, cafe_id, conversation_id, "system_error",
                               table_session_id=convo.table_session_id if convo else None)
    if opened.created:
        await schedule_help_escalation(cafe_id, opened.request_id)


pipeline.on_dead_letter(_on_dead_letter)
