"""Reads behind recommendations (docs/backend.md#recommendations): a customer's past dishes and the café's
popular dishes. Non-cancelled orders only; order_items joined on (order_id, cafe_id)."""

import asyncpg


async def item_history(conn: asyncpg.Connection, cafe_id: str, customer_id: str, days: int = 180,
                       limit: int = 15) -> list[tuple[str, int, int, int]]:
    """(item_id, orders it was in, total qty, days since last ordered), most ordered first (orders_customer)."""
    rows = await conn.fetch(
        """select oi.item_id::text as item_id, count(distinct o.id)::int as times, sum(oi.qty)::int as qty,
                  floor(extract(epoch from now() - max(o.created_at)) / 86400)::int as days_ago
           from orders o join order_items oi on oi.order_id = o.id and oi.cafe_id = o.cafe_id
           where o.cafe_id = $1 and o.customer_id = $2 and o.status <> 'CANCELLED'
             and o.created_at > now() - make_interval(days => $3)
           group by oi.item_id order by times desc, max(o.created_at) desc limit $4""",
        cafe_id, customer_id, days, limit,
    )
    return [(r["item_id"], r["times"], r["qty"], r["days_ago"]) for r in rows]


async def popular_items(conn: asyncpg.Connection, cafe_id: str, days: int = 30, limit: int = 20) -> list[str]:
    """Item ids by quantity sold in the window, most first."""
    rows = await conn.fetch(
        """select oi.item_id::text as item_id
           from orders o join order_items oi on oi.order_id = o.id and oi.cafe_id = o.cafe_id
           where o.cafe_id = $1 and o.status <> 'CANCELLED' and o.created_at > now() - make_interval(days => $2)
           group by oi.item_id order by sum(oi.qty) desc, oi.item_id limit $3""",
        cafe_id, days, limit,
    )
    return [r["item_id"] for r in rows]


async def last_text(conn: asyncpg.Connection, cafe_id: str, conversation_id: str) -> str | None:
    """The customer's last typed message (only to pick the reply language on a tap; never sent to a model)."""
    text = await conn.fetchval(
        """select payload -> 'text' ->> 'body' from inbound_messages
           where cafe_id = $1 and conversation_id = $2 and type = 'text' order by received_at desc limit 1""",
        cafe_id, conversation_id,
    )
    return str(text) if text is not None else None
