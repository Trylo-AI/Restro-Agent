"""Insights: the improvement backlog (PDS §9). Each detector is a query over the window (default 7 days) and returns
findings ranked by estimated saving, each with evidence and example runs. Only detectors whose data this app has
are built (docs/OPS_PLAN.md §4): D1, D2, D3, D4, D6, D7, D13, D14, D16, D17. D15 (cost) waits for prices.
"""

import asyncio
import hashlib
import json
from collections import Counter
from datetime import timedelta
from typing import Any

from app.core.config import get_settings
from app.ops import queries
from app.ops.queries import Window, _fetch, mann_whitney_p, pct

N_PLUS_ONE_MIN = 10
CHATTY_MIN = 10
REGRESSION_PCT = 15
SLOW_TAIL_RATIO = 5
LEASE_S = 30  # domain/conversation/lease.py LEASE_SECONDS; the read side never imports business code


def finding(detector: str, severity: str, title: str, where: dict[str, Any], evidence: dict[str, Any],
            saving_ms_day: float | None, examples: list[str], first_seen: Any = None, confidence: str = "high",
            fix: str = "") -> dict[str, Any]:
    key = detector + ":" + hashlib.sha1(json.dumps(where, sort_keys=True, default=str).encode()).hexdigest()[:12]  # noqa: S324
    return {"key": key, "detector": detector, "severity": severity, "title": title, "where": where,
            "evidence": evidence, "estimated_saving": {"ms_per_day": round(saving_ms_day, 1) if saving_ms_day else None,
                                                       "usd_per_day": None},
            "example_run_ids": examples[:3], "first_seen": first_seen, "confidence": confidence, "fix": fix}


def _days(w: Window) -> float:
    return max(w.seconds / 86400, 1 / 24)


async def n_plus_one(w: Window) -> list[dict[str, Any]]:
    """D3: ≥ 10 statements with the same hash under one parent span in one run."""
    rows = await _fetch(
        """with g as (
             select r.run_id, r.name as run_name, p.name as parent, s.attrs ->> 'db.statement_hash' as stmt,
                    min(s.target) as tbl, count(*) as n, avg(s.duration_ms) as mean_ms, sum(s.duration_ms) as ms
             from ops_spans s join ops_runs r on r.run_id = s.run_id
             left join ops_spans p on p.run_id = s.run_id and p.span_id = s.parent_id
             where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
               and s.name = 'db.query'
             group by 1, 2, 3, 4 having count(*) >= $4)
           select run_name, parent, stmt, min(tbl) as tbl, count(*) as runs,
                  percentile_cont(0.5) within group (order by n) as calls_p50, avg(mean_ms) as mean_ms,
                  avg(ms) as ms_per_run, (array_agg(run_id::text order by ms desc))[1:3] as examples
           from g group by 1, 2, 3""", w.start, w.end, w.service, N_PLUS_ONE_MIN)
    out = []
    for r in rows:
        per_day = r["runs"] / _days(w)
        saving = (float(r["calls_p50"]) - 1) * float(r["mean_ms"]) * per_day
        out.append(finding(
            "n_plus_one", "high" if saving > 60_000 else "medium",
            f"N+1: the same statement {int(r['calls_p50'])}× per run on {r['tbl'] or '?'}",
            {"name": r["run_name"], "span": r["parent"], "target": r["tbl"], "statement": r["stmt"]},
            {"runs_affected": r["runs"], "calls_per_run_p50": float(r["calls_p50"]),
             "ms_per_run": round(float(r["ms_per_run"]), 1), "mean_ms": round(float(r["mean_ms"]), 1)},
            saving, r["examples"], fix="Fetch the rows in one statement (any($1::uuid[]) / a join) instead of a loop."))
    return out


async def chatty(w: Window) -> list[dict[str, Any]]:
    """D4: ≥ 10 calls to the same outbound HTTP / model / queue target in one run."""
    rows = await _fetch(
        """with g as (select r.run_id, r.name as run_name, s.kind, s.target, count(*) as n, avg(s.duration_ms) as mean_ms
                      from ops_spans s join ops_runs r on r.run_id = s.run_id
                      where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
                        and s.level = 3 and s.kind in ('http_out', 'llm', 'queue')
                      group by 1, 2, 3, 4 having count(*) >= $4)
           select run_name, kind, target, count(*) as runs, percentile_cont(0.5) within group (order by n) as calls_p50,
                  avg(mean_ms) as mean_ms, (array_agg(run_id::text))[1:3] as examples from g group by 1, 2, 3""",
        w.start, w.end, w.service, CHATTY_MIN)
    return [finding("chatty_dependency", "medium", f"{int(r['calls_p50'])} calls to {r['target']} per run",
                    {"name": r["run_name"], "kind": r["kind"], "target": r["target"]},
                    {"runs_affected": r["runs"], "calls_per_run_p50": float(r["calls_p50"])},
                    (float(r["calls_p50"]) - 1) * float(r["mean_ms"]) * 0.5 * r["runs"] / _days(w), r["examples"],
                    fix="Batch the calls, or cache the answer for the run.") for r in rows]


async def regression(w: Window) -> list[dict[str, Any]]:
    """D1: per run name, p50 or p95 after the latest deploy in the window > 15 % worse, ≥ 30 runs each side, MWU p < 0.05."""
    deploys = await _fetch("select service, max(at) as at from ops_deploys where at >= $1 and at < $2 group by 1",
                           w.start, w.end)
    out = []
    for d in deploys:
        rows = await _fetch(
            """select name, (started_at >= $4) as after, duration_ms, run_id::text from ops_runs
               where service = $3 and started_at >= $1 and started_at < $2
                 and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED')""", w.start, w.end, d["service"], d["at"])
        by: dict[str, dict[bool, list[float]]] = {}
        ex: dict[str, list[str]] = {}
        for r in rows:
            by.setdefault(r["name"], {True: [], False: []})[r["after"]].append(float(r["duration_ms"]))
            if r["after"]:
                ex.setdefault(r["name"], []).append(r["run_id"])
        for name, sides in by.items():
            before, after = sides[False], sides[True]
            if len(before) < 30 or len(after) < 30:
                continue
            pb, pa = pct(before), pct(after)
            p = mann_whitney_p(before, after)
            worse50 = (pa["p50"] - pb["p50"]) / pb["p50"] * 100 if pb["p50"] else 0
            worse95 = (pa["p95"] - pb["p95"]) / pb["p95"] * 100 if pb["p95"] else 0
            if max(worse50, worse95) > REGRESSION_PCT and p is not None and p < 0.05:
                per_day = len(after) / max((w.end - d["at"]).total_seconds() / 86400, 1 / 24)
                out.append(finding(
                    "latency_regression", "high", f"{name} got {max(worse50, worse95):.0f} % slower after the deploy",
                    {"name": name, "service": d["service"], "deploy_at": d["at"]},
                    {"before": pb, "after": pa, "p_value": p}, (pa["p50"] - pb["p50"]) * per_day, ex[name],
                    first_seen=d["at"], fix="Compare the two cohorts span by span on the Latency page."))
    return out


async def new_failures(w: Window) -> list[dict[str, Any]]:
    """D2: a fingerprint first seen in the window, or today's rate > 3× its 7-day daily median."""
    rows = await _fetch(
        """with f as (select failure_fingerprint as fp, min(started_at) as first_seen from ops_runs
                      where failure_fingerprint is not null group by 1),
                d as (select failure_fingerprint as fp, date_trunc('day', started_at) as day, count(*) as n,
                             count(distinct coalesce(subject_id, run_id::text)) as units
                      from ops_runs where failure_fingerprint is not null and started_at >= $2::timestamptz - interval '8 days'
                        and started_at < $2::timestamptz group by 1, 2)
           select f.fp, f.first_seen, min(r.name) as name, min(r.error_stage) as stage, min(r.error_code) as code,
                  min(r.failure_reason) as sample,
                  (select n from d where d.fp = f.fp order by day desc limit 1) as today,
                  (select percentile_cont(0.5) within group (order by n) from d where d.fp = f.fp
                     and d.day < date_trunc('day', $2::timestamptz)) as median_daily,
                  count(distinct coalesce(r.subject_id, r.run_id::text)) as units,
                  (array_agg(r.run_id::text order by r.started_at desc))[1:3] as examples
           from f join ops_runs r on r.failure_fingerprint = f.fp
           where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
           group by f.fp, f.first_seen""", w.start, w.end, w.service)
    out = []
    for r in rows:
        new = r["first_seen"] >= w.start
        spike = r["median_daily"] and r["today"] and r["today"] > 3 * float(r["median_daily"])
        if not (new or spike):
            continue
        out.append(finding(
            "new_failure" if new else "error_spike", "high" if r["units"] >= 5 else "medium",
            f"{'New' if new else 'Spiking'} failure in {r['name']}: {r['code']} at {r['stage'] or '?'}",
            {"name": r["name"], "fingerprint": r["fp"]},
            {"units_affected": r["units"], "today": r["today"], "median_daily": r["median_daily"], "sample": r["sample"]},
            None, r["examples"], first_seen=r["first_seen"], fix="Open an example trace; the failure reason and events say where."))
    return out


async def retry_waste(w: Window) -> list[dict[str, Any]]:
    """D6: time spent on attempts that failed is > 5 % of all work, by fingerprint."""
    total = float(await queries._val(
        "select coalesce(sum(duration_ms), 0) from ops_runs where started_at >= $1 and started_at < $2 "
        "and ($3::text is null and service in ('api', 'worker') or service = $3)", w.start, w.end, w.service) or 0)
    rows = await _fetch(
        """select name, failure_fingerprint as fp, min(error_code) as code, count(*) as runs, sum(duration_ms) as ms,
                  (array_agg(run_id::text))[1:3] as examples
           from ops_runs where started_at >= $1 and started_at < $2 and ($3::text is null and service in ('api', 'worker') or service = $3)
             and outcome in ('RETRYING', 'FAILED', 'ABANDONED') group by 1, 2""", w.start, w.end, w.service)
    wasted = sum(float(r["ms"] or 0) for r in rows)
    if not total or wasted / total <= 0.05:
        return []
    return [finding("retry_waste", "medium", f"Failed attempts cost {wasted / total * 100:.1f} % of all work time",
                    {"name": r["name"], "fingerprint": r["fp"]},
                    {"runs": r["runs"], "ms": round(float(r["ms"]), 1), "share_of_all_work": round(float(r["ms"]) / total, 4),
                     "code": r["code"]}, float(r["ms"]) / _days(w), r["examples"],
                    fix="Fix the failure behind the fingerprint; retries only hide it.") for r in rows if r["ms"]]


async def slow_tail(w: Window) -> list[dict[str, Any]]:
    """D7: p99 ÷ p50 > 5 with ≥ 100 runs; says what the slow runs have in common."""
    rows = await _fetch(
        """select name, count(*) as n, percentile_cont(0.5) within group (order by duration_ms) as p50,
                  percentile_cont(0.99) within group (order by duration_ms) as p99
           from ops_runs where started_at >= $1 and started_at < $2 and ($3::text is null and service in ('api', 'worker') or service = $3)
             and outcome in ('SUCCEEDED', 'SUCCEEDED_DEGRADED') group by name having count(*) >= 100""",
        w.start, w.end, w.service)
    out = []
    for r in rows:
        if not r["p50"] or r["p99"] / r["p50"] <= SLOW_TAIL_RATIO:
            continue
        slow = await _fetch(
            """select run_id::text, instance, tenant_id, labels from ops_runs
               where name = $1 and started_at >= $2 and started_at < $3 and duration_ms >= $4
               order by duration_ms desc limit 50""", r["name"], w.start, w.end, r["p99"])
        common: Counter[str] = Counter()
        for s in slow:
            common.update([f"instance={s['instance']}", f"tenant={s['tenant_id']}"] +
                          [f"{k}={v}" for k, v in (s["labels"] or {}).items()])
        shared = [k for k, n in common.most_common(5) if n >= 0.8 * len(slow)]
        out.append(finding(
            "slow_tail", "medium", f"{r['name']}: p99 is {r['p99'] / r['p50']:.1f}× p50",
            {"name": r["name"]}, {"p50": round(r["p50"], 1), "p99": round(r["p99"], 1), "runs": r["n"],
                                 "slow_runs_share": shared},
            (r["p99"] - r["p50"]) * 0.01 * r["n"] / _days(w), [s["run_id"] for s in slow], confidence="candidate",
            fix="Check what the slow runs share (listed); open the slowest traces."))
    return out


async def near_timeout(w: Window) -> list[dict[str, Any]]:
    """D13: > 5 % of model calls within 10 % of the timeout, or turns within 10 % of the lease."""
    limit_ms = get_settings().llm_timeout_seconds * 1000
    out = []
    for span, limit, label in (("llm.call", limit_ms, "the model timeout"), (None, LEASE_S * 1000, "the 30 s lease")):
        if span:
            rows = await _fetch(
                """select count(*) as n, count(*) filter (where s.duration_ms >= $4 * 0.9) as near,
                          (array_agg(r.run_id::text order by s.duration_ms desc))[1:3] as examples
                   from ops_spans s join ops_runs r on r.run_id = s.run_id
                   where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
                     and s.name = 'llm.call'""", w.start, w.end, w.service, limit)
        else:
            rows = await _fetch(
                """select count(*) as n, count(*) filter (where duration_ms >= $4 * 0.9) as near,
                          (array_agg(run_id::text order by duration_ms desc))[1:3] as examples
                   from ops_runs where started_at >= $1 and started_at < $2 and ($3::text is null and service in ('api', 'worker') or service = $3)
                     and name = 'turn'""", w.start, w.end, w.service, limit)
        r = rows[0]
        if r["n"] and r["near"] / r["n"] > 0.05:
            out.append(finding("timeout_near_limit", "high", f"{r['near'] / r['n'] * 100:.0f} % of {span or 'turns'} run close to {label}",
                               {"span": span or "turn", "limit_ms": limit}, {"calls": r["n"], "near_limit": r["near"]},
                               None, r["examples"], fix="Raise the limit only with evidence; first find why they are slow."))
    return out


async def degradation_creep(w: Window) -> list[dict[str, Any]]:
    """D14: a reason's rate this window > 2× the previous window."""
    now, prev = await queries.degradation_counts(w), {r["reason"]: r for r in await queries.degradation_counts(w.previous())}
    out = []
    for r in now:
        before = prev.get(r["reason"], {}).get("count", 0)
        if r["count"] >= 5 and r["count"] > 2 * before:
            ex = await queries.degradation_runs(w, r["reason"], 3)
            out.append(finding("degradation_creep", "medium", f"Fallback {r['reason']} is up {r['count']} vs {before}",
                               {"reason": r["reason"]}, {"now": r["count"], "before": before,
                                                         "description": r["description"]},
                               None, [e["run_id"] for e in ex], fix="See the Degradations tab for the runs."))
    return out


async def payload_bloat(w: Window) -> list[dict[str, Any]]:
    """D16: model prompt tokens p95 > 2× p50 for a purpose."""
    rows = await _fetch(
        """select purpose, count(*) as n, percentile_cont(0.5) within group (order by input_tokens) as p50,
                  percentile_cont(0.95) within group (order by input_tokens) as p95
           from llm_calls where created_at >= $1 and created_at < $2 and input_tokens is not null group by 1""",
        w.start, w.end)
    return [finding("payload_bloat", "low", f"{r['purpose']} prompts: p95 {r['p95']:.0f} tokens vs p50 {r['p50']:.0f}",
                    {"purpose": r["purpose"]}, {"calls": r["n"], "tokens_in_p50": r["p50"], "tokens_in_p95": r["p95"]},
                    None, [], fix="Look at what grows the prompt (history, menu size).")
            for r in rows if r["p50"] and r["n"] >= 20 and r["p95"] > 2 * r["p50"]]


async def slowest_statements(w: Window) -> list[dict[str, Any]]:
    """D17: the top 10 statements by total time, with their three slowest runs (one query)."""
    rows = await _fetch(
        """with s as (select sp.attrs ->> 'db.statement_hash' as stmt, sp.attrs ->> 'db.operation' as op, sp.target,
                             sp.duration_ms, sp.run_id
                      from ops_spans sp join ops_runs r on r.run_id = sp.run_id
                      where r.started_at >= $1 and r.started_at < $2 and ($3::text is null and r.service in ('api', 'worker') or r.service = $3)
                        and sp.name = 'db.query')
           select stmt, min(op) as op, min(target) as tbl, count(*) as calls, avg(duration_ms) as mean_ms,
                  sum(duration_ms) as total_ms, percentile_cont(0.95) within group (order by duration_ms) as p95,
                  (array_agg(run_id::text order by duration_ms desc))[1:3] as examples
           from s group by stmt order by total_ms desc limit 10""", w.start, w.end, w.service)
    return [finding("slowest_statement", "low" if i > 2 else "medium",
                    f"#{i + 1} statement by total time: {r['op']} {r['tbl'] or ''}",
                    {"statement": r["stmt"], "target": r["tbl"]},
                    {"calls": r["calls"], "mean_ms": round(float(r["mean_ms"]), 1),
                     "total_ms": round(float(r["total_ms"]), 1), "p95_ms": round(float(r["p95"] or 0), 1)},
                    None, r["examples"],
                    fix="Most of its time is the network round trip when mean ≈ ping; fewer calls beat faster SQL.")
            for i, r in enumerate(rows)]


DETECTORS = [n_plus_one, chatty, regression, new_failures, retry_waste, slow_tail, near_timeout, degradation_creep,
             payload_bloat, slowest_statements]
SEVERITY = {"high": 0, "medium": 1, "low": 2}


async def insights(w: Window, include_dismissed: bool = False) -> list[dict[str, Any]]:
    found = [f for batch in await asyncio.gather(*(d(w) for d in DETECTORS)) for f in batch]
    dismissed = {r["finding_key"]: r for r in await _fetch("select finding_key, dismissed_at, note from ops_insight_state")}
    for f in found:
        if f["key"] in dismissed:
            f["dismissed"] = dismissed[f["key"]]
    if not include_dismissed:
        found = [f for f in found if "dismissed" not in f]
    found.sort(key=lambda f: (-(f["estimated_saving"]["ms_per_day"] or 0), SEVERITY[f["severity"]]))
    return found


def default_window() -> Window:
    w = queries.window(None, None)
    return Window(w.end - timedelta(days=7), w.end, None)
