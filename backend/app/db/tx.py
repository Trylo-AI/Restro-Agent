"""Short transactions. Never hold one across an LLM call or a WhatsApp send (docs/rules.md#backend)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.db.pool import get_pool


@asynccontextmanager
async def tx() -> AsyncIterator[asyncpg.Connection]:
    async with get_pool().acquire() as conn, conn.transaction():
        yield conn


@asynccontextmanager
async def conn() -> AsyncIterator[asyncpg.Connection]:
    """A connection for a single read (autocommit per statement)."""
    async with get_pool().acquire() as c:
        yield c
