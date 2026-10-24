"""Customer summary for the prompt and the profile panel (SQL only; never phone or wa_id to the LLM)."""

from dataclasses import dataclass, field
from typing import Any

import asyncpg


@dataclass
class Summary:
    first_name: str | None
    visits: int
    last_order_items: list[str]
    top_items: list[str]
    preferences: dict[str, list[str]] = field(default_factory=dict)  # kind → values (allergy included)


async def summary(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> Summary:
    card = await conn.fetchrow(
        "select first_name, visits, top_items from v_customer_card where cafe_id = $1 and customer_id = $2",
        cafe_id, customer_id,
    )
    last = await conn.fetch(
        """select oi.name_snapshot, oi.qty from order_items oi
           where oi.cafe_id = $1 and oi.order_id = (
             select id from orders where cafe_id = $1 and customer_id = $2 and status <> 'CANCELLED'
             order by created_at desc limit 1)""",
        cafe_id, customer_id,
    )
    prefs = await conn.fetch(
        "select kind, value from customer_preferences where cafe_id = $1 and customer_id = $2 order by kind, value",
        cafe_id, customer_id,
    )
    out: dict[str, list[str]] = {}
    for p in prefs:
        out.setdefault(p["kind"], []).append(p["value"])
    return Summary(
        first_name=(card["first_name"] or None) if card else None,
        visits=int(card["visits"]) if card else 0,
        last_order_items=[f"{r['qty']} × {r['name_snapshot']}" if r["qty"] > 1 else r["name_snapshot"] for r in last],
        top_items=list(card["top_items"] or []) if card else [],
        preferences=out,
    )


async def recent_chat(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, customer_id: str,
                      exclude_ids: list[str], limit: int = 6) -> list[tuple[str, str]]:
    """Last few text turns, both directions: ("customer" | "bot", text). Oldest first."""
    rows = await conn.fetch(
        """(select 'customer' as who, payload -> 'text' ->> 'body' as text, received_at as at from inbound_messages
            where cafe_id = $1 and conversation_id = $2 and type = 'text' and not (id = any($4::uuid[])))
           union all
           (select 'bot', coalesce(payload -> 'text' ->> 'body', payload -> 'interactive' -> 'body' ->> 'text'), created_at
            from wa_messages where cafe_id = $1 and customer_id = $3 and type in ('text', 'interactive'))
           order by at desc limit $5""",
        cafe_id, conversation_id, customer_id, exclude_ids, limit,
    )
    return [(r["who"], (r["text"] or "")[:300]) for r in reversed(rows)]


def as_dict(s: Summary) -> dict[str, Any]:
    return {"first_name": s.first_name, "visits": s.visits, "last": s.last_order_items, "top": s.top_items,
            "preferences": s.preferences}
