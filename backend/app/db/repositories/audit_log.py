from typing import Any

import asyncpg


async def insert(conn: asyncpg.Connection, cafe_id: str, actor_id: str | None, actor_role: str, support: bool,
                 action: str, change: dict[str, Any]) -> None:
    await conn.execute(
        """insert into audit_log (actor_id, actor_role, support, cafe_id, action, change)
           values ($1, $2, $3, $4, $5, $6)""",
        actor_id, actor_role, support, cafe_id, action, change,
    )
