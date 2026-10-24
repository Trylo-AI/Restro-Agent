from typing import Any

import asyncpg


async def insert(conn: asyncpg.Connection, payload: dict[str, Any], signature_valid: bool) -> str:
    return str(await conn.fetchval(
        "insert into webhook_events (signature_valid, payload) values ($1, $2) returning id::text",
        signature_valid, payload,
    ))


async def mark_processed(conn: asyncpg.Connection, event_id: str) -> None:
    await conn.execute("update webhook_events set processed_at = now() where id = $1", event_id)
