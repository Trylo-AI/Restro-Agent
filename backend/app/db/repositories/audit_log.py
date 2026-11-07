"""audit_log: append-only. Writes come only from app.domain.audit.write; the owner reads through /audit/* (decision 19).

`search` builds its WHERE from a fixed list of fragments; every value is a bound `$n`. Keyset paging on (at, id).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg


async def insert(conn: asyncpg.Connection, cafe_id: str, actor_id: str | None, actor_role: str, support: bool,
                 action: str, change: dict[str, Any], *, severity: str, order_id: str | None, customer_id: str | None,
                 conversation_id: str | None) -> None:
    await conn.execute(
        """insert into audit_log (actor_id, actor_role, support, cafe_id, action, change, severity, order_id, customer_id,
                                  conversation_id, actor_name)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                   (select display_name from memberships where cafe_id = $4 and user_id = $1))""",
        actor_id, actor_role, support, cafe_id, action, change, severity, order_id, customer_id, conversation_id,
    )


@dataclass(frozen=True)
class AuditRow:
    id: str
    at: datetime
    actor_id: str | None
    actor_role: str
    support: bool
    action: str
    change: dict[str, Any]
    severity: str
    order_id: str | None = None
    customer_id: str | None = None
    conversation_id: str | None = None
    actor_name: str | None = None      # snapshot at write time
    member_name: str | None = None     # current membership display name, if any


_COLS = """a.id::text, a.at, a.actor_id::text, a.actor_role, a.support, a.action, a.change, a.severity, a.order_id::text,
           a.customer_id::text, a.conversation_id::text, a.actor_name, m.display_name as member_name"""
_FROM = """audit_log a left join memberships m on m.cafe_id = a.cafe_id and m.user_id = a.actor_id"""


def _row(r: asyncpg.Record) -> AuditRow:
    d = dict(r)
    d["change"] = d["change"] or {}
    d["severity"] = d["severity"] or "info"  # only before 20261101000002
    return AuditRow(**d)


def _escape_like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@dataclass(frozen=True)
class Where:
    """The filters `search` and `facets` understand. Empty lists mean "no filter"."""
    since: datetime | None = None
    until: datetime | None = None
    actions: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()   # (action, change.to): filter-only order.accept/ready/serve
    other_than: tuple[str, ...] | None = None   # category "other": any action not in this list
    severities: tuple[str, ...] = ()
    actor_ids: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    order_id: str | None = None
    customer_id: str | None = None
    conversation_id: str | None = None
    q: str | None = None
    q_actions: tuple[str, ...] = ()
    q_aliases: tuple[tuple[str, str], ...] = ()  # (action, change.to) whose alias label matches `q`


def _where(cafe_id: str, w: Where) -> tuple[list[str], list[Any]]:
    args: list[Any] = [cafe_id]
    parts = ["a.cafe_id = $1"]

    def arg(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if w.since is not None:
        parts.append(f"a.at >= {arg(w.since)}")
    if w.until is not None:
        parts.append(f"a.at < {arg(w.until)}")
    kinds: list[str] = []
    if w.actions:
        kinds.append(f"a.action = any({arg(list(w.actions))}::text[])")
    for action, to in w.aliases:
        kinds.append(f"(a.action = {arg(action)} and a.change->>'to' = {arg(to)})")
    if w.other_than is not None:
        kinds.append(f"a.action <> all({arg(list(w.other_than))}::text[])")
    if kinds:
        parts.append("(" + " or ".join(kinds) + ")")
    if w.severities:
        if "info" in w.severities:
            parts.append(f"a.severity = any({arg(list(w.severities))}::text[])")
        else:  # literal predicate so the partial index audit_log_cafe_flagged applies
            parts.append(f"a.severity <> 'info' and a.severity = any({arg(list(w.severities))}::text[])")
    if w.actor_ids:
        parts.append(f"a.actor_id = any({arg(list(w.actor_ids))}::uuid[])")
    if w.roles:
        parts.append(f"a.actor_role = any({arg(list(w.roles))}::text[])")
    if w.order_id:
        parts.append(f"a.order_id = {arg(w.order_id)}::uuid")
    if w.customer_id:
        p = arg(w.customer_id)
        parts.append(f"""(a.customer_id = {p}::uuid or a.conversation_id in
                         (select cv.id from conversations cv where cv.cafe_id = $1 and cv.customer_id = {p}::uuid))""")
    if w.conversation_id:
        parts.append(f"a.conversation_id = {arg(w.conversation_id)}::uuid")
    if w.q:
        p = arg(_escape_like(w.q))
        q_aliases = "".join(f"\n                          or (a.action = {arg(action)} and a.change->>'to' = {arg(to)})"
                            for action, to in w.q_aliases)
        parts.append(f"""(coalesce(m.display_name, a.actor_name) ilike {p} escape '\\'
                          or a.change->>'name' ilike {p} escape '\\' or a.change->>'label' ilike {p} escape '\\'
                          or a.change->>'table' ilike {p} escape '\\'
                          or exists (select 1 from customers cu where cu.cafe_id = $1 and cu.id = a.customer_id
                                     and cu.name ilike {p} escape '\\')
                          or a.action = any({arg(list(w.q_actions))}::text[]){q_aliases})""")
    return parts, args


async def search(conn: asyncpg.Connection, cafe_id: str, w: Where, *, before: tuple[datetime, str] | None = None,
                 limit: int = 50) -> list[AuditRow]:
    """Newest first. Returns up to `limit + 1` rows so the caller knows there is a next page."""
    parts, args = _where(cafe_id, w)
    if before is not None:
        args.extend(before)
        parts.append(f"(a.at, a.id) < (${len(args) - 1}, ${len(args)}::uuid)")
    args.append(limit + 1)
    rows = await conn.fetch(
        "select " + _COLS + " from " + _FROM + " where " + " and ".join(parts)
        + f" order by a.at desc, a.id desc limit ${len(args)}", *args)
    return [_row(r) for r in rows]


async def facets(conn: asyncpg.Connection, cafe_id: str, since: datetime, until: datetime) -> list[asyncpg.Record]:
    """Counts per (action, transition target, actor, role, support, severity) in the window; the viewer folds them."""
    return list(await conn.fetch(
        """select a.action, case when a.action = 'order.transition' then a.change->>'to' end as to_status,
                  a.actor_id::text, a.actor_role, a.support, coalesce(a.severity, 'info') as severity,
                  max(coalesce(m.display_name, a.actor_name)) as name, count(*)::int as n
           from audit_log a left join memberships m on m.cafe_id = a.cafe_id and m.user_id = a.actor_id
           where a.cafe_id = $1 and a.at >= $2 and a.at < $3
           group by 1, 2, 3, 4, 5, 6""",
        cafe_id, since, until))


async def for_order(conn: asyncpg.Connection, cafe_id: str, order_id: str, table_session_id: str | None,
                    since: datetime, limit: int = 200) -> list[AuditRow]:
    """Rows on the order, plus bill and code rows on its table session from the order's creation. Oldest first."""
    rows = await conn.fetch(
        "select " + _COLS + " from " + _FROM + """
           where a.cafe_id = $1 and (a.order_id = $2::uuid
                 or ($3::text is not null and a.action in ('bill.deliver', 'offer.redeem')
                     and a.change->>'table_session_id' = $3 and a.at >= $4))
           order by a.at, a.id limit $5""",
        cafe_id, order_id, table_session_id, since, limit)
    return [_row(r) for r in rows]


async def names(conn: asyncpg.Connection, cafe_id: str, *, staff: list[str], customers: list[str], conversations: list[str],
                orders: list[str], sessions: list[str], campaigns: list[str]) -> dict[str, list[asyncpg.Record]]:
    """Display names for a page of rows, in at most 5 batched queries (each skipped when it has nothing to look up)."""
    out: dict[str, list[asyncpg.Record]] = {"staff": [], "customers": [], "orders": [], "refs": []}
    if staff:
        out["staff"] = list(await conn.fetch(
            "select user_id::text, display_name from memberships where cafe_id = $1 and user_id = any($2::uuid[])",
            cafe_id, staff))
    if customers or conversations:
        out["customers"] = list(await conn.fetch(
            """select cu.id::text, cu.name, cu.wa_id like '000%' as deleted, cv.id::text as conversation_id
               from customers cu left join conversations cv on cv.cafe_id = cu.cafe_id and cv.customer_id = cu.id
               where cu.cafe_id = $1 and (cu.id = any($2::uuid[]) or cv.id = any($3::uuid[]))""",
            cafe_id, customers, conversations))
    if orders:
        out["orders"] = list(await conn.fetch(
            "select id::text, number, status, cancel_reason_text from orders where cafe_id = $1 and id = any($2::uuid[])",
            cafe_id, orders))
    if sessions or campaigns:
        out["refs"] = list(await conn.fetch(
            """select 'table' as kind, ts.id::text, t.label as name
               from table_sessions ts join tables t on t.cafe_id = ts.cafe_id and t.id = ts.table_id
               where ts.cafe_id = $1 and ts.id = any($2::uuid[])
               union all
               select 'campaign', id::text, name from campaigns where cafe_id = $1 and id = any($3::uuid[])""",
            cafe_id, sessions, campaigns))
    out["timezone"] = list(await conn.fetch("select timezone from cafes where id = $1", cafe_id))
    return out


async def order_id_by_number(conn: asyncpg.Connection, cafe_id: str, number: int) -> str | None:
    value = await conn.fetchval("select id::text from orders where cafe_id = $1 and number = $2", cafe_id, number)
    return str(value) if value else None
