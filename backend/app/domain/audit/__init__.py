"""One audit_log row per console write, in the caller's transaction (docs/data-and-api.md#endpoints, docs/backend.md#audit).

`change` holds changed fields only: never secrets, tokens, full phone numbers or message text. `FORBIDDEN_KEYS` is
checked at every depth (nested dicts and lists such as `ops[*]`); `settings.update` keeps the names of changed
fields (`fields: ["alert_phone"]`), never a value. Severity comes from `catalog.classify` at write time (decision 19)
and the subject columns (`order_id`, `customer_id`, `conversation_id`) are copied from `change` for the filters.
"""

from dataclasses import dataclass
from typing import Any

import asyncpg

from app.db.repositories import audit_log
from app.domain.audit import catalog


@dataclass(frozen=True)
class Actor:
    user_id: str | None
    role: str  # shift_manager · manager · owner · platform_admin · staff_via_ai · system
    support: bool = False


SYSTEM = Actor(user_id=None, role="system")

FORBIDDEN_KEYS = frozenset({"phone", "wa_id", "email", "text", "body", "message", "token", "secret", "password",
                            "alert_phone", "cancel_reason_text", "note", "allergy_note"})


def _forbidden(value: Any) -> str | None:
    """The first forbidden key anywhere in `value`, or None."""
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                return str(k)
            found = _forbidden(v)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for v in value:
            found = _forbidden(v)
            if found:
                return found
    return None


async def write(conn: asyncpg.Connection, cafe_id: str, actor: Actor, action: str, change: dict[str, Any]) -> None:
    key = _forbidden(change)
    if key:
        raise ValueError(f"audit change for {action} must not hold {key!r}")
    severity = catalog.classify(action, actor.role, actor.support, change, actor_id=actor.user_id).severity
    await audit_log.insert(conn, cafe_id, actor.user_id, actor.role, actor.support, action, change,
                           severity=severity, **catalog.subjects(change))
