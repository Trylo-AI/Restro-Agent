"""Short transactions. Never hold one across an LLM call or a WhatsApp send (docs/rules.md#backend).

Inside a traced run, the wait for a pooled connection is recorded (`db.acquire`) and a transaction is a `db.tx`
span; the statements themselves come from the pool's query logger (app/ops/tracing/hooks/db.py).
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.db.pool import get_pool
from app.ops.tracing import span
from app.ops.tracing.hooks.db import on_acquire


@asynccontextmanager
async def tx() -> AsyncIterator[asyncpg.Connection]:
    started = time.perf_counter()
    async with get_pool().acquire() as conn:
        on_acquire((time.perf_counter() - started) * 1000, started)
        with span("db.tx"):
            async with conn.transaction():
                yield conn


@asynccontextmanager
async def conn() -> AsyncIterator[asyncpg.Connection]:
    """A connection for a single read (autocommit per statement)."""
    started = time.perf_counter()
    async with get_pool().acquire() as c:
        on_acquire((time.perf_counter() - started) * 1000, started)
        yield c
