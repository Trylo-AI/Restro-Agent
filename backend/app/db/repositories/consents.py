import asyncpg


async def latest_status(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> str | None:
    return await conn.fetchval(  # type: ignore[no-any-return]
        "select status from consents where cafe_id = $1 and customer_id = $2 order by created_at desc limit 1",
        cafe_id, customer_id,
    )


async def append(conn: asyncpg.Connection, cafe_id: str, customer_id: str, status: str, source: str,
                 wording_version: int | None = None) -> None:
    """Append-only: the newest row is the state."""
    await conn.execute(
        """insert into consents (cafe_id, customer_id, type, status, source, wording_version)
           values ($1, $2, 'marketing', $3, $4, $5)""",
        cafe_id, customer_id, status, source, wording_version,
    )
