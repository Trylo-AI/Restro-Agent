"""Audit catalogue: labels, categories, severity rules and sentences (docs/retention-audit-recommend.md#feature-2-audit-log).

`classify` is pure and reads only stored fields, so the severity written by `audit.write` can be recomputed at read
time for the reason text (the stored severity wins, decision 19). Rules apply top to bottom; the first match wins.
Restro support (or a platform_admin) is floored at `warning`. Customers appear by first name only (D21); money goes
through `rupees()`, which matches the console's `inr()`.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.db.repositories.audit_log import AuditRow

Severity = Literal["critical", "warning", "info"]
Category = Literal["orders", "calls", "chats", "menu", "customers", "campaigns", "offers", "team", "settings", "platform",
                   "other"]

SEVERITIES: tuple[Severity, ...] = ("critical", "warning", "info")
RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}
LARGE_DISCOUNT_PAISE = 50_000
LONG_DELAY_MINUTES = 15


@dataclass(frozen=True)
class Spec:
    label: str
    category: Category
    max_severity: Severity
    rule: str


@dataclass(frozen=True)
class Classified:
    severity: Severity
    reason: str | None


CATEGORY_LABELS: dict[Category, str] = {
    "orders": "Orders", "calls": "Calls and bills", "chats": "Chats", "menu": "Menu", "customers": "Customers",
    "campaigns": "Campaigns", "offers": "Offers", "team": "Team", "settings": "Settings", "platform": "Restro support",
    "other": "Other",
}

ROLE_LABELS = {"shift_manager": "Shift manager", "manager": "Manager", "owner": "Owner", "platform_admin": "Restro support",
               "staff_via_ai": "AI (from a staff comment)", "system": "Automatic"}

# D28a: filter-only actions, never written. Each is `order.transition` with that `change.to`.
FILTER_ALIASES = {"order.accept": ("order.transition", "ACCEPTED"), "order.ready": ("order.transition", "READY"),
                  "order.serve": ("order.transition", "SERVED")}

CATALOG: dict[str, Spec] = {
    "order.accept": Spec("Order accepted", "orders", "info", "Filter only: a status change to Accepted"),
    "order.ready": Spec("Order marked ready", "orders", "info", "Filter only: a status change to Ready"),
    "order.serve": Spec("Order served", "orders", "info", "Filter only: a status change to Served"),
    "order.transition": Spec("Order status changed", "orders", "info", "Always info"),
    "order.cancel": Spec("Order cancelled", "orders", "critical", "Every staff cancel is critical"),
    "order.undo": Spec("Order change undone", "orders", "warning", "Always a warning: the status was reversed"),
    "order.delay": Spec("Order delayed", "orders", "warning", f"Warning from {LONG_DELAY_MINUTES} minutes"),
    "order.manual": Spec("Manual order added", "orders", "info", "Always info"),
    "notification.ack": Spec("Notifications read", "orders", "info", "Always info"),
    "request.ack": Spec("Call acknowledged", "calls", "info", "Always info"),
    "request.resolve": Spec("Call resolved", "calls", "info", "Always info"),
    "bill.deliver": Spec("Bill delivered", "calls", "info", "Always info"),
    "chat.takeover": Spec("Chat taken over", "chats", "warning", "Warning when taken from a teammate"),
    "chat.reply": Spec("Staff reply sent", "chats", "info", "Always info"),
    "chat.handback": Spec("Chat handed back to the AI", "chats", "info", "Always info"),
    "menu.availability": Spec("Sold out / back on", "menu", "warning", "Warning when open orders contain the dish"),
    "menu.update": Spec("Menu item added or edited", "menu", "critical",
                        "Critical when an allergen is marked absent; warning for other allergen or price changes"),
    "menu.import": Spec("Menu imported", "menu", "warning", "Always a warning: bulk change"),
    "ordering.pause": Spec("WhatsApp ordering paused", "settings", "warning", "Always a warning: customers can't order"),
    "ordering.resume": Spec("WhatsApp ordering resumed", "settings", "info", "Always info"),
    "settings.update": Spec("Settings changed", "settings", "warning", "Warning when the alert number changes"),
    "table.update": Spec("Table added or edited", "settings", "info", "Always info"),
    "audit.export": Spec("Audit log exported", "settings", "info", "Always info"),
    "team.invite": Spec("Team member invited", "team", "critical", "Critical for a new owner"),
    "team.update": Spec("Team member changed", "team", "critical",
                        "Critical when the access level changes; warning on deactivation"),
    "customer.note": Spec("Profile comment added", "customers", "info", "Always info"),
    "customer.profile_update": Spec("Customer profile changed", "customers", "critical", "Critical when an allergy is removed"),
    "customer.profile_undo": Spec("Profile change undone", "customers", "critical", "Critical when the undo removes an allergy"),
    "customer.delete": Spec("Customer data deleted", "customers", "critical", "Always critical: irreversible"),
    "consent.export": Spec("Consent register exported", "customers", "critical", "Always critical: the customer list left the console"),
    "campaign.draft": Spec("Campaign draft saved", "campaigns", "info", "Always info"),
    "campaign.schedule": Spec("Campaign scheduled", "campaigns", "warning", "Always a warning: marketing messages will be sent"),
    "campaign.cancel": Spec("Campaign cancelled", "campaigns", "warning", "Always a warning"),
    "campaign.export": Spec("Campaign recipients exported", "campaigns", "warning", "Always a warning: the list left the console"),
    "offer.redeem": Spec("Offer redeemed", "offers", "critical",
                         f"Warning from a {LARGE_DISCOUNT_PAISE // 100} rupee discount; critical with an override"),
    "offer.void": Spec("Redemption reversed (reserved)", "offers", "critical", "Always critical"),
    "support.open": Spec("Restro support opened your console", "platform", "warning", "Always a warning"),
    "support.exit": Spec("Restro support closed your console", "platform", "warning", "Always a warning"),
    "cafe.create": Spec("Café created", "platform", "info", "Info (Restro support is at least a warning)"),
    "cafe.checklist": Spec("Launch checklist updated", "platform", "info", "Info (Restro support is at least a warning)"),
    "template.sync": Spec("WhatsApp templates synced", "platform", "info", "Info (Restro support is at least a warning)"),
    "dead_letter.retry": Spec("Failed message retried", "platform", "info", "Info (Restro support is at least a warning)"),
    "dead_letter.resolve": Spec("Failed message resolved", "platform", "info", "Info (Restro support is at least a warning)"),
}

SUPPORT_REASON = "Restro support acted in your café"
STATUS_LABELS = {"NEW": "New", "ACCEPTED": "Accepted", "READY": "Ready", "SERVED": "Served", "CANCELLED": "Cancelled"}
CANCEL_REASONS = {"out_of_stock": "Out of stock", "kitchen_issue": "Kitchen issue", "customer_asked": "Customer asked",
                  "other": "Other", "customer": "Customer cancelled"}
REQUEST_KINDS = {"help": "a call for help", "bill": "a bill request", "allergy": "an allergy question",
                 "low_rating": "a low rating"}
SETTING_LABELS = {"opening_hours": "opening hours", "cutoff_minutes": "last-order cutoff", "tone": "tone",
                  "feedback_delay_minutes": "feedback delay", "alert_phone": "alert number"}
STAFF_ROLES = ("shift_manager", "manager", "owner")
_UUID = re.compile(r"^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)


def spec(action: str) -> Spec:
    return CATALOG.get(action) or Spec(action, "other", "info", "Not in the catalogue")


def rupees(paise: int) -> str:
    """Same output as the console's `inr()`: 124000 → "₹ 1,240", 12345 → "₹ 123.45", 10000000 → "₹ 1,00,000"."""
    sign = "-" if paise < 0 else ""
    whole, rem = divmod(abs(int(paise)), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])
    return f"₹ {sign}{s}" + (f".{rem:02d}" if rem else "")


def as_uuid(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and _UUID.match(value) else None


def subjects(change: dict[str, Any]) -> dict[str, str | None]:
    """The filter columns copied from `change` (decision 19): only valid UUIDs, never a guess."""
    return {k: as_uuid(change.get(k)) for k in ("order_id", "customer_id", "conversation_id")}


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return 0


def _ops(change: dict[str, Any]) -> list[dict[str, Any]]:
    ops = change.get("ops")
    return [o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else []


def _rule(action: str, actor_role: str, change: dict[str, Any], actor_id: str | None) -> Classified:
    info = Classified("info", None)
    if action == "order.cancel":
        late = change.get("from") in ("ACCEPTED", "READY")
        return Classified("critical", "Cancelled after the kitchen accepted it" if late else "Cancelled by staff")
    if action == "order.undo":
        return Classified("warning", "Status was reversed")
    if action == "order.delay":
        return Classified("warning", "Long delay") if _int(change.get("minutes")) >= LONG_DELAY_MINUTES else info
    if action == "chat.takeover":
        taken_from = change.get("from_assigned_to")
        return Classified("warning", "Took the chat from a teammate") if taken_from and taken_from != actor_id else info
    if action == "menu.availability":
        if change.get("available") is False and _int(change.get("orders_flagged")) > 0:
            return Classified("warning", "Open orders contain this dish")
        return info
    if action == "menu.update":
        allergens = [a for a in change.get("allergens_changed") or [] if isinstance(a, dict)]
        if any(a.get("from") == "contains" and a.get("to") == "free_from" for a in allergens):
            return Classified("critical", "An allergen was marked absent")
        if allergens:
            return Classified("warning", "Allergens changed")
        if change.get("price_paise_from") is not None and change.get("price_paise_from") != change.get("price_paise_to"):
            return Classified("warning", "Price changed")
        if change.get("available_to") is False and _int(change.get("orders_flagged")) > 0:
            return Classified("warning", "Open orders contain this dish")
        return info
    if action == "menu.import":
        return Classified("warning", "Bulk change to prices and dishes")
    if action == "ordering.pause":
        return Classified("warning", "Customers can't order")
    if action == "settings.update":
        fields = change.get("fields")
        return Classified("warning", "Alert number changed") if isinstance(fields, list) and "alert_phone" in fields else info
    if action == "team.invite":
        return Classified("critical", "New owner") if change.get("role") == "owner" else info
    if action == "team.update":
        if "from_role" in change and change.get("from_role") != change.get("role"):
            return Classified("critical", "Access level changed")
        if change.get("active") is False:
            return Classified("warning", "Deactivated")
        if "from_role" not in change:
            return Classified("warning", "Access changed (details not recorded)")
        return info
    if action == "customer.profile_update":
        removed = any(o.get("kind") == "allergy" and (o.get("op") == "remove" or (o.get("op") == "set" and o.get("previous")))
                      for o in _ops(change))
        if removed:
            return Classified("critical", "AI removed an allergy from a staff comment" if actor_role == "staff_via_ai"
                              else "An allergy was removed")
        return info
    if action == "customer.profile_undo":
        if any(o.get("op") == "add" and o.get("kind") == "allergy" for o in _ops(change)):
            return Classified("critical", "Undo removed an allergy")
        return info
    if action == "customer.delete":
        return Classified("critical", "Irreversible deletion")
    if action == "consent.export":
        return Classified("critical", "Customer list left the console")
    if action == "campaign.schedule":
        return Classified("warning", "Marketing messages will be sent")
    if action == "campaign.cancel":
        return Classified("warning", "Campaign stopped")
    if action == "campaign.export":
        return Classified("warning", "Recipient list left the console (masked)")
    if action == "offer.redeem":
        if change.get("override") is True:
            return Classified("critical", "Redeemed with an override")
        if _int(change.get("discount_paise")) >= LARGE_DISCOUNT_PAISE:
            return Classified("warning", "Large discount")
        return info
    if action == "offer.void":
        return Classified("critical", "A redemption was reversed")
    if action in ("support.open", "support.exit"):
        return Classified("warning", SUPPORT_REASON)
    return info


def classify(action: str, actor_role: str, support: bool, change: dict[str, Any], *,
             actor_id: str | None = None) -> Classified:
    """Severity and reason from stored fields only. Restro support is at least a warning."""
    got = _rule(action, actor_role, change or {}, actor_id)
    if (support or actor_role == "platform_admin") and got.severity == "info":
        return Classified("warning", SUPPORT_REASON)
    return got


def actions_in(categories: Iterable[str]) -> list[str]:
    """Stored actions in these categories (filter-only aliases excluded: their rows are `order.transition`)."""
    wanted = set(categories)
    return [a for a, s in CATALOG.items() if s.category in wanted and a not in FILTER_ALIASES]


def _label_matcher(q: str) -> Callable[[Spec], bool] | None:
    """Matches `q` at the start of a word of the label or category label (case-insensitive): "served" finds
    "Order served", never "reserved"."""
    needle = q.strip()
    if not needle:
        return None
    pattern = re.compile(r"(?<!\w)" + re.escape(needle), re.IGNORECASE)
    return lambda s: bool(pattern.search(s.label) or pattern.search(CATEGORY_LABELS[s.category]))


def match_labels(q: str) -> list[str]:
    """Stored actions whose label or category label matches `q`."""
    matches = _label_matcher(q)
    return [a for a, s in CATALOG.items() if a not in FILTER_ALIASES and matches(s)] if matches else []


def match_aliases(q: str) -> list[tuple[str, str]]:
    """(action, change.to) of the filter-only aliases whose label matches `q`: the labels shown on accept, ready and
    serve rows ("Order accepted"), which are stored as `order.transition`."""
    matches = _label_matcher(q)
    return [FILTER_ALIASES[a] for a, s in CATALOG.items() if a in FILTER_ALIASES and matches(s)] if matches else []


def public() -> dict[str, Any]:
    """GET /audit/catalog (AuditCatalog)."""
    actions: list[dict[str, Any]] = []
    for action, s in CATALOG.items():
        entry: dict[str, Any] = {"action": action, "label": s.label, "category": s.category,
                                 "max_severity": s.max_severity, "rule": s.rule}
        if action in FILTER_ALIASES:
            entry["filter_only"] = True
        actions.append(entry)
    return {"actions": actions,
            "categories": [{"id": c, "label": label} for c, label in CATEGORY_LABELS.items()],
            "roles": [{"id": r, "label": label} for r, label in ROLE_LABELS.items()],
            "severities": list(SEVERITIES)}


# ── sentences ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CustomerName:
    first: str | None
    deleted: bool = False


@dataclass(frozen=True)
class OrderRef:
    number: int
    status: str
    cancel_reason_text: str | None = None


@dataclass
class Names:
    """Looked up in a few batched queries by the viewer; empty maps give the fallbacks."""
    staff: dict[str, str] = field(default_factory=dict)                  # user_id → display name
    customers: dict[str, CustomerName] = field(default_factory=dict)     # customer_id → first name
    conversations: dict[str, str] = field(default_factory=dict)          # conversation_id → customer_id
    orders: dict[str, OrderRef] = field(default_factory=dict)            # order_id → number, status, staff note
    tables: dict[str, str] = field(default_factory=dict)                 # table_session_id → label
    campaigns: dict[str, str] = field(default_factory=dict)              # campaign_id → name
    timezone: str = "Asia/Kolkata"                                       # cafes.timezone, for dates in details


def first_name(name: str | None) -> str | None:
    parts = (name or "").split()
    return parts[0] if parts else None


def actor_kind(row: AuditRow) -> str:
    """customer · staff · support · ai · system (OrderHistory actor.type)."""
    if row.support or row.actor_role == "platform_admin":
        return "support"
    if row.actor_role == "staff_via_ai":
        return "ai"
    if row.actor_role == "system":
        return "system"
    return "staff"


def staff_name(row: AuditRow, n: Names) -> str | None:
    return (n.staff.get(row.actor_id) if row.actor_id else None) or row.member_name or row.actor_name


def actor_label(row: AuditRow, n: Names) -> str:
    """The person column: a name, "Restro support", "AI (from Ravi's comments)" or "Restro (automatic)"."""
    kind = actor_kind(row)
    name = staff_name(row, n)
    if kind == "support":
        return "Restro support"
    if kind == "system":
        return "Restro (automatic)"
    if kind == "ai":
        return f"AI (from {name}'s comments)" if name else "AI (from comments)"
    return name or "A former team member"


def _prefix(row: AuditRow, n: Names) -> str:
    kind = actor_kind(row)
    name = staff_name(row, n)
    role = ROLE_LABELS.get(row.actor_role, row.actor_role)
    if kind == "support":
        return "Restro support"
    if kind == "system":
        return "Restro (automatic)"
    if kind == "ai":
        return f"AI, from {name}'s comment," if name else "AI, from a team member's comment,"
    return f"{name} ({role})" if name else f"A former team member ({role})"


def customer_id_of(row: AuditRow, n: Names) -> str | None:
    cid = row.customer_id or as_uuid(row.change.get("customer_id"))
    conv = row.conversation_id or as_uuid(row.change.get("conversation_id"))
    return cid or (n.conversations.get(conv) if conv else None)


def customer_label(row: AuditRow, n: Names) -> str:
    cid = customer_id_of(row, n)
    c = n.customers.get(cid) if cid else None
    if c is None:
        return "a guest"
    if c.deleted:
        return "a customer (data deleted)"
    return c.first or "a guest"


def _possessive(who: str) -> str:
    return f"the chat with {who}" if who.startswith("a ") else f"{who}'s chat"


def _profile(who: str) -> str:
    return f"the profile of {who}" if who.startswith("a ") else f"{who}'s profile"


def _order(row: AuditRow, n: Names) -> str:
    number = row.change.get("number")
    if number is None:
        oid = row.order_id or as_uuid(row.change.get("order_id"))
        ref = n.orders.get(oid) if oid else None
        number = ref.number if ref else None
    return f"order #{number}" if number is not None else "an order"


def _table(change: dict[str, Any], n: Names) -> str | None:
    if isinstance(change.get("table"), str):
        return str(change["table"])
    sid = as_uuid(change.get("table_session_id"))
    return n.tables.get(sid) if sid else None


def _status(s: object) -> str:
    return STATUS_LABELS.get(str(s), str(s))


def _quoted_ops(ops: list[dict[str, Any]]) -> list[str]:
    verbs = {"add": "added", "remove": "removed", "set": "set"}
    out = []
    for o in ops:
        kind = str(o.get("kind", "")).replace("_", " ")
        verb = verbs.get(str(o.get("op")), str(o.get("op")))
        out.append(f'{verb} {kind} to "{o.get("value")}"' if o.get("op") == "set" else f'{verb} {kind} "{o.get("value")}"')
    return out


def _allergen_changes(change: dict[str, Any]) -> list[str]:
    out = []
    for a in change.get("allergens_changed") or []:
        if isinstance(a, dict):
            out.append(f"{str(a.get('allergen', '')).replace('_', ' ')}: {str(a.get('from', '')).replace('_', ' ')} → "
                       f"{str(a.get('to', '')).replace('_', ' ')}")
    return out


def _menu_edit(c: dict[str, Any]) -> list[str]:
    parts = []
    if "price_paise_from" in c and c.get("price_paise_from") != c.get("price_paise_to") \
            and isinstance(c.get("price_paise_from"), int) and isinstance(c.get("price_paise_to"), int):
        parts.append(f"price {rupees(c['price_paise_from'])} → {rupees(c['price_paise_to'])}")
    if "available_from" in c and c.get("available_from") != c.get("available_to"):
        parts.append("sold out" if c.get("available_to") is False else "back on")
    allergens = _allergen_changes(c)
    if allergens:
        parts.append(f"allergens ({', '.join(allergens)})")
    return parts


def _phrase(row: AuditRow, n: Names) -> str:
    a, c = row.action, row.change
    if a == "order.transition":
        to = c.get("to")
        if to == "ACCEPTED":
            return f"accepted {_order(row, n)}"
        if to == "READY":
            return f"marked {_order(row, n)} ready"
        if to == "SERVED":
            return f"marked {_order(row, n)} served"
        return f"moved {_order(row, n)} from {_status(c.get('from'))} to {_status(to)}"
    if a == "order.cancel":
        was = f" (was {_status(c['from'])})" if c.get("from") else ""
        reason = CANCEL_REASONS.get(str(c.get("reason_code")))
        return f"cancelled {_order(row, n)}{was}" + (f" — {reason}" if reason else "")
    if a == "order.undo":
        return f"undid {_order(row, n)}: {_status(c.get('from'))} → {_status(c.get('to'))}"
    if a == "order.delay":
        return f"delayed {_order(row, n)} by {_int(c.get('minutes'))} min"
    if a == "order.manual":
        at_table = f" for table {c['table']}" if c.get("table") else ""
        total = f" ({rupees(c['total_paise'])})" if isinstance(c.get("total_paise"), int) else ""
        return f"added manual {_order(row, n)}{at_table}{total}"
    if a == "notification.ack":
        count = _int(c.get("count"))
        return "marked a notification read" if count == 1 else f"marked {count} notifications read"
    if a in ("request.ack", "request.resolve"):
        verb = "acknowledged" if a == "request.ack" else "resolved"
        table = _table(c, n)
        return f"{verb} {REQUEST_KINDS.get(str(c.get('kind')), 'a call')}" + (f" from table {table}" if table else "")
    if a == "bill.deliver":
        table = _table(c, n)
        out = f"delivered the bill for {table}" if table else "delivered a bill"
        if isinstance(c.get("total_paise"), int):
            out += f" — {rupees(c['total_paise'])}"
        if c.get("code"):
            out += f" (code {c['code']}, {rupees(_int(c.get('discount_paise')))} off)"
        return out
    who = customer_label(row, n)
    if a == "chat.takeover":
        via, taken_from = c.get("via"), c.get("from_assigned_to")
        if via == "table":
            table = _table(c, n)
            return f"opened {_possessive(who)}" + (f" from table {table}" if table else "")
        out = f"took over {_possessive(who)}"
        if taken_from and taken_from != row.actor_id:
            out += f" from {n.staff.get(taken_from) or 'a teammate'}"
        elif via == "reply":
            out += " to reply"
        return out
    if a == "chat.reply":
        return f"replied to {who} ({_int(c.get('chars'))} characters)"
    if a == "chat.handback":
        return f"handed {_possessive(who)} back to the AI"
    dish = c.get("name") or "a dish"
    if a == "menu.availability":
        if c.get("available") is False:
            flagged = _int(c.get("orders_flagged"))
            return f"marked {dish} sold out" + (f" ({flagged} open order{'s' if flagged != 1 else ''} flagged)" if flagged else "")
        return f"put {dish} back on"
    if a == "menu.update":
        if c.get("new"):
            return f"added {dish} to the menu"
        parts = _menu_edit(c)
        return f"edited {dish}" + (f": {', '.join(parts)}" if parts else "")
    if a == "menu.import":
        return f"imported the menu: {_int(c.get('created'))} new, {_int(c.get('updated'))} updated"
    if a == "ordering.pause":
        return "paused WhatsApp ordering"
    if a == "ordering.resume":
        return "resumed WhatsApp ordering"
    if a == "settings.update":
        fields = [SETTING_LABELS.get(str(f), str(f)) for f in c.get("fields") or []]
        return "changed settings" + (f": {', '.join(fields)}" if fields else "")
    if a == "table.update":
        return f"{'added' if c.get('new') else 'edited'} table {c.get('label') or ''}".rstrip()
    if a == "audit.export":
        return f"exported the audit log ({_int(c.get('rows'))} rows)"
    member = n.staff.get(str(c.get("user_id"))) if c.get("user_id") else None
    if a == "team.invite":
        return f"invited {member or 'a new team member'} as {ROLE_LABELS.get(str(c.get('role')), str(c.get('role')))}"
    if a == "team.update":
        target = member or "a team member"
        parts = []
        if "from_role" in c and c.get("from_role") != c.get("role"):
            parts.append(f"changed {target}'s role: {ROLE_LABELS.get(str(c['from_role']), str(c['from_role']))} → "
                         f"{ROLE_LABELS.get(str(c.get('role')), str(c.get('role')))}")
        if c.get("from_active") is True and c.get("active") is False:
            parts.append(f"deactivated {target}" if not parts else "deactivated them")
        elif c.get("from_active") is False and c.get("active") is True:
            parts.append(f"reactivated {target}" if not parts else "reactivated them")
        if not parts:
            role = ROLE_LABELS.get(str(c.get("role")), str(c.get("role")))
            return f"updated {target}'s access ({role}, {'active' if c.get('active') is not False else 'inactive'})"
        return " and ".join(parts)
    if a == "customer.note":
        return f"added a comment on {_profile(who)}"
    if a == "customer.profile_update":
        ops = _ops(c)
        said = _quoted_ops(ops)
        if not said:
            return f"changed {_profile(who)}"
        if all(o.get("op") == "remove" for o in ops):
            return f"{', '.join(said)} from {_profile(who)}"
        if all(o.get("op") == "add" for o in ops):
            return f"{', '.join(said)} to {_profile(who)}"
        return f"changed {_profile(who)}: {', '.join(said)}"
    if a == "customer.profile_undo":
        reversed_ops = [{**o, "op": {"add": "remove", "remove": "add"}.get(str(o.get("op")), o.get("op"))}
                        for o in _ops(c) if o.get("op") in ("add", "remove")]
        said = _quoted_ops(reversed_ops)
        return f"undid a comment's changes on {_profile(who)}" + (f" ({', '.join(said)})" if said else "")
    if a == "customer.delete":
        return "deleted a customer's data"
    if a == "consent.export":
        return f"exported the consent register ({_int(c.get('rows'))} rows)"
    campaign = n.campaigns.get(str(c.get("campaign_id"))) if c.get("campaign_id") else None
    named = f'"{campaign}"' if campaign else "a campaign"
    if a == "campaign.draft":
        return f"saved a new campaign draft {named}" if c.get("new") else f"edited the campaign draft {named}"
    if a == "campaign.schedule":
        return f"scheduled campaign {named}" + (f" for {_int(c.get('audience'))} customers" if "audience" in c else "")
    if a == "campaign.cancel":
        return f"cancelled campaign {named}"
    if a == "campaign.export":
        return f"exported the recipients of {named}" + (f" ({_int(c.get('rows'))} rows, masked)" if "rows" in c else " (masked)")
    if a == "offer.redeem":
        code = f"code {c['code']}" if c.get("code") else "a code"
        target = f" for {who}" if customer_id_of(row, n) else ""
        if c.get("channel") == "counter":
            where = " at the counter"
        else:
            table = _table(c, n)
            where = f" on {table}'s bill" if table else " on a table bill"
        pct = f" ({c['discount_percent']}%)" if isinstance(c.get("discount_percent"), int) else ""
        return f"redeemed {code}{target}{where} — {rupees(_int(c.get('discount_paise')))} off{pct}"
    if a == "offer.void":
        return "reversed a redemption" + (f" (code {c['code']})" if c.get("code") else "")
    if a == "support.open":
        return "opened your console"
    if a == "support.exit":
        return "closed your console"
    if a in CATALOG:
        return CATALOG[a].label[0].lower() + CATALOG[a].label[1:]
    return f"did {a}"


def describe(row: AuditRow, n: Names) -> str:
    """One plain sentence: actor first, customers by first name, money as ₹."""
    return f"{_prefix(row, n)} {_phrase(row, n)}"


def details(row: AuditRow, n: Names) -> list[tuple[str, str]]:
    """Label/value pairs for the expanded row. Never a phone number or message text."""
    c, out = row.change, []
    if row.action == "order.cancel":
        if CANCEL_REASONS.get(str(c.get("reason_code"))):
            out.append(("Reason", CANCEL_REASONS[str(c["reason_code"])]))
        oid = row.order_id or as_uuid(c.get("order_id"))
        ref = n.orders.get(oid) if oid else None
        if ref and ref.cancel_reason_text:
            out.append(("Staff note", ref.cancel_reason_text))
    if row.action.startswith("order.") and c.get("from") and c.get("to"):
        out.append(("Was / Now", f"{_status(c['from'])} → {_status(c['to'])}"))
    if isinstance(c.get("subtotal_paise"), int):
        out.append(("Bill total", rupees(c["subtotal_paise"])))
    elif isinstance(c.get("bill_paise"), int):
        out.append(("Bill total", rupees(c["bill_paise"])))
    if isinstance(c.get("discount_paise"), int) and (c.get("discount_paise") or row.action == "offer.redeem"):
        out.append(("Discount", rupees(c["discount_paise"])))
    if row.action in ("menu.update", "settings.update") and c.get("fields"):
        labels = [SETTING_LABELS.get(str(f), str(f).replace("_", " ")) for f in c["fields"]]
        out.append(("Fields changed", ", ".join(labels)))
    allergens = _allergen_changes(c)
    if allergens:
        out.append(("Allergen changes", "; ".join(allergens)))
    if row.action == "chat.takeover":
        if c.get("from_assigned_to"):
            out.append(("Taken from", n.staff.get(str(c["from_assigned_to"])) or "a teammate"))
        if c.get("via"):
            out.append(("Via", {"takeover": "Take over", "reply": "A staff reply", "table": "Message the table"}
                        .get(str(c["via"]), str(c["via"]))))
    table = _table(c, n)
    if table:
        out.append(("Table", table))
    if c.get("campaign_id"):
        out.append(("Campaign", n.campaigns.get(str(c["campaign_id"])) or "A campaign"))
    if c.get("code"):
        out.append(("Code", str(c["code"])))
    if "audience" in c:
        out.append(("Audience", f"{_int(c['audience'])} customers"))
    if isinstance(c.get("scheduled_at"), str):
        try:
            when = datetime.fromisoformat(c["scheduled_at"]).astimezone(ZoneInfo(n.timezone))
            out.append(("Scheduled for", when.strftime("%d %b %Y, %H:%M")))
        except ValueError:
            pass
    return out

