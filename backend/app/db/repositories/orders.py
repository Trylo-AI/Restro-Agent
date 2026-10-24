from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

OPEN = ("NEW", "ACCEPTED", "READY")


@dataclass(frozen=True)
class Order:
    id: str
    cafe_id: str
    customer_id: str | None
    table_session_id: str | None
    number: int
    status: str
    flags: list[str]
    allergy_note: str | None
    expected_minutes: int
    delay_minutes: int
    total_paise: int
    source: str
    idempotency_key: str
    version: int
    cancel_reason_code: str | None
    created_at: datetime
    accepted_at: datetime | None
    ready_at: datetime | None
    served_at: datetime | None
    cancelled_at: datetime | None
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d


@dataclass(frozen=True)
class OrderLine:
    item_id: str
    name: str
    price_paise: int
    qty: int
    note: str | None


COLS = """id::text, cafe_id::text, customer_id::text, table_session_id::text, number, status, flags, allergy_note,
          expected_minutes, delay_minutes, total_paise, source, idempotency_key, version, cancel_reason_code,
          created_at, accepted_at, ready_at, served_at, cancelled_at, updated_at"""


def from_row(row: asyncpg.Record | None) -> Order | None:
    return Order(**dict(row)) if row else None


async def get(conn: asyncpg.Connection, cafe_id: str, order_id: str, *, for_update: bool = False) -> Order | None:
    return from_row(await conn.fetchrow(
        "select " + COLS + " from orders where cafe_id = $1 and id = $2" + (" for update" if for_update else ""),
        cafe_id, order_id,
    ))


async def by_key(conn: asyncpg.Connection, cafe_id: str, key: str) -> Order | None:
    return from_row(await conn.fetchrow("select " + COLS + " from orders where cafe_id = $1 and idempotency_key = $2",
                                        cafe_id, key))


async def create(conn: asyncpg.Connection, cafe_id: str, *, customer_id: str | None, table_session_id: str | None,
                 lines: list[OrderLine], expected_minutes: int, allergy_note: str | None, source: str,
                 idempotency_key: str, status: str, flags: list[str]) -> tuple[Order, bool]:
    """Order + items in the caller's transaction. Returns (order, created). A repeated key returns the first order."""
    existing = await by_key(conn, cafe_id, idempotency_key)
    if existing:
        return existing, False
    number = await conn.fetchval("select next_order_number($1)", cafe_id)
    total = sum(line.price_paise * line.qty for line in lines)
    row = await conn.fetchrow(
        "insert into orders (cafe_id, customer_id, table_session_id, number, status, flags, allergy_note, "
        "expected_minutes, total_paise, source, idempotency_key, accepted_at) "
        "values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, case when $5 = 'ACCEPTED' then now() end) "
        "on conflict (idempotency_key) do nothing returning " + COLS,
        cafe_id, customer_id, table_session_id, number, status, flags, allergy_note, expected_minutes, total, source,
        idempotency_key,
    )
    if row is None:  # lost a race on the key: the other transaction's order stands
        existing = await by_key(conn, cafe_id, idempotency_key)
        assert existing is not None
        return existing, False
    order = Order(**dict(row))
    await conn.executemany(
        """insert into order_items (cafe_id, order_id, item_id, name_snapshot, price_snapshot_paise, qty, note)
           values ($1, $2, $3, $4, $5, $6, $7)""",
        [(cafe_id, order.id, ln.item_id, ln.name, ln.price_paise, ln.qty, ln.note) for ln in lines],
    )
    return order, True


async def replace_items(conn: asyncpg.Connection, cafe_id: str, order_id: str, lines: list[OrderLine]) -> int:
    await conn.execute("delete from order_items where cafe_id = $1 and order_id = $2", cafe_id, order_id)
    await conn.executemany(
        """insert into order_items (cafe_id, order_id, item_id, name_snapshot, price_snapshot_paise, qty, note)
           values ($1, $2, $3, $4, $5, $6, $7)""",
        [(cafe_id, order_id, ln.item_id, ln.name, ln.price_paise, ln.qty, ln.note) for ln in lines],
    )
    return sum(ln.price_paise * ln.qty for ln in lines)


async def lines(conn: asyncpg.Connection, cafe_id: str, order_id: str) -> list[OrderLine]:
    rows = await conn.fetch(
        """select item_id::text, name_snapshot as name, price_snapshot_paise as price_paise, qty, note
           from order_items where cafe_id = $1 and order_id = $2 order by created_at, name_snapshot""",
        cafe_id, order_id,
    )
    return [OrderLine(**dict(r)) for r in rows]


async def open_for_customer(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> list[Order]:
    rows = await conn.fetch(
        "select " + COLS + " from orders where cafe_id = $1 and customer_id = $2 and status = any($3::text[]) order by created_at",
        cafe_id, customer_id, list(OPEN),
    )
    return [Order(**dict(r)) for r in rows]


async def last_served(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> Order | None:
    return from_row(await conn.fetchrow(
        "select " + COLS + " from orders where cafe_id = $1 and customer_id = $2 and status = 'SERVED' "
        "order by created_at desc limit 1",
        cafe_id, customer_id,
    ))


async def add_flag(conn: asyncpg.Connection, cafe_id: str, order_id: str, flag: str) -> bool:
    """Flags are attributes set by timers and toggles; they don't bump `version` (staff keep their expected_version)."""
    return bool(await conn.fetchval(
        """update orders set flags = array_append(flags, $3)
           where cafe_id = $1 and id = $2 and not ($3 = any(flags)) returning id""",
        cafe_id, order_id, flag,
    ))


async def table_label(conn: asyncpg.Connection, cafe_id: str, table_session_id: str | None) -> str | None:
    if not table_session_id:
        return None
    return await conn.fetchval(  # type: ignore[no-any-return]
        """select t.label from table_sessions s join tables t on t.id = s.table_id
           where s.cafe_id = $1 and s.id = $2""", cafe_id, table_session_id)
