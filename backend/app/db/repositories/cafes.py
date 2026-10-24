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
