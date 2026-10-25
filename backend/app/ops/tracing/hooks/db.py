"""Database hook: one `db.query` span per statement, from asyncpg's query logger (PDS §6.3).

asyncpg times each statement itself and calls the logger with `call_soon`, which carries the context of the task
that ran the statement, so the span lands in the right run under the right parent. Only the operation, first
table and a hash of the normalised statement are kept; parameter values never leave asyncpg.
"""

import time
from typing import Any

from app.ops.tracing import trace
from app.ops.tracing.fingerprint import statement

ACQUIRE_MIN_MS = 5.0
_cache: dict[str, tuple[str, str, str | None]] = {}


def on_query(record: Any) -> None:
    if trace.current_run() is None:
        return
    try:
        sql: str = record.query
        meta = _cache.get(sql)
        if meta is None:
            meta = statement(sql)
            if len(_cache) < 2000:
                _cache[sql] = meta
        op, digest, table = meta
        elapsed_ms = record.elapsed * 1000
        trace.record_span(
            "db.query", started_perf=time.perf_counter() - record.elapsed, duration_ms=elapsed_ms,
            outcome="failed" if record.exception is not None else "ok", target=table or op,
            error=type(record.exception).__name__ if record.exception is not None else None,
            **{"db.operation": op, "db.table": table, "db.statement_hash": digest},
        )
    except Exception:  # noqa: BLE001,S110  (tracing never raises into the work)
        pass


def on_acquire(waited_ms: float, started_perf: float) -> None:
    """Called by db/tx.py after a pooled connection was handed over."""
    r = trace.current_run()
    if r is None:
        return
    r.count(db_wait_ms=round(waited_ms, 3))
    if waited_ms >= ACQUIRE_MIN_MS:
        trace.record_span("db.acquire", started_perf=started_perf, duration_ms=waited_ms, target="pool")
