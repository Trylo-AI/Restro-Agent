from dataclasses import dataclass
from typing import Any

import asyncpg

DEFAULT_STATE: dict[str, Any] = {
    "step": "greeting", "awaiting": None, "cart": {"items": [], "version": 0}, "clarify_strikes": 0,
}


@dataclass
class Conversation:
    id: str
    cafe_id: str
    customer_id: str
    mode: str
    assigned_to: str | None
    table_session_id: str | None
    state: dict[str, Any]


_COLS = "id::text, cafe_id::text, customer_id::text, mode, assigned_to::text, table_session_id::text, state"


async def get_or_create(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> str:
    return str(await conn.fetchval(
        """insert into conversations (cafe_id, customer_id) values ($1, $2)
           on conflict (cafe_id, customer_id) do update set updated_at = conversations.updated_at
           returning id::text""",
        cafe_id, customer_id,
    ))


async def get(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, *, for_update: bool = False) -> Conversation | None:
    sql = "select " + _COLS + " from conversations where cafe_id = $1 and id = $2" + (" for update" if for_update else "")
    row = await conn.fetchrow(sql, cafe_id, conversation_id)
    return Conversation(**dict(row)) if row else None


async def by_customer(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> Conversation | None:
    row = await conn.fetchrow("select " + _COLS + " from conversations where cafe_id = $1 and customer_id = $2",
                              cafe_id, customer_id)
    return Conversation(**dict(row)) if row else None


async def save_state(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, state: dict[str, Any]) -> None:
    await conn.execute("update conversations set state = $3 where cafe_id = $1 and id = $2",
                       cafe_id, conversation_id, state)


async def set_mode(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, mode: str,
                   assigned_to: str | None) -> bool:
    """Returns True when something changed (set-state calls are no-ops when already set)."""
    changed = await conn.fetchval(
        """update conversations set mode = $3, assigned_to = $4
           where cafe_id = $1 and id = $2 and (mode <> $3 or assigned_to is distinct from $4::uuid)
           returning id""",
        cafe_id, conversation_id, mode, assigned_to,
    )
    return bool(changed)


async def set_table_session(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, session_id: str | None) -> None:
    await conn.execute("update conversations set table_session_id = $3 where cafe_id = $1 and id = $2",
                       cafe_id, conversation_id, session_id)
