from dataclasses import dataclass
from typing import Any

import asyncpg


@dataclass(frozen=True)
class Cafe:
    id: str
    name: str
    status: str
    phone_number_id: str
    display_phone_number: str | None
    token_ref: str | None
    timezone: str
    opening_hours: dict[str, Any]
    cutoff_minutes: int
    settings: dict[str, Any]


_COLS = """id::text, name, status, phone_number_id, display_phone_number, token_ref, timezone, opening_hours,
           cutoff_minutes, settings"""


async def by_phone_number_id(conn: asyncpg.Connection, phone_number_id: str) -> Cafe | None:
    """Tenant lookup for webhooks: the one repository call without a cafe_id (it finds it)."""
    row = await conn.fetchrow("select " + _COLS + " from cafes where phone_number_id = $1", phone_number_id)
    return Cafe(**dict(row)) if row else None


async def get(conn: asyncpg.Connection, cafe_id: str) -> Cafe | None:
    row = await conn.fetchrow("select " + _COLS + " from cafes where id = $1", cafe_id)
    return Cafe(**dict(row)) if row else None


async def set_status(conn: asyncpg.Connection, cafe_id: str, status: str) -> None:
    await conn.execute("update cafes set status = $2 where id = $1", cafe_id, status)


async def set_paused(conn: asyncpg.Connection, cafe_id: str, paused: bool) -> Cafe | None:
    """Returns the café when the value changed; None when it was already set (no-op)."""
    row = await conn.fetchrow(
        "update cafes set settings = jsonb_set(settings, '{paused}', to_jsonb($2::boolean)) "
        "where id = $1 and coalesce((settings ->> 'paused')::boolean, false) <> $2 returning " + _COLS,
        cafe_id, paused,
    )
    return Cafe(**dict(row)) if row else None


async def health(conn: asyncpg.Connection, cafe_id: str) -> dict[str, object]:
    """Number health signals (token, quality, messaging tier): docs/backend.md#number-health."""
    return dict(await conn.fetchval("select health from cafes where id = $1", cafe_id) or {})
