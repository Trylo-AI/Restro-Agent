"""Console API performance (docs/perf-test.md): the reads the console makes through Supabase, and FastAPI reads and
writes, timed from the client and from the server's own trace (ops_runs: time, DB statements, DB time).

Writes are reversible ones on the seeded café: a dish's availability off then on, ordering paused then resumed,
notifications acknowledged, console telemetry. Synthetic data only; local or staging only.

    backend/.venv/Scripts/python.exe loadtest/perf_api.py [--n 15] [--burst 10]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"


def env_value(path: Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{name} missing in {path.name}")


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * q))], 1)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15, help="sequential calls per endpoint")
    ap.add_argument("--burst", type=int, default=10, help="concurrent calls per endpoint")
    args = ap.parse_args()
    s = get_settings()
    if s.app_env not in ("local", "staging"):
        raise SystemExit("performance tests run locally or on staging only")
    sb_url = s.supabase_url.rstrip("/")
    key = env_value(ROOT / "frontend" / ".env.local", "VITE_SUPABASE_PUBLISHABLE_KEY")
    password = env_value(ROOT / "backend" / ".env", "SEED_STAFF_PASSWORD")
    api = s.api_url.rstrip("/")

    async with httpx.AsyncClient(timeout=60) as client:
        async def token(email: str) -> str:
            r = await client.post(f"{sb_url}/auth/v1/token", params={"grant_type": "password"},
                                  headers={"apikey": key}, json={"email": email, "password": password})
            r.raise_for_status()
            return str(r.json()["access_token"])

        manager, owner = await token("manager@restro.test"), await token("owner@restro.test")
        shift = await token("shift@restro.test")
        rest = {"apikey": key}
        r = await client.get(f"{sb_url}/rest/v1/cafes", params={"select": "id", "name": "eq.Kaapi House"},
                             headers={**rest, "Authorization": f"Bearer {owner}"})
        cafe_id = r.json()[0]["id"]
        r = await client.get(f"{sb_url}/rest/v1/menu_items",
                             params={"select": "id", "cafe_id": f"eq.{cafe_id}", "name": "eq.Espresso"},
                             headers={**rest, "Authorization": f"Bearer {owner}"})
        espresso = r.json()[0]["id"]

        def sb(jwt: str, table: str, params: dict[str, str]) -> Any:
            return lambda: client.get(f"{sb_url}/rest/v1/{table}", params=params,
                                      headers={**rest, "Authorization": f"Bearer {jwt}"})

        def fa(jwt: str, method: str, path: str, body: Any = None) -> Any:
            headers = {"Authorization": f"Bearer {jwt}", "X-Cafe-Id": cafe_id}
            return lambda: client.request(method, f"{api}{path}", json=body, headers=headers)

        flip = {"available": False}

        def availability() -> Any:
            flip["available"] = not flip["available"]  # off, on, off, on …: ends where it started (n even)
            return fa(manager, "POST", f"/menu/{espresso}/availability", {"available": flip["available"]})()

        paused = {"v": False}

        def pause() -> Any:
            paused["v"] = not paused["v"]
            return fa(manager, "POST", f"/cafes/{cafe_id}/pause-ordering", {"paused": paused["v"]})()

        now = datetime.now(UTC).isoformat()
        endpoints: list[tuple[str, str, Any]] = [
            ("read", "Live orders (Supabase, orders + items + table)", sb(manager, "orders", {
                "select": "*,order_items(*),table_sessions(tables(label))", "cafe_id": f"eq.{cafe_id}",
                "status": "in.(NEW,ACCEPTED,READY)", "order": "created_at"})),
            ("read", "Menu (Supabase, menu_items)", sb(manager, "menu_items", {"select": "*", "cafe_id": f"eq.{cafe_id}"})),
            ("read", "Customer cards (Supabase view v_customer_card, 50)", sb(owner, "v_customer_card", {
                "select": "*", "cafe_id": f"eq.{cafe_id}", "limit": "50"})),
            ("read", "Revenue by day (Supabase view v_daily_revenue)", sb(owner, "v_daily_revenue", {
                "select": "*", "cafe_id": f"eq.{cafe_id}"})),
            ("read", "Offer lookup by name (FastAPI, shift manager) [offers_lookup]", fa(shift, "POST", "/offers/lookup",
                                                                                     {"q": "Riya"})),
            ("read", "Campaign insights (Supabase view v_campaign_insights) [campaign_insights_read]",
             sb(owner, "v_campaign_insights", {"select": "*", "cafe_id": f"eq.{cafe_id}"})),
            ("read", "Audience preview (FastAPI, owner)", fa(owner, "POST", "/campaigns/audience-preview",
                                                            {"segment": "all_opted_in", "template_name": "weekly_offer"})),
            ("write", "Dish availability off/on (FastAPI)", availability),
            ("write", "Pause / resume ordering (FastAPI)", pause),
            ("write", "Acknowledge all notifications (FastAPI)", fa(manager, "POST", "/notifications/ack-all")),
            ("write", "Console telemetry (FastAPI)", fa(manager, "POST", "/telemetry/console",
                                                         {"events": [{"kind": "realtime_reconnect", "at": now}]})),
        ]

        dsn = (s.database_url_session or s.database_url).get_secret_value()
        db = await asyncpg.connect(dsn, statement_cache_size=0)
        rtt = []
        for _ in range(5):
            t = time.perf_counter()
            await db.fetchval("select 1")
            rtt.append((time.perf_counter() - t) * 1000)
        rows = []
        try:
            for kind, name, call in endpoints:
                since = await db.fetchval("select now()")
                seq, statuses = [], set()
                for _ in range(args.n + (args.n % 2)):  # even, so toggles end where they started
                    t = time.perf_counter()
                    resp = await call()
                    seq.append((time.perf_counter() - t) * 1000)
                    statuses.add(resp.status_code)
                burst_n = args.burst + (args.burst % 2)
                t = time.perf_counter()
                burst = await asyncio.gather(*(call() for _ in range(burst_n)))
                burst_wall = (time.perf_counter() - t) * 1000
                statuses |= {b.status_code for b in burst}
                await asyncio.sleep(2)  # the api writes its ops run off the request path
                server = await db.fetch(
                    """select duration_ms, (dep_calls->>'db')::int as stmts, (dep_ms->>'db')::float as db_ms
                       from ops_runs where service = 'api' and kind = 'http' and started_at >= $1
                         and name not like '%/webhooks/%'""", since)
                rows.append({
                    "kind": kind, "name": name, "statuses": sorted(statuses),
                    "client_ms": {"p50": pct(seq, 0.5), "p95": pct(seq, 0.95)},
                    "burst": {"n": burst_n, "wall_ms": round(burst_wall), "per_call": round(burst_wall / burst_n)},
                    "server": {"n": len(server), "p50_ms": pct([x["duration_ms"] for x in server if x["duration_ms"]], 0.5),
                               "db_statements": pct([x["stmts"] or 0 for x in server], 0.5),
                               "db_ms": pct([x["db_ms"] or 0 for x in server], 0.5)} if server else None,
                })
                print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        finally:
            # Leave the café as it was, whatever happened above.
            await client.post(f"{api}/menu/{espresso}/availability", json={"available": True},
                              headers={"Authorization": f"Bearer {manager}", "X-Cafe-Id": cafe_id})
            await client.post(f"{api}/cafes/{cafe_id}/pause-ordering", json={"paused": False},
                              headers={"Authorization": f"Bearer {manager}", "X-Cafe-Id": cafe_id})
            await db.close()

    report = {"at": datetime.now(UTC).isoformat(), "db_rtt_ms": pct(rtt, 0.5), "endpoints": rows}
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-perf-api.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"db round trip {report['db_rtt_ms']} ms · report: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
