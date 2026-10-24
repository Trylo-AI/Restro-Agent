from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg


@dataclass(frozen=True)
class InboundMessage:
    id: str
    wamid: str
    type: str
    payload: dict[str, Any]
    received_at: datetime


async def insert(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, wamid: str, type_: str,
                 payload: dict[str, Any], received_at: datetime) -> bool:
    """Insert-or-ignore on wamid. False = duplicate (Meta retry): no side effects follow."""
    inserted = await conn.fetchval(
        """insert into inbound_messages (cafe_id, conversation_id, wamid, type, payload, received_at)
           values ($1, $2, $3, $4, $5, $6) on conflict (wamid) do nothing returning id""",
        cafe_id, conversation_id, wamid, type_, payload, received_at,
    )
    return inserted is not None


async def unprocessed(conn: asyncpg.Connection, cafe_id: str, conversation_id: str) -> list[InboundMessage]:
    rows = await conn.fetch(
        """select id::text, wamid, type, payload, received_at from inbound_messages
           where cafe_id = $1 and conversation_id = $2 and processed_at is null
           order by received_at, created_at""",
        cafe_id, conversation_id,
    )
    return [InboundMessage(**dict(r)) for r in rows]


async def mark_processed(conn: asyncpg.Connection, cafe_id: str, ids: list[str]) -> None:
    await conn.execute(
        "update inbound_messages set processed_at = now() where cafe_id = $1 and id = any($2::uuid[]) and processed_at is null",
        cafe_id, ids,
    )


async def recent(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, limit: int = 6) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select type, payload, received_at from inbound_messages
           where cafe_id = $1 and conversation_id = $2 order by received_at desc limit $3""",
        cafe_id, conversation_id, limit,
    )
    return [dict(r) for r in reversed(rows)]
