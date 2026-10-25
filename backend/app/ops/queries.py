"""Every dashboard number, one function per definition (PDS §7). Read-only SQL over the ops tables (and, for the
Live page and tokens per order, a few business tables). Pages never compute a metric themselves.

Definitions used everywhere:
- unit: one subject of one run name (a turn = one pending_work row); runs without a subject are their own unit.
- final run: the unit's latest run in the window; success / failure is judged on it. Retried then successful
  = recovered, not failed. REJECTED, CANCELLED, SUPERSEDED count in neither side of the success rate.
- percentiles: over final successful runs, so a fast failure never makes the app look quick.
"""

import asyncio
import math
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from app.db import pool as pool_mod
from app.ops.tracing import names

SUCCESS = ("SUCCEEDED", "SUCCEEDED_DEGRADED")
FAILURE = ("FAILED", "ABANDONED")
HIST_BUCKETS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000]


@dataclass
class Window:
    start: datetime
    end: datetime
    service: str | None = None

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def previous(self) -> "Window":
        return Window(self.start - (self.end - self.start), self.start, self.service)

    @property
    def interval(self) -> timedelta:
        """Chart bucket: 5 min up to 6 h, 1 h up to 3 days, else 1 day."""
        s = self.seconds
        return timedelta(minutes=5) if s <= 6 * 3600 else timedelta(hours=1) if s <= 3 * 86400 else timedelta(days=1)


def window(start: str | None, end: str | None, service: str | None = None) -> Window:
    to = datetime.fromisoformat(end) if end else datetime.now(UTC)
    frm = datetime.fromisoformat(start) if start else to - timedelta(hours=24)
    if to.tzinfo is None:
        to = to.replace(tzinfo=UTC)
    if frm.tzinfo is None:
        frm = frm.replace(tzinfo=UTC)
    return Window(frm, to, service or None)


# The dashboard shares the api's pool with webhooks and staff calls: at most 3 of its queries at a time.
_gate: asyncio.Semaphore | None = None
_gate_loop: asyncio.AbstractEventLoop | None = None


def _sem() -> asyncio.Semaphore:
    global _gate, _gate_loop
    loop = asyncio.get_running_loop()
    if _gate is None or _gate_loop is not loop:
        _gate, _gate_loop = asyncio.Semaphore(3), loop
    return _gate


async def _fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
    async with _sem(), pool_mod.get_pool().acquire() as c:
        return [dict(r) for r in await c.fetch(sql, *args)]


async def _val(sql: str, *args: Any) -> Any:
    async with _sem(), pool_mod.get_pool().acquire() as c:
        return await c.fetchval(sql, *args)


async def together(**jobs: Awaitable[Any]) -> dict[str, Any]:
    """Runs independent queries concurrently (bounded by the gate) and returns them by name."""
    values = await asyncio.gather(*jobs.values())
    return dict(zip(jobs.keys(), values, strict=True))


# A CTE every unit-level metric starts from: runs in the window, their unit, their position among the unit's runs.
UNITS = """
w as (select r.*, coalesce(r.subject_type || ':' || r.subject_id, r.run_id::text) as unit
      from ops_runs r
      where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
        and r.outcome <> 'RUNNING' and r.name <> 'job.ops_sweep'),
u as (select w.*, row_number() over (partition by name, unit order by started_at desc) as rn,
             count(*) over (partition by name, unit) as runs
      from w)
"""


def _args(w: Window) -> tuple[Any, ...]:
    return (w.start, w.end, w.service)


async def tables_ok() -> bool:
    try:
        return bool(await _val("select to_regclass('ops_runs') is not null and to_regclass('ops_spans') is not null"))
    except (asyncpg.PostgresError, RuntimeError):
        return False


def registry() -> dict[str, Any]:
    return {
        "spans": {k: {"level": v.level, "kind": v.kind, "description": v.description} for k, v in names.SPANS.items()},
        "reasons": {k: {"cost_only": v.cost_only, "description": v.description} for k, v in names.REASONS.items()},
    }


# ── reliability and volume (§7.1) ──────────────────────────────────────

async def unit_outcomes(w: Window, name: str | None = None, label: tuple[str, str] | None = None) -> dict[str, Any]:
    """Counts by the unit's final run, plus retried / recovered, success and degraded rates."""
    rows = await _fetch(
        f"""with {UNITS}
            select count(*) filter (where rn = 1) as units,
                   count(*) filter (where rn = 1 and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')) as succeeded,
                   count(*) filter (where rn = 1 and outcome = 'SUCCEEDED_DEGRADED') as degraded,
                   count(*) filter (where rn = 1 and outcome in ('FAILED', 'ABANDONED')) as failed,
                   count(*) filter (where rn = 1 and outcome = 'REJECTED') as rejected,
                   count(*) filter (where rn = 1 and outcome = 'CANCELLED') as cancelled,
                   count(*) filter (where rn = 1 and outcome = 'SUPERSEDED') as superseded,
                   count(*) filter (where rn = 1 and outcome = 'RETRYING') as retrying,
                   count(*) filter (where rn = 1 and runs > 1) as retried,
                   count(*) filter (where rn = 1 and runs > 1 and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED'))
                       as recovered,
                   count(*) as runs
            from u where ($4::text is null or name = $4)
              and ($5::text is null or labels ->> $5 = $6)""",  # noqa: S608  (fixed CTE)
        *_args(w), name, label[0] if label else None, label[1] if label else None)
    r = rows[0]
    decided = r["succeeded"] + r["failed"]
    r["success_rate"] = round(r["succeeded"] / decided, 4) if decided else None
    r["degraded_rate"] = round(r["degraded"] / r["succeeded"], 4) if r["succeeded"] else None
    return r


async def percentiles(w: Window, *, name: str | None = None, field: str = "duration_ms",
                      label: tuple[str, str] | None = None) -> dict[str, float | None]:
    """p50 / p90 / p95 / p99 over final successful runs. `field`: duration_ms, queue_wait_ms or counts.<key>."""
    expr = f"(counts ->> '{field.removeprefix('counts.')}')::float" if field.startswith("counts.") else field
    if not (field in ("duration_ms", "queue_wait_ms") or field.removeprefix("counts.").isidentifier()):
        raise ValueError(field)
    rows = await _fetch(
        f"""with {UNITS}
            select percentile_cont(array[0.5, 0.9, 0.95, 0.99]) within group (order by {expr}) as p, count({expr}) as n,
                   max({expr}) as max
            from u where rn = 1 and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED') and {expr} is not null
              and ($4::text is null or name = $4) and ($5::text is null or labels ->> $5 = $6)""",  # noqa: S608
        *_args(w), name, label[0] if label else None, label[1] if label else None)
    p = rows[0]["p"] or [None] * 4
    return {"p50": _r(p[0]), "p90": _r(p[1]), "p95": _r(p[2]), "p99": _r(p[3]), "max": _r(rows[0]["max"]),
            "n": rows[0]["n"]}


def _r(v: float | None) -> float | None:
    return None if v is None else round(float(v), 1)


async def series(w: Window, name: str | None = None) -> list[dict[str, Any]]:
    """Per interval: final runs by outcome, p50 / p95 work time and p50 / p95 reply time (turns)."""
    return await _fetch(
        f"""with {UNITS}
            select date_bin($5::interval, started_at, $1) as t,
                   count(*) filter (where outcome in ('SUCCEEDED')) as succeeded,
                   count(*) filter (where outcome = 'SUCCEEDED_DEGRADED') as degraded,
                   count(*) filter (where outcome in ('FAILED', 'ABANDONED')) as failed,
                   count(*) filter (where outcome = 'RETRYING') as retrying,
                   count(*) filter (where outcome = 'REJECTED') as rejected,
                   percentile_cont(0.5) within group (order by duration_ms)
                       filter (where outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')) as p50,
                   percentile_cont(0.95) within group (order by duration_ms)
                       filter (where outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')) as p95,
                   percentile_cont(0.5) within group (order by (counts ->> 'reply_ms')::float) as reply_p50,
                   percentile_cont(0.95) within group (order by (counts ->> 'reply_ms')::float) as reply_p95
            from u where rn = 1 and ($4::text is null or name = $4)
            group by 1 order by 1""",  # noqa: S608
        *_args(w), name, w.interval)


async def stage_share(w: Window, name: str = "turn") -> list[dict[str, Any]]:
    """Critical-path time per stage ÷ total work time (stages in this app are serial, top-level only)."""
    return await _fetch(
        f"""with {UNITS}, tot as (select sum(duration_ms) as total from u where name = $4)
            select k as stage, round(sum(v::float)::numeric, 1) as ms,
                   round((sum(v::float) / nullif((select total from tot), 0))::numeric, 4) as share
            from u, jsonb_each_text(stage_ms) as s(k, v) where name = $4
            group by k order by 2 desc""",  # noqa: S608
        *_args(w), name)


async def dependency_share(w: Window, name: str | None = None) -> list[dict[str, Any]]:
    return await _fetch(
        f"""with {UNITS}, tot as (select sum(duration_ms) as total from u where ($4::text is null or name = $4))
            select k as kind, round(sum(v::float)::numeric, 1) as ms,
                   round((sum(v::float) / nullif((select total from tot), 0))::numeric, 4) as share,
                   (select sum(x::int) from u u2, jsonb_each_text(u2.dep_calls) as d(kk, x)
                     where d.kk = s.k and ($4::text is null or u2.name = $4)) as calls
            from u, jsonb_each_text(dep_ms) as s(k, v) where ($4::text is null or name = $4)
            group by k order by 2 desc""",  # noqa: S608
        *_args(w), name)


async def top_failures(w: Window, limit: int = 5) -> list[dict[str, Any]]:
    return await _fetch(
        f"""with {UNITS}
            select failure_fingerprint as fingerprint, name, error_stage, error_code, error_type,
                   min(failure_reason) as sample, count(*) as runs, count(distinct unit) as units,
                   min(started_at) as first_seen, max(started_at) as last_seen
            from u where failure_fingerprint is not null
            group by 1, 2, 3, 4, 5 order by runs desc limit $4""",  # noqa: S608
        *_args(w), limit)


async def degradation_counts(w: Window) -> list[dict[str, Any]]:
    rows = await _fetch(
        f"""with {UNITS}
            select d ->> 'reason' as reason, count(*) as count, count(distinct run_id) as runs,
                   min(started_at) as first_seen, max(started_at) as last_seen
            from u, jsonb_array_elements(degradations) as d
            group by 1 order by 2 desc""",  # noqa: S608
        *_args(w))
    for r in rows:
        meta = names.REASONS.get(r["reason"])
        r["cost_only"] = meta.cost_only if meta else None
        r["description"] = meta.description if meta else None
    return rows


async def deploys(w: Window) -> list[dict[str, Any]]:
    return await _fetch("select at, service, git_sha, app_version from ops_deploys where at >= $1 and at < $2 "
                        "and ($3::text is null and service in ('api', 'worker') or service = $3) order by at", *_args(w))


async def throughput_per_min(w: Window, name: str | None = None) -> float:
    o = await unit_outcomes(w, name)
    return round(float(o["succeeded"] + o["failed"]) / max(w.seconds / 60, 1e-9), 3)


# ── run explorer and trace ─────────────────────────────────────────────

RUN_LIST_COLS = """run_id::text, service, kind, name, subject_type, subject_id, tenant_id, attempt, started_at,
    duration_ms, queue_wait_ms, outcome, http_status, error_stage, error_code, failure_fingerprint, labels, counts,
    current_stage, span_count, jsonb_array_length(degradations) as degradations"""


async def runs(w: Window, f: dict[str, str], before: datetime | None, limit: int) -> list[dict[str, Any]]:
    where = ["r.started_at >= $1", "r.started_at < $2", "($3::text is null and r.service in ('api', 'worker') or r.service = $3)"]
    args: list[Any] = [w.start, w.end, w.service]

    def add(cond: str, value: Any) -> None:
        args.append(value)
        where.append(cond.replace("?", f"${len(args)}"))

    if v := f.get("kind"):
        add("r.kind = ?", v)
    if v := f.get("name"):
        add("r.name = ?", v)
    if v := f.get("outcome"):
        add("r.outcome = any(?::text[])", v.split(","))
    if v := f.get("subject"):
        typ, _, sid = v.partition(":")
        add("r.subject_type = ?", typ)
        add("r.subject_id = ?", sid)
    if v := f.get("tenant"):
        add("r.tenant_id = ?", v)
    if v := f.get("fingerprint"):
        add("r.failure_fingerprint = ?", v)
    if v := f.get("reason"):
        add("r.degradations @> jsonb_build_array(jsonb_build_object('reason', ?::text))", v)
    if v := f.get("min_ms"):
        add("r.duration_ms >= ?", float(v))
    if v := f.get("q"):
        add("(r.run_id::text like ? || '%' or r.subject_id = ? or r.name ilike '%' || ? || '%')", v)
    for k, v in f.items():
        if k.startswith("label.") and k[6:].isidentifier():
            add(f"r.labels ->> '{k[6:]}' = ?", v)
    if before:
        add("r.started_at < ?", before)
    args.append(min(max(limit, 1), 200))
    sql = (f"select {RUN_LIST_COLS} from ops_runs r where {' and '.join(where)} "  # noqa: S608  (fixed fragments)
           f"order by r.started_at desc limit ${len(args)}")
    return await _fetch(sql, *args)


async def run_detail(run_id: str) -> dict[str, Any] | None:
    rows = await _fetch("select * from ops_runs where run_id = $1::uuid", run_id)
    if not rows:
        return None
    run = rows[0]
    run["run_id"] = str(run["run_id"])
    run["parent_run_id"] = str(run["parent_run_id"]) if run["parent_run_id"] else None
    spans = await _fetch("select span_id, parent_id, name, level, kind, start_ms, duration_ms, outcome, target, attrs, "
                         "error from ops_spans where run_id = $1::uuid order by start_ms, span_id", run_id)
    events = await _fetch("select seq, at_ms, level, span_name, logger, message, detail from ops_run_events "
                          "where run_id = $1::uuid order by seq", run_id)
    attempts: list[dict[str, Any]] = []
    if run["subject_id"]:
        attempts = await _fetch(
            "select run_id::text, attempt, started_at, duration_ms, outcome, error_stage, error_code, failure_reason "
            "from ops_runs where name = $1 and subject_type = $2 and subject_id = $3 order by started_at",
            run["name"], run["subject_type"], run["subject_id"])
    baseline = await _fetch(
        """select k as stage, percentile_cont(0.5) within group (order by v::float) as p50
           from ops_runs r, jsonb_each_text(r.stage_ms) as s(k, v)
           where r.name = $1 and r.started_at > now() - interval '7 days'
             and r.outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED') group by k""", run["name"])
    upstream = None
    if run["upstream_trace"]:
        up = await _fetch("select run_id::text, service, kind, name, started_at, outcome from ops_runs "
                          "where run_id = $1::uuid", _uuid(run["upstream_trace"]))
        upstream = up[0] if up else {"trace": run["upstream_trace"], "service": run["upstream_service"]}
    downstream = await _fetch("select run_id::text, service, kind, name, started_at, outcome, duration_ms "
                              "from ops_runs where upstream_trace = $1 order by started_at limit 50",
                              run["run_id"].replace("-", ""))
    return {"run": run, "spans": spans, "events": events, "attempts": attempts,
            "stage_p50": {b["stage"]: _r(b["p50"]) for b in baseline}, "upstream": upstream, "downstream": downstream}


def _uuid(trace: str) -> str:
    return f"{trace[:8]}-{trace[8:12]}-{trace[12:16]}-{trace[16:20]}-{trace[20:32]}"


async def subject_runs(subject_type: str, subject_id: str) -> list[dict[str, Any]]:
    return await _fetch(f"select {RUN_LIST_COLS} from ops_runs r where subject_type = $1 and subject_id = $2 "  # noqa: S608
                        "order by started_at", subject_type, subject_id)


# ── endpoints, failures, degradations ──────────────────────────────────

async def endpoints(w: Window) -> list[dict[str, Any]]:
    """Per run name: units, outcome rates and percentiles of final successful runs, and the per-interval trend."""
    rows = await _fetch(
        f"""with {UNITS}
            select kind, name, count(*) filter (where rn = 1) as units, count(*) as runs,
                   count(*) filter (where rn = 1 and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')) as succeeded,
                   count(*) filter (where rn = 1 and outcome in ('FAILED', 'ABANDONED')) as failed,
                   count(*) filter (where rn = 1 and outcome = 'REJECTED') as rejected,
                   count(*) filter (where rn = 1 and outcome = 'SUCCEEDED_DEGRADED') as degraded,
                   percentile_cont(array[0.5, 0.95, 0.99]) within group (order by duration_ms)
                       filter (where rn = 1 and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')) as p,
                   sum(duration_ms) as total_ms
            from u group by kind, name order by units desc""",  # noqa: S608
        *_args(w))
    trend = await _fetch(
        f"""with {UNITS}
            select name, date_bin($4::interval, started_at, $1) as t, count(*) as n,
                   percentile_cont(0.95) within group (order by duration_ms) as p95
            from u where rn = 1 group by 1, 2 order by 1, 2""",  # noqa: S608
        *_args(w), w.interval)
    by: dict[str, list[dict[str, Any]]] = {}
    for t in trend:
        by.setdefault(t["name"], []).append({"t": t["t"], "n": t["n"], "p95": _r(t["p95"])})
    minutes = max(w.seconds / 60, 1e-9)
    for r in rows:
        p = r.pop("p") or [None] * 3
        r.update(p50=_r(p[0]), p95=_r(p[1]), p99=_r(p[2]), per_min=round(r["units"] / minutes, 3), trend=by.get(r["name"], []))
        decided = r["succeeded"] + r["failed"]
        r["success_rate"] = round(r["succeeded"] / decided, 4) if decided else None
        r["reject_rate"] = round(r["rejected"] / r["units"], 4) if r["units"] else None
    return rows


async def failures(w: Window) -> list[dict[str, Any]]:
    """Stage → code → fingerprint, with units, recovered vs final, first / last seen and a daily trend."""
    rows = await _fetch(
        f"""with {UNITS}, fin as (select name, unit, outcome as final from u where rn = 1)
            select coalesce(u.error_stage, '(none)') as stage, coalesce(u.error_code, '(none)') as code,
                   u.failure_fingerprint as fingerprint, u.error_type, u.name, min(u.failure_reason) as sample,
                   count(*) as runs, count(distinct u.unit) as units,
                   count(distinct u.unit) filter (where fin.final in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')) as recovered,
                   count(distinct u.unit) filter (where fin.final in ('FAILED', 'ABANDONED')) as final,
                   min(u.started_at) as first_seen, max(u.started_at) as last_seen,
                   (array_agg(u.run_id::text order by u.started_at desc))[1:5] as example_run_ids
            from u join fin on fin.name = u.name and fin.unit = u.unit
            where u.failure_fingerprint is not null
            group by 1, 2, 3, 4, 5 order by runs desc""",  # noqa: S608
        *_args(w))
    daily = await _fetch(
        f"""with {UNITS}
            select failure_fingerprint as fingerprint, date_bin($4::interval, started_at, $1) as t, count(*) as n
            from u where failure_fingerprint is not null group by 1, 2 order by 2""",  # noqa: S608
        *_args(w), w.interval)
    by: dict[str, list[dict[str, Any]]] = {}
    for d in daily:
        by.setdefault(d["fingerprint"], []).append({"t": d["t"], "n": d["n"]})
    for r in rows:
        r["trend"] = by.get(r["fingerprint"], [])
    return rows


async def degradation_runs(w: Window, reason: str, limit: int = 50) -> list[dict[str, Any]]:
    return await _fetch(
        f"""select {RUN_LIST_COLS} from ops_runs r
            where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
              and r.degradations @> jsonb_build_array(jsonb_build_object('reason', $4::text))
            order by r.started_at desc limit $5""",  # noqa: S608
        *_args(w), reason, limit)


# ── latency (§7.2, §11.3 page 8) ───────────────────────────────────────

GROUPS = {"git_sha": "r.git_sha", "config_hash": "r.config_hash", "instance": "r.instance", "tenant": "r.tenant_id",
          "service": "r.service", "outcome": "r.outcome"}


def _group_expr(key: str | None) -> str | None:
    if key is None:
        return None
    if key in GROUPS:
        return GROUPS[key]
    if key.startswith("label.") and key[6:].isidentifier():
        return f"r.labels ->> '{key[6:]}'"
    raise ValueError(key)


async def latency_table(w: Window, level: int) -> list[dict[str, Any]]:
    """level 0: runs by name; 1–3: spans by name. Count, percentiles, max, and share of total work time."""
    if level == 0:
        rows = await _fetch(
            f"""with {UNITS}, tot as (select sum(duration_ms) t from u)
                select name, count(*) as n,
                       percentile_cont(array[0.5, 0.9, 0.95, 0.99]) within group (order by duration_ms) as p,
                       max(duration_ms) as max, sum(duration_ms) / nullif((select t from tot), 0) as share
                from u where outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED') group by name order by sum(duration_ms) desc""",  # noqa: S608,E501
            *_args(w))
    else:
        rows = await _fetch(
            """with tot as (select sum(duration_ms) t from ops_runs r where r.started_at >= $1 and r.started_at < $2
                             and ($3::text is null and r.service in ('api', 'worker') or r.service = $3) and r.outcome <> 'RUNNING')
               select s.name, s.kind, count(*) as n,
                      percentile_cont(array[0.5, 0.9, 0.95, 0.99]) within group (order by s.duration_ms) as p,
                      max(s.duration_ms) as max, sum(s.duration_ms) / nullif((select t from tot), 0) as share,
                      count(distinct s.run_id) as runs
               from ops_spans s join ops_runs r on r.run_id = s.run_id
               where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
                 and s.level = $4 group by s.name, s.kind order by sum(s.duration_ms) desc""",
            *_args(w), level)
    for r in rows:
        p = r.pop("p") or [None] * 4
        r.update(p50=_r(p[0]), p90=_r(p[1]), p95=_r(p[2]), p99=_r(p[3]), max=_r(r["max"]),
                 share=round(float(r["share"]), 4) if r["share"] is not None else None)
    return rows


async def latency_detail(w: Window, name: str, level: int, group_by: str | None) -> dict[str, Any]:
    """Histogram (fixed PDS buckets), per-interval p50 / p95, slowest 20, and a breakdown by `group_by`."""
    g = _group_expr(group_by)
    if level == 0:
        base = """select r.run_id::text, r.started_at, r.duration_ms as d, {g} as g from ops_runs r
                  where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
                    and r.name = $4 and r.outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')"""
    else:
        base = """select r.run_id::text, r.started_at, s.duration_ms as d, {g} as g from ops_spans s
                  join ops_runs r on r.run_id = s.run_id
                  where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
                    and s.name = $4 and s.level = $5"""
    sql = base.format(g=g or "null")
    args: list[Any] = [*_args(w), name] + ([level] if level else [])
    rows = await _fetch(sql + " order by r.started_at limit 100000", *args)
    ds = [float(r["d"]) for r in rows]
    hist = [0] * (len(HIST_BUCKETS) + 1)
    for d in ds:
        hist[next((i for i, b in enumerate(HIST_BUCKETS) if d <= b), len(HIST_BUCKETS))] += 1
    by_t: dict[datetime, list[float]] = {}
    step = w.interval.total_seconds()
    for r in rows:
        t = datetime.fromtimestamp(w.start.timestamp() + ((r["started_at"].timestamp() - w.start.timestamp()) // step) * step, UTC)
        by_t.setdefault(t, []).append(float(r["d"]))
    groups: dict[str, list[float]] = {}
    for r in rows:
        if g:
            groups.setdefault(str(r["g"]), []).append(float(r["d"]))
    slow = sorted(rows, key=lambda r: -float(r["d"]))[:20]
    return {
        "name": name, "level": level, "n": len(ds), "summary": pct(ds),
        "histogram": [{"le": b, "n": n} for b, n in zip([*HIST_BUCKETS, None], hist, strict=True)],
        "trend": [{"t": t, **pct(v)} for t, v in sorted(by_t.items())],
        "slowest": [{"run_id": r["run_id"], "started_at": r["started_at"], "ms": _r(float(r["d"]))} for r in slow],
        "breakdown": [{"group": k, **pct(v)} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))],
    }


def pct(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50": None, "p90": None, "p95": None, "p99": None, "max": None}
    v = sorted(values)

    def q(p: float) -> float:
        k = (len(v) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        return v[lo] + (v[hi] - v[lo]) * (k - lo)

    return {"n": len(v), "p50": _r(q(0.5)), "p90": _r(q(0.9)), "p95": _r(q(0.95)), "p99": _r(q(0.99)), "max": _r(v[-1])}


def mann_whitney_p(a: list[float], b: list[float]) -> float | None:
    """Two-sided p-value of the Mann-Whitney U test (normal approximation with tie correction)."""
    n1, n2 = len(a), len(b)
    if n1 < 3 or n2 < 3:
        return None
    allv = sorted([(x, 0) for x in a] + [(x, 1) for x in b])
    ranks: list[float] = [0.0] * len(allv)
    i, ties = 0, 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = r
        t = j - i + 1
        ties += t ** 3 - t
        i = j + 1
    r1 = sum(r for r, (_, grp) in zip(ranks, allv, strict=True) if grp == 0)
    u = r1 - n1 * (n1 + 1) / 2
    n = n1 + n2
    sigma = math.sqrt(n1 * n2 / 12 * ((n + 1) - ties / (n * (n - 1))))
    if sigma == 0:
        return 1.0
    z = (u - n1 * n2 / 2) / sigma
    return round(math.erfc(abs(z) / math.sqrt(2)), 6)


async def latency_compare(w: Window, name: str, key: str, a: str, b: str) -> dict[str, Any]:
    """Two cohorts of one run name (by git_sha, config_hash, instance, tenant or label.*): the run and every span,
    p50 / p95 each side, the delta and a significance flag (Mann-Whitney p < 0.05 with ≥ 10 each side)."""
    g = _group_expr(key)
    runs_rows = await _fetch(
        f"""select r.run_id, {g} as g, r.duration_ms as d from ops_runs r
            where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3) and r.name = $4
              and r.outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED') and {g} = any($5::text[])""",  # noqa: S608
        *_args(w), name, [a, b])
    span_rows = await _fetch(
        f"""select s.name, {g} as g, s.duration_ms as d from ops_spans s join ops_runs r on r.run_id = s.run_id
            where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3) and r.name = $4
              and r.outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED') and {g} = any($5::text[])""",  # noqa: S608
        *_args(w), name, [a, b])
    groups: dict[str, dict[str, list[float]]] = {"(run)": {a: [], b: []}}
    for r in runs_rows:
        groups["(run)"][r["g"]].append(float(r["d"]))
    for r in span_rows:
        groups.setdefault(r["name"], {a: [], b: []})[r["g"]].append(float(r["d"]))
    out: list[dict[str, Any]] = []
    for span_name, sides in groups.items():
        pa, pb = pct(sides[a]), pct(sides[b])
        p = mann_whitney_p(sides[a], sides[b])
        delta = (pb["p50"] - pa["p50"]) if pa["p50"] is not None and pb["p50"] is not None else None
        out.append({"span": span_name, "a": pa, "b": pb, "delta_p50": _r(delta),
                    "delta_pct": round(delta / pa["p50"] * 100, 1) if delta is not None and pa["p50"] else None,
                    "p_value": p, "significant": bool(p is not None and p < 0.05 and len(sides[a]) >= 10
                                                       and len(sides[b]) >= 10)})
    out.sort(key=lambda x: -abs(float(x["delta_p50"] or 0)))
    return {"name": name, "key": key, "a": a, "b": b, "spans": out}


async def latency_scatter(w: Window, name: str, limit: int = 2000) -> list[dict[str, Any]]:
    return await _fetch(
        """select run_id::text, input_size as x, duration_ms as y, outcome from ops_runs r
           where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3) and r.name = $4
             and input_size is not null and duration_ms is not null order by started_at desc limit $5""",
        *_args(w), name, limit)


async def cohort_values(w: Window, name: str, key: str) -> list[dict[str, Any]]:
    g = _group_expr(key)
    return await _fetch(
        f"""select {g} as value, count(*) as n, min(r.started_at) as first, max(r.started_at) as last from ops_runs r
            where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3) and r.name = $4
              and {g} is not null group by 1 order by first""",  # noqa: S608
        *_args(w), name)


# ── dependencies (§11.3 page 9) ────────────────────────────────────────

async def dependencies(w: Window) -> dict[str, Any]:
    days = max(w.seconds / 86400, 1e-9)
    rows = await _fetch(
        """select s.kind, coalesce(s.target, '(none)') as target, count(*) as calls, count(distinct s.run_id) as runs,
                  percentile_cont(array[0.5, 0.95]) within group (order by s.duration_ms) as p,
                  count(*) filter (where s.outcome = 'failed') as errors, sum(s.duration_ms) as total_ms
           from ops_spans s join ops_runs r on r.run_id = s.run_id
           where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
             and s.level = 3 and s.kind <> 'internal'
           group by 1, 2 order by total_ms desc""", *_args(w))
    for r in rows:
        p = r.pop("p") or [None, None]
        r.update(p50=_r(p[0]), p95=_r(p[1]), calls_per_run=round(r["calls"] / r["runs"], 2) if r["runs"] else None,
                 error_rate=round(r["errors"] / r["calls"], 4) if r["calls"] else None,
                 per_day=round(r["calls"] / days, 1), ms_per_day=round(float(r["total_ms"]) / days, 1))
    statements = await _fetch(
        """select s.attrs ->> 'db.statement_hash' as statement, min(s.attrs ->> 'db.operation') as operation,
                  min(s.target) as tbl, count(*) as calls, count(distinct s.run_id) as runs,
                  round(avg(s.duration_ms)::numeric, 1) as mean_ms, round(sum(s.duration_ms)::numeric, 1) as total_ms,
                  round((percentile_cont(0.95) within group (order by s.duration_ms))::numeric, 1) as p95
           from ops_spans s join ops_runs r on r.run_id = s.run_id
           where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
             and s.name = 'db.query' group by 1 order by total_ms desc limit 25""", *_args(w))
    return {"targets": rows, "statements": statements}


async def dependency_detail(w: Window, kind: str, target: str) -> dict[str, Any]:
    trend = await _fetch(
        """select date_bin($6::interval, r.started_at, $1) as t, count(*) as calls,
                  percentile_cont(0.5) within group (order by s.duration_ms) as p50,
                  percentile_cont(0.95) within group (order by s.duration_ms) as p95,
                  count(*) filter (where s.outcome = 'failed') as errors
           from ops_spans s join ops_runs r on r.run_id = s.run_id
           where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
             and s.kind = $4 and coalesce(s.target, '(none)') = $5 and s.level = 3 group by 1 order by 1""",
        *_args(w), kind, target, w.interval)
    slow = await _fetch(
        """select r.run_id::text, r.name as run_name, s.name, s.duration_ms, s.outcome, s.attrs, s.error, r.started_at
           from ops_spans s join ops_runs r on r.run_id = s.run_id
           where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
             and s.kind = $4 and coalesce(s.target, '(none)') = $5 and s.level = 3
           order by s.duration_ms desc limit 20""", *_args(w), kind, target)
    by_run = await _fetch(
        """select r.name, count(*) as calls, count(distinct r.run_id) as runs, sum(s.duration_ms) as total_ms
           from ops_spans s join ops_runs r on r.run_id = s.run_id
           where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
             and s.kind = $4 and coalesce(s.target, '(none)') = $5 and s.level = 3 group by 1 order by 4 desc""",
        *_args(w), kind, target)
    for t in trend:
        t["p50"], t["p95"] = _r(t["p50"]), _r(t["p95"])
    return {"kind": kind, "target": target, "trend": trend, "slowest": slow, "by_run": by_run}


# ── efficiency (§7.4; no prices until GCP: tokens, not money) ──────────

async def efficiency(w: Window) -> dict[str, Any]:
    o, waste, llm, orders, series_rows = await asyncio.gather(unit_outcomes(w, "turn"), _fetch(
        f"""with {UNITS}
            select coalesce(sum(duration_ms) filter (where outcome in ('RETRYING', 'FAILED', 'ABANDONED')), 0) as wasted_ms,
                   coalesce(sum(duration_ms), 0) as total_ms,
                   coalesce(sum((counts ->> 'tokens_in')::float) filter (where outcome in ('RETRYING', 'FAILED', 'ABANDONED')), 0) as wasted_tokens_in,
                   coalesce(sum((counts ->> 'llm_calls')::float), 0) as llm_calls
            from u where name = 'turn'""",  # noqa: S608,E501
        *_args(w)), _fetch(
        """select model, purpose, outcome, count(*) as calls, sum(input_tokens) as tokens_in,
                  sum(output_tokens) as tokens_out, sum(cached_tokens) as tokens_cached,
                  round(avg(latency_ms)::numeric) as mean_latency_ms,
                  round((percentile_cont(0.95) within group (order by latency_ms))::numeric) as p95_latency_ms
           from llm_calls where created_at >= $1 and created_at < $2 group by 1, 2, 3 order by calls desc""",
        w.start, w.end), _val("select count(*) from orders where created_at >= $1 and created_at < $2", w.start, w.end),
        _fetch(
        """select date_bin($3::interval, created_at, $1) as t, sum(input_tokens) as tokens_in,
                  sum(output_tokens) as tokens_out, count(*) as calls
           from llm_calls where created_at >= $1 and created_at < $2 group by 1 order by 1""",
        w.start, w.end, w.interval))
    tokens = sum(int(r["tokens_in"] or 0) + int(r["tokens_out"] or 0) for r in llm)
    calls = sum(int(r["calls"]) for r in llm)
    wst = waste[0]
    return {
        "prices": "not tracked until GCP (OPS_PLAN decisions)",
        "turns": o["units"], "orders": orders, "llm_calls": calls, "tokens": tokens,
        "tokens_per_order": round(tokens / orders, 1) if orders else None,
        "llm_calls_per_order": round(calls / orders, 2) if orders else None,
        "tokens_per_turn": round(tokens / o["units"], 1) if o["units"] else None,
        "retry_waste_ms": round(float(wst["wasted_ms"]), 1),
        "retry_waste_share": round(float(wst["wasted_ms"]) / float(wst["total_ms"]), 4) if wst["total_ms"] else None,
        "llm": llm, "series": series_rows,
    }


# ── live (§11.3 page 2) ────────────────────────────────────────────────

async def live() -> dict[str, Any]:
    running = await _fetch(
        """with p95 as (select name, percentile_cont(0.95) within group (order by duration_ms) as p95 from ops_runs
                        where started_at > now() - interval '7 days' and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')
                        group by name)
           select r.run_id::text, r.service, r.kind, r.name, r.subject_type, r.subject_id, r.current_stage, r.attempt,
                  r.started_at, extract(epoch from now() - r.started_at) * 1000 as elapsed_ms, p95.p95,
                  (extract(epoch from now() - r.started_at) * 1000 > 3 * p95.p95) as slow
           from ops_runs r left join p95 on p95.name = r.name where r.outcome = 'RUNNING' order by r.started_at""")
    queue = await _fetch(
        """select count(*) filter (where status = 'pending' and next_attempt_at <= now()) as due,
                  count(*) filter (where status = 'pending' and next_attempt_at > now()) as waiting,
                  count(*) filter (where status = 'running') as running,
                  count(*) filter (where status = 'dead_lettered' and dead_lettered_at > now() - interval '24 hours')
                      as dead_letters_24h,
                  min(next_attempt_at) filter (where status = 'pending') as oldest_due
           from pending_work where status in ('pending', 'running', 'dead_lettered')""")
    leases = await _val("select count(*) from conversations where lease_expires_at > now()")
    ambiguous = await _val("select count(*) from wa_messages where status = 'ambiguous'")
    instances = await _fetch(
        """select instance, service, git_sha, started_at, last_seen,
                  (last_seen < now() - interval '60 seconds') as stale from ops_instances
           where last_seen > now() - interval '1 day' order by service, last_seen desc""")
    hour = await _fetch(
        """select count(*) filter (where outcome = 'RETRYING') as requeued,
                  count(*) filter (where outcome = 'ABANDONED') as abandoned,
                  count(*) filter (where outcome = 'FAILED') as failed
           from ops_runs where started_at > now() - interval '1 hour'""")
    for r in running:
        r["elapsed_ms"], r["p95"] = _r(r["elapsed_ms"]), _r(r["p95"])
    return {"running": running, "queue": queue[0], "leases_held": leases, "ambiguous_sends": ambiguous,
            "instances": instances, "last_hour": hour[0]}


async def overview(w: Window) -> dict[str, Any]:
    prev = w.previous()
    r = await together(
        turns=unit_outcomes(w, "turn"), turns_previous=unit_outcomes(prev, "turn"), all=unit_outcomes(w),
        reply_ms=percentiles(w, name="turn", field="counts.reply_ms"),
        reply_ms_previous=percentiles(prev, name="turn", field="counts.reply_ms"),
        turn_work_ms=percentiles(w, name="turn"),
        tap_reply_ms=percentiles(w, name="turn", field="counts.reply_ms", label=("turn_kind", "tap")),
        webhook_ms=percentiles(w, name="POST /webhooks/whatsapp"), eff=efficiency(w), series=series(w, "turn"),
        stage_share=stage_share(w, "turn"), dependency_share=dependency_share(w, "turn"), top_failures=top_failures(w),
        degradations=degradation_counts(w), deploys=deploys(w),
    )
    eff = r.pop("eff")
    return {
        "window": {"from": w.start, "to": w.end, "interval_s": w.interval.total_seconds(), "service": w.service},
        **r, "tokens_per_order": eff["tokens_per_order"], "orders": eff["orders"], "llm_calls": eff["llm_calls"],
        "budget": {"reply_p50_ms": 3000, "reply_p95_ms": 5000, "tap_reply_ms": 1000, "webhook_ms": 300},
    }
