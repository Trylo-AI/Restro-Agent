"""Audit viewer helpers without a database: the date window, the page cursor, CSV safety, `q` as an order number and
how categories narrow the action filter (docs/retention-audit-recommend.md#write-path, D23)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import BadRequest
from app.db.repositories.audit_log import AuditRow
from app.domain.audit import catalog, viewer
from app.domain.audit.viewer import AuditFilters

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def test_window_default_7_days() -> None:
    assert viewer.window(AuditFilters(), NOW) == (NOW - timedelta(days=7), NOW)
    since = NOW - timedelta(days=2)
    assert viewer.window(AuditFilters(since=since), NOW) == (since, NOW)
    until = NOW - timedelta(days=1)
    assert viewer.window(AuditFilters(until=until), NOW) == (until - timedelta(days=7), until)


def test_window_entity_filter_is_all_time() -> None:
    for f in (AuditFilters(order_id=str(uuid.uuid4())), AuditFilters(customer_id=str(uuid.uuid4())),
              AuditFilters(conversation_id=str(uuid.uuid4())), AuditFilters(order_number=42)):
        assert viewer.window(f, NOW) == (None, None)
    # Explicit dates still apply with an entity filter.
    since = NOW - timedelta(days=30)
    assert viewer.window(AuditFilters(order_number=42, since=since), NOW) == (since, NOW)
    until = NOW - timedelta(days=1)
    assert viewer.window(AuditFilters(order_number=42, until=until), NOW) == (None, until)
    with pytest.raises(BadRequest):
        viewer.window(AuditFilters(order_number=42, since=NOW - timedelta(days=400), until=NOW), NOW)


def test_window_rejects_from_after_to_and_over_366_days() -> None:
    with pytest.raises(BadRequest, match="before the end"):
        viewer.window(AuditFilters(since=NOW, until=NOW), NOW)
    with pytest.raises(BadRequest, match="before the end"):
        viewer.window(AuditFilters(since=NOW, until=NOW - timedelta(hours=1)), NOW)
    with pytest.raises(BadRequest, match="366"):
        viewer.window(AuditFilters(since=NOW - timedelta(days=367), until=NOW), NOW)
    assert viewer.window(AuditFilters(since=NOW - timedelta(days=366), until=NOW), NOW)[0] == NOW - timedelta(days=366)
    naive = datetime(2026, 10, 30, 9, 0)  # noqa: DTZ001 (a query value without an offset is read as UTC)
    assert viewer.window(AuditFilters(since=naive), NOW)[0] == naive.replace(tzinfo=UTC)


def test_cursor_roundtrip() -> None:
    r = AuditRow(id=str(uuid.uuid4()), at=datetime(2026, 11, 1, 12, 41, 5, 123456, tzinfo=UTC), actor_id=None,
                 actor_role="system", support=False, action="order.transition", change={}, severity="info")
    cursor = viewer.encode_cursor(r)
    assert "=" not in cursor and "/" not in cursor and "+" not in cursor
    assert viewer.decode_cursor(cursor) == (r.at, r.id)
    assert viewer.decode_cursor(None) is None and viewer.decode_cursor("") is None


@pytest.mark.parametrize("bad", ["???", "bm90LWEtY3Vyc29y", "MjAyNi0xMS0wMXxub3QtYS11dWlk", "x" * 10])
def test_bad_cursor_is_400(bad: str) -> None:
    with pytest.raises(BadRequest, match="page link"):
        viewer.decode_cursor(bad)


def test_csv_escapes_formula_cells() -> None:
    for risky in ("=SUM(A1)", "+91 99", "-1", "@cmd", "\tx", "\rx"):
        assert viewer.csv_cell(risky) == "'" + risky
    assert viewer.csv_cell("Ravi (Shift manager) cancelled order #42 — Other") == \
        "Ravi (Shift manager) cancelled order #42 — Other"
    assert viewer.csv_cell(None) == "" and viewer.csv_cell(42) == "42"


def test_q_digits_become_order_number() -> None:
    assert viewer.normalise(AuditFilters(q="#42")) == AuditFilters(order_number=42)
    assert viewer.normalise(AuditFilters(q=" 42 ")) == AuditFilters(order_number=42)
    assert viewer.normalise(AuditFilters(q="Riya")).q == "Riya"
    assert viewer.normalise(AuditFilters(q="42 chai")).order_number is None
    assert viewer.normalise(AuditFilters(q="   ")).q is None
    assert viewer.normalise(AuditFilters(q="#1234567890")).order_number is None  # at most 9 digits


def test_categories_intersect_actions() -> None:
    f = AuditFilters(actions=("order.cancel", "chat.takeover", "order.accept"), categories=("orders",))
    assert viewer.action_filter(f) == (("order.cancel",), (("order.transition", "ACCEPTED"),), None)
    assert viewer.action_filter(AuditFilters(actions=("chat.takeover",), categories=("orders",))) is None
    chats = viewer.action_filter(AuditFilters(categories=("chats",)))
    assert chats == (("chat.takeover", "chat.reply", "chat.handback"), (), None)
    stored, _, other = viewer.action_filter(AuditFilters(categories=("other",))) or ((), (), None)
    assert stored == () and other is not None and "order.cancel" in other and "order.accept" not in other
    assert viewer.action_filter(AuditFilters()) == ((), (), None)
    assert viewer.action_filter(AuditFilters(actions=("thing.happened",), categories=("other",))) == \
        (("thing.happened",), (), None)
    assert set(catalog.actions_in(["orders"])) >= {"order.transition", "order.cancel", "order.undo"}
