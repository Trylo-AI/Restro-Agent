"""Audit catalogue (docs/rules.md#unit-and-integration, Audit row): every written action is catalogued, severities
follow the plan's table, sentences read as the plan writes them, and `change` never holds a phone, email or text."""

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.db.repositories.audit_log import AuditRow
from app.domain import audit
from app.domain.audit import catalog
from app.domain.audit.catalog import RANK, CustomerName, Names, OrderRef

APP = Path(__file__).resolve().parents[2] / "app"
PRIYA, RAVI, ARJUN = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
RIYA, ORDER, SESSION, CONVO = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
ROLES = ("shift_manager", "manager", "owner")

PLAN_ACTIONS = {
    "order.transition", "order.accept", "order.ready", "order.serve", "order.cancel", "order.undo", "order.delay",
    "order.manual", "notification.ack", "request.ack", "request.resolve", "bill.deliver", "chat.takeover", "chat.reply",
    "chat.handback", "menu.availability", "menu.update", "menu.import", "ordering.pause", "ordering.resume",
    "settings.update", "table.update", "audit.export", "team.invite", "team.update", "customer.note",
    "customer.profile_update", "customer.profile_undo", "customer.delete", "consent.export", "campaign.draft",
    "campaign.schedule", "campaign.cancel", "campaign.export", "offer.redeem", "offer.void", "support.open",
    "support.exit", "cafe.create", "cafe.checklist", "template.sync", "dead_letter.retry", "dead_letter.resolve",
}


def written_actions() -> set[str]:
    """The `action` argument (4th) of every `audit.write(...)` in backend/app, both branches of a conditional."""
    found: set[str] = set()

    def literals(node: ast.expr) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.IfExp):
            return literals(node.body) + literals(node.orelse)
        raise AssertionError(f"audit.write action is not a literal: {ast.unparse(node)}")

    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "audit"):
                assert len(node.args) >= 4, f"{path}: audit.write needs (conn, cafe_id, actor, action, change)"
                found.update(literals(node.args[3]))
    return found


def test_every_written_action_is_in_catalog() -> None:
    written = written_actions()
    assert {"order.cancel", "order.transition", "chat.takeover", "offer.redeem", "menu.update"} <= written
    missing = written - set(catalog.CATALOG)
    assert not missing, f"add these to domain/audit/catalog.py: {sorted(missing)}"
    assert not written & set(catalog.FILTER_ALIASES), "filter-only actions are never written"


def test_catalog_specs_have_label_category_and_max_severity() -> None:
    assert PLAN_ACTIONS <= set(catalog.CATALOG)
    for action, spec in catalog.CATALOG.items():
        assert spec.label and spec.rule, action
        assert spec.category in catalog.CATEGORY_LABELS and spec.category != "other", action
        assert spec.max_severity in catalog.SEVERITIES, action
    assert catalog.spec("something.new").category == "other"


SAMPLES: list[tuple[str, dict[str, Any]]] = [
    ("order.cancel", {"from": "ACCEPTED"}), ("order.cancel", {"from": "NEW"}), ("order.undo", {}),
    ("order.delay", {"minutes": 20}), ("order.delay", {"minutes": 5}), ("chat.takeover", {"from_assigned_to": ARJUN}),
    ("menu.availability", {"available": False, "orders_flagged": 2}),
    ("menu.update", {"allergens_changed": [{"allergen": "milk", "from": "contains", "to": "free_from"}]}),
    ("menu.update", {"price_paise_from": 100, "price_paise_to": 200}), ("menu.import", {}), ("ordering.pause", {}),
    ("settings.update", {"fields": ["alert_phone"]}), ("team.invite", {"role": "owner"}),
    ("team.update", {"from_role": "manager", "role": "owner"}), ("team.update", {"from_role": "owner", "role": "owner", "active": False}),
    ("customer.profile_update", {"ops": [{"op": "remove", "kind": "allergy", "value": "peanuts"}]}),
    ("customer.profile_undo", {"ops": [{"op": "add", "kind": "allergy", "value": "peanuts"}]}),
    ("customer.delete", {}), ("consent.export", {}), ("campaign.schedule", {}), ("campaign.cancel", {}),
    ("campaign.export", {}), ("offer.redeem", {"discount_paise": 60_000}), ("offer.redeem", {"override": True}),
    ("offer.void", {}), ("support.open", {}), ("support.exit", {}), ("order.transition", {"to": "ACCEPTED"}),
]


@pytest.mark.parametrize(("action", "change"), SAMPLES)
@pytest.mark.parametrize("role", (*ROLES, "staff_via_ai", "system"))
def test_classify_never_exceeds_max_severity(action: str, change: dict[str, Any], role: str) -> None:
    got = catalog.classify(action, role, False, change, actor_id=PRIYA)
    assert RANK[got.severity] <= RANK[catalog.CATALOG[action].max_severity]
    assert (got.reason is None) == (got.severity == "info")


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("frm", ("NEW", "ACCEPTED", "READY", None))
def test_order_cancel_critical_for_every_role_and_from_status(role: str, frm: str | None) -> None:
    assert catalog.classify("order.cancel", role, False, {"from": frm} if frm else {}).severity == "critical"


def test_order_cancel_after_accept_reason_mentions_kitchen() -> None:
    assert catalog.classify("order.cancel", "manager", False, {"from": "ACCEPTED"}).reason == "Cancelled after the kitchen accepted it"
    assert catalog.classify("order.cancel", "manager", False, {"from": "READY"}).reason == "Cancelled after the kitchen accepted it"
    assert catalog.classify("order.cancel", "shift_manager", False, {"from": "NEW"}).reason == "Cancelled by staff"


def test_order_undo_is_warning() -> None:
    assert catalog.classify("order.undo", "shift_manager", False, {"from": "ACCEPTED", "to": "NEW"}) == \
        catalog.Classified("warning", "Status was reversed")


def test_order_delay_warning_from_15_minutes() -> None:
    assert catalog.classify("order.delay", "manager", False, {"minutes": 14}).severity == "info"
    assert catalog.classify("order.delay", "manager", False, {"minutes": 15}) == catalog.Classified("warning", "Long delay")


def test_menu_availability_off_with_flagged_orders_is_warning() -> None:
    got = catalog.classify("menu.availability", "shift_manager", False, {"available": False, "orders_flagged": 1})
    assert got == catalog.Classified("warning", "Open orders contain this dish")


def test_menu_availability_off_without_orders_is_info() -> None:
    assert catalog.classify("menu.availability", "shift_manager", False, {"available": False, "orders_flagged": 0}).severity == "info"
    assert catalog.classify("menu.availability", "shift_manager", False, {"available": True, "orders_flagged": 0}).severity == "info"


def test_menu_update_price_change_is_warning() -> None:
    got = catalog.classify("menu.update", "manager", False, {"new": False, "price_paise_from": 12000, "price_paise_to": 15000})
    assert got == catalog.Classified("warning", "Price changed")
    new = catalog.classify("menu.update", "manager", False, {"new": True, "price_paise_from": None, "price_paise_to": 15000})
    assert new.severity == "info"  # a new dish isn't a price change


def test_menu_update_contains_to_free_from_is_critical() -> None:
    change = {"price_paise_from": 100, "price_paise_to": 200,
              "allergens_changed": [{"allergen": "milk", "from": "contains", "to": "free_from"}]}
    assert catalog.classify("menu.update", "manager", False, change) == catalog.Classified("critical", "An allergen was marked absent")
    other = {"allergens_changed": [{"allergen": "milk", "from": "unknown", "to": "contains"}]}
    assert catalog.classify("menu.update", "manager", False, other).severity == "warning"


def test_menu_import_is_warning() -> None:
    assert catalog.classify("menu.import", "owner", False, {"rows": 3}).severity == "warning"


def test_team_update_role_change_is_critical() -> None:
    change = {"from_role": "shift_manager", "role": "manager", "from_active": True, "active": True}
    assert catalog.classify("team.update", "owner", False, change) == catalog.Classified("critical", "Access level changed")


def test_team_update_deactivate_is_warning() -> None:
    change = {"from_role": "manager", "role": "manager", "from_active": True, "active": False}
    assert catalog.classify("team.update", "owner", False, change) == catalog.Classified("warning", "Deactivated")
    assert catalog.classify("team.update", "owner", False, {"role": "manager", "active": True}).severity == "warning"  # legacy


def test_team_update_reactivate_is_info() -> None:
    change = {"from_role": "manager", "role": "manager", "from_active": False, "active": True}
    assert catalog.classify("team.update", "owner", False, change).severity == "info"


def test_team_invite_owner_is_critical() -> None:
    assert catalog.classify("team.invite", "owner", False, {"role": "owner"}) == catalog.Classified("critical", "New owner")
    assert catalog.classify("team.invite", "owner", False, {"role": "manager"}).severity == "info"


def test_profile_update_allergy_removal_is_critical_including_staff_via_ai() -> None:
    change = {"ops": [{"op": "remove", "kind": "allergy", "value": "peanuts"}]}
    assert catalog.classify("customer.profile_update", "manager", False, change) == \
        catalog.Classified("critical", "An allergy was removed")
    assert catalog.classify("customer.profile_update", "staff_via_ai", False, change) == \
        catalog.Classified("critical", "AI removed an allergy from a staff comment")
    added = {"ops": [{"op": "add", "kind": "allergy", "value": "peanuts"}]}
    assert catalog.classify("customer.profile_update", "staff_via_ai", False, added).severity == "info"


def test_profile_undo_removing_allergy_is_critical() -> None:
    change = {"ops": [{"op": "add", "kind": "allergy", "value": "peanuts"}]}
    assert catalog.classify("customer.profile_undo", "manager", False, change) == \
        catalog.Classified("critical", "Undo removed an allergy")
    assert catalog.classify("customer.profile_undo", "manager", False, {"ops": [{"op": "add", "kind": "like", "value": "tea"}]}).severity == "info"


def test_offer_redeem_large_discount_warning() -> None:
    assert catalog.classify("offer.redeem", "shift_manager", False, {"discount_paise": 49_999}).severity == "info"
    assert catalog.classify("offer.redeem", "shift_manager", False, {"discount_paise": 50_000}) == \
        catalog.Classified("warning", "Large discount")


def test_offer_redeem_override_critical() -> None:
    assert catalog.classify("offer.redeem", "manager", False, {"override": True, "discount_paise": 100}).severity == "critical"


def test_customer_delete_and_consent_export_are_critical() -> None:
    assert catalog.classify("customer.delete", "owner", False, {}) == catalog.Classified("critical", "Irreversible deletion")
    assert catalog.classify("consent.export", "owner", False, {"rows": 3}) == \
        catalog.Classified("critical", "Customer list left the console")


def test_campaign_export_is_warning() -> None:
    assert catalog.classify("campaign.export", "owner", False, {"rows": 3}) == \
        catalog.Classified("warning", "Recipient list left the console (masked)")


def test_settings_alert_phone_change_is_warning() -> None:
    assert catalog.classify("settings.update", "owner", False, {"fields": ["tone", "alert_phone"]}) == \
        catalog.Classified("warning", "Alert number changed")
    assert catalog.classify("settings.update", "owner", False, {"fields": ["tone"]}).severity == "info"


def test_chat_takeover_from_teammate_is_warning() -> None:
    assert catalog.classify("chat.takeover", "shift_manager", False, {"from_assigned_to": ARJUN}, actor_id=PRIYA) == \
        catalog.Classified("warning", "Took the chat from a teammate")
    assert catalog.classify("chat.takeover", "shift_manager", False, {"from_assigned_to": None}, actor_id=PRIYA).severity == "info"
    assert catalog.classify("chat.takeover", "shift_manager", False, {"from_assigned_to": PRIYA}, actor_id=PRIYA).severity == "info"


def test_support_actor_floors_at_warning() -> None:
    assert catalog.classify("table.update", "owner", True, {}) == catalog.Classified("warning", catalog.SUPPORT_REASON)
    assert catalog.classify("cafe.create", "platform_admin", False, {}).severity == "warning"
    assert catalog.classify("order.cancel", "platform_admin", True, {"from": "NEW"}).severity == "critical"  # never lowered


def test_unknown_action_is_info_other() -> None:
    assert catalog.classify("thing.happened", "owner", False, {}).severity == "info"
    assert catalog.spec("thing.happened").category == "other"
    assert catalog.spec("thing.happened").label == "thing.happened"


# ── sentences ─────────────────────────────────────────────────────────────


def row(action: str, change: dict[str, Any], *, role: str = "shift_manager", actor: str | None = PRIYA,
        support: bool = False, actor_name: str | None = None) -> AuditRow:
    s = catalog.subjects(change)
    return AuditRow(id=str(uuid.uuid4()), at=datetime(2026, 11, 1, 12, 41, tzinfo=UTC), actor_id=actor, actor_role=role,
                    support=support, action=action, change=change, severity="info", order_id=s["order_id"],
                    customer_id=s["customer_id"], conversation_id=s["conversation_id"], actor_name=actor_name)


NAMES = Names(staff={PRIYA: "Priya", RAVI: "Ravi", ARJUN: "Arjun"}, customers={RIYA: CustomerName("Riya")},
              orders={ORDER: OrderRef(42, "CANCELLED", "Ran out of oat milk")}, tables={SESSION: "T4"},
              conversations={CONVO: RIYA})
SENTENCES: list[tuple[AuditRow, str]] = [
    (row("order.cancel", {"order_id": ORDER, "number": 42, "from": "ACCEPTED", "to": "CANCELLED", "reason_code": "out_of_stock"}),
     "Priya (Shift manager) cancelled order #42 (was Accepted) — Out of stock"),
    (row("order.transition", {"order_id": ORDER, "number": 42, "from": "NEW", "to": "ACCEPTED"}),
     "Priya (Shift manager) accepted order #42"),
    (row("order.undo", {"order_id": ORDER, "number": 42, "from": "ACCEPTED", "to": "NEW"}),
     "Priya (Shift manager) undid order #42: Accepted → New"),
    (row("order.delay", {"order_id": ORDER, "number": 42, "minutes": 10, "has_note": False}),
     "Priya (Shift manager) delayed order #42 by 10 min"),
    (row("order.manual", {"order_id": ORDER, "number": 42, "table": "T4", "total_paise": 54000}),
     "Priya (Shift manager) added manual order #42 for table T4 (₹ 540)"),
    (row("chat.takeover", {"conversation_id": CONVO, "customer_id": RIYA, "via": "takeover", "from_mode": "human",
                           "from_assigned_to": ARJUN}),
     "Priya (Shift manager) took over Riya's chat from Arjun"),
    (row("chat.takeover", {"conversation_id": CONVO, "customer_id": RIYA, "via": "table", "from_mode": "bot",
                           "from_assigned_to": None, "table_session_id": SESSION}),
     "Priya (Shift manager) opened Riya's chat from table T4"),
    (row("chat.reply", {"conversation_id": CONVO, "customer_id": RIYA, "chars": 84}),
     "Priya (Shift manager) replied to Riya (84 characters)"),
    (row("bill.deliver", {"table_session_id": SESSION, "total_paise": 124000, "orders": [42], "subtotal_paise": 136000,
                          "discount_paise": 12000, "code": "DIWALI7KQ"}),
     "Priya (Shift manager) delivered the bill for T4 — ₹ 1,240 (code DIWALI7KQ, ₹ 120 off)"),
    (row("menu.update", {"item_id": str(uuid.uuid4()), "name": "Masala Chai", "new": False, "fields": ["price_paise"],
                         "price_paise_from": 12000, "price_paise_to": 15000, "available_from": True, "available_to": True,
                         "allergens_changed": [{"allergen": "milk", "from": "contains", "to": "free_from"}]}, role="manager"),
     "Priya (Manager) edited Masala Chai: price ₹ 120 → ₹ 150, allergens (milk: contains → free from)"),
    (row("team.update", {"membership_id": str(uuid.uuid4()), "user_id": RAVI, "from_role": "shift_manager",
                         "role": "manager", "from_active": True, "active": True}, role="owner"),
     "Priya (Owner) changed Ravi's role: Shift manager → Manager"),
    (row("customer.profile_update", {"customer_id": RIYA, "ops": [{"op": "remove", "kind": "allergy", "value": "peanuts"}]},
         role="manager"),
     'Priya (Manager) removed allergy "peanuts" from Riya\'s profile'),
    (row("offer.redeem", {"table_session_id": SESSION, "order_id": ORDER, "order_number": 42, "customer_id": RIYA,
                          "code": "DIWALI7KQ", "channel": "table_bill", "discount_percent": 10, "bill_paise": 120000,
                          "discount_paise": 12000}),
     "Priya (Shift manager) redeemed code DIWALI7KQ for Riya on T4's bill — ₹ 120 off (10%)"),
    (row("offer.redeem", {"table_session_id": None, "order_id": None, "customer_id": RIYA, "code": "DIWALI7KQ",
                          "channel": "counter", "discount_percent": None, "bill_paise": 120000, "discount_paise": 12000}),
     "Priya (Shift manager) redeemed code DIWALI7KQ for Riya at the counter — ₹ 120 off"),
    (row("audit.export", {"rows": 312, "filters": []}, role="owner"), "Priya (Owner) exported the audit log (312 rows)"),
]


@pytest.mark.parametrize(("audit_row", "sentence"), SENTENCES)
def test_describe_sentences(audit_row: AuditRow, sentence: str) -> None:
    assert catalog.describe(audit_row, NAMES) == sentence


def test_describe_fallbacks_deleted_customer_former_staff_support_ai_system() -> None:
    gone = str(uuid.uuid4())
    names = Names(staff={RAVI: "Ravi"}, customers={gone: CustomerName(None, deleted=True)})
    assert catalog.describe(row("chat.reply", {"customer_id": gone, "chars": 3}, actor=RAVI), names) == \
        "Ravi (Shift manager) replied to a customer (data deleted) (3 characters)"
    assert catalog.describe(row("chat.handback", {"customer_id": str(uuid.uuid4())}, actor=RAVI), names) == \
        "Ravi (Shift manager) handed the chat with a guest back to the AI"
    former = row("order.delay", {"number": 7, "minutes": 5}, actor=str(uuid.uuid4()))
    assert catalog.describe(former, names) == "A former team member (Shift manager) delayed order #7 by 5 min"
    snapshot = row("order.delay", {"number": 7, "minutes": 5}, actor=str(uuid.uuid4()), actor_name="Sam")
    assert catalog.describe(snapshot, names) == "Sam (Shift manager) delayed order #7 by 5 min"  # name kept at write time
    assert catalog.describe(row("table.update", {"label": "T9", "new": True}, role="platform_admin", actor=None, support=True),
                            names) == "Restro support added table T9"
    ai = row("customer.profile_update", {"customer_id": gone, "ops": [{"op": "remove", "kind": "allergy", "value": "soy"}]},
             role="staff_via_ai", actor=RAVI)
    assert catalog.describe(ai, names) == 'AI, from Ravi\'s comment, removed allergy "soy" from the profile of a customer (data deleted)'
    assert catalog.describe(row("order.transition", {"number": 9, "from": "READY", "to": "SERVED"}, role="system", actor=None),
                            names) == "Restro (automatic) marked order #9 served"
    assert catalog.describe(row("thing.happened", {}, actor=RAVI), names) == "Ravi (Shift manager) did thing.happened"


def test_details_join_the_staff_note_and_never_copy_it() -> None:
    r = row("order.cancel", {"order_id": ORDER, "number": 42, "from": "ACCEPTED", "to": "CANCELLED", "reason_code": "other"})
    assert dict(catalog.details(r, NAMES)) == {"Reason": "Other", "Staff note": "Ran out of oat milk",
                                               "Was / Now": "Accepted → Cancelled"}


def test_rupees_matches_console_inr() -> None:
    assert catalog.rupees(124000) == "₹ 1,240"
    assert catalog.rupees(12345) == "₹ 123.45"
    assert catalog.rupees(10000000) == "₹ 1,00,000"
    assert catalog.rupees(0) == "₹ 0"
    assert catalog.rupees(1234567890) == "₹ 1,23,45,678.90"


def test_subjects_extracts_valid_uuids_only() -> None:
    oid = str(uuid.uuid4()).upper()
    assert catalog.subjects({"order_id": oid, "customer_id": "not-a-uuid", "conversation_id": 12}) == \
        {"order_id": oid.lower(), "customer_id": None, "conversation_id": None}
    assert catalog.subjects({}) == {"order_id": None, "customer_id": None, "conversation_id": None}


@pytest.mark.parametrize("change", [
    {"phone": "x"}, {"text": "hello"}, {"ops": [{"op": "add", "note": "x"}]}, {"nested": {"deep": [{"Email": "a@b"}]}},
    {"alert_phone": "919999000900"}, {"cancel_reason_text": "x"}, {"allergy_note": "x"}, {"lines": [{"note": "x"}]},
])
async def test_write_rejects_forbidden_change_keys(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must not hold"):
        await audit.write(None, str(uuid.uuid4()), audit.Actor(PRIYA, "owner"), "order.manual", change)  # type: ignore[arg-type]


def test_filter_aliases_map_to_transition_to_status() -> None:
    assert catalog.FILTER_ALIASES == {"order.accept": ("order.transition", "ACCEPTED"),
                                      "order.ready": ("order.transition", "READY"),
                                      "order.serve": ("order.transition", "SERVED")}
    actions = {a["action"]: a for a in catalog.public()["actions"]}
    assert all(actions[a].get("filter_only") is True for a in catalog.FILTER_ALIASES)
    assert "filter_only" not in actions["order.transition"]
    assert not set(catalog.FILTER_ALIASES) & set(catalog.actions_in(["orders"]))


@pytest.mark.parametrize(("q", "actions", "aliases"), [
    ("accepted", [], [("order.transition", "ACCEPTED")]),
    ("Ready", [], [("order.transition", "READY")]),
    ("served", [], [("order.transition", "SERVED")]),  # word start: never "reserved" (offer.void)
    ("serve", [], [("order.transition", "SERVED")]),
    ("cancel", ["order.cancel", "campaign.cancel"], []),
    ("reserved", ["offer.void"], []),
    ("  ", [], []),
    ("100%_", [], []),
])
def test_match_labels_and_aliases_match_at_word_start(q: str, actions: list[str], aliases: list[tuple[str, str]]) -> None:
    assert catalog.match_labels(q) == actions
    assert catalog.match_aliases(q) == aliases


CALL_SITE_PAYLOADS: list[tuple[str, dict[str, Any]]] = [
    ("order.transition", {"order_id": ORDER, "number": 42, "from": "NEW", "to": "ACCEPTED", "customer_id": RIYA}),
    ("order.cancel", {"order_id": ORDER, "number": 42, "from": "NEW", "to": "CANCELLED", "customer_id": RIYA,
                      "reason_code": "other"}),
    ("order.undo", {"order_id": ORDER, "number": 42, "from": "ACCEPTED", "to": "NEW", "customer_id": RIYA}),
    ("order.delay", {"order_id": ORDER, "number": 42, "minutes": 10, "has_note": True, "customer_id": RIYA}),
    ("order.manual", {"order_id": ORDER, "number": 42, "table": "T4", "total_paise": 9000, "has_allergy_note": True,
                      "lines": [{"item_id": str(uuid.uuid4()), "name": "Masala Chai", "qty": 1, "price_paise": 9000}]}),
    ("chat.takeover", {"conversation_id": CONVO, "customer_id": RIYA, "via": "table", "from_mode": "bot",
                       "from_assigned_to": None, "table_session_id": SESSION}),
    ("chat.reply", {"conversation_id": CONVO, "customer_id": RIYA, "chars": 12}),
    ("chat.handback", {"conversation_id": CONVO, "customer_id": RIYA}),
    ("request.ack", {"request_id": str(uuid.uuid4()), "kind": "help", "order_id": ORDER, "conversation_id": CONVO,
                     "table_session_id": SESSION}),
    ("request.resolve", {"request_id": str(uuid.uuid4()), "kind": "bill", "table_session_id": SESSION}),
    ("bill.deliver", {"table_session_id": SESSION, "total_paise": 100, "orders": [42], "subtotal_paise": 100,
                      "discount_paise": 0, "code": None}),
    ("menu.update", {"item_id": str(uuid.uuid4()), "name": "Masala Chai", "new": False, "fields": ["price_paise"],
                     "allergens": 1, "orders_flagged": 0, "price_paise_from": 100, "price_paise_to": 200,
                     "available_from": True, "available_to": True,
                     "allergens_changed": [{"allergen": "milk", "from": "contains", "to": "free_from"}]}),
    ("team.invite", {"membership_id": str(uuid.uuid4()), "user_id": RAVI, "role": "manager"}),
    ("team.update", {"membership_id": str(uuid.uuid4()), "user_id": RAVI, "from_role": "manager", "role": "owner",
                     "from_active": True, "active": True}),
    ("customer.profile_undo", {"customer_id": RIYA, "note_id": str(uuid.uuid4()),
                               "ops": [{"op": "add", "kind": "allergy", "value": "peanuts"}]}),
    ("settings.update", {"fields": ["alert_phone", "tone"]}),
    ("offer.redeem", {"table_session_id": None, "order_id": None, "order_number": None, "campaign_id": str(uuid.uuid4()),
                      "recipient_id": str(uuid.uuid4()), "customer_id": RIYA, "code": "DIWALI7KQ", "channel": "counter",
                      "discount_percent": 10, "bill_paise": 120000, "discount_paise": 12000}),
    ("campaign.draft", {"campaign_id": str(uuid.uuid4()), "new": True, "fields": ["name"], "discount_percent": 10}),
    ("campaign.export", {"campaign_id": str(uuid.uuid4()), "rows": 12}),
    ("audit.export", {"rows": 3, "filters": ["q"]}),
]


@pytest.mark.parametrize(("action", "change"), CALL_SITE_PAYLOADS)
def test_every_current_call_site_payload_passes_the_guard(action: str, change: dict[str, Any]) -> None:
    assert audit._forbidden(change) is None
    assert action in catalog.CATALOG
    assert catalog.describe(row(action, change), NAMES)  # every payload renders
