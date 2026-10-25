"""Database side of a performance run (docs/perf-test.md): what our code executed, measured inside Postgres.

`snapshot NAME` saves pg_stat_statements (calls, execution time, rows per normalised statement) and per-table
counters (sequential vs index scans, rows read, inserts / updates / deletes). `diff BEFORE AFTER` prints what
happened between two snapshots: the top statements by total and mean execution time, reads vs writes, and
tables read by sequential scan. Times are server execution only, no network; read-only queries.

    backend/.venv/Scripts/python.exe loadtest/db_stats.py snapshot before
    ... run the workload ...
    backend/.venv/Scripts/python.exe loadtest/db_stats.py snapshot after
    backend/.venv/Scripts/python.exe loadtest/db_stats.py diff before after [--json out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402

SNAPSHOTS = Path(__file__).resolve().parent / "results" / "db-snapshots"
NOISE = re.compile(r"pg_stat_statements|pg_catalog|information_schema|^\s*(begin|commit|rollback|set |show |select 1\b|"
                   r"select \$1\b)|ops_(runs|spans|run_events|instances|deploys)|realtime\.|net\.|cron\.|"
                   r"supabase_|vault\.|storage\.|auth\.|pgbouncer|pg_advisory", re.I)
WRITE = re.compile(r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from)\b", re.I)
# PostgREST sets the role and JWT claims once per console request (Supabase reads and Realtime): counted apart.
POSTGREST = re.compile(r"set_config\('search_path', \$1, true\)", re.I)
# The pooler resets a server connection each time a client takes one (transaction mode): one count per checkout.
POOLER_RESET = re.compile(r"^\s*RESET ALL\s*$", re.I)
POOLER_NOISE = re.compile(r"^\s*(CLOSE ALL|UNLISTEN \*|DISCARD ALL|RESET ALL)\s*$", re.I)
# The eval harness's own polling query (evals/app_run.py), not app traffic.
HARNESS = re.compile(r"select i\.processed_at is not null as processed", re.I)


async def snapshot(name: str) -> Path:
    s = get_settings()
    c = await asyncpg.connect((s.database_url_session or s.database_url).get_secret_value(), statement_cache_size=0)
    try:
        statements = await c.fetch(
            """select queryid::text, calls, total_exec_time, rows, query from extensions.pg_stat_statements
               where dbid = (select oid from pg_database where datname = current_database())""")
        tables = await c.fetch(
            """select relname, seq_scan, seq_tup_read, coalesce(idx_scan, 0) as idx_scan, n_tup_ins, n_tup_upd,
                      n_tup_del, n_live_tup from pg_stat_user_tables where schemaname = 'public'""")
    finally:
        await c.close()
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS / f"{name}.json"
    path.write_text(json.dumps({
        "statements": [{"queryid": r["queryid"], "calls": r["calls"], "total": r["total_exec_time"], "rows": r["rows"],
                        "query": r["query"][:2000]} for r in statements],
        "tables": [dict(r) for r in tables],
    }), encoding="utf-8")
    print(f"snapshot {name}: {len(statements)} statements, {len(tables)} tables → {path.relative_to(ROOT)}")
    return path


def load(name: str) -> dict[str, Any]:
    path = Path(name) if name.endswith(".json") else SNAPSHOTS / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"statements": data, "tables": []}


def by_query(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """pg_stat_statements keeps one row per role for the same statement: sum them per queryid."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        o = out.setdefault(r["queryid"], {**r, "calls": 0, "total": 0.0, "rows": 0})
        o["calls"] += r["calls"]
        o["total"] += r["total"]
        o["rows"] += r["rows"]
    return out


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = by_query(before["statements"])
    stmts = []
    postgrest = {"requests": 0, "total_ms": 0.0}
    checkouts = 0
    for r in by_query(after["statements"]).values():
        o = old.get(r["queryid"], {"calls": 0, "total": 0.0, "rows": 0})
        calls = r["calls"] - o["calls"]
        total = r["total"] - o["total"]
        if calls > 0 and POSTGREST.search(r["query"]):
            postgrest["requests"] += calls
            postgrest["total_ms"] = round(postgrest["total_ms"] + total, 1)
            continue
        if calls > 0 and POOLER_RESET.match(r["query"]):
            checkouts += calls
        if calls <= 0 or NOISE.search(r["query"]) or POOLER_NOISE.match(r["query"]) or HARNESS.search(r["query"]):
            continue
        stmts.append({"query": " ".join(r["query"].split())[:220], "calls": calls, "total_ms": round(total, 1),
                      "mean_ms": round(total / calls, 3), "rows_per_call": round((r["rows"] - o["rows"]) / calls, 1),
                      "write": bool(WRITE.search(r["query"]))})
    told = {t["relname"]: t for t in before.get("tables", [])}
    tables = []
    for t in after.get("tables", []):
        o = told.get(t["relname"])
        if o is None:
            continue
        d = {k: t[k] - o[k] for k in ("seq_scan", "seq_tup_read", "idx_scan", "n_tup_ins", "n_tup_upd", "n_tup_del")}
        if any(d.values()):
            tables.append({"table": t["relname"], "rows": t["n_live_tup"], **d})
    reads = [x for x in stmts if not x["write"]]
    writes = [x for x in stmts if x["write"]]

    def total(xs: list[dict[str, Any]]) -> dict[str, Any]:
        calls = sum(x["calls"] for x in xs)
        ms = sum(x["total_ms"] for x in xs)
        return {"statements": len(xs), "calls": calls, "total_ms": round(ms, 1),
                "mean_ms": round(ms / calls, 3) if calls else None}

    return {
        "reads": total(reads), "writes": total(writes), "postgrest_request_setup": postgrest,
        "pooler_checkouts": checkouts,
        "top_by_total": sorted(stmts, key=lambda x: -x["total_ms"])[:15],
        "top_by_mean": sorted([x for x in stmts if x["calls"] >= 5], key=lambda x: -x["mean_ms"])[:10],
        "most_called": sorted(stmts, key=lambda x: -x["calls"])[:10],
        "seq_scans": sorted([t for t in tables if t["seq_scan"] > 0], key=lambda t: -t["seq_tup_read"])[:10],
        "tables": sorted(tables, key=lambda t: -(t["n_tup_ins"] + t["n_tup_upd"] + t["n_tup_del"]))[:15],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot").add_argument("name")
    d = sub.add_parser("diff")
    d.add_argument("before")
    d.add_argument("after")
    d.add_argument("--json")
    args = ap.parse_args()
    if args.cmd == "snapshot":
        asyncio.run(snapshot(args.name))
        return 0
    result = diff(load(args.before), load(args.after))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
