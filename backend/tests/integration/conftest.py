"""Database integration tests run against INTEGRATION_DATABASE_URL (the hosted dev project by hand, the local
stack in CI). Every test gets its own throwaway café, deleted afterwards; seeded data is never touched.
"""

import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest

from app.db import pool as pool_mod

DSN = os.environ.get("INTEGRATION_DATABASE_URL", "")
APPEND_ONLY = ["consents", "order_events", "redemptions", "llm_calls", "audit_log"]


@dataclass
class CafeFixture:
    cafe_id: str
    customer_id: str
    conversation_id: str


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Pool]:
    if not DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set")
    pool_mod.set_pool(None)
    pool = await pool_mod.open_pool(DSN, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool_mod.close_pool()


async def create_test_cafe(conn: asyncpg.Connection) -> CafeFixture:
    tag = secrets.token_hex(6)
    cafe_id = await conn.fetchval(
        "insert into cafes (name, phone_number_id, status) values ($1, $2, 'live') returning id::text",
        f"Test café {tag}", f"test-{tag}",
    )
    await conn.execute("insert into cafe_counters (cafe_id) values ($1) on conflict do nothing", cafe_id)
    customer_id = await conn.fetchval(
        "insert into customers (cafe_id, wa_id, name) values ($1, $2, 'Test') returning id::text",
        cafe_id, f"91000{tag[:7]}",
    )
    conversation_id = await conn.fetchval(
        "insert into conversations (cafe_id, customer_id) values ($1, $2) returning id::text", cafe_id, customer_id,
    )
    return CafeFixture(cafe_id, customer_id, conversation_id)


async def drop_test_cafe(conn: asyncpg.Connection, cafe_id: str) -> None:
    async with conn.transaction():
        for t in APPEND_ONLY:
            await conn.execute(f'alter table "{t}" disable trigger "{t}_append_only"')
        await conn.execute("delete from cafes where id = $1 and phone_number_id like 'test-%'", cafe_id)
        for t in APPEND_ONLY:
            await conn.execute(f'alter table "{t}" enable trigger "{t}_append_only"')


@pytest.fixture
async def cafe(db: asyncpg.Pool) -> AsyncIterator[CafeFixture]:
    async with db.acquire() as conn:
        c = await create_test_cafe(conn)
    try:
        yield c
    finally:
        async with db.acquire() as conn:
            await drop_test_cafe(conn, c.cafe_id)
