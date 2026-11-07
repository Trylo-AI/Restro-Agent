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
    customer_id: str
    code: str
    campaign_name: str
    offer_text: str | None
    sent_at: datetime | None
    consent: str | None  # the customer's latest consent status, None = never asked
    timezone: str        # the café's: expiry and "today" are café-local dates


@dataclass(frozen=True)
class Redemption:
    id: str
    code: str
    table_session_id: str | None
    order_number: int | None
    discount_paise: int
    redeemed_at: datetime
    channel: str = "table_bill"
    bill_paise: int | None = None
    redeemed_by_name: str | None = None
    table_label: str | None = None


# The code, its campaign and what the verdict needs. The send key is `campaign:{recipient_id}` (domain/campaigns).
CODE_COLS = """r.id::text as recipient_id, r.campaign_id::text, r.status, c.valid_until, c.discount_percent,
               c.status as campaign_status, r.customer_id::text, r.code, c.name as campaign_name, c.offer_text,
               (select w.sent_at from wa_messages w where w.idempotency_key = 'campaign:' || r.id::text) as sent_at,
               (select s.status from consents s where s.customer_id = r.customer_id
                order by s.created_at desc limit 1) as consent,
               cf.timezone"""

REDEMPTION_COLS = """d.id::text, d.code, d.table_session_id::text, o.number as order_number, d.discount_paise,
                     d.redeemed_at, d.channel, d.bill_paise, m.display_name as redeemed_by_name, t.label as table_label"""

REDEMPTION_JOINS = """left join orders o on o.id = d.order_id
           left join memberships m on m.cafe_id = d.cafe_id and m.user_id = d.redeemed_by
           left join table_sessions ts on ts.id = d.table_session_id
           left join tables t on t.id = ts.table_id"""


def _redemption(row: asyncpg.Record | None) -> Redemption | None:
    return Redemption(**dict(row)) if row else None


async def code(conn: asyncpg.Connection, cafe_id: str, code_text: str, *, lock: bool = False) -> CodeInfo | None:
    """Only this café's codes: another café's code is simply not found. Codes are stored upper case (new_code), so
    the unique index serves the match. `lock` takes `for share` on the recipient and its campaign: a cancel waits
    for the redemption to commit."""
    row = await conn.fetchrow(
        "select " + CODE_COLS + """
           from campaign_recipients r join campaigns c on c.id = r.campaign_id join cafes cf on cf.id = r.cafe_id
           where r.cafe_id = $1 and r.code = upper($2)""" + (" for share of r, c" if lock else ""), cafe_id, code_text)
    return CodeInfo(**dict(row)) if row else None


async def by_recipient(conn: asyncpg.Connection, cafe_id: str, recipient_id: str) -> Redemption | None:
    return _redemption(await conn.fetchrow(
        "select " + REDEMPTION_COLS + " from redemptions d " + REDEMPTION_JOINS + """
           where d.cafe_id = $1 and d.campaign_recipient_id = $2""", cafe_id, recipient_id))


async def for_session(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> Redemption | None:
    return _redemption(await conn.fetchrow(
        "select " + REDEMPTION_COLS + " from redemptions d " + REDEMPTION_JOINS + """
           where d.cafe_id = $1 and d.table_session_id = $2""", cafe_id, table_session_id))


async def insert(conn: asyncpg.Connection, cafe_id: str, recipient_id: str, code_text: str, order_id: str | None,
                 table_session_id: str | None, discount_paise: int, redeemed_by: str, *, channel: str = "table_bill",
                 bill_paise: int | None = None, idempotency_key: str | None = None) -> str | None:
    """The new id, or None when the code (or the key) was taken first. No conflict target: either unique index
    (`campaign_recipient_id`, `(cafe_id, idempotency_key)`) turns a clash into None, never a UniqueViolation."""
    return await conn.fetchval(  # type: ignore[no-any-return]
        """insert into redemptions (cafe_id, campaign_recipient_id, code, order_id, table_session_id, discount_paise,
                                    redeemed_by, channel, bill_paise, idempotency_key)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) on conflict do nothing returning id::text""",
        cafe_id, recipient_id, code_text, order_id, table_session_id, discount_paise, redeemed_by, channel, bill_paise,
        idempotency_key)


async def by_idempotency_key(conn: asyncpg.Connection, cafe_id: str, key: str) -> asyncpg.Record | None:
    """A counter redemption by its key, with everything `CounterRedemption` shows."""
    return await conn.fetchrow(
        """select d.id::text as redemption_id, d.code, d.channel, r.campaign_id::text, ca.name as campaign_name,
                  ca.offer_text, ca.discount_percent, r.customer_id::text, cu.name, (cu.wa_id like '000%') as deleted,
                  d.bill_paise, d.discount_paise,
                  d.redeemed_at, m.display_name as redeemed_by_name
           from redemptions d
           join campaign_recipients r on r.id = d.campaign_recipient_id
           join campaigns ca on ca.id = r.campaign_id
           join customers cu on cu.id = r.customer_id
           left join memberships m on m.cafe_id = d.cafe_id and m.user_id = d.redeemed_by
           where d.cafe_id = $1 and d.idempotency_key = $2""", cafe_id, key)


async def recent_for_customers(conn: asyncpg.Connection, cafe_id: str, customer_ids: list[str],
                               since: datetime) -> dict[str, Redemption]:
    """Each customer's latest redemption since `since`, either channel (one offer per visit at the counter)."""
    if not customer_ids:
        return {}
    rows = await conn.fetch(
        "select distinct on (r.customer_id) r.customer_id::text as customer_id, " + REDEMPTION_COLS + """
           from redemptions d join campaign_recipients r on r.id = d.campaign_recipient_id """ + REDEMPTION_JOINS + """
           where d.cafe_id = $1 and r.customer_id = any($2::uuid[]) and d.redeemed_at > $3
           order by r.customer_id, d.redeemed_at desc""", cafe_id, customer_ids, since)
    return {row["customer_id"]: Redemption(**{k: v for k, v in dict(row).items() if k != "customer_id"}) for row in rows}


async def recent_for_customer(conn: asyncpg.Connection, cafe_id: str, customer_id: str,
                              since: datetime) -> Redemption | None:
    return (await recent_for_customers(conn, cafe_id, [customer_id], since)).get(customer_id)


# A recipient that could be redeemed now, as SQL: only for ordering search results (the verdict is code's).
ELIGIBLE_SQL = """exists (
  select 1 from campaign_recipients er join campaigns ec on ec.id = er.campaign_id
  where er.cafe_id = cu.cafe_id and er.customer_id = cu.id
    and er.status in ('sent', 'delivered', 'read', 'held') and ec.status <> 'cancelled'
    and (ec.valid_until is null
         or (now() at time zone cf.timezone)::date <= (ec.valid_until at time zone cf.timezone)::date)
    and not exists (select 1 from redemptions ed where ed.campaign_recipient_id = er.id))"""


async def search_customers(conn: asyncpg.Connection, cafe_id: str, kind: str, value: str,
                           limit: int) -> list[asyncpg.Record]:
    """Customers for a counter search. `name`: anywhere in the name (value already escaped for LIKE), or a code typed
    without its dash; `phone_last4`: the number's last 4 digits; `code`: exact. Deleted customers (wa_id `000…`)
    are found only by code. Visits as `v_customer_card`."""
    match = {
        "name": """((cu.wa_id not like '000%' and cu.name ilike '%' || $2 || '%')
                    or exists (select 1 from campaign_recipients q where q.cafe_id = cu.cafe_id and q.customer_id = cu.id
                               and replace(q.code, '-', '') = upper($3)))""",
        "phone_last4": "(cu.wa_id not like '000%' and right(cu.wa_id, 4) = $2)",
        "code": """exists (select 1 from campaign_recipients q where q.cafe_id = cu.cafe_id and q.customer_id = cu.id
                           and q.code = upper($2))""",
    }[kind]
    params: list[object] = [cafe_id, value]
    if kind == "name":
        params.append(value.replace("\\", "").replace(" ", ""))
    params.append(limit)
    return list(await conn.fetch(
        """select cu.id::text as customer_id, cu.name, cu.wa_id, (cu.wa_id like '000%') as deleted, cu.last_visit_at,
                  (select count(distinct coalesce(o.table_session_id, o.id)) from orders o
                   where o.customer_id = cu.id and o.status <> 'CANCELLED')::int as visits,
                  """ + ELIGIBLE_SQL + """ as has_eligible
           from customers cu join cafes cf on cf.id = cu.cafe_id
           where cu.cafe_id = $1 and """ + match + f"""
           order by has_eligible desc, cu.last_visit_at desc nulls last, cu.name, cu.id
           limit ${len(params)}""", *params))


async def offers_for(conn: asyncpg.Connection, cafe_id: str, customer_ids: list[str], matched_code: str | None,
                     per_customer: int = 10) -> list[tuple[CodeInfo, Redemption | None]]:
    """Each customer's newest codes (the searched code always kept), newest first, with any redemption."""
    if not customer_ids:
        return []
    rows = await conn.fetch(
        """select * from (
             select """ + CODE_COLS + """, r.created_at as recipient_created_at,
                    row_number() over (partition by r.customer_id
                                       order by (replace(r.code, '-', '') = replace(coalesce($3, ''), '-', '')) desc,
                                                r.created_at desc) as rn,
                    d.id::text as d_id, d.code as d_code, d.table_session_id::text as d_table_session_id,
                    o.number as d_order_number, d.discount_paise as d_discount_paise, d.redeemed_at as d_redeemed_at,
                    d.channel as d_channel, d.bill_paise as d_bill_paise, m.display_name as d_redeemed_by_name,
                    t.label as d_table_label
             from campaign_recipients r join campaigns c on c.id = r.campaign_id join cafes cf on cf.id = r.cafe_id
             left join redemptions d on d.campaign_recipient_id = r.id
             left join orders o on o.id = d.order_id
             left join memberships m on m.cafe_id = d.cafe_id and m.user_id = d.redeemed_by
             left join table_sessions ts on ts.id = d.table_session_id
             left join tables t on t.id = ts.table_id
             where r.cafe_id = $1 and r.customer_id = any($2::uuid[])) x
           where x.rn <= $4
           order by x.customer_id, x.recipient_created_at desc""", cafe_id, customer_ids, matched_code, per_customer)
    out: list[tuple[CodeInfo, Redemption | None]] = []
    for row in rows:
        d = dict(row)
        info = CodeInfo(**{f: d[f] for f in CodeInfo.__dataclass_fields__})
        used = Redemption(**{f: d["d_" + f] for f in Redemption.__dataclass_fields__}) if d["d_id"] else None
        out.append((info, used))
    return out


async def open_bills(conn: asyncpg.Connection, cafe_id: str, customer_ids: list[str]) -> dict[str, asyncpg.Record]:
    """The table bill a customer is sitting at: their latest non-cancelled order's session, if still open, with its
    subtotal and any code already on it."""
    if not customer_ids:
        return {}
    rows = await conn.fetch(
        """select l.customer_id::text as customer_id, ts.id::text as table_session_id, t.label as table_label,
                  (select coalesce(sum(o2.total_paise), 0) from orders o2
                   where o2.cafe_id = $1 and o2.table_session_id = ts.id and o2.status <> 'CANCELLED')::int as subtotal_paise,
                  (select d.code from redemptions d where d.cafe_id = $1 and d.table_session_id = ts.id) as code
           from (select distinct on (o.customer_id) o.customer_id, o.table_session_id from orders o
                 where o.cafe_id = $1 and o.customer_id = any($2::uuid[]) and o.status <> 'CANCELLED'
                 order by o.customer_id, o.created_at desc) l
           join table_sessions ts on ts.id = l.table_session_id and ts.closed_at is null
           join tables t on t.id = ts.table_id""", cafe_id, customer_ids)
    return {row["customer_id"]: row for row in rows}


async def lock_session(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> bool:
    """Serialises codes applied to one bill (a double tap, two codes at once); False when there is no such session.
    Taken before the customer row; the counter never locks a session, so the two paths keep one order. `no key
    update` still lets a new order reference the session."""
    return bool(await conn.fetchval(
        "select 1 from table_sessions where cafe_id = $1 and id = $2 for no key update", cafe_id, table_session_id))


async def lock_customer(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> str | None:
    """Serialises redemptions for one customer (two tills, a till and a bill, same-key retries) and returns their
    name. `no key update` still lets a new order reference the customer (the FK check takes `key share`)."""
    return await conn.fetchval(  # type: ignore[no-any-return]
        "select name from customers where cafe_id = $1 and id = $2 for no key update", cafe_id, customer_id)


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
