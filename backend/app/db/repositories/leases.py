"""Conversation lease statements (logic in domain/conversation/lease.py)."""

import asyncpg


async def claim(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, token: str, seconds: int) -> bool:
    return bool(await conn.fetchval(
        """update conversations set lease_owner = $3, lease_expires_at = now() + make_interval(secs => $4)
           where id = $2 and cafe_id = $1 and (lease_owner is null or lease_expires_at < now())
           returning id""",
        cafe_id, conversation_id, token, seconds,
    ))


async def renew(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, token: str, seconds: int) -> bool:
    return bool(await conn.fetchval(
        """update conversations set lease_expires_at = now() + make_interval(secs => $4)
           where id = $2 and cafe_id = $1 and lease_owner = $3 returning id""",
        cafe_id, conversation_id, token, seconds,
    ))


async def owner_for_update(conn: asyncpg.Connection, cafe_id: str, conversation_id: str) -> str | None:
    """Locks the conversation row until the caller's transaction ends."""
    return await conn.fetchval(  # type: ignore[no-any-return]
        "select lease_owner from conversations where id = $2 and cafe_id = $1 for update", cafe_id, conversation_id)


async def release(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, token: str) -> None:
    await conn.execute(
        """update conversations set lease_owner = null, lease_expires_at = null
           where id = $2 and cafe_id = $1 and lease_owner = $3""",
        cafe_id, conversation_id, token,
    )
