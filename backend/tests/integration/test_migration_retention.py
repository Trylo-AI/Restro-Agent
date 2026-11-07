"""Migration 20261101000001_retention_audit_recommend (docs/retention-audit-recommend.md#data-model).

Redemption shapes, the `recommend` purpose, audit_log append-only, and the new views as the console's role sees them
(`set local role authenticated` + JWT claims: the SQL that tests/integration/test_rls.py exercises over HTTP).
"""

import json
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest

from tests.integration.conftest import CafeFixture, create_test_cafe, drop_test_cafe
from tests.integration.conversation_kit import create_staff, drop_staff

pytestmark = pytest.mark.integration


@dataclass
class Shop:
    db: asyncpg.Pool
    cafe_id: str
    customer_id: str
    conversation_id: str
    session_id: str
    campaign_id: str
    staff: dict[str, str]


@pytest.fixture
async def shop(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Shop]:
    async with db.acquire() as c:
        staff = await create_staff(c, cafe.cafe_id)
        table_id = await c.fetchval(
            "insert into tables (cafe_id, label, qr_text) values ($1, 'T7', 'Hi, I''m at Table 7') returning id::text",
            cafe.cafe_id)
        session_id = await c.fetchval(
            "insert into table_sessions (cafe_id, table_id) values ($1, $2) returning id::text", cafe.cafe_id, table_id)
        campaign_id = await c.fetchval(
            """insert into campaigns (cafe_id, name, segment, template_name, discount_percent, status)
               values ($1, 'Weekend comeback', 'lapsed', 'winback', 20, 'sending') returning id::text""", cafe.cafe_id)
    try:
        yield Shop(db, cafe.cafe_id, cafe.customer_id, cafe.conversation_id, session_id, campaign_id, staff)
    finally:
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


async def recipient(c: asyncpg.Connection, s: Shop, status: str = "delivered") -> tuple[str, str]:
    """A fresh customer with one code from the shop's campaign: (recipient_id, code)."""
    customer_id = await c.fetchval(
        "insert into customers (cafe_id, wa_id, name) values ($1, $2, 'Riya Sharma') returning id::text",
        s.cafe_id, f"91999{secrets.token_hex(4)[:7]}")
    code = f"BACK{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()[:3]}"
    rid = await c.fetchval(
        """insert into campaign_recipients (cafe_id, campaign_id, customer_id, code, status)
           values ($1, $2, $3, $4, $5) returning id::text""", s.cafe_id, s.campaign_id, customer_id, code, status)
    return rid, code


async def counter(c: asyncpg.Connection, s: Shop, rid: str, code: str, bill: int | None, discount: int,
                  key: str | None = None, session_id: str | None = None) -> str:
    return str(await c.fetchval(
        """insert into redemptions (cafe_id, campaign_recipient_id, code, channel, table_session_id, bill_paise,
                                    discount_paise, idempotency_key, redeemed_by)
           values ($1, $2, $3, 'counter', $4, $5, $6, $7, $8) returning id""",
        s.cafe_id, rid, code, session_id, bill, discount, key, s.staff["shift_manager"]))


def claims(user_id: str) -> str:
    return json.dumps({"sub": user_id, "role": "authenticated"})


async def as_member(c: asyncpg.Connection, user_id: str) -> None:
    """Inside a transaction: the rest of it runs as the console would through PostgREST."""
    await c.execute("set local role authenticated")
    await c.execute("select set_config('request.jwt.claims', $1, true)", claims(user_id))


# ── Redemptions ────────────────────────────────────────────────────────────────────────────────────────────────


async def test_counter_shape_accepted_and_bad_shapes_refused(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        rid, code = await recipient(c, shop)
        rid_id = await counter(c, shop, rid, code, 124000, 24800, key=f"key-{secrets.token_hex(6)}")
        row = await c.fetchrow("select channel, bill_paise, order_id, table_session_id from redemptions where id = $1",
                               uuid.UUID(rid_id))
        assert dict(row) == {"channel": "counter", "bill_paise": 124000, "order_id": None, "table_session_id": None}

        bad = [
            # counter without the amount
            ("counter", None, None),
            # counter on a table session
            ("counter", 50000, shop.session_id),
            # table bill without a session
            ("table_bill", 50000, None),
        ]
        for channel, bill, session_id in bad:
            other, other_code = await recipient(c, shop)
            with pytest.raises(asyncpg.CheckViolationError, match="redemptions_channel_shape"):
                await c.execute(
                    """insert into redemptions (cafe_id, campaign_recipient_id, code, channel, table_session_id,
                                                bill_paise, discount_paise)
                       values ($1, $2, $3, $4, $5, $6, 0)""", shop.cafe_id, other, other_code, channel, session_id, bill)
        other, other_code = await recipient(c, shop)
        with pytest.raises(asyncpg.CheckViolationError, match="redemptions_channel_check"):
            await c.execute(
                """insert into redemptions (cafe_id, campaign_recipient_id, code, channel, bill_paise, discount_paise)
                   values ($1, $2, $3, 'till', 100, 0)""", shop.cafe_id, other, other_code)


async def test_discount_never_exceeds_the_bill(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        rid, code = await recipient(c, shop)
        with pytest.raises(asyncpg.CheckViolationError, match="redemptions_discount_within_bill"):
            await counter(c, shop, rid, code, 10000, 10001)
        await counter(c, shop, rid, code, 10000, 10000)


async def test_idempotency_key_unique_per_cafe(shop: Shop) -> None:
    key = f"till-{secrets.token_hex(8)}"
    async with shop.db.acquire() as c:
        rid, code = await recipient(c, shop)
        await counter(c, shop, rid, code, 50000, 0, key=key)
        rid2, code2 = await recipient(c, shop)
        with pytest.raises(asyncpg.UniqueViolationError, match="redemptions_idempotency"):
            await counter(c, shop, rid2, code2, 50000, 0, key=key)
        with pytest.raises(asyncpg.CheckViolationError, match="redemptions_idempotency_key_check"):
            await counter(c, shop, rid2, code2, 50000, 0, key="short")

        other = await create_test_cafe(c)
        try:
            cid = await c.fetchval(
                """insert into campaigns (cafe_id, name, segment, template_name) values ($1, 'B', 'lapsed', 'winback')
                   returning id::text""", other.cafe_id)
            orid = await c.fetchval(
                """insert into campaign_recipients (cafe_id, campaign_id, customer_id, code, status)
                   values ($1, $2, $3, $4, 'delivered') returning id::text""",
                other.cafe_id, cid, other.customer_id, f"OTHER{secrets.token_hex(2).upper()}-ABC")
            await c.execute(
                """insert into redemptions (cafe_id, campaign_recipient_id, code, channel, bill_paise, discount_paise,
                                            idempotency_key)
                   values ($1, $2, 'X', 'counter', 100, 0, $3)""", other.cafe_id, orid, key)
        finally:
            await drop_test_cafe(c, other.cafe_id)


async def test_old_style_table_bill_insert_still_works(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        rid, code = await recipient(c, shop)
        row = await c.fetchrow(
            """insert into redemptions (cafe_id, campaign_recipient_id, code, table_session_id, discount_paise, redeemed_by)
               values ($1, $2, $3, $4, 2400, $5) returning channel, bill_paise, idempotency_key""",
            shop.cafe_id, rid, code, shop.session_id, shop.staff["shift_manager"])
        assert dict(row) == {"channel": "table_bill", "bill_paise": None, "idempotency_key": None}


async def test_one_redemption_per_code_across_channels(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        rid, code = await recipient(c, shop)
        await c.execute(
            """insert into redemptions (cafe_id, campaign_recipient_id, code, table_session_id, discount_paise)
               values ($1, $2, $3, $4, 0)""", shop.cafe_id, rid, code, shop.session_id)
        with pytest.raises(asyncpg.UniqueViolationError):
            await counter(c, shop, rid, code, 50000, 0)


# ── LLM purpose ────────────────────────────────────────────────────────────────────────────────────────────────


async def test_llm_calls_accepts_recommend_and_refuses_unknown(shop: Shop) -> None:
    sql = """insert into llm_calls (cafe_id, provider, model, purpose, conversation_id, pricing_version, latency_ms, outcome)
             values ($1, 'stub', 'stub', $2, $3, 'test', 5, 'ok')"""
    async with shop.db.acquire() as c:
        await c.execute(sql, shop.cafe_id, "recommend", shop.conversation_id)
        with pytest.raises(asyncpg.CheckViolationError, match="llm_calls_purpose_check"):
            await c.execute(sql, shop.cafe_id, "foo", shop.conversation_id)


# ── Audit log ──────────────────────────────────────────────────────────────────────────────────────────────────


async def audit_row(c: asyncpg.Connection, s: Shop, action: str, change: dict[str, object], *,
                    actor: str | None = None, role: str = "shift_manager", support: bool = False,
                    severity: str = "info") -> uuid.UUID:
    """A row inserted directly (severity is required since 20261101000002)."""
    row_id: uuid.UUID = await c.fetchval(
        """insert into audit_log (cafe_id, actor_id, actor_role, support, action, change, severity)
           values ($1, $2, $3, $4, $5, $6, $7) returning id""",
        s.cafe_id, actor, role, support, action, change, severity)
    return row_id


# The backfill itself is covered after 20261101000002 by
# tests/integration/test_audit_log.py::test_backfilled_rows_have_severity_after_0002.


async def test_audit_log_still_append_only(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        row = await audit_row(c, shop, "order.transition", {"to": "READY"})
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            await c.execute("update audit_log set action = 'x' where id = $1", row)
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            await c.execute("delete from audit_log where id = $1", row)


async def test_owner_cannot_select_audit_log_directly(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        await audit_row(c, shop, "order.cancel", {"order_id": str(uuid.uuid4())}, actor=shop.staff["shift_manager"],
                        severity="critical")
        async with c.transaction():
            await as_member(c, shop.staff["owner"])
            assert await c.fetchval("select count(*) from audit_log where cafe_id = $1", shop.cafe_id) == 0


# ── Views as the console sees them ─────────────────────────────────────────────────────────────────────────────


async def test_campaign_views_owner_only_and_count_a_counter_redemption(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        rid, code = await recipient(c, shop)
        await recipient(c, shop, status="failed")
        await counter(c, shop, rid, code, 124000, 24800)
        for role, expected in (("owner", 2), ("manager", 0), ("shift_manager", 0)):
            async with c.transaction():
                await as_member(c, shop.staff[role])
                n = await c.fetchval("select count(*) from v_campaign_recipients where cafe_id = $1", shop.cafe_id)
                insights = await c.fetch("select * from v_campaign_insights where cafe_id = $1", shop.cafe_id)
            assert n == expected, role
            assert len(insights) == (1 if expected else 0), role
        async with c.transaction():
            await as_member(c, shop.staff["owner"])
            row = await c.fetchrow(
                """select redemption_channel, bill_paise, discount_paise, redeemed_by_name, came_back, masked_phone, deleted
                   from v_campaign_recipients where recipient_id = $1""", uuid.UUID(rid))
            ins = await c.fetchrow("select * from v_campaign_insights where campaign_id = $1", uuid.UUID(shop.campaign_id))
        assert row["redemption_channel"] == "counter" and row["bill_paise"] == 124000 and row["discount_paise"] == 24800
        assert row["redeemed_by_name"] == "Ravi" and row["came_back"] is True and row["deleted"] is False
        assert "••" in row["masked_phone"]
        assert (ins["recipients"], ins["failed"], ins["redeemed"], ins["redeemed_counter"], ins["came_back"]) == (2, 1, 1, 1, 1)
        assert (ins["counter_bill_paise"], ins["came_back_paise"], ins["discount_paise"]) == (124000, 124000, 24800)


async def test_redemptions_view_member_only_own_cafe(shop: Shop) -> None:
    async with shop.db.acquire() as c:
        rid, code = await recipient(c, shop)
        await counter(c, shop, rid, code, 50000, 10000)
        other = await create_test_cafe(c)
        other_staff = await create_staff(c, other.cafe_id)
        try:
            async with c.transaction():
                await as_member(c, shop.staff["shift_manager"])
                rows = await c.fetch("select * from v_redemptions")
            assert {str(r["cafe_id"]) for r in rows} == {shop.cafe_id}
            mine = next(r for r in rows if str(r["campaign_recipient_id"]) == rid)
            assert mine["campaign_name"] == "Weekend comeback" and mine["first_name"] == "Riya"
            assert mine["redeemed_by_name"] == "Ravi" and mine["channel"] == "counter" and "••" in mine["masked_phone"]
            assert "wa_id" not in mine.keys()
            async with c.transaction():
                await as_member(c, other_staff["owner"])
                assert await c.fetchval("select count(*) from v_redemptions where cafe_id = $1", shop.cafe_id) == 0
            async with c.transaction():
                await c.execute("set local role anon")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await c.fetch("select * from v_redemptions limit 1")
        finally:
            await drop_test_cafe(c, other.cafe_id)
            await drop_staff(c, other_staff)


@pytest.mark.parametrize("view", ["v_campaign_recipients", "v_campaign_insights", "v_redemptions"])
async def test_new_views_are_read_only_for_clients(shop: Shop, view: str) -> None:
    async with shop.db.acquire() as c:
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await c.fetchval("select has_table_privilege('authenticated', $1, $2)", view, privilege)
        assert await c.fetchval("select has_table_privilege('authenticated', $1, 'SELECT')", view)
        assert not await c.fetchval("select has_table_privilege('anon', $1, 'SELECT')", view)
        async with c.transaction():
            await as_member(c, shop.staff["owner"])
            with pytest.raises(asyncpg.PostgresError):  # not updatable or no privilege: refused either way
                await c.execute(f"delete from {view}")
