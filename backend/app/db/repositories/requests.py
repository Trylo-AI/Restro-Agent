from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass(frozen=True)
class Request:
    id: str
    cafe_id: str
    conversation_id: str | None
    table_session_id: str | None
    order_id: str | None
    kind: str
    status: str
    reason_tag: str | None
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime


COLS = """id::text, cafe_id::text, conversation_id::text, table_session_id::text, order_id::text, kind, status,
          reason_tag, acknowledged_by::text, acknowledged_at, resolved_at, created_at"""


async def insert_if_none_open(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, kind: str,
                              reason_tag: str | None, table_session_id: str | None, order_id: str | None) -> tuple[str, bool]:
    """One unresolved request per (conversation, kind). Returns (request_id, created)."""
    created = await conn.fetchval(
        """insert into requests (cafe_id, conversation_id, kind, reason_tag, table_session_id, order_id)
           values ($1, $2, $3, $4, $5, $6)
           on conflict (conversation_id, kind) where status <> 'resolved' do nothing returning id::text""",
        cafe_id, conversation_id, kind, reason_tag, table_session_id, order_id,
    )
    if created:
        return str(created), True
    existing = await conn.fetchval(
        "select id::text from requests where cafe_id = $1 and conversation_id = $2 and kind = $3 and status <> 'resolved'",
        cafe_id, conversation_id, kind,
    )
    return str(existing), False


async def get(conn: asyncpg.Connection, cafe_id: str, request_id: str) -> Request | None:
    row = await conn.fetchrow("select " + COLS + " from requests where cafe_id = $1 and id = $2", cafe_id, request_id)
    return Request(**dict(row)) if row else None


async def acknowledge(conn: asyncpg.Connection, cafe_id: str, request_id: str, user_id: str) -> Request | None:
    """open → acknowledged. None when nothing changed (already acknowledged or resolved)."""
    row = await conn.fetchrow(
        "update requests set status = 'acknowledged', acknowledged_by = $3, acknowledged_at = now() "
        "where cafe_id = $1 and id = $2 and status = 'open' returning " + COLS,
        cafe_id, request_id, user_id,
    )
    return Request(**dict(row)) if row else None


async def resolve(conn: asyncpg.Connection, cafe_id: str, request_id: str) -> Request | None:
    row = await conn.fetchrow(
        "update requests set status = 'resolved', resolved_at = now() "
        "where cafe_id = $1 and id = $2 and status <> 'resolved' returning " + COLS,
        cafe_id, request_id,
    )
    return Request(**dict(row)) if row else None


async def resolve_bill_for_session(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> int:
    rows = await conn.fetch(
        """update requests set status = 'resolved', resolved_at = now()
           where cafe_id = $1 and table_session_id = $2 and kind = 'bill' and status <> 'resolved' returning id""",
        cafe_id, table_session_id,
    )
    return len(rows)


async def open_for_conversation(conn: asyncpg.Connection, cafe_id: str, conversation_id: str,
                                kinds: tuple[str, ...]) -> list[Request]:
    rows = await conn.fetch(
        "select " + COLS + " from requests where cafe_id = $1 and conversation_id = $2 and status <> 'resolved' "
        "and kind = any($3::text[])",
        cafe_id, conversation_id, list(kinds),
    )
    return [Request(**dict(r)) for r in rows]
