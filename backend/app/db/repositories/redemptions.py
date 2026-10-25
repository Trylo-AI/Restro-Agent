from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass(frozen=True)
class CodeInfo:
    recipient_id: str
    campaign_id: str
    status: str
    valid_until: datetime | None
    discount_percent: int | None
    campaign_status: str


@dataclass(frozen=True)
class Redemption:
    id: str
    code: str
    table_session_id: str | None
    order_number: int | None
    discount_paise: int
    redeemed_at: datetime


async def code(conn: asyncpg.Connection, cafe_id: str, code_text: str) -> CodeInfo | None:
    """Only this café's codes: another café's code is simply not found."""
    row = await conn.fetchrow(
        """select r.id::text as recipient_id, r.campaign_id::text, r.status, c.valid_until, c.discount_percent,
                  c.status as campaign_status
           from campaign_recipients r join campaigns c on c.id = r.campaign_id
           where r.cafe_id = $1 and upper(r.code) = upper($2)""", cafe_id, code_text)
    return CodeInfo(**dict(row)) if row else None


async def by_recipient(conn: asyncpg.Connection, cafe_id: str, recipient_id: str) -> Redemption | None:
    row = await conn.fetchrow(
        """select d.id::text, d.code, d.table_session_id::text, o.number as order_number, d.discount_paise, d.redeemed_at
           from redemptions d left join orders o on o.id = d.order_id
           where d.cafe_id = $1 and d.campaign_recipient_id = $2""", cafe_id, recipient_id)
    return Redemption(**dict(row)) if row else None


async def for_session(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> Redemption | None:
    row = await conn.fetchrow(
        """select d.id::text, d.code, d.table_session_id::text, o.number as order_number, d.discount_paise, d.redeemed_at
           from redemptions d left join orders o on o.id = d.order_id
           where d.cafe_id = $1 and d.table_session_id = $2""", cafe_id, table_session_id)
    return Redemption(**dict(row)) if row else None


async def insert(conn: asyncpg.Connection, cafe_id: str, recipient_id: str, code_text: str, order_id: str | None,
                 table_session_id: str, discount_paise: int, redeemed_by: str) -> bool:
    created = await conn.fetchval(
        """insert into redemptions (cafe_id, campaign_recipient_id, code, order_id, table_session_id, discount_paise,
                                    redeemed_by)
           values ($1, $2, $3, $4, $5, $6, $7) on conflict (campaign_recipient_id) do nothing returning id""",
        cafe_id, recipient_id, code_text, order_id, table_session_id, discount_paise, redeemed_by)
    return created is not None


async def bill_lines(conn: asyncpg.Connection, cafe_id: str, order_ids: list[str]) -> list[asyncpg.Record]:
    if not order_ids:
        return []
    return list(await conn.fetch(
        """select order_id::text, name_snapshot, qty, qty * price_snapshot_paise as line_paise from order_items
           where cafe_id = $1 and order_id = any($2::uuid[]) order by created_at""", cafe_id, order_ids))


async def link_order(conn: asyncpg.Connection, cafe_id: str, order_id: str, recipient_id: str) -> None:
    """Attribution: the session's latest non-cancelled order came from the campaign."""
    await conn.execute(
        "update orders set source = 'campaign', campaign_recipient_id = $3 where cafe_id = $1 and id = $2",
        cafe_id, order_id, recipient_id)
