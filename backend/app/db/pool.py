"""asyncpg pool on the Supabase pooler in transaction mode.

statement_cache_size=0: the transaction pooler can't keep prepared statements across transactions.
Small pool per instance (docs/architecture.md). jsonb/json come back as Python objects.
"""

import json
from typing import Any

import asyncpg

from app.core.config import get_settings

POOL_MAX_SIZE = 5
_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(typename, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    if get_settings().ops_tracing_enabled:
        from app.ops.tracing.hooks.db import on_query

        conn.add_query_logger(on_query)  # one db.query span per statement inside a traced run


async def open_pool(dsn: str | None = None, *, min_size: int = 1, max_size: int = POOL_MAX_SIZE) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            dsn or settings.database_url.get_secret_value(),
            min_size=min_size,
            max_size=max_size,
            statement_cache_size=0,
            init=_init_connection,
            command_timeout=15,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not open")
    return _pool


def set_pool(pool: Any) -> None:
    """Tests inject a pool (or a fake)."""
    global _pool
    _pool = pool
