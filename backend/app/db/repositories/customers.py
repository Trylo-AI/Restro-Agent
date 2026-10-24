from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass(frozen=True)
class Customer:
    id: str
    cafe_id: str
    wa_id: str
    name: str | None
    last_inbound_at: datetime | None
    last_visit_at: datetime | None


_COLS = "id::text, cafe_id::text, wa_id, name, last_inbound_at, last_visit_at"


async def upsert_on_inbound(conn: asyncpg.Connection, cafe_id: str, wa_id: str, at: datetime) -> str:
    """Every inbound: create or touch the customer; last_inbound_at is the 24-hour window clock."""
    return str(await conn.fetchval(
        """insert into customers (cafe_id, wa_id, first_seen_at, last_inbound_at) values ($1, $2, $3, $3)
           on conflict (cafe_id, wa_id) do update
             set last_inbound_at = greatest(coalesce(customers.last_inbound_at, excluded.last_inbound_at),
                                            excluded.last_inbound_at)
           returning id::text""",
        cafe_id, wa_id, at,
    ))


async def get(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> Customer | None:
    row = await conn.fetchrow("select " + _COLS + " from customers where cafe_id = $1 and id = $2", cafe_id, customer_id)
    return Customer(**dict(row)) if row else None


async def by_wa_id(conn: asyncpg.Connection, cafe_id: str, wa_id: str) -> Customer | None:
    row = await conn.fetchrow("select " + _COLS + " from customers where cafe_id = $1 and wa_id = $2", cafe_id, wa_id)
    return Customer(**dict(row)) if row else None


async def set_name_if_missing(conn: asyncpg.Connection, cafe_id: str, customer_id: str, name: str) -> None:
    await conn.execute("update customers set name = $3 where cafe_id = $1 and id = $2 and name is null",
                       cafe_id, customer_id, name)


async def touch_visit(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> None:
    await conn.execute("update customers set last_visit_at = now() where cafe_id = $1 and id = $2", cafe_id, customer_id)
