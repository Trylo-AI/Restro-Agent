"""Orders change only through `transition()` (CLAUDE.md invariant 7, contracts/state-machines.md#order-status).

Optimistic locking on `version`; every change writes `order_events`; staff changes also write `audit_log`.
Staff-transition customer messages wait 5 s (`status_message`) so Undo can stop them; customer-initiated
changes reply at once. Timers re-read state and no-op if the order moved on.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from app.core.errors import Forbidden, InvalidTransition, NotFound, VersionConflict
from app.db.repositories import order_writes
from app.db.repositories import orders as repo
from app.db.repositories.orders import Order
from app.domain import audit

ALLOWED = {
    ("NEW", "NEW"), ("NEW", "ACCEPTED"), ("ACCEPTED", "READY"), ("READY", "SERVED"),
    ("NEW", "CANCELLED"), ("ACCEPTED", "CANCELLED"), ("READY", "CANCELLED"),
}
STAFF_REASONS = ("out_of_stock", "kitchen_issue", "customer_asked", "other")
UNDO_SECONDS = 5
STATUS_MESSAGE_DELAY = 5
AUTO_SERVED_AFTER = 600
ROLE_RANK = {"shift_manager": 1, "manager": 2, "owner": 3}


@dataclass(frozen=True)
class OrderActor:
    type: str                 # customer · staff · system
    id: str | None = None     # staff user id
    role: str | None = None   # staff role

    @property
    def audit_actor(self) -> audit.Actor:
        return audit.Actor(user_id=self.id, role=self.role or self.type)


CUSTOMER = OrderActor("customer")
SYSTEM = OrderActor("system")


async def transition(conn: asyncpg.Connection, cafe_id: str, order_id: str, to: str, actor: OrderActor,
                     expected_version: int, *, reason: str | None = None,
                     extra: dict[str, Any] | None = None) -> Order:
    """The only way an order's status changes. Caller provides the transaction."""
    order = await repo.get(conn, cafe_id, order_id)
    if order is None:
        raise NotFound()
    if (order.status, to) not in ALLOWED:
        raise InvalidTransition()
    updated = await order_writes.set_status(conn, cafe_id, order_id, to, expected_version, extra)
    if updated is None:
        current = await repo.get(conn, cafe_id, order_id)
        raise VersionConflict("This order changed — check it again.",
                              details={"current": current.as_dict() if current else None})
    await order_writes.insert_event(conn, cafe_id, order_id, order.status, to, actor.type, actor.id, reason)
    if actor.type == "staff":
        change: dict[str, Any] = {"order_id": order_id, "number": order.number, "from": order.status, "to": to}
        if to == "CANCELLED":
            change["reason_code"] = (extra or {}).get("cancel_reason_code")
        await audit.write(conn, cafe_id, actor.audit_actor, "order.cancel" if to == "CANCELLED" else "order.transition", change)
    return updated


async def undo(conn: asyncpg.Connection, cafe_id: str, order_id: str, actor: OrderActor) -> Order:
    """Same actor, within 5 s: back to the previous status. The queued message is never sent (status_message
    re-checks the status and version)."""
    order = await repo.get(conn, cafe_id, order_id)
    if order is None:
        raise NotFound()
    last = await order_writes.last_event(conn, cafe_id, order_id)
    if (last is None or last["actor_id"] != actor.id or last["to_status"] != order.status or last["from_status"] is None
            or last["to_status"] == "CANCELLED" or last["from_status"] == last["to_status"]
            or last["reason"] == "undo"
            or datetime.now(UTC) - last["created_at"] > timedelta(seconds=UNDO_SECONDS)):
        raise InvalidTransition("Undo is only possible for 5 seconds after your own change.")
    back = str(last["from_status"])
    reverted = await order_writes.revert_status(conn, cafe_id, order_id, back, order.status, order.version)
    if reverted is None:
        raise VersionConflict()
    await order_writes.insert_event(conn, cafe_id, order_id, order.status, back, "staff", actor.id, "undo")
    await audit.write(conn, cafe_id, actor.audit_actor, "order.undo",
                      {"order_id": order_id, "number": order.number, "from": order.status, "to": back})
    return reverted


def check_cancel_role(order: Order, role: str) -> None:
    """shift_manager cancels only while NEW; manager+ from ACCEPTED or READY."""
    need = "shift_manager" if order.status == "NEW" else "manager"
    if ROLE_RANK.get(role, 0) < ROLE_RANK[need]:
        raise Forbidden("Only a manager can cancel an order the kitchen has accepted.")


def due_at(order: Order) -> datetime | None:
    if order.accepted_at is None:
        return None
    return order.accepted_at + timedelta(minutes=order.expected_minutes + order.delay_minutes)
