"""Reads for the owner's order history (GET /orders/{id}/history). Every query filters by cafe_id.

Staff status changes come from audit_log (domain/audit/viewer.py); here only the header, the customer and system
`order_events`, and the order's escalations.
"""

from typing import Any

import asyncpg


async def order_header(conn: asyncpg.Connection, cafe_id: str, order_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """select o.id::text, o.number, o.status, o.source, o.created_at, o.total_paise, o.cancel_reason_code,
                  o.cancel_reason_text, o.table_session_id::text, t.label as table_label, o.customer_id::text,
                  cu.name as customer_name, cu.wa_id like '000%' as customer_deleted,
                  coalesce(m.display_name,
                           (select a.actor_name from audit_log a where a.cafe_id = $1 and a.order_id = o.id
                              and a.action = 'order.cancel' order by a.at desc limit 1)) as cancelled_by_name
           from orders o
           left join table_sessions ts on ts.cafe_id = o.cafe_id and ts.id = o.table_session_id
           left join tables t on t.cafe_id = o.cafe_id and t.id = ts.table_id
           left join customers cu on cu.cafe_id = o.cafe_id and cu.id = o.customer_id
           left join memberships m on m.cafe_id = o.cafe_id and m.user_id = o.cancelled_by
           where o.cafe_id = $1 and o.id = $2""",
        cafe_id, order_id)
    return dict(row) if row else None


async def events(conn: asyncpg.Connection, cafe_id: str, order_id: str) -> list[asyncpg.Record]:
    """Customer and automatic status changes, oldest first (staff ones are in audit_log)."""
    return list(await conn.fetch(
        """select id::text, created_at, from_status, to_status, actor_type, reason from order_events
           where cafe_id = $1 and order_id = $2 and actor_type in ('customer', 'system')
           order by created_at, id""",
        cafe_id, order_id))


async def escalations(conn: asyncpg.Connection, cafe_id: str, order_id: str) -> list[asyncpg.Record]:
    return list(await conn.fetch(
        """select id::text, sent_at, level, target from escalations
           where cafe_id = $1 and subject_type = 'order' and subject_id = $2 order by sent_at, level""",
        cafe_id, order_id))
