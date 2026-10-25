from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

COLS = """id::text, cafe_id::text, name, segment, template_name, offer_text, discount_percent, code_prefix, valid_until,
          scheduled_at, status, created_at"""

# Segment ∩ latest consent opted_in (docs/backend.md#campaigns). Lapsed: no order in 30 days · regulars: 3+ in 60 days.
AUDIENCE = """
with v as (
  select cu.id, cu.cafe_id, cu.name, cu.last_visit_at,
         count(o.id) filter (where o.status <> 'CANCELLED') as visits,
         count(o.id) filter (where o.status <> 'CANCELLED' and o.created_at > now() - interval '60 days') as orders_60d
  from customers cu left join orders o on o.customer_id = cu.id
  where cu.cafe_id = $1 and cu.wa_id not like '000%'
  group by cu.id
),
seg as (
  select v.*, (select s.status from consents s where s.customer_id = v.id order by s.created_at desc limit 1) as consent
  from v where v.visits > 0 and (
    $2 = 'all_opted_in'
    or ($2 = 'lapsed' and v.last_visit_at < now() - interval '30 days')
    or ($2 = 'regulars' and v.orders_60d >= 3))
)
"""


@dataclass(frozen=True)
class Campaign:
    id: str
    cafe_id: str
    name: str
    segment: str
    template_name: str
    offer_text: str | None
    discount_percent: int | None
    code_prefix: str | None
    valid_until: datetime | None
    scheduled_at: datetime | None
    status: str
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in self.__dict__.items()}


def _c(row: asyncpg.Record | None) -> Campaign | None:
    return Campaign(**dict(row)) if row else None


async def get(conn: asyncpg.Connection, cafe_id: str, campaign_id: str, *, lock: bool = False) -> Campaign | None:
    return _c(await conn.fetchrow("select " + COLS + " from campaigns where cafe_id = $1 and id = $2"
                                  + (" for update" if lock else ""), cafe_id, campaign_id))


async def insert(conn: asyncpg.Connection, cafe_id: str, f: dict[str, Any]) -> Campaign:
    row = await conn.fetchrow(
        """insert into campaigns (cafe_id, name, segment, template_name, offer_text, discount_percent, code_prefix, valid_until)
           values ($1, $2, $3, $4, $5, $6, $7, $8) returning """ + COLS,
        cafe_id, f["name"], f["segment"], f["template_name"], f.get("offer_text"), f.get("discount_percent"),
        f.get("code_prefix"), f.get("valid_until"))
    return Campaign(**dict(row))


async def update_draft(conn: asyncpg.Connection, cafe_id: str, campaign_id: str, f: dict[str, Any]) -> Campaign | None:
    return _c(await conn.fetchrow(
        """update campaigns set name = $3, segment = $4, template_name = $5, offer_text = $6, discount_percent = $7,
                  code_prefix = $8, valid_until = $9
           where cafe_id = $1 and id = $2 and status = 'draft' returning """ + COLS,
        cafe_id, campaign_id, f["name"], f["segment"], f["template_name"], f.get("offer_text"), f.get("discount_percent"),
        f.get("code_prefix"), f.get("valid_until")))


async def set_status(conn: asyncpg.Connection, cafe_id: str, campaign_id: str, frm: tuple[str, ...], to: str,
                     scheduled_at: datetime | None = None) -> Campaign | None:
    return _c(await conn.fetchrow(
        """update campaigns set status = $4, scheduled_at = coalesce($5, scheduled_at)
           where cafe_id = $1 and id = $2 and status = any($3::text[]) returning """ + COLS,
        cafe_id, campaign_id, list(frm), to, scheduled_at))


async def template_status(conn: asyncpg.Connection, cafe_id: str, name: str) -> str | None:
    return await conn.fetchval(  # type: ignore[no-any-return]
        "select status from templates where cafe_id = $1 and name = $2 and category = 'marketing' and language = 'en'",
        cafe_id, name)


async def audience_counts(conn: asyncpg.Connection, cafe_id: str, segment: str) -> tuple[int, int]:
    """(in segment, opted in)."""
    row = await conn.fetchrow(AUDIENCE + "select count(*) as n, count(*) filter (where consent = 'opted_in') as ok from seg",
                              cafe_id, segment)
    return int(row["n"]), int(row["ok"])


async def audience(conn: asyncpg.Connection, cafe_id: str, segment: str) -> list[str]:
    rows = await conn.fetch(AUDIENCE + "select id::text from seg where consent = 'opted_in' order by id", cafe_id, segment)
    return [r["id"] for r in rows]


async def due(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    """Scheduled campaigns whose time has come, all cafés (the start job)."""
    rows = await conn.fetch("select cafe_id::text, id::text from campaigns where status = 'scheduled' and scheduled_at <= now()")
    return [(r["cafe_id"], r["id"]) for r in rows]


async def add_recipient(conn: asyncpg.Connection, cafe_id: str, campaign_id: str, customer_id: str, code: str) -> str | None:
    return await conn.fetchval(  # type: ignore[no-any-return]
        """insert into campaign_recipients (cafe_id, campaign_id, customer_id, code) values ($1, $2, $3, $4)
           on conflict do nothing returning id::text""", cafe_id, campaign_id, customer_id, code)


async def sent_today(conn: asyncpg.Connection, cafe_id: str) -> int:
    """Marketing sends created today (café time zone): the messaging-tier limit counts these."""
    return int(await conn.fetchval(
        """select count(*) from wa_messages w join cafes c on c.id = w.cafe_id
           where w.cafe_id = $1 and w.idempotency_key like 'campaign:%'
             and (w.created_at at time zone c.timezone)::date = (now() at time zone c.timezone)::date""", cafe_id) or 0)


async def take(conn: asyncpg.Connection, cafe_id: str, campaign_id: str, status: str, limit: int) -> list[str]:
    rows = await conn.fetch(
        """select id::text from campaign_recipients where cafe_id = $1 and campaign_id = $2 and status = $3
           order by created_at limit $4""", cafe_id, campaign_id, status, limit)
    return [r["id"] for r in rows]


async def set_recipients(conn: asyncpg.Connection, cafe_id: str, ids: list[str], frm: str, to: str) -> None:
    await conn.execute("update campaign_recipients set status = $4 where cafe_id = $1 and id = any($2::uuid[]) and status = $3",
                       cafe_id, ids, frm, to)


async def recipient(conn: asyncpg.Connection, cafe_id: str, recipient_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """select r.id::text, r.campaign_id::text, r.customer_id::text, r.code, r.status, cu.name,
                  (select s.status from consents s where s.customer_id = r.customer_id order by s.created_at desc limit 1) as consent
           from campaign_recipients r join customers cu on cu.id = r.customer_id
           where r.cafe_id = $1 and r.id = $2""", cafe_id, recipient_id)


async def mark_recipient(conn: asyncpg.Connection, cafe_id: str, recipient_id: str, status: str, wamid: str | None = None) -> None:
    await conn.execute("""update campaign_recipients set status = $3, wamid = coalesce($4, wamid)
                          where cafe_id = $1 and id = $2""", cafe_id, recipient_id, status, wamid)


async def remaining(conn: asyncpg.Connection, cafe_id: str, campaign_id: str) -> int:
    return int(await conn.fetchval(
        "select count(*) from campaign_recipients where cafe_id = $1 and campaign_id = $2 and status in ('queued', 'deferred')",
        cafe_id, campaign_id) or 0)


async def sending(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    rows = await conn.fetch("select cafe_id::text, id::text from campaigns where status = 'sending'")
    return [(r["cafe_id"], r["id"]) for r in rows]


STATUS_RANK = {"queued": 0, "deferred": 0, "sent": 1, "held": 1, "delivered": 2, "read": 3, "failed": 4}


async def apply_status(conn: asyncpg.Connection, cafe_id: str, wamid: str, status: str, error_code: str | None) -> None:
    """Status webhooks move a recipient forward only; 131049 (Meta's marketing limit) is `held`, never retried."""
    new = "held" if error_code == "131049" else status
    if new not in STATUS_RANK:
        return
    allowed = [s for s, r in STATUS_RANK.items() if r < STATUS_RANK[new] and s != "failed"]
    await conn.execute("""update campaign_recipients set status = $3
                          where cafe_id = $1 and wamid = $2 and status = any($4::text[])""", cafe_id, wamid, new, allowed)
