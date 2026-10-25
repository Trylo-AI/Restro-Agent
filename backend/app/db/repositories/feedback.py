import asyncpg

ASK_HOURS = 24  # a typed "2" counts as a rating while the ask is this recent


async def exists(conn: asyncpg.Connection, cafe_id: str, order_id: str) -> bool:
    return bool(await conn.fetchval("select exists (select 1 from feedback where cafe_id = $1 and order_id = $2)",
                                    cafe_id, order_id))


async def insert(conn: asyncpg.Connection, cafe_id: str, order_id: str, rating: int, text: str | None) -> bool:
    """One rating per order; False when it was already rated."""
    created = await conn.fetchval(
        """insert into feedback (cafe_id, order_id, rating, text) values ($1, $2, $3, $4)
           on conflict (order_id) do nothing returning id""", cafe_id, order_id, rating, text)
    return created is not None


async def set_text(conn: asyncpg.Connection, cafe_id: str, order_id: str, text: str) -> int | None:
    """Saves the follow-up text once; returns the rating, or None when there's no rating without text."""
    return await conn.fetchval(  # type: ignore[no-any-return]
        "update feedback set text = $3 where cafe_id = $1 and order_id = $2 and text is null returning rating",
        cafe_id, order_id, text)


async def open_ask(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> str | None:
    """The newest served order of this customer that was asked for a rating recently and not rated yet."""
    return await conn.fetchval(  # type: ignore[no-any-return]
        f"""select o.id::text from orders o
            join wa_messages w on w.cafe_id = o.cafe_id and w.idempotency_key = 'feedback:' || o.id::text
            where o.cafe_id = $1 and o.customer_id = $2 and o.status = 'SERVED'
              and w.created_at > now() - interval '{ASK_HOURS} hours'
              and not exists (select 1 from feedback f where f.order_id = o.id)
            order by w.created_at desc limit 1""",  # noqa: S608  (constant)
        cafe_id, customer_id)
