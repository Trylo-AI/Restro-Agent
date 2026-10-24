import re
import unicodedata

import asyncpg


def normalise_qr(text: str) -> str:
    """'Hi, I'm at Table 5!' and 'hi im at table 5' match the same table."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


async def by_qr_text(conn: asyncpg.Connection, cafe_id: str, text: str) -> tuple[str, str] | None:
    """(table_id, label) whose qr_text matches the message after normalising."""
    wanted = normalise_qr(text)
    if not wanted or len(wanted) > 60:
        return None
    rows = await conn.fetch("select id::text, label, qr_text from tables where cafe_id = $1", cafe_id)
    for r in rows:
        if normalise_qr(r["qr_text"]) == wanted:
            return r["id"], r["label"]
    return None


async def get(conn: asyncpg.Connection, cafe_id: str, table_id: str) -> tuple[str, str] | None:
    row = await conn.fetchrow("select id::text, label from tables where cafe_id = $1 and id = $2", cafe_id, table_id)
    return (row["id"], row["label"]) if row else None


async def all_tables(conn: asyncpg.Connection, cafe_id: str) -> list[tuple[str, str]]:
    rows = await conn.fetch("select id::text, label from tables where cafe_id = $1 order by length(label), label", cafe_id)
    return [(r["id"], r["label"]) for r in rows]


async def open_session(conn: asyncpg.Connection, cafe_id: str, table_id: str) -> str:
    """Join the table's open session or open one (one open session per table; races resolve to the same row)."""
    existing = await conn.fetchval(
        "select id::text from table_sessions where cafe_id = $1 and table_id = $2 and closed_at is null", cafe_id, table_id)
    if existing:
        return str(existing)
    created = await conn.fetchval(
        """insert into table_sessions (cafe_id, table_id) values ($1, $2)
           on conflict (table_id) where closed_at is null do nothing returning id::text""",
        cafe_id, table_id,
    )
    if created:
        return str(created)
    return str(await conn.fetchval(
        "select id::text from table_sessions where cafe_id = $1 and table_id = $2 and closed_at is null", cafe_id, table_id))


async def close_session(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> bool:
    return bool(await conn.fetchval(
        "update table_sessions set closed_at = now() where cafe_id = $1 and id = $2 and closed_at is null returning id",
        cafe_id, table_session_id))


async def session_exists(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> bool:
    return bool(await conn.fetchval("select 1 from table_sessions where cafe_id = $1 and id = $2", cafe_id, table_session_id))


async def bill_orders(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> list[tuple[str, int, int]]:
    """(order_id, number, total_paise) for every non-cancelled order of the session."""
    rows = await conn.fetch(
        """select id::text, number, total_paise from orders
           where cafe_id = $1 and table_session_id = $2 and status <> 'CANCELLED' order by number""",
        cafe_id, table_session_id,
    )
    return [(r["id"], r["number"], r["total_paise"]) for r in rows]
