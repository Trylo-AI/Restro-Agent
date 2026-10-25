"""PDS Gate 2: tracing overhead, measured. The same turn-shaped workload (20 statements through the app's own
conn() / tx() helpers, inside stage spans) runs with tracing on and off, alternating, against the real dev DB.
Also reports the in-process cost of one span and one statement hook, without the network.

    backend/.venv/Scripts/python.exe scripts/ops_overhead.py [--reps 10] [--turns 5]
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.db import pool as pool_mod  # noqa: E402
from app.db.tx import conn, tx  # noqa: E402
from app.ops import tracing  # noqa: E402
from app.ops.tracing import recorder  # noqa: E402


async def turn() -> None:
    """The shape of a real tap turn: loads, one commit transaction, the send bookkeeping."""
    with tracing.span("turn.load"):
        for _ in range(4):
            async with conn() as c:
                await c.fetchval("select count(*) from cafes where status = $1", "live")
    with tracing.span("turn.route"):
        async with conn() as c:
            await c.fetch("select id, name from menu_items limit 20")
    with tracing.span("turn.commit"):
        async with tx() as c:
            for _ in range(8):
                await c.fetchval("select 1")
    with tracing.span("turn.send"):
        for _ in range(6):
            async with conn() as c:
                await c.fetchval("select now()")


async def timed(n: int, on: bool) -> float:
    started = time.perf_counter()
    for _ in range(n):
        if on:
            async with tracing.run("job", "turn", labels={"bench": "overhead"}):
                await turn()
        else:
            await turn()
    return (time.perf_counter() - started) * 1000 / n


async def micro() -> tuple[float, float]:
    from app.ops.tracing.hooks.db import on_query

    class Rec:
        query, elapsed, exception = "select * from orders where id = $1", 0.001, None

    n = 20_000
    async with tracing.run("job", "turn") as r:
        r.recorded = False  # don't write the benchmark run
        t = time.perf_counter()
        for _ in range(n):
            with tracing.span("turn.load"):
                pass
        span_us = (time.perf_counter() - t) * 1e6 / n
        r.spans.clear()
        t = time.perf_counter()
        for _ in range(n):
            on_query(Rec)
        hook_us = (time.perf_counter() - t) * 1e6 / n
        r.spans.clear()
    return span_us, hook_us


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--turns", type=int, default=5)
    args = ap.parse_args()
    settings = get_settings()
    recorder.configure("bench")

    results: dict[bool, list[float]] = {True: [], False: []}
    for rep in range(args.reps):
        for on in ((True, False) if rep % 2 == 0 else (False, True)):  # alternate so drift hits both sides
            settings.ops_tracing_enabled = on  # the pool's statement hook is attached only when tracing is on
            pool_mod.set_pool(None)
            await pool_mod.open_pool(min_size=5, max_size=5)
            await timed(1, on)  # warm the pool
            results[on].append(await timed(args.turns, on))
            await recorder.flush()
            await pool_mod.close_pool()
    settings.ops_tracing_enabled = True
    span_us, hook_us = await micro()

    on, off = results[True], results[False]
    m_on, m_off = statistics.mean(on), statistics.mean(off)
    print(f"turn-shaped workload, {args.reps} reps × {args.turns} turns, 20 statements each")
    print(f"  tracing off: mean {m_off:7.1f} ms  stdev {statistics.stdev(off):6.1f}  min {min(off):7.1f}  max {max(off):7.1f}")
    print(f"  tracing on : mean {m_on:7.1f} ms  stdev {statistics.stdev(on):6.1f}  min {min(on):7.1f}  max {max(on):7.1f}")
    print(f"  difference : {m_on - m_off:+.1f} ms per turn ({(m_on - m_off) / m_off * 100:+.2f} %)")
    print(f"in-process cost: one span {span_us:.2f} µs, one statement hook {hook_us:.2f} µs "
          f"→ a 20-statement turn with 5 spans ≈ {(5 * span_us + 20 * hook_us) / 1000:.3f} ms of CPU")
    async with __import__("asyncpg").create_pool(settings.database_url.get_secret_value(), statement_cache_size=0,
                                                 min_size=1, max_size=1) as p, p.acquire() as c:
        await c.execute("delete from ops_runs where service = 'bench'")
        await c.execute("delete from ops_instances where service = 'bench'")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
