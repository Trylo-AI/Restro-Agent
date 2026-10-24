"""One audit_log row per console write, in the caller's transaction (docs/data-and-api.md#endpoints).

`change` holds changed fields only: never secrets, tokens, full phone numbers or message text.
"""

from dataclasses import dataclass
from typing import Any

import asyncpg

from app.db.repositories import audit_log


@dataclass(frozen=True)
class Actor:
    user_id: str | None
    role: str  # shift_manager · manager · owner · platform_admin · system
    support: bool = False


SYSTEM = Actor(user_id=None, role="system")


async def write(conn: asyncpg.Connection, cafe_id: str, actor: Actor, action: str, change: dict[str, Any]) -> None:
    await audit_log.insert(conn, cafe_id, actor.user_id, actor.role, actor.support, action, change)
