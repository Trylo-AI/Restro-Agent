"""Writes runs, spans and events (PDS §6.4, §6.6). The only code that writes `ops_*` tables.

A background writer per event loop drains a bounded queue: one statement to insert a RUNNING row, one upsert
plus one multi-row insert of spans and events when the run ends. Rules that keep tracing harmless:

- never inside the business transaction: its own connection, after the work, off the request path;
- never raises: a write failure logs one `ops.write_failed` warning and is dropped;
- self-disabling: missing tables → one `ops.tables_missing` warning, then nothing is written;
- no pool open (unit tests, scripts) → nothing is written;
- at most one pooled connection at a time is used for tracing.
"""

import asyncio
import contextlib
import contextvars
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg

from app.core.logging import get_logger
from app.db import pool as pool_mod
from app.ops.tracing import version
from app.ops.tracing.trace import Run, _warn_once, set_db_offset

log = get_logger(__name__)

QUEUE_MAX = 10_000
HEARTBEAT_S = 15
STALE_INSTANCE_S = 60
STALE_RUN_MIN = 30

_service = "app"
_tables_missing = False
_writer: "_Writer | None" = None


def configure(service: str) -> None:
    global _service
    _service = service


def service() -> str:
    return _service


def _now() -> datetime:
    return datetime.now(UTC)


class _Writer:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(QUEUE_MAX)
        # A fresh context: the writer's own statements must never land in the run that happened to start it.
        self.task = loop.create_task(self._run(), context=contextvars.Context())
        self.idle = asyncio.Event()
        self.idle.set()
        self.deploy_checked = False
        self.started_at = _now()

    async def _run(self) -> None:
        last_beat = 0.0
        while True:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=HEARTBEAT_S)
            except TimeoutError:
                item = None
            now = self.loop.time()
            if now - last_beat >= HEARTBEAT_S:
                last_beat = now
                await self._safe(self._heartbeat)
            if item is None:
                if self.queue.empty():
                    self.idle.set()
                continue
            op, payload = item
            await self._safe(lambda: _WRITES[op](payload))  # noqa: B023  (called at once)
            self.queue.task_done()
            if self.queue.empty():
                self.idle.set()

    async def _safe(self, fn: Callable[[], Awaitable[None]]) -> None:
        global _tables_missing
        if _tables_missing or pool_mod._pool is None:
            return
        try:
            await fn()
        except asyncpg.UndefinedTableError:
            _tables_missing = True
            log.warning("ops.tables_missing")
        except Exception as exc:  # never raise; one warning per process
            _warn_once("ops.write_failed", error_class=type(exc).__name__)

    async def _heartbeat(self) -> None:
        async with pool_mod.get_pool().acquire() as c:
            sent = _now()
            db_now = await c.fetchval(
                """insert into ops_instances (instance, service, git_sha, started_at, last_seen)
                   values ($1, $2, $3, $4, now())
                   on conflict (instance) do update set last_seen = now() returning last_seen""",
                version.instance_id(_service), _service, version.git_sha(), self.started_at)
            back = _now()
            set_db_offset((db_now - (sent + (back - sent) / 2)).total_seconds())
            if not self.deploy_checked:
                self.deploy_checked = True
                last = await c.fetchval("select git_sha from ops_deploys where service = $1 order by at desc limit 1",
                                        _service)
                if last != version.git_sha():
                    await c.execute("insert into ops_deploys (at, service, git_sha, app_version) values (now(), $1, $2, $3)",
                                    _service, version.git_sha(), version.app_version())

    def put(self, op: str, payload: Any) -> None:
        try:
            self.queue.put_nowait((op, payload))
            self.idle.clear()
        except asyncio.QueueFull:
            _warn_once("ops.queue_full")


def _get_writer() -> _Writer | None:
    global _writer
    if _tables_missing or pool_mod._pool is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _writer is None or _writer.loop is not loop or _writer.task.done():
        _writer = _Writer(loop)
    return _writer


# ── called by trace.py ─────────────────────────────────────────────────

def started(r: Run) -> None:
    if (w := _get_writer()) is not None:
        r.inserted = True
        w.put("start", _start_row(r))


def stage(r: Run) -> None:
    if (w := _get_writer()) is not None:
        w.put("stage", r)


def finished(r: Run) -> None:
    if (w := _get_writer()) is not None:
        w.put("end", r)


async def flush(timeout: float = 10) -> None:  # noqa: ASYNC109
    """Wait until everything queued so far is written (shutdown, tests)."""
    w = _writer
    if w is None or w.loop is not asyncio.get_running_loop():
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(w.queue.join(), timeout)


async def heartbeat_now() -> None:
    if (w := _get_writer()) is not None:
        await w._safe(w._heartbeat)


# ── the writes ─────────────────────────────────────────────────────────

_RUN_COLS = ("run_id, parent_run_id, upstream_trace, upstream_service, service, kind, name, subject_type, subject_id, "
             "tenant_id, attempt, instance, queued_at, started_at, current_stage, git_sha, app_version, config_hash, "
             "settings, labels")


def _start_row(r: Run) -> tuple[Any, ...]:
    return (r.run_id, r.parent_run_id, r.upstream_trace, r.upstream_service, r.service, r.kind, r.name, r.subject_type,
            r.subject_id, r.tenant_id, r.attempt, version.instance_id(r.service), r.queued_at, r.started_at,
            r.current_stage, version.git_sha(), version.app_version(), version.config_hash(),
            version.settings_snapshot(), r.labels)


_INSERT_START = f"insert into ops_runs ({_RUN_COLS}) values ({', '.join(f'${i}' for i in range(1, 21))}) on conflict (run_id) do nothing"  # noqa: E501,S608  (fixed columns)


async def _write_start(row: tuple[Any, ...]) -> None:
    async with pool_mod.get_pool().acquire() as c:
        await c.execute(_INSERT_START, *row)


async def _write_stage(r: Run) -> None:
    """Throttled by the writer's own pace: one pending update per run, carrying the stage current at write time."""
    r._stage_pending = False
    if r.closed:
        return
    async with pool_mod.get_pool().acquire() as c:
        await c.execute("update ops_runs set current_stage = $2 where run_id = $1 and outcome = 'RUNNING'",
                        r.run_id, r.current_stage)


async def _write_end(r: Run) -> None:
    finished_at = _now()
    row = _start_row(r) + (
        finished_at, r.duration_ms, r.queue_wait_ms, r.outcome, r.http_status, r.error_stage, r.error_code,
        r.error_type, r.failure_reason, r.failure_fingerprint, r.degradations, r.input_size, r.input_size_unit,
        r.counts, r.stage_ms, r.dep_ms, r.dep_calls, r.bytes_in, r.bytes_out, len(r.spans),
    )
    end_cols = ("finished_at, duration_ms, queue_wait_ms, outcome, http_status, error_stage, error_code, error_type, "
                "failure_reason, failure_fingerprint, degradations, input_size, input_size_unit, counts, stage_ms, "
                "dep_ms, dep_calls, bytes_in, bytes_out, span_count")
    updates = ", ".join(f"{c.strip()} = excluded.{c.strip()}" for c in end_cols.split(",")) + \
        ", labels = excluded.labels, tenant_id = coalesce(ops_runs.tenant_id, excluded.tenant_id), " \
        "subject_type = coalesce(ops_runs.subject_type, excluded.subject_type), " \
        "subject_id = coalesce(ops_runs.subject_id, excluded.subject_id), name = excluded.name"
    spans, events = r.spans, r.events
    async with pool_mod.get_pool().acquire() as c:
        await c.execute(
            _end_sql(len(row), end_cols, updates), *row,
            [s.span_id for s in spans], [s.parent_id for s in spans], [s.name for s in spans], [s.level for s in spans],
            [s.kind for s in spans], [s.start_ms for s in spans], [s.duration_ms for s in spans],
            [s.outcome for s in spans], [s.target for s in spans], [json.dumps(s.attrs, default=str) for s in spans],
            [s.error for s in spans],
            [e["seq"] for e in events], [e["at_ms"] for e in events], [e["level"] for e in events],
            [e["span_name"] for e in events], [e["logger"] for e in events], [e["message"] for e in events],
            [json.dumps(e["detail"], default=str) if e["detail"] else None for e in events])


def _end_sql(n: int, end_cols: str, updates: str) -> str:
    """One statement, one round trip: the run upsert plus its spans and events, atomic together."""
    p = [f"${i}" for i in range(n + 1, n + 19)]
    values = ", ".join(f"${i}" for i in range(1, n + 1))
    # Fixed columns; every value is a parameter.
    return (
        f"with r as (insert into ops_runs ({_RUN_COLS}, {end_cols}) values ({values}) "  # noqa: S608
        f"on conflict (run_id) do update set {updates} returning run_id), "
        "s as (insert into ops_spans (run_id, span_id, parent_id, name, level, kind, start_ms, duration_ms, outcome, "
        "target, attrs, error) select (select run_id from r), x.span_id, x.parent_id, x.name, x.level, x.kind, "
        "x.start_ms, x.duration_ms, x.outcome, x.target, x.attrs::jsonb, x.error "
        f"from unnest({p[0]}::int[], {p[1]}::int[], {p[2]}::text[], {p[3]}::smallint[], {p[4]}::text[], "
        f"{p[5]}::float8[], {p[6]}::float8[], {p[7]}::text[], {p[8]}::text[], {p[9]}::text[], {p[10]}::text[]) "
        "as x(span_id, parent_id, name, level, kind, start_ms, duration_ms, outcome, target, attrs, error) "
        "on conflict do nothing returning 1), "
        "e as (insert into ops_run_events (run_id, seq, at_ms, level, span_name, logger, message, detail) "
        "select (select run_id from r), y.seq, y.at_ms, y.level, y.span_name, y.logger, y.message, y.detail::jsonb "
        f"from unnest({p[11]}::int[], {p[12]}::float8[], {p[13]}::text[], {p[14]}::text[], {p[15]}::text[], "
        f"{p[16]}::text[], {p[17]}::text[]) as y(seq, at_ms, level, span_name, logger, message, detail) "
        "on conflict do nothing returning 1) "
        "select (select count(*) from s), (select count(*) from e)")


_WRITES: dict[str, Callable[[Any], Awaitable[None]]] = {"start": _write_start, "stage": _write_stage, "end": _write_end}


# ── sweeper and retention (registered as scheduled jobs by main_worker) ──

async def sweep() -> dict[str, int]:
    """RUNNING runs whose process stopped heart-beating, or that ran for 30 min, become ABANDONED."""
    if pool_mod._pool is None or _tables_missing:
        return {"abandoned": 0}
    async with pool_mod.get_pool().acquire() as c:
        status = await c.execute(
            f"""update ops_runs set outcome = 'ABANDONED', finished_at = now(), error_code = 'ABANDONED',
                       error_stage = current_stage,
                       duration_ms = extract(epoch from now() - started_at) * 1000
                where outcome = 'RUNNING'
                  and (started_at < now() - interval '{STALE_RUN_MIN} minutes'
                       or instance not in (select instance from ops_instances
                                           where last_seen > now() - interval '{STALE_INSTANCE_S} seconds'))
                  and started_at < now() - interval '{STALE_INSTANCE_S} seconds'""")  # noqa: S608  (constants)
    n = int(status.split()[-1]) if status else 0
    if n:
        log.warning("ops.runs_abandoned", count=n)
    return {"abandoned": n}


RETENTION = [  # (table, where, keep days) — PDS §5.5
    ("ops_spans", "level = 3 and run_id in (select run_id from ops_runs where started_at < now() - interval '{d} days')", 14),
    ("ops_spans", "run_id in (select run_id from ops_runs where started_at < now() - interval '{d} days')", 90),
    ("ops_run_events", "run_id in (select run_id from ops_runs where started_at < now() - interval '{d} days')", 90),
    ("ops_runs", "started_at < now() - interval '{d} days'", 365),
    ("ops_instances", "last_seen < now() - interval '{d} days'", 7),
]


async def retention() -> dict[str, int]:
    """Batched deletes, 5,000 rows at a time."""
    deleted = 0
    if pool_mod._pool is None or _tables_missing:
        return {"deleted": 0}
    for table, where, days in RETENTION:
        pk = "ctid"
        cond = where.format(d=days)
        while True:
            async with pool_mod.get_pool().acquire() as c:
                status = await c.execute(
                    f"delete from {table} where {pk} in (select {pk} from {table} where {cond} limit 5000)")  # noqa: S608
            n = int(status.split()[-1])
            deleted += n
            if n < 5000:
                break
    return {"deleted": deleted}
