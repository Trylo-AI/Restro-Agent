"""customer_preferences and customer_notes (docs/backend.md#customer-profile-and-staff-comments)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg


@dataclass(frozen=True)
class Note:
    id: str
    cafe_id: str
    customer_id: str
    author_id: str
    text: str
    ai_status: str
    ai_ops: list[dict[str, Any]]
    ai_summary: str | None
    undone_at: datetime | None
    created_at: datetime


NOTE_COLS = """id::text, cafe_id::text, customer_id::text, author_id::text, text, ai_status, ai_ops, ai_summary, undone_at,
               created_at"""


async def all_for(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> list[tuple[str, str]]:
    rows = await conn.fetch(
        "select kind, value from customer_preferences where cafe_id = $1 and customer_id = $2 order by kind, value",
        cafe_id, customer_id,
    )
    return [(r["kind"], r["value"]) for r in rows]


async def values_of(conn: asyncpg.Connection, cafe_id: str, customer_id: str, kind: str) -> list[str]:
    rows = await conn.fetch(
        "select value from customer_preferences where cafe_id = $1 and customer_id = $2 and kind = $3 order by value",
        cafe_id, customer_id, kind,
    )
    return [r["value"] for r in rows]


async def has(conn: asyncpg.Connection, cafe_id: str, customer_id: str, kind: str, value: str) -> bool:
    return bool(await conn.fetchval(
        "select 1 from customer_preferences where cafe_id = $1 and customer_id = $2 and kind = $3 and value = $4",
        cafe_id, customer_id, kind, value,
    ))


async def insert(conn: asyncpg.Connection, cafe_id: str, customer_id: str, kind: str, value: str, source: str,
                 note_id: str | None) -> bool:
    return bool(await conn.fetchval(
        """insert into customer_preferences (cafe_id, customer_id, kind, value, source, note_id)
           values ($1, $2, $3, $4, $5, $6) on conflict (customer_id, kind, value) do nothing returning id""",
        cafe_id, customer_id, kind, value, source, note_id,
    ))


async def delete_value(conn: asyncpg.Connection, cafe_id: str, customer_id: str, kind: str, value: str) -> bool:
    return bool(await conn.fetchval(
        "delete from customer_preferences where cafe_id = $1 and customer_id = $2 and kind = $3 and value = $4 returning id",
        cafe_id, customer_id, kind, value,
    ))


async def delete_kind(conn: asyncpg.Connection, cafe_id: str, customer_id: str, kind: str) -> list[str]:
    rows = await conn.fetch(
        "delete from customer_preferences where cafe_id = $1 and customer_id = $2 and kind = $3 returning value",
        cafe_id, customer_id, kind,
    )
    return [r["value"] for r in rows]


async def insert_note(conn: asyncpg.Connection, cafe_id: str, customer_id: str, author_id: str, text: str) -> Note:
    row = await conn.fetchrow(
        "insert into customer_notes (cafe_id, customer_id, author_id, text) values ($1, $2, $3, $4) returning " + NOTE_COLS,
        cafe_id, customer_id, author_id, text,
    )
    assert row is not None
    return Note(**dict(row))


async def get_note(conn: asyncpg.Connection, cafe_id: str, note_id: str, *, for_update: bool = False) -> Note | None:
    row = await conn.fetchrow(
        "select " + NOTE_COLS + " from customer_notes where cafe_id = $1 and id = $2" + (" for update" if for_update else ""),
        cafe_id, note_id,
    )
    return Note(**dict(row)) if row else None


async def set_note_result(conn: asyncpg.Connection, cafe_id: str, note_id: str, status: str, ops: list[dict[str, Any]],
                          summary: str | None) -> Note | None:
    row = await conn.fetchrow(
        "update customer_notes set ai_status = $3, ai_ops = $4, ai_summary = $5 "
        "where cafe_id = $1 and id = $2 and ai_status = 'pending' returning " + NOTE_COLS,
        cafe_id, note_id, status, ops, summary,
    )
    return Note(**dict(row)) if row else None


async def mark_undone(conn: asyncpg.Connection, cafe_id: str, note_id: str, user_id: str) -> Note | None:
    row = await conn.fetchrow(
        "update customer_notes set undone_at = now(), undone_by = $3 "
        "where cafe_id = $1 and id = $2 and undone_at is null returning " + NOTE_COLS,
        cafe_id, note_id, user_id,
    )
    return Note(**dict(row)) if row else None


async def later_applied_notes(conn: asyncpg.Connection, cafe_id: str, customer_id: str, after: datetime) -> list[Note]:
    rows = await conn.fetch(
        "select " + NOTE_COLS + " from customer_notes where cafe_id = $1 and customer_id = $2 and created_at > $3 "
        "and ai_status = 'applied' and undone_at is null",
        cafe_id, customer_id, after,
    )
    return [Note(**dict(r)) for r in rows]
