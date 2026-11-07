"""Audit log (docs/rules.md#unit-and-integration, Audit row): every staff write leaves one classified row with its
subjects; the owner's filters, paging, facets, export and order history read them back without crossing cafés.

Staff actions go through the REST API (as the console sends them); a few rows are written with `audit.write` directly
where only the read side is under test.
"""

import asyncio
import csv
import io
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from app.db.repositories import audit_log
from app.db.repositories.audit_log import Where
from app.domain import audit
from app.domain.ai_agent import prompt
from app.domain.audit import catalog, viewer
from app.domain.orders import service as order_service
from app.integrations import llm
from app.integrations.llm import StubLlm
from tests.integration.conftest import CafeFixture, create_test_cafe, drop_test_cafe
from tests.integration.conversation_kit import (
    Kitchen,
    RecordingTransport,
    TaskRecorder,
    build_kitchen,
    create_staff,
    drop_staff,
    install,
    uninstall,
)
from tests.integration.test_staff_api import client_as

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
WA, WA2 = "919999760001", "919999760002"


@pytest.fixture
async def k(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Kitchen]:
    async with db.acquire() as c:
        items, tables = await build_kitchen(c, cafe.cafe_id)
        staff = await create_staff(c, cafe.cafe_id)
        pnid = await c.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id)
    transport, rec = RecordingTransport(), TaskRecorder()
    install(transport, rec)
    prompt._cache.clear()
    try:
        yield Kitchen(db, cafe.cafe_id, pnid, items, tables, transport, rec, staff)
    finally:
        uninstall()
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


# ── helpers ───────────────────────────────────────────────────────────────


async def wa_order(k: Kitchen, wa: str = WA, name: str = "Sana", item: str = "Cold Coffee") -> asyncpg.Record:
    """A WhatsApp order at table T5, still NEW."""
    await k.say(wa, "Hi, I'm at Table 5")
    await k.say(wa, name)
    await k.say(wa, tap=f"item:{k.items[item]}")
    await k.say(wa, tap=f"confirm:{(await k.state(wa))['cart']['version']}")
    return await k.db.fetchrow(
        """select o.id::text, o.number, o.status, o.version, o.customer_id::text, o.table_session_id::text, o.created_at
           from orders o join customers c on c.id = o.customer_id
           where o.cafe_id = $1 and c.wa_id = $2 order by o.created_at desc limit 1""", k.cafe_id, wa)


async def conv(k: Kitchen, wa: str = WA) -> asyncpg.Record:
    return await k.db.fetchrow(
        """select cv.id::text, cv.customer_id::text, cv.table_session_id::text from conversations cv
           join customers c on c.id = cv.customer_id where cv.cafe_id = $1 and c.wa_id = $2""", k.cafe_id, wa)


async def version(k: Kitchen, order_id: str) -> int:
    return int(await k.db.fetchval("select version from orders where id = $1", order_id))


async def move(k: Kitchen, role: str, order_id: str, to: str) -> None:
    async with client_as(k, role) as api:
        r = await api.post(f"/orders/{order_id}/transition", json={"to": to, "expected_version": await version(k, order_id)})
        assert r.status_code == 200, r.text


async def rows(k: Kitchen, action: str | None = None) -> list[asyncpg.Record]:
    return list(await k.db.fetch(
        """select id::text, at, actor_id::text, actor_role, support, action, change, severity, order_id::text,
                  customer_id::text, conversation_id::text, actor_name
           from audit_log where cafe_id = $1 and ($2::text is null or action = $2) order by at, id""", k.cafe_id, action))


async def write(k: Kitchen, role: str, action: str, change: dict[str, Any], *, cafe_id: str | None = None,
                user_id: str | None = None) -> str:
    """One row through audit.write in its own transaction; returns its id."""
    actor = audit.Actor(user_id if user_id is not None else k.staff.get(role), role)
    async with k.db.acquire() as c, c.transaction():
        await audit.write(c, cafe_id or k.cafe_id, actor, action, change)
        return str(await c.fetchval(
            "select id::text from audit_log where cafe_id = $1 and at = now() order by id desc limit 1", cafe_id or k.cafe_id))


async def entries(k: Kitchen, *params: tuple[str, Any], cafe_id: str | None = None) -> dict[str, Any]:
    async with client_as(k, "owner", cafe_id=cafe_id) as api:
        r = await api.get("/audit/entries", params=[(key, str(v)) for key, v in params])
        assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def ids(k: Kitchen, *params: tuple[str, Any]) -> set[str]:
    return {e["id"] for e in (await entries(k, *params, ("limit", 200)))["entries"]}


async def history(k: Kitchen, order_id: str, cafe_id: str | None = None) -> httpx.Response:
    async with client_as(k, "owner", cafe_id=cafe_id) as api:
        return await api.get(f"/orders/{order_id}/history")


def classify(r: asyncpg.Record) -> str:
    return catalog.classify(r["action"], r["actor_role"], r["support"], r["change"], actor_id=r["actor_id"]).severity


# ── write path ────────────────────────────────────────────────────────────


async def test_write_sets_severity_subjects_and_actor_name(k: Kitchen) -> None:
    order_id, customer_id, conversation_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    rid = await write(k, "shift_manager", "order.cancel",
                      {"order_id": order_id.upper(), "customer_id": customer_id, "conversation_id": conversation_id,
                       "number": 7, "from": "NEW", "to": "CANCELLED", "reason_code": "other"})
    row = next(r for r in await rows(k) if r["id"] == rid)
    assert row["severity"] == "critical" and row["actor_name"] == "Ravi"
    assert (row["order_id"], row["customer_id"], row["conversation_id"]) == (order_id, customer_id, conversation_id)

    rid = await write(k, "system", "order.transition", {"order_id": "not-a-uuid", "customer_id": 42, "to": "SERVED"},
                      user_id=None)
    row = next(r for r in await rows(k) if r["id"] == rid)
    assert row["severity"] == "info" and row["actor_name"] is None
    assert (row["order_id"], row["customer_id"], row["conversation_id"]) == (None, None, None)

    async with k.db.acquire() as c:
        with pytest.raises(ValueError, match="must not hold 'text'"):
            await audit.write(c, k.cafe_id, audit.Actor(k.staff["owner"], "owner"), "chat.reply", {"text": "hello"})
    assert len(await rows(k)) == 2  # the refused write left nothing behind


async def test_insert_without_severity_fails(k: Kitchen) -> None:
    async with k.db.acquire() as c:
        with pytest.raises(asyncpg.NotNullViolationError, match="severity"):
            await c.execute("insert into audit_log (cafe_id, actor_role, action, change) values ($1, 'system', 'x.y', '{}')",
                            k.cafe_id)


async def test_backfilled_rows_have_severity_after_0002(k: Kitchen) -> None:
    """Rows the old backend wrote without severity (between 0001 and 0002) are classified by 0002's backfill, then the
    column is required and the function is gone. Replayed in a transaction that is rolled back: the 0002 rollback,
    the 0001 backfill function, legacy-shaped rows, then the 0002 migration."""
    up_0001 = (MIGRATIONS / "20261101000001_retention_audit_recommend.sql").read_text(encoding="utf-8")
    function = re.search(r"create or replace function audit_log_backfill_derived\(\).*?end \$\$;", up_0001, re.DOTALL)
    assert function
    down_0002 = (ROOT / "supabase" / "rollback" / "20261101000002_audit_severity_not_null_down.sql").read_text(encoding="utf-8")
    up_0002 = (MIGRATIONS / "20261101000002_audit_severity_not_null.sql").read_text(encoding="utf-8")
    order_id, customer_id = str(uuid.uuid4()), str(uuid.uuid4())
    legacy = [  # (action, role, support, change, expected severity)
        ("order.cancel", "shift_manager", False, {"order_id": order_id, "customer_id": customer_id, "from": "NEW"}, "critical"),
        ("order.transition", "shift_manager", False, {"order_id": order_id, "to": "ACCEPTED"}, "info"),
        ("customer.profile_update", "manager", False, {"customer_id": customer_id,
                                                       "ops": [{"op": "remove", "kind": "allergy", "value": "soy"}]}, "critical"),
        ("offer.redeem", "shift_manager", False, {"discount_paise": 60_000}, "warning"),
        ("table.update", "platform_admin", True, {"label": "T9"}, "warning"),
        ("menu.availability", "shift_manager", False, {"available": False, "orders_flagged": 2}, "warning"),
    ]
    async with k.db.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("set local lock_timeout = '30s'")
            await c.execute("\n".join(line for line in down_0002.splitlines() if line.startswith("alter table")))
            await c.execute(function.group(0))
            for action, role, support, change, _ in legacy:
                await c.execute(
                    """insert into audit_log (cafe_id, actor_id, actor_role, support, action, change)
                       values ($1, $2, $3, $4, $5, $6)""",
                    k.cafe_id, k.staff["shift_manager"] if role == "shift_manager" else None, role, support, action, change)
            await c.execute(up_0002)
            got = {r["action"]: r for r in await c.fetch(
                """select action, severity, order_id::text, customer_id::text, actor_name from audit_log
                   where cafe_id = $1""", k.cafe_id)}
            for action, _, _, _, expected in legacy:
                assert got[action]["severity"] == expected, action
            assert (got["order.cancel"]["order_id"], got["order.cancel"]["customer_id"]) == (order_id, customer_id)
            assert got["order.cancel"]["actor_name"] == "Ravi"
            assert await c.fetchval("""select is_nullable from information_schema.columns
                                       where table_name = 'audit_log' and column_name = 'severity'""") == "NO"
            assert await c.fetchval("select to_regprocedure('audit_log_backfill_derived()')") is None
        finally:
            await tr.rollback()


async def test_audit_log_still_append_only(k: Kitchen) -> None:
    rid = await write(k, "owner", "settings.update", {"fields": ["tone"]})
    async with k.db.acquire() as c:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            await c.execute("update audit_log set severity = 'critical' where id = $1", uuid.UUID(rid))
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            await c.execute("delete from audit_log where id = $1", uuid.UUID(rid))


# ── call sites ────────────────────────────────────────────────────────────


async def test_filter_order_accept_returns_only_accepts_and_who_did_it(k: Kitchen) -> None:
    a = await wa_order(k)
    b = await wa_order(k, WA2, "Riya", "Masala Chai")
    await move(k, "shift_manager", a["id"], "ACCEPTED")
    await move(k, "manager", a["id"], "READY")
    await move(k, "manager", b["id"], "ACCEPTED")

    page = await entries(k, ("action", "order.accept"))
    assert {e["order"]["number"]: e["actor"]["name"] for e in page["entries"]} == {a["number"]: "Ravi", b["number"]: "Kiran"}
    assert {e["label"] for e in page["entries"]} == {"Order accepted"}
    assert all(e["action"] == "order.transition" for e in page["entries"])
    who = await entries(k, ("action", "order.accept"), ("order_number", a["number"]))
    assert [e["sentence"] for e in who["entries"]] == [f"Ravi (Shift manager) accepted order #{a['number']}"]
    ready = await entries(k, ("action", "order.ready"))
    assert [e["order"]["number"] for e in ready["entries"]] == [a["number"]]

    async with client_as(k, "owner") as api:
        facets = (await api.get("/audit/facets")).json()
    counts = {f["action"]: f["count"] for f in facets["actions"]}
    assert counts == {"order.accept": 2, "order.ready": 1}  # an accept counts as "Order accepted", not a status change
    assert {f["action"]: f["label"] for f in facets["actions"]}["order.accept"] == "Order accepted"


async def test_accept_row_carries_number_and_customer(k: Kitchen) -> None:
    order = await wa_order(k)
    await move(k, "shift_manager", order["id"], "ACCEPTED")
    [row] = await rows(k, "order.transition")
    assert row["change"] == {"order_id": order["id"], "number": order["number"], "from": "NEW", "to": "ACCEPTED",
                             "customer_id": order["customer_id"]}
    assert (row["order_id"], row["customer_id"], row["severity"]) == (order["id"], order["customer_id"], "info")
    [e] = (await entries(k))["entries"]
    assert e["order"] == {"id": order["id"], "number": order["number"], "status": "ACCEPTED"}
    assert e["customer"] == {"id": order["customer_id"], "name": "Sana"}
    assert e["category"] == "orders" and e["severity"] == "info" and e["severity_reason"] is None
    assert e["actor"] == {"user_id": k.staff["shift_manager"], "name": "Ravi", "role": "shift_manager", "support": False}


async def test_staff_cancel_row_is_critical_with_order_and_customer(k: Kitchen) -> None:
    a = await wa_order(k)
    b = await wa_order(k, WA2, "Riya", "Masala Chai")
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/orders/{a['id']}/cancel", json={"reason_code": "customer_asked", "expected_version": a["version"]})
        assert r.status_code == 200, r.text
    await move(k, "manager", b["id"], "ACCEPTED")
    async with client_as(k, "manager") as api:
        r = await api.post(f"/orders/{b['id']}/cancel", json={"reason_code": "other", "reason_text": "Out of oat milk",
                                                                "expected_version": await version(k, b["id"])})
        assert r.status_code == 200, r.text
    cancels = {r["order_id"]: r for r in await rows(k, "order.cancel")}
    assert {r["severity"] for r in cancels.values()} == {"critical"}  # every role, every status
    assert cancels[b["id"]]["customer_id"] == b["customer_id"]
    assert "oat milk" not in str(cancels[b["id"]]["change"])  # the staff note stays on the order

    page = await entries(k, ("severity", "critical"))
    by_order = {e["order"]["id"]: e for e in page["entries"]}
    late = by_order[b["id"]]
    assert late["sentence"] == f"Kiran (Manager) cancelled order #{b['number']} (was Accepted) — Other"
    assert late["severity_reason"] == "Cancelled after the kitchen accepted it"
    assert {"label": "Staff note", "value": "Out of oat milk"} in late["details"]
    assert late["customer"] == {"id": b["customer_id"], "name": "Riya"}
    assert by_order[a["id"]]["severity_reason"] == "Cancelled by staff"


async def test_takeover_records_via_and_previous_assignee(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, "Sana")
    c = await conv(k)
    async with client_as(k, "shift_manager") as api:
        assert (await api.post(f"/conversations/{c['id']}/takeover")).status_code == 200
    async with client_as(k, "manager") as api:
        assert (await api.post(f"/conversations/{c['id']}/takeover")).status_code == 200
        assert (await api.post(f"/conversations/{c['id']}/handback")).status_code == 200
    async with client_as(k, "shift_manager") as api:
        r = await api.post("/conversations/open", json={"table_session_id": c["table_session_id"]})
        assert r.status_code == 200, r.text
    first, second, by_table = await rows(k, "chat.takeover")
    assert first["change"] == {"conversation_id": c["id"], "customer_id": c["customer_id"], "via": "takeover",
                               "from_mode": "bot", "from_assigned_to": None}
    assert first["severity"] == "info"
    assert second["change"]["from_mode"] == "human" and second["change"]["from_assigned_to"] == k.staff["shift_manager"]
    assert second["severity"] == "warning"
    assert by_table["change"]["via"] == "table" and by_table["change"]["table_session_id"] == c["table_session_id"]
    assert (by_table["conversation_id"], by_table["customer_id"]) == (c["id"], c["customer_id"])

    page = await entries(k, ("action", "chat.takeover"))
    sentences = [e["sentence"] for e in reversed(page["entries"])]
    assert sentences == ["Ravi (Shift manager) took over Sana's chat", "Kiran (Manager) took over Sana's chat from Ravi",
                         "Ravi (Shift manager) opened Sana's chat from table T5"]
    taken = page["entries"][1]
    assert taken["severity_reason"] == "Took the chat from a teammate"
    assert {"label": "Taken from", "value": "Ravi"} in taken["details"]


async def test_concurrent_takeovers_record_the_teammate_they_took_from(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, "Sana")
    c = await conv(k)
    async with k.db.acquire() as blocker:
        lock = blocker.transaction()
        await lock.start()
        await blocker.execute("select 1 from conversations where id = $1 for update", c["id"])  # both tap while it's held
        async with client_as(k, "shift_manager") as a, client_as(k, "manager") as b:
            taps = asyncio.gather(a.post(f"/conversations/{c['id']}/takeover"), b.post(f"/conversations/{c['id']}/takeover"))
            for _ in range(100):  # wait until both requests are queued on the row lock
                waiting = await k.db.fetchval(
                    """select count(*) from pg_stat_activity where wait_event_type = 'Lock'
                       and query ilike '%conversations%' and query not ilike '%pg_stat_activity%'""")
                if waiting >= 2:
                    break
                await asyncio.sleep(0.05)
            await lock.rollback()
            assert [r.status_code for r in await taps] == [200, 200]
    first, second = await rows(k, "chat.takeover")
    assert first["change"]["from_mode"] == "bot" and first["change"]["from_assigned_to"] is None
    assert first["severity"] == "info"
    assert second["change"]["from_mode"] == "human" and second["change"]["from_assigned_to"] == first["actor_id"]
    assert second["actor_id"] != first["actor_id"] and second["severity"] == "warning"


async def test_reply_from_bot_mode_writes_takeover_via_reply_then_reply(k: Kitchen) -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    c = await conv(k)
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/conversations/{c['id']}/reply", json={"text": "Coming to your table now"})
        assert r.status_code == 200, r.text
    takeover, reply = [r for r in await rows(k) if r["action"].startswith("chat.")]
    assert takeover["action"] == "chat.takeover" and takeover["change"]["via"] == "reply"
    assert takeover["change"]["from_mode"] == "bot"
    assert reply["action"] == "chat.reply"
    assert reply["change"] == {"conversation_id": c["id"], "customer_id": c["customer_id"], "chars": 24}
    assert "Coming" not in str(takeover["change"]) + str(reply["change"])


async def test_request_ack_carries_order_conversation_session(k: Kitchen) -> None:
    order = await wa_order(k)
    await k.say(WA, "call waiter")
    c = await conv(k)
    req = await k.db.fetchval("select id::text from requests where cafe_id = $1 and kind = 'help'", k.cafe_id)
    await k.db.execute("update requests set order_id = $2 where id = $1", req, order["id"])
    async with client_as(k, "shift_manager") as api:
        assert (await api.post(f"/requests/{req}/ack")).status_code == 200
        assert (await api.post(f"/requests/{req}/resolve")).status_code == 200
    for action in ("request.ack", "request.resolve"):
        [row] = await rows(k, action)
        assert row["change"] == {"request_id": req, "kind": "help", "order_id": order["id"], "conversation_id": c["id"],
                                 "table_session_id": c["table_session_id"]}
        assert (row["order_id"], row["conversation_id"]) == (order["id"], c["id"])
    [e] = (await entries(k, ("action", "request.ack")))["entries"]
    assert e["sentence"] == "Ravi (Shift manager) acknowledged a call for help from table T5"
    assert e["links"] == {"table_session_id": c["table_session_id"], "request_id": req}


async def test_bill_deliver_carries_orders_and_totals(k: Kitchen) -> None:
    order = await wa_order(k)
    await k.say(WA, "bill please")
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/bills/{order['table_session_id']}/deliver")
        assert r.status_code == 200, r.text
    [row] = await rows(k, "bill.deliver")
    assert row["change"] == {"table_session_id": order["table_session_id"], "total_paise": 19000, "orders": [order["number"]],
                             "subtotal_paise": 19000, "discount_paise": 0, "code": None}
    [e] = (await entries(k, ("action", "bill.deliver")))["entries"]
    assert e["sentence"] == "Ravi (Shift manager) delivered the bill for T5 — ₹ 190"
    assert {"label": "Bill total", "value": "₹ 190"} in e["details"]


async def test_menu_update_records_price_and_allergen_changes(k: Kitchen) -> None:
    chai = k.items["Masala Chai"]
    body = {"name": "Masala Chai", "category": "Tea", "price_paise": 9500, "prep_minutes": 5, "veg": True,
            "allergens": [{"allergen": "milk", "state": "free_from"}]}
    async with client_as(k, "manager") as api:
        r = await api.put(f"/menu/{chai}", json={**body, "version": await k.db.fetchval(
            "select version from menu_items where id = $1", chai)})
        assert r.status_code == 200, r.text
        r = await api.put(f"/menu/{chai}", json={**body, "price_paise": 9900, "version": r.json()["version"]})
        assert r.status_code == 200, r.text
        r = await api.post("/menu", json={**body, "name": "Ginger Chai", "allergens": []})
        assert r.status_code == 200, r.text
    allergen_edit, price_edit, new = await rows(k, "menu.update")
    assert allergen_edit["change"]["price_paise_from"] == 9000 and allergen_edit["change"]["price_paise_to"] == 9500
    assert allergen_edit["change"]["allergens_changed"] == [{"allergen": "milk", "from": "contains", "to": "free_from"}]
    assert allergen_edit["change"]["available_from"] is True and allergen_edit["change"]["available_to"] is True
    assert allergen_edit["severity"] == "critical"
    assert price_edit["change"]["allergens_changed"] == [] and price_edit["severity"] == "warning"
    assert new["change"]["new"] is True and new["change"]["price_paise_from"] is None and "allergens_changed" not in new["change"]
    assert new["severity"] == "info"
    page = await entries(k, ("action", "menu.update"), ("severity", "critical"))
    [e] = page["entries"]
    assert e["sentence"] == "Kiran (Manager) edited Masala Chai: price ₹ 90 → ₹ 95, allergens (milk: contains → free from)"
    assert e["severity_reason"] == "An allergen was marked absent"
    assert e["links"] == {"item_id": chai}


async def test_team_update_records_from_role(k: Kitchen) -> None:
    mid = await k.db.fetchval("select id::text from memberships where cafe_id = $1 and user_id = $2", k.cafe_id,
                              k.staff["shift_manager"])
    async with client_as(k, "owner") as api:
        assert (await api.patch(f"/team/{mid}", json={"role": "manager"})).status_code == 200
        assert (await api.patch(f"/team/{mid}", json={"active": False})).status_code == 200
        assert (await api.patch(f"/team/{mid}", json={"active": True})).status_code == 200
    role, off, on = await rows(k, "team.update")
    assert role["change"] == {"membership_id": mid, "user_id": k.staff["shift_manager"], "from_role": "shift_manager",
                              "role": "manager", "from_active": True, "active": True}
    assert [r["severity"] for r in (role, off, on)] == ["critical", "warning", "info"]
    sentences = [e["sentence"] for e in reversed((await entries(k, ("action", "team.update")))["entries"])]
    assert sentences == ["Neha (Owner) changed Ravi's role: Shift manager → Manager", "Neha (Owner) deactivated Ravi",
                         "Neha (Owner) reactivated Ravi"]


async def test_profile_undo_records_reversed_ops(k: Kitchen) -> None:
    from app.domain import customers

    cust = str(await k.db.fetchval(
        "insert into customers (cafe_id, wa_id, name) values ($1, '919999760009', 'Riya Shah') returning id::text", k.cafe_id))
    llm.set_client(StubLlm(lambda s, u: {"ops": [{"op": "add", "kind": "allergy", "value": "peanuts"}],
                                         "summary": "Allergy + peanuts"}))
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/customers/{cust}/notes", json={"text": "Riya is allergic to peanuts"})
        assert r.status_code == 200, r.text
        note_id = r.json()["id"]
        await customers.process_note(k.cafe_id, note_id)
        assert (await api.post(f"/customer-notes/{note_id}/undo")).status_code == 200
    [update] = await rows(k, "customer.profile_update")
    assert update["actor_role"] == "staff_via_ai" and update["severity"] == "info"
    [undo] = await rows(k, "customer.profile_undo")
    assert undo["change"] == {"customer_id": cust, "note_id": note_id,
                              "ops": [{"op": "add", "kind": "allergy", "value": "peanuts"}]}
    assert undo["severity"] == "critical" and undo["customer_id"] == cust
    assert "allergic" not in str(undo["change"])  # the comment text never reaches the audit log
    [e] = (await entries(k, ("action", "customer.profile_undo")))["entries"]
    assert e["sentence"] == 'Ravi (Shift manager) undid a comment\'s changes on Riya\'s profile (removed allergy "peanuts")'
    assert e["severity_reason"] == "Undo removed an allergy"


# ── read path ─────────────────────────────────────────────────────────────


async def test_list_filters_each(k: Kitchen) -> None:
    order = await wa_order(k)
    c = await conv(k)
    n, oid, cid = order["number"], order["id"], order["customer_id"]
    r1 = await write(k, "shift_manager", "order.transition",
                     {"order_id": oid, "number": n, "from": "NEW", "to": "ACCEPTED", "customer_id": cid})
    r2 = await write(k, "manager", "order.cancel",
                     {"order_id": oid, "number": n, "from": "ACCEPTED", "to": "CANCELLED", "customer_id": cid,
                      "reason_code": "other"})
    r3 = await write(k, "shift_manager", "chat.takeover",  # legacy shape: chat only, no customer_id
                     {"conversation_id": c["id"], "via": "takeover", "from_mode": "bot", "from_assigned_to": None})
    r4 = await write(k, "manager", "menu.availability",
                     {"item_id": k.items["Cold Coffee"], "name": "Cold Coffee", "available": False, "orders_flagged": 1})
    r5 = await write(k, "owner", "settings.update", {"fields": ["tone"]})
    r6 = await write(k, "system", "order.transition", {"order_id": oid, "number": n, "from": "READY", "to": "SERVED"},
                     user_id=None)
    at = {r["id"]: r["at"] for r in await rows(k)}
    assert await ids(k) == {r1, r2, r3, r4, r5, r6}
    cases: list[tuple[list[tuple[str, Any]], set[str]]] = [
        ([("action", "order.cancel")], {r2}),
        ([("action", "order.accept")], {r1}),
        ([("action", "order.accept"), ("action", "menu.availability")], {r1, r4}),
        ([("action", "order.transition")], {r1, r6}),
        ([("category", "chats")], {r3}),
        ([("category", "menu"), ("category", "settings")], {r4, r5}),
        ([("category", "orders"), ("severity", "critical")], {r2}),
        ([("category", "other")], set()),
        ([("severity", "critical")], {r2}),
        ([("severity", "critical"), ("severity", "warning")], {r2, r4}),
        ([("severity", "info")], {r1, r3, r5, r6}),
        ([("actor_id", k.staff["shift_manager"])], {r1, r3}),
        ([("actor_id", k.staff["shift_manager"]), ("actor_id", k.staff["owner"])], {r1, r3, r5}),
        ([("role", "system")], {r6}),
        ([("role", "owner"), ("role", "manager")], {r2, r4, r5}),
        ([("order_number", n)], {r1, r2, r6}),
        ([("order_number", n + 1000)], set()),
        ([("order_id", oid)], {r1, r2, r6}),
        ([("customer_id", cid)], {r1, r2, r3}),  # the customer's chat rows too
        ([("conversation_id", c["id"])], {r3}),
        ([("q", "Kiran")], {r2, r4}),
        ([("q", "cold")], {r4}),
        ([("q", "Sana")], {r1, r2}),
        ([("q", "cancel")], {r2}),
        ([("q", "accepted")], {r1}),  # the label shown on an accept row (filter alias), not just stored actions
        ([("q", "served")], {r6}),  # never "Redemption reversed (reserved)"
        ([("q", "ready")], set()),
        ([("q", f"#{n}")], {r1, r2, r6}),
        ([("q", "100%_")], set()),
        ([("from", at[r5].isoformat())], {r5, r6}),
        ([("to", at[r5].isoformat())], {r1, r2, r3, r4}),
    ]
    for params, expected in cases:
        assert await ids(k, *params) == expected, params


async def test_critical_only_uses_flagged_index(k: Kitchen) -> None:
    async with k.db.acquire() as c:
        await c.execute(
            """insert into audit_log (cafe_id, actor_role, action, change, severity, at)
               select $1, 'system', 'order.transition', '{}'::jsonb, case when g % 1000 = 0 then 'critical' else 'info' end,
                      now() - g * interval '1 minute' from generate_series(1, 3000) g""", k.cafe_id)
        await c.execute("analyze audit_log")
        for severities in (("critical",), ("critical", "warning")):
            parts, args = audit_log._where(k.cafe_id, Where(severities=severities))
            sql = (f"explain select {audit_log._COLS} from {audit_log._FROM} where {' and '.join(parts)} "
                   f"order by a.at desc, a.id desc limit 51")
            assert "a.severity <> 'info'" in sql
            async with c.transaction():
                await c.execute("set local enable_seqscan = off")
                plan = "\n".join(r[0] for r in await c.fetch(sql, *args))
            assert "audit_log_cafe_flagged" in plan, plan
    page = await entries(k, ("severity", "critical"))
    assert len(page["entries"]) == 3 and page["next"] is None


async def test_keyset_stable_with_equal_timestamps(k: Kitchen) -> None:
    async with k.db.acquire() as c, c.transaction():  # one transaction: every row gets the same `at`
        for minutes in range(7):
            await audit.write(c, k.cafe_id, audit.Actor(k.staff["manager"], "manager"), "order.delay",
                              {"number": minutes + 1, "minutes": minutes + 1, "has_note": False})
    expected = sorted((r["id"] for r in await rows(k)), reverse=True)
    assert len({r["at"] for r in await rows(k)}) == 1
    seen: list[str] = []
    before: str | None = None
    for size in (3, 3, 1):
        params: list[tuple[str, Any]] = [("limit", 3)] + ([("before", before)] if before else [])
        page = await entries(k, *params)
        assert len(page["entries"]) == size
        seen += [e["id"] for e in page["entries"]]
        before = page["next"]
    assert before is None and seen == expected


async def test_other_cafe_rows_never_returned(k: Kitchen) -> None:
    own = await write(k, "shift_manager", "order.delay", {"number": 1, "minutes": 5})
    async with k.db.acquire() as c:
        other = await create_test_cafe(c)
        other_order = await c.fetchval(
            """insert into orders (cafe_id, customer_id, number, status, expected_minutes, total_paise, source, idempotency_key)
               values ($1, $2, 4242, 'NEW', 5, 100, 'qr', $3) returning id::text""",
            other.cafe_id, other.customer_id, f"test:{uuid.uuid4()}")
    try:
        theirs = await write(k, "owner", "order.cancel",
                             {"order_id": other_order, "number": 4242, "customer_id": other.customer_id,
                              "conversation_id": other.conversation_id, "from": "NEW"},
                             cafe_id=other.cafe_id, user_id=str(uuid.uuid4()))
        assert await ids(k) == {own}
        for params in ([("customer_id", other.customer_id)], [("order_id", other_order)], [("order_number", 4242)],
                       [("conversation_id", other.conversation_id)], [("severity", "critical")], [("q", "Test")]):
            assert await ids(k, *params) == set(), params
        async with client_as(k, "owner") as api:
            facets = (await api.get("/audit/facets")).json()
            export = (await api.get("/audit/export")).text
        assert facets["total"] == 1 and facets["by_severity"]["critical"] == 0
        assert theirs not in export and own in export
    finally:
        async with k.db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)


async def test_order_history_other_cafe_404(k: Kitchen) -> None:
    order = await wa_order(k)
    assert (await history(k, order["id"])).status_code == 200
    async with k.db.acquire() as c:
        other = await create_test_cafe(c)
    try:
        r = await history(k, order["id"], cafe_id=other.cafe_id)
        assert r.status_code == 404 and r.json()["error"]["message"] == "Order not found in this café."
        assert (await history(k, str(uuid.uuid4()))).status_code == 404
    finally:
        async with k.db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)


async def test_facets_counts_and_former_staff_names(k: Kitchen) -> None:
    await write(k, "shift_manager", "order.transition", {"number": 1, "from": "NEW", "to": "ACCEPTED"})
    await write(k, "shift_manager", "chat.reply", {"chars": 3})
    await write(k, "manager", "order.cancel", {"number": 2, "from": "NEW"})
    await write(k, "manager", "order.undo", {"number": 3, "from": "ACCEPTED", "to": "NEW"})
    await write(k, "system", "order.transition", {"number": 4, "from": "READY", "to": "SERVED"}, user_id=None)
    gone = str(uuid.uuid4())
    await write(k, "shift_manager", "order.delay", {"number": 5, "minutes": 3}, user_id=gone)  # never a member here
    await k.db.execute("delete from memberships where cafe_id = $1 and user_id = $2", k.cafe_id, k.staff["manager"])
    async with client_as(k, "owner") as api:
        facets = (await api.get("/audit/facets")).json()
    assert facets["total"] == 6
    assert facets["by_severity"] == {"critical": 1, "warning": 1, "info": 4}
    actors = {(a["name"], a["role"]): a["count"] for a in facets["actors"]}
    assert actors == {("Ravi", "shift_manager"): 2, ("Kiran", "manager"): 2, ("Restro (automatic)", "system"): 1,
                      ("A former team member", "shift_manager"): 1}  # Kiran's name kept from the write-time snapshot
    assert {a["action"]: a["count"] for a in facets["actions"]} == {
        "order.accept": 1, "chat.reply": 1, "order.cancel": 1, "order.undo": 1, "order.serve": 1, "order.delay": 1}
    assert {c["category"]: (c["label"], c["count"]) for c in facets["categories"]} == {
        "orders": ("Orders", 5), "chats": ("Chats", 1)}
    page = await entries(k, ("severity", "critical"))
    assert page["entries"][0]["actor"]["name"] == "Kiran"
    assert page["entries"][0]["sentence"] == "Kiran (Manager) cancelled order #2 (was New)"


async def test_order_history_merges_customer_staff_system_escalation_and_bill_without_duplicates(k: Kitchen) -> None:
    order = await wa_order(k)
    await k.db.execute("insert into escalations (cafe_id, subject_type, subject_id, level, target) values ($1, 'order', $2, 1, 'managers')",
                       k.cafe_id, order["id"])
    await move(k, "shift_manager", order["id"], "ACCEPTED")
    await move(k, "shift_manager", order["id"], "READY")
    await k.db.execute("update orders set ready_at = now() - interval '11 minutes' where id = $1", order["id"])
    await order_service.auto_served(k.cafe_id, order["id"], {})
    await k.say(WA, "bill please")
    async with client_as(k, "shift_manager") as api:
        assert (await api.post(f"/bills/{order['table_session_id']}/deliver")).status_code == 200
    r = await history(k, order["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order"] | {"created_at": None} == {
        "id": order["id"], "number": order["number"], "status": "SERVED", "source": "qr", "table": "T5",
        "customer": {"id": order["customer_id"], "name": "Sana"}, "created_at": None, "total_paise": 19000,
        "cancel_reason_code": None, "cancel_reason_text": None, "cancelled_by_name": None}
    events = body["events"]
    assert [e["at"] for e in events] == sorted(e["at"] for e in events)
    n = order["number"]
    assert [(e["kind"], e["actor"]["type"], e["sentence"]) for e in events] == [
        ("status", "customer", f"Sana placed order #{n}"),
        ("escalation", "system", f"Order #{n}: no one accepted in 60 s — sent to managers"),
        ("action", "staff", f"Ravi (Shift manager) accepted order #{n}"),
        ("action", "staff", f"Ravi (Shift manager) marked order #{n} ready"),
        ("status", "system", f"Restro (automatic) marked order #{n} served (no one did after 10 min)"),
        ("action", "staff", "Ravi (Shift manager) delivered the bill for T5 — ₹ 190"),
    ]
    assert events[1]["severity"] == "warning"
    for to in ("NEW", "ACCEPTED", "READY", "SERVED"):
        assert [e["to_status"] for e in events].count(to) == 1, to
    assert all(e["audit_id"] for e in events if e["kind"] == "action")


async def test_every_staff_order_event_has_matching_audit_row(k: Kitchen) -> None:
    async with client_as(k, "shift_manager") as api:
        r = await api.post("/orders/manual", json={"table_id": k.tables["T1"], "allergy_note": "no peanuts",
                                                   "items": [{"item_id": k.items["Masala Chai"], "qty": 2}]},
                           headers={"Idempotency-Key": "audit-manual-0001"})
        assert r.status_code == 200, r.text
        manual = r.json()
    a = await wa_order(k)
    await move(k, "shift_manager", a["id"], "ACCEPTED")
    async with client_as(k, "shift_manager") as api:
        assert (await api.post(f"/orders/{a['id']}/delay", json={"minutes": 20, "note": "Busy kitchen"})).status_code == 200
    await move(k, "shift_manager", a["id"], "READY")
    async with client_as(k, "shift_manager") as api:
        assert (await api.post(f"/orders/{a['id']}/undo")).status_code == 200
    await move(k, "manager", a["id"], "READY")
    await move(k, "manager", a["id"], "SERVED")
    b = await wa_order(k, WA2, "Riya", "Masala Chai")
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/orders/{b['id']}/cancel", json={"reason_code": "customer_asked", "expected_version": b["version"]})
        assert r.status_code == 200, r.text

    staff_events = await k.db.fetch(
        """select order_id::text, from_status, to_status, actor_id::text, reason from order_events
           where cafe_id = $1 and actor_type = 'staff' order by created_at""", k.cafe_id)
    audits = [r for r in await rows(k) if r["action"] in ("order.transition", "order.cancel", "order.undo", "order.manual")]
    assert len(staff_events) == len(audits) == 7
    unmatched = list(audits)
    for ev in staff_events:
        match = next(r for r in unmatched if r["order_id"] == ev["order_id"] and r["actor_id"] == ev["actor_id"] and (
            (r["action"] == "order.manual" and ev["reason"] == "manual")
            or (r["change"].get("from") == ev["from_status"] and r["change"].get("to") == ev["to_status"])))
        unmatched.remove(match)
    assert not unmatched
    [delay] = await rows(k, "order.delay")
    assert delay["change"] == {"order_id": a["id"], "number": a["number"], "minutes": 20, "has_note": True,
                               "customer_id": a["customer_id"]}
    assert delay["severity"] == "warning"
    [man] = await rows(k, "order.manual")
    assert man["change"]["total_paise"] == 18000 and man["change"]["has_allergy_note"] is True
    assert man["change"]["lines"] == [{"item_id": k.items["Masala Chai"], "name": "Masala Chai", "qty": 2, "price_paise": 9000}]
    assert "peanuts" not in str(man["change"]) and "Busy" not in str(delay["change"])
    assert man["order_id"] == manual["id"]


async def test_export_writes_audit_export_and_caps_rows(k: Kitchen, monkeypatch: pytest.MonkeyPatch) -> None:
    cust = str(await k.db.fetchval(
        "insert into customers (cafe_id, wa_id, name) values ($1, '919999760077', '=cmd Bob') returning id::text", k.cafe_id))
    await write(k, "shift_manager", "chat.reply", {"customer_id": cust, "chars": 12})
    await write(k, "manager", "order.cancel", {"number": 9, "from": "NEW", "reason_code": "out_of_stock"})
    async with client_as(k, "owner") as api:
        r = await api.get("/audit/export", params={"severity": ["critical", "info"]})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert re.fullmatch(r'attachment; filename="audit-log-\d{4}-\d{2}-\d{2}\.csv"', r.headers["content-disposition"])
        lines = list(csv.reader(io.StringIO(r.text)))
        assert lines[0] == viewer.CSV_COLUMNS and len(lines) == 3
        by_action = {line[6]: dict(zip(lines[0], line, strict=True)) for line in lines[1:]}
        assert by_action["chat.reply"]["customer_first_name"] == "'=cmd"  # formula-escaped
        assert by_action["order.cancel"]["severity"] == "critical"
        assert by_action["order.cancel"]["severity_reason"] == "Cancelled by staff"
        assert by_action["order.cancel"]["sentence"] == "Kiran (Manager) cancelled order #9 (was New) — Out of stock"
        assert "919999760077" not in r.text

        [exported] = await rows(k, "audit.export")
        assert exported["change"] == {"rows": 2, "filters": ["severities"]}
        assert exported["actor_role"] == "owner" and exported["severity"] == "info"

        monkeypatch.setattr(viewer, "EXPORT_CAP", 2)
        r = await api.get("/audit/export")  # 3 rows now, the export row included
        assert r.status_code == 400 and r.json()["error"]["message"] == "Too many rows — narrow the dates or filters."
    assert len(await rows(k, "audit.export")) == 1  # a refused export isn't audited


async def test_stored_severity_equals_classify_for_all_exercised_actions(k: Kitchen) -> None:
    a = await wa_order(k)
    b = await wa_order(k, WA2, "Riya", "Masala Chai")
    await move(k, "shift_manager", a["id"], "ACCEPTED")
    async with client_as(k, "shift_manager") as api:
        assert (await api.post(f"/orders/{a['id']}/delay", json={"minutes": 5})).status_code == 200
        assert (await api.post(f"/orders/{a['id']}/undo")).status_code in (200, 409, 422)
        assert (await api.post(f"/menu/{k.items['Avocado Toast']}/availability", json={"available": False})).status_code == 200
        c = await conv(k)
        assert (await api.post(f"/conversations/{c['id']}/reply", json={"text": "On it"})).status_code == 200
    async with client_as(k, "manager") as api:
        assert (await api.post(f"/conversations/{c['id']}/takeover")).status_code == 200
        assert (await api.post(f"/conversations/{c['id']}/handback")).status_code == 200
        r = await api.post(f"/orders/{b['id']}/cancel", json={"reason_code": "kitchen_issue", "expected_version": b["version"]})
        assert r.status_code == 200, r.text
        assert (await api.post(f"/cafes/{k.cafe_id}/pause-ordering", json={"paused": True})).status_code == 200
        assert (await api.post(f"/cafes/{k.cafe_id}/pause-ordering", json={"paused": False})).status_code == 200
    async with client_as(k, "owner") as api:
        assert (await api.put(f"/cafes/{k.cafe_id}/settings", json={"alert_phone": "+919999000901"})).status_code == 200
        assert (await api.put(f"/cafes/{k.cafe_id}/settings", json={"tone": "crisp"})).status_code == 200
        mid = await k.db.fetchval("select id::text from memberships where cafe_id = $1 and user_id = $2", k.cafe_id,
                                  k.staff["manager"])
        assert (await api.patch(f"/team/{mid}", json={"role": "shift_manager"})).status_code == 200
        assert (await api.get("/audit/export")).status_code == 200
    written = await rows(k)
    actions = {r["action"] for r in written}
    assert {"order.transition", "order.delay", "menu.availability", "chat.takeover", "chat.reply", "chat.handback",
            "order.cancel", "ordering.pause", "ordering.resume", "settings.update", "team.update", "audit.export"} <= actions
    assert {r["severity"] for r in written} == {"critical", "warning", "info"}
    for r in written:
        assert r["severity"] == classify(r), (r["action"], r["change"])
