"""PDS Gate 1 on the local stack (docs/OPS_PLAN.md). Simulated customers with forced failures, then checks on the
ops tables:

- one run per attempt; a failed attempt keeps its reason after the retry succeeds;
- four crashes → three RETRYING runs and one FAILED (dead letter);
- an ambiguous send and a model fallback make the turn SUCCEEDED_DEGRADED with the reason recorded;
- Σ stage_ms is within 5 % of duration_ms for turns;
- the webhook run and the turn it woke are linked by traceparent.

Run with api and worker up. Synthetic customers only; prints ids and numbers, never message text.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from run_conversations import Sim  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.pool import close_pool, open_pool  # noqa: E402
from app.db.tx import conn  # noqa: E402

FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        FAILS.append(what)


async def customer(client: httpx.AsyncClient, table: int, name: str) -> Sim:
    r = await client.post("/dev/sim/customers")
    sim = Sim(client, r.json()["wa_id"])
    await sim.say(f"Hi, I'm at Table {table}")
    await sim.expect(r"What should we call you\?", 90)
    await sim.say(name)
    await sim.expect(r'"id": "menu"', 90)
    for _ in range(60):  # rows are written before the send: let the last reply leave before arming a fault
        t = (await client.get("/dev/sim/thread", params={"wa_id": sim.wa_id})).json()
        if all(i["status"] not in ("pending", "ambiguous") for i in t["items"] if i["dir"] == "out"):
            break
        await asyncio.sleep(1)
    return sim


async def arm(client: httpx.AsyncClient, fault: str, times: int = 1) -> None:
    for _ in range(times):
        (await client.post("/dev/sim/faults", json={"fault": fault})).raise_for_status()


async def turn_runs(conversation_wa: str, since: object) -> list[dict[str, object]]:
    async with conn() as c:
        rows = await c.fetch(
            """select r.run_id::text, r.subject_id, r.attempt, r.outcome, r.error_code, r.failure_reason, r.degradations,
                      r.duration_ms, r.stage_ms, r.upstream_trace
               from ops_runs r join pending_work w on w.id::text = r.subject_id
               join conversations cv on cv.id = w.conversation_id join customers cu on cu.id = cv.customer_id
               where r.name = 'turn' and cu.wa_id = $1 and r.started_at > $2
               order by r.started_at""", conversation_wa, since)
    return [dict(r) for r in rows]


async def main() -> int:
    s = get_settings()
    await open_pool()
    try:
        async with conn() as c:
            since = await c.fetchval("select now()")
        async with httpx.AsyncClient(base_url=s.api_url, timeout=30,
                                     headers={"x-sim-token": s.simulator_token.get_secret_value()}) as client:  # type: ignore[union-attr]
            print("1. one crash, then recovery")
            a = await customer(client, 3, "Tara")
            await arm(client, "turn_crash")
            await a.tap("menu")
            await a.expect(r'"id": "cat:', 90)
            print("2. four crashes → dead letter")
            b = await customer(client, 4, "Kabir")
            await arm(client, "turn_crash", 4)
            await b.tap("menu")
            await b.expect("something went wrong on our side", 120)
            print("3. ambiguous send")
            c3 = await customer(client, 5, "Zoya")
            await arm(client, "send_timeout")
            await c3.tap("menu")
            await c3.expect(r'"id": "cat:', 90)
            print("4. model fails once → the other model answers")
            d = await customer(client, 6, "Meher")
            await arm(client, "llm_fail")
            await d.say("2 cold coffee please")
            await d.expect(r'"id": "confirm:\d+"|Could you say that again|team member', 120)
            await asyncio.sleep(8)  # the last runs are written by the background writer

        print("\nchecks")
        ra = [r for r in await turn_runs(a.wa_id, since) if r["subject_id"] == (await turn_runs(a.wa_id, since))[-1]["subject_id"]]
        check([r["outcome"] for r in ra] == ["RETRYING", "SUCCEEDED"], f"crash once: {[r['outcome'] for r in ra]}")
        check(bool(ra) and ra[0]["failure_reason"] == "RuntimeError: simulated turn crash" and ra[0]["attempt"] == 1
              and ra[-1]["attempt"] == 2, "the failed attempt keeps its reason after the retry succeeded")
        rb = await turn_runs(b.wa_id, since)
        last = [r for r in rb if r["subject_id"] == rb[-1]["subject_id"]]
        check([r["outcome"] for r in last] == ["RETRYING"] * 3 + ["FAILED"],
              f"four crashes: {[r['outcome'] for r in last]}")
        check(bool(last) and last[-1]["error_code"] == "DEAD_LETTER", "dead letter coded DEAD_LETTER")
        rc = await turn_runs(c3.wa_id, since)
        check(any(r["outcome"] == "SUCCEEDED_DEGRADED" and any(d["reason"] == "send_ambiguous" for d in r["degradations"])
                  for r in rc), "ambiguous send → SUCCEEDED_DEGRADED (send_ambiguous)")
        rd = await turn_runs(d.wa_id, since)
        check(any(any(x["reason"] == "llm_fallback_model" for x in r["degradations"]) for r in rd),
              "model failure → llm_fallback_model recorded")
        every = rc + rd + ra
        off = [r for r in every if r["outcome"] in ("SUCCEEDED", "SUCCEEDED_DEGRADED") and r["duration_ms"]
               and abs(sum(r["stage_ms"].values()) - r["duration_ms"]) / r["duration_ms"] > 0.05]
        check(not off, f"Σ stage_ms within 5 % of duration_ms ({len(every)} turns, {len(off)} outside)")
        async with conn() as c:
            linked = await c.fetchval(
                """select count(*) from ops_runs t join ops_runs w on replace(w.run_id::text, '-', '') = t.upstream_trace
                   where t.name = 'turn' and t.started_at > $1 and w.kind = 'message'""", since)
        check(linked > 0, f"turns linked to the webhook run that woke them ({linked})")
    finally:
        await close_pool()
    print("\nGate 1:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
