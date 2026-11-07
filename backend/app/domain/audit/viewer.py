"""The owner's audit log reads (GET /audit/*, GET /orders/{id}/history). Owner only; decision 19, D23.

Default range 7 days; any order, customer or chat filter searches all time; at most 366 days. The export is capped
at 10 000 rows and is itself audited (`audit.export`). `q` that looks like "#42" is an order number. Logs carry
filter names and `q_present`, never `q` itself (it can hold a customer's first name).
"""

import base64
import binascii
import csv
import io
import re
import uuid
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from app.core.auth import Principal
from app.core.errors import BadRequest, NotFound
from app.core.logging import get_logger
from app.db.repositories import audit_log
from app.db.repositories import order_history as history_repo
from app.db.repositories.audit_log import AuditRow, Where
from app.db.tx import conn, tx
from app.domain import audit
from app.domain.audit import catalog
from app.domain.audit.catalog import CustomerName, Names, OrderRef

log = get_logger(__name__)

DEFAULT_DAYS = 7
MAX_DAYS = 366
EXPORT_CAP = 10_000
HISTORY_CAP = 200
ORDER_NUMBER = re.compile(r"^#?\d{1,9}$")
LINK_KEYS = ("campaign_id", "item_id", "table_session_id", "membership_id", "request_id")
CSV_COLUMNS = ["id", "at_local", "at_utc", "severity", "severity_reason", "category", "action", "label", "actor",
               "actor_role", "support", "order_number", "customer_first_name", "sentence", "details"]


@dataclass(frozen=True)
class AuditFilters:
    actions: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    actor_ids: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    order_number: int | None = None
    order_id: str | None = None
    customer_id: str | None = None
    conversation_id: str | None = None
    since: datetime | None = None   # `from`
    until: datetime | None = None   # `to`, exclusive
    q: str | None = None

    def has_entity(self) -> bool:
        return any(v is not None for v in (self.order_number, self.order_id, self.customer_id, self.conversation_id))

    def names(self) -> list[str]:
        """The filters in use, by name (for logs and the export's audit row; never their values)."""
        return [f.name for f in fields(self) if getattr(self, f.name) not in (None, ())]


def normalise(f: AuditFilters) -> AuditFilters:
    """A `q` like "#42" or "42" becomes the order-number filter."""
    q = (f.q or "").strip() or None
    if q and ORDER_NUMBER.match(q) and f.order_number is None:
        return AuditFilters(**{**f.__dict__, "q": None, "order_number": int(q.lstrip("#"))})
    return AuditFilters(**{**f.__dict__, "q": q})


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def window(f: AuditFilters, now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    """(since, until). No `from`: the last 7 days, or all time with an order, customer or chat filter."""
    now = now or datetime.now(UTC)
    since, until = _utc(f.since), _utc(f.until)
    if since is None and until is None and f.has_entity():
        return None, None
    until = until or now
    since = since or (None if f.has_entity() else until - timedelta(days=DEFAULT_DAYS))
    if since is not None:
        if since >= until:
            raise BadRequest("The start must be before the end.")
        if until - since > timedelta(days=MAX_DAYS):
            raise BadRequest(f"Pick at most {MAX_DAYS} days.")
    return since, until


def encode_cursor(row: AuditRow) -> str:
    raw = f"{row.at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        at, row_id = raw.split("|")
        return _utc(datetime.fromisoformat(at)) or datetime.now(UTC), str(uuid.UUID(row_id))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise BadRequest("That page link isn't valid. Reload the audit log.") from exc


def action_filter(f: AuditFilters) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], tuple[str, ...] | None] | None:
    """(stored actions, aliases, other_than) for the action and category filters; None when they can't match anything.

    Categories intersect actions: an action filter keeps only actions in the chosen categories.
    """
    cats = set(f.categories)
    if f.actions:
        chosen = [a for a in dict.fromkeys(f.actions) if not cats or catalog.spec(a).category in cats]
        if not chosen:
            return None
        return (tuple(a for a in chosen if a not in catalog.FILTER_ALIASES),
                tuple(catalog.FILTER_ALIASES[a] for a in chosen if a in catalog.FILTER_ALIASES), None)
    if cats:
        stored = [a for a in catalog.CATALOG if a not in catalog.FILTER_ALIASES]
        return tuple(catalog.actions_in(cats)), (), (tuple(stored) if "other" in cats else None)
    return (), (), None


async def _where(c: asyncpg.Connection, cafe_id: str, f: AuditFilters, since: datetime | None,
                 until: datetime | None) -> Where | None:
    kinds = action_filter(f)
    if kinds is None:
        return None
    order_id = f.order_id
    if f.order_number is not None:
        by_number = await audit_log.order_id_by_number(c, cafe_id, f.order_number)
        if by_number is None or (order_id is not None and by_number != order_id):
            return None
        order_id = by_number
    return Where(since=since, until=until, actions=kinds[0], aliases=kinds[1], other_than=kinds[2],
                 severities=tuple(dict.fromkeys(f.severities)), actor_ids=tuple(dict.fromkeys(f.actor_ids)),
                 roles=tuple(dict.fromkeys(f.roles)), order_id=order_id, customer_id=f.customer_id,
                 conversation_id=f.conversation_id, q=f.q, q_actions=tuple(catalog.match_labels(f.q)) if f.q else (),
                 q_aliases=tuple(catalog.match_aliases(f.q)) if f.q else ())


# ── names ─────────────────────────────────────────────────────────────────


def _ids(values: list[object]) -> list[str]:
    out = []
    for v in values:
        if v is None:
            continue
        try:
            out.append(str(uuid.UUID(str(v))))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


async def _names(c: asyncpg.Connection, cafe_id: str, rows: list[AuditRow], *, extra_customers: list[str] | None = None) -> Names:
    ch = [r.change for r in rows]

    def pick(*keys: str) -> list[object]:
        return [x.get(k) for x in ch for k in keys if x.get(k)]

    found = await audit_log.names(
        c, cafe_id,
        staff=_ids([r.actor_id for r in rows] + pick("from_assigned_to", "user_id")),
        customers=_ids([r.customer_id for r in rows] + pick("customer_id") + list(extra_customers or [])),
        conversations=_ids([r.conversation_id for r in rows]),
        orders=_ids([r.order_id for r in rows] + pick("order_id")),
        sessions=_ids(pick("table_session_id")),
        campaigns=_ids(pick("campaign_id")),
    )
    n = Names()
    n.staff = {r["user_id"]: r["display_name"] for r in found["staff"] if r["display_name"]}
    for r in found["customers"]:
        n.customers[r["id"]] = CustomerName(None if r["deleted"] else catalog.first_name(r["name"]), bool(r["deleted"]))
        if r["conversation_id"]:
            n.conversations[r["conversation_id"]] = r["id"]
    n.orders = {r["id"]: OrderRef(int(r["number"]), r["status"], r["cancel_reason_text"]) for r in found["orders"]}
    for r in found["refs"]:
        (n.tables if r["kind"] == "table" else n.campaigns)[r["id"]] = r["name"]
    if found["timezone"]:
        n.timezone = found["timezone"][0]["timezone"]
    return n


# ── entries ───────────────────────────────────────────────────────────────


def _reason(row: AuditRow) -> str | None:
    """Stored severity wins; classify re-runs only for the reason text."""
    if row.severity == "info":
        return None
    return catalog.classify(row.action, row.actor_role, row.support, row.change, actor_id=row.actor_id).reason


def _label(row: AuditRow) -> str:
    if row.action == "order.transition":
        for alias, (_, to) in catalog.FILTER_ALIASES.items():
            if row.change.get("to") == to:
                return catalog.CATALOG[alias].label
    return catalog.spec(row.action).label


def entry(row: AuditRow, n: Names) -> dict[str, Any]:
    """One AuditEntry (contracts/openapi.yaml)."""
    oid = row.order_id or catalog.as_uuid(row.change.get("order_id"))
    ref = n.orders.get(oid) if oid else None
    cid = catalog.customer_id_of(row, n)
    cust = n.customers.get(cid) if cid else None
    return {
        "id": row.id, "at": row.at.isoformat(), "action": row.action, "label": _label(row),
        "category": catalog.spec(row.action).category, "severity": row.severity, "severity_reason": _reason(row),
        "sentence": catalog.describe(row, n),
        "actor": {"user_id": row.actor_id, "name": catalog.actor_label(row, n), "role": row.actor_role,
                  "support": row.support},
        "order": {"id": oid, "number": ref.number, "status": ref.status} if oid and ref else None,
        "customer": {"id": cid, "name": cust.first if cust else None} if cid else None,
        "conversation_id": row.conversation_id,
        "links": {k: v for k in LINK_KEYS if (v := catalog.as_uuid(row.change.get(k)))},
        "details": [{"label": k, "value": v} for k, v in catalog.details(row, n)],
        "change": row.change,
    }


def _read_log(cafe_id: str, f: AuditFilters, rows: int, what: str) -> None:
    log.info("audit_read", cafe_id=cafe_id, what=what, rows=rows, filters=f.names(), q_present=bool(f.q))


async def entries(p: Principal, f: AuditFilters, before: str | None, limit: int = 50) -> dict[str, Any]:
    f = normalise(f)
    since, until = window(f)
    cursor = decode_cursor(before)
    async with conn() as c:
        where = await _where(c, p.cafe_id, f, since, until)
        rows = await audit_log.search(c, p.cafe_id, where, before=cursor, limit=limit) if where else []
        page = rows[:limit]
        n = await _names(c, p.cafe_id, page)
    _read_log(p.cafe_id, f, len(page), "entries")
    return {"entries": [entry(r, n) for r in page], "next": encode_cursor(page[-1]) if len(rows) > limit else None}


async def facets(p: Principal, since: datetime | None, until: datetime | None) -> dict[str, Any]:
    start, end = window(AuditFilters(since=since, until=until))
    assert start is not None and end is not None  # no entity filter here
    async with conn() as c:
        rows = await audit_log.facets(c, p.cafe_id, start, end)
    by_severity = {"critical": 0, "warning": 0, "info": 0}
    actions: dict[str, int] = {}
    actors: dict[tuple[str | None, str, bool], dict[str, Any]] = {}
    categories: dict[catalog.Category, int] = {}
    for r in rows:
        count = int(r["n"])
        by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + count
        action = r["action"]
        for alias, (_, to) in catalog.FILTER_ALIASES.items():
            if action == "order.transition" and r["to_status"] == to:
                action = alias  # an accept counts under "Order accepted", not "Order status changed"
        actions[action] = actions.get(action, 0) + count
        cat = catalog.spec(r["action"]).category
        categories[cat] = categories.get(cat, 0) + count
        key = (r["actor_id"], r["actor_role"], bool(r["support"]))
        if key not in actors:
            row = AuditRow(id="", at=start, actor_id=r["actor_id"], actor_role=r["actor_role"], support=bool(r["support"]),
                           action=r["action"], change={}, severity=r["severity"], actor_name=r["name"])
            actors[key] = {"user_id": r["actor_id"], "name": catalog.actor_label(row, Names()), "role": r["actor_role"],
                           "count": 0}
        actors[key]["count"] += count
    _read_log(p.cafe_id, AuditFilters(since=since, until=until), sum(by_severity.values()), "facets")
    return {
        "from": start.isoformat(), "to": end.isoformat(), "total": sum(by_severity.values()), "by_severity": by_severity,
        "actions": [{"action": a, "label": catalog.spec(a).label, "category": catalog.spec(a).category, "count": k}
                    for a, k in sorted(actions.items(), key=lambda x: (-x[1], x[0]))],
        "actors": sorted(actors.values(), key=lambda x: (-x["count"], x["name"])),
        "categories": [{"category": cat, "label": catalog.CATEGORY_LABELS[cat], "count": k}
                       for cat, k in sorted(categories.items(), key=lambda x: (-x[1], x[0]))],
    }


def catalog_json() -> dict[str, Any]:
    return catalog.public()


# ── export ────────────────────────────────────────────────────────────────


def csv_cell(value: object) -> str:
    """Spreadsheet-safe: a cell that starts like a formula is prefixed with a quote."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


async def export_csv(p: Principal, f: AuditFilters) -> tuple[str, str]:
    """(CSV text, café-local date for the file name). Over 10 000 rows → 400."""
    f = normalise(f)
    since, until = window(f)
    async with conn() as c:
        where = await _where(c, p.cafe_id, f, since, until)
        rows = await audit_log.search(c, p.cafe_id, where, limit=EXPORT_CAP) if where else []
        if len(rows) > EXPORT_CAP:
            raise BadRequest("Too many rows — narrow the dates or filters.")
        n = await _names(c, p.cafe_id, rows)
    tz = ZoneInfo(n.timezone)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        e = entry(r, n)
        w.writerow([csv_cell(x) for x in (
            r.id, r.at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"), r.at.astimezone(UTC).isoformat(), r.severity,
            e["severity_reason"] or "", e["category"], r.action, e["label"], e["actor"]["name"], r.actor_role,
            "yes" if r.support else "no", e["order"]["number"] if e["order"] else "",
            (e["customer"] or {}).get("name") or "", e["sentence"],
            "; ".join(f"{d['label']}: {d['value']}" for d in e["details"]))])
    async with tx() as c:
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "audit.export", {"rows": len(rows), "filters": f.names()})
    _read_log(p.cafe_id, f, len(rows), "export")
    return out.getvalue(), datetime.now(tz).date().isoformat()


# ── order history ─────────────────────────────────────────────────────────

ESCALATION_TEXT = {1: "No one accepted in 60 s — sent to managers", 2: "Still not accepted — sent to the owner"}


def _who(name: str | None, deleted: bool) -> str:
    if deleted:
        return "A customer (data deleted)"
    return name or "The customer"


def _status_sentence(who: str, number: int, ev: asyncpg.Record) -> str:
    frm, to = ev["from_status"], ev["to_status"]
    if frm is None:
        return f"{who} placed order #{number}" if who != "Restro (automatic)" else f"{who} created order #{number}"
    if frm == to:
        return f"{who} changed order #{number}"
    if to == "CANCELLED":
        return f"{who} cancelled order #{number}"
    if ev["reason"] == "auto_served":
        return f"{who} marked order #{number} served (no one did after 10 min)"
    return f"{who} moved order #{number} from {catalog.STATUS_LABELS.get(frm, frm)} to {catalog.STATUS_LABELS.get(to, to)}"


async def order_history(p: Principal, order_id: str) -> dict[str, Any]:
    try:
        order_id = str(uuid.UUID(order_id))
    except ValueError as exc:
        raise NotFound() from exc
    async with conn() as c:
        head = await history_repo.order_header(c, p.cafe_id, order_id)
        if head is None:
            raise NotFound("Order not found in this café.")
        status_events = await history_repo.events(c, p.cafe_id, order_id)
        escalations = await history_repo.escalations(c, p.cafe_id, order_id)
        rows = await audit_log.for_order(c, p.cafe_id, order_id, head["table_session_id"], head["created_at"],
                                         limit=HISTORY_CAP)
        n = await _names(c, p.cafe_id, rows, extra_customers=[head["customer_id"]] if head["customer_id"] else None)
    number = int(head["number"])
    customer_first = None if head["customer_deleted"] else catalog.first_name(head["customer_name"])
    timeline: list[tuple[datetime, int, dict[str, Any]]] = []
    for ev in status_events:
        is_customer = ev["actor_type"] == "customer"
        who = _who(customer_first, bool(head["customer_deleted"])) if is_customer else "Restro (automatic)"
        timeline.append((ev["created_at"], 0, {
            "at": ev["created_at"].isoformat(), "kind": "status", "severity": "info", "severity_reason": None,
            "sentence": _status_sentence(who, number, ev),
            "actor": {"type": ev["actor_type"], "name": who if is_customer else "Restro (automatic)", "role": None},
            "from_status": ev["from_status"], "to_status": ev["to_status"], "audit_id": None, "details": []}))
    for es in escalations:
        text = ESCALATION_TEXT.get(int(es["level"]), "Escalated")
        timeline.append((es["sent_at"], 1, {
            "at": es["sent_at"].isoformat(), "kind": "escalation", "severity": "warning", "severity_reason": text,
            "sentence": f"Order #{number}: {text[0].lower()}{text[1:]}",
            "actor": {"type": "system", "name": "Restro (automatic)", "role": None},
            "from_status": None, "to_status": None, "audit_id": None, "details": []}))
    for r in rows:
        frm, to = r.change.get("from"), r.change.get("to")
        on_order = r.action.startswith("order.")
        timeline.append((r.at, 2, {
            "at": r.at.isoformat(), "kind": "action", "severity": r.severity, "severity_reason": _reason(r),
            "sentence": catalog.describe(r, n),
            "actor": {"type": catalog.actor_kind(r), "name": catalog.actor_label(r, n), "role": r.actor_role},
            "from_status": frm if on_order and frm in catalog.STATUS_LABELS else None,
            "to_status": to if on_order and to in catalog.STATUS_LABELS else None,
            "audit_id": r.id, "details": [{"label": k, "value": v} for k, v in catalog.details(r, n)]}))
    timeline.sort(key=lambda t: (t[0], t[1]))
    log.info("audit_read", cafe_id=p.cafe_id, what="order_history", rows=len(timeline), filters=["order_id"], q_present=False)
    return {
        "order": {
            "id": head["id"], "number": number, "status": head["status"], "source": head["source"],
            "table": head["table_label"],
            "customer": {"id": head["customer_id"], "name": customer_first} if head["customer_id"] else None,
            "created_at": head["created_at"].isoformat(), "total_paise": int(head["total_paise"]),
            "cancel_reason_code": head["cancel_reason_code"], "cancel_reason_text": head["cancel_reason_text"],
            "cancelled_by_name": head["cancelled_by_name"],
        },
        "events": [e for _, _, e in timeline][:HISTORY_CAP],
    }
