"""Metric definitions (PDS §7) on fixture rows with known answers, against the real DB engine; plus Gate 4 (a planted
slowdown shows up in Compare on that span only) and Gate 5 (a planted N+1 is reported with a saving and an example
run). Rows live under a throwaway service name, so the window filter sees only them."""

import random
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.ops import insights, queries

NOW = datetime.now(UTC)


class Fx:
    def __init__(self, c: asyncpg.Connection, service: str) -> None:
        self.c, self.service, self.t = c, service, NOW - timedelta(minutes=50)

    async def run(self, name: str, outcome: str, ms: float, *, subject: str | None = None, attempt: int = 1,
                  kind: str = "job", labels: dict[str, str] | None = None, counts: dict[str, float] | None = None,
                  stage_ms: dict[str, float] | None = None, degradations: list[dict[str, str]] | None = None,
                  fingerprint: str | None = None, spans: list[tuple[str, int, str, float, int | None, dict[str, str]]] = ()
                  ) -> str:
        self.t += timedelta(seconds=1)
        run_id = str(uuid.uuid4())
        await self.c.execute(
            """insert into ops_runs (run_id, service, kind, name, subject_type, subject_id, attempt, started_at,
                                     finished_at, duration_ms, outcome, labels, counts, stage_ms, degradations,
                                     failure_fingerprint, error_code)
               values ($1, $2, $3, $4, $5, $6, $7, $8, $8, $9, $10, $11, $12, $13, $14, $15, $16)""",
            run_id, self.service, kind, name, "pending_work" if subject else None, subject, attempt, self.t, ms,
            outcome, labels or {}, counts or {}, stage_ms or {}, degradations or [], fingerprint,
            "X" if fingerprint else None)
        for i, (sname, level, skind, sms, parent, attrs) in enumerate(spans, start=1):
            await self.c.execute(
                """insert into ops_spans (run_id, span_id, parent_id, name, level, kind, start_ms, duration_ms, outcome,
                                          target, attrs) values ($1, $2, $3, $4, $5, $6, 0, $7, 'ok', $8, $9)""",
                run_id, i, parent, sname, level, skind, sms, attrs.get("db.table"), attrs)  # the pool encodes jsonb
        return run_id


@pytest.fixture
async def fx(db: asyncpg.Pool) -> AsyncIterator[Fx]:
    service = f"test-{secrets.token_hex(4)}"
    async with db.acquire() as c:
        yield Fx(c, service)
        await c.execute("delete from ops_runs where service = $1", service)


def win(fx: Fx) -> queries.Window:
    return queries.Window(NOW - timedelta(hours=1), NOW + timedelta(minutes=1), fx.service)


async def test_unit_outcomes_rates_and_percentiles(fx: Fx) -> None:
    await fx.run("turn", "SUCCEEDED", 1000, subject="u1", counts={"reply_ms": 2000})
    await fx.run("turn", "RETRYING", 500, subject="u2", fingerprint="f1")
    await fx.run("turn", "SUCCEEDED", 1200, subject="u2", attempt=2, counts={"reply_ms": 2500})
    for a in (1, 2, 3):
        await fx.run("turn", "RETRYING", 400, subject="u3", attempt=a, fingerprint="f2")
    await fx.run("turn", "FAILED", 400, subject="u3", attempt=4, fingerprint="f2")
    await fx.run("turn", "SUCCEEDED_DEGRADED", 3000, subject="u4", counts={"reply_ms": 6000},
                 degradations=[{"reason": "llm_fallback_model"}])
    await fx.run("POST /orders/{order_id}/transition", "REJECTED", 50, kind="http")

    o = await queries.unit_outcomes(win(fx), "turn")
    assert (o["units"], o["succeeded"], o["degraded"], o["failed"], o["retried"], o["recovered"]) == (4, 3, 1, 1, 2, 1)
    assert o["success_rate"] == 0.75 and o["degraded_rate"] == round(1 / 3, 4)
    every = await queries.unit_outcomes(win(fx))
    assert every["rejected"] == 1 and every["success_rate"] == 0.75  # rejected counts in neither side

    p = await queries.percentiles(win(fx), name="turn")
    assert (p["p50"], p["n"]) == (1200.0, 3)  # final successful runs only: the fast failures don't count
    r = await queries.percentiles(win(fx), name="turn", field="counts.reply_ms")
    assert r["p50"] == 2500.0

    fails = {f["fingerprint"]: f for f in await queries.failures(win(fx))}
    assert fails["f1"]["recovered"] == 1 and fails["f1"]["final"] == 0
    assert fails["f2"]["final"] == 1 and fails["f2"]["runs"] == 4
    degr = await queries.degradation_counts(win(fx))
    assert degr[0]["reason"] == "llm_fallback_model" and degr[0]["count"] == 1 and degr[0]["cost_only"] is False


async def test_stage_share_counts_each_stage_once(fx: Fx) -> None:
    await fx.run("turn", "SUCCEEDED", 1000, subject="s1", stage_ms={"turn.load": 200, "turn.route": 800})
    await fx.run("turn", "SUCCEEDED", 1000, subject="s2", stage_ms={"turn.load": 300, "turn.route": 700})
    shares = {s["stage"]: float(s["share"]) for s in await queries.stage_share(win(fx))}
    assert shares == {"turn.route": 0.75, "turn.load": 0.25}


async def test_gate4_planted_slowdown_is_significant_on_that_span_only(fx: Fx) -> None:
    rnd = random.Random(7)  # noqa: S311  (reproducible test jitter, not security)
    for cohort, slow_ms in (("a", 10.0), ("b", 60.0)):
        for _ in range(15):
            fast = 10 + rnd.random()
            slow = slow_ms + rnd.random()
            await fx.run("bench.turn", "SUCCEEDED", fast + slow, labels={"cohort": cohort},
                         spans=[("turn.load", 1, "internal", fast, None, {}),
                                ("turn.route", 1, "internal", slow, None, {})])
    cmp = await queries.latency_compare(win(fx), "bench.turn", "label.cohort", "a", "b")
    by = {s["span"]: s for s in cmp["spans"]}
    assert by["turn.route"]["significant"] and by["turn.route"]["delta_p50"] == pytest.approx(50, abs=1)
    assert not by["turn.load"]["significant"]


async def test_gate5_planted_n_plus_one_is_reported_with_saving_and_example(fx: Fx) -> None:
    stmt = {"db.statement_hash": "abc123", "db.table": "order_items", "db.operation": "select"}
    run_ids = []
    for _ in range(3):
        spans = [("turn.commit", 1, "internal", 400, None, {})] + [("db.query", 3, "db", 20, 1, stmt)] * 12
        run_ids.append(await fx.run("turn", "SUCCEEDED", 500, subject=str(uuid.uuid4()), spans=spans))
    await fx.run("turn", "SUCCEEDED", 100, subject="clean", spans=[("db.query", 3, "db", 20, None, stmt)] * 3)
    found = [f for f in await insights.n_plus_one(win(fx))]
    assert len(found) == 1
    f = found[0]
    assert f["where"] == {"name": "turn", "span": "turn.commit", "target": "order_items", "statement": "abc123"}
    assert f["evidence"]["calls_per_run_p50"] == 12 and f["evidence"]["runs_affected"] == 3
    assert f["estimated_saving"]["ms_per_day"] > 0 and f["example_run_ids"][0] in run_ids
    detail = await queries.run_detail(f["example_run_ids"][0])  # the example link opens a trace
    assert detail is not None and len(detail["spans"]) == 13


def test_mann_whitney_matches_known_values() -> None:
    assert queries.mann_whitney_p([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0, abs=0.01)
    assert queries.mann_whitney_p(list(range(20)), list(range(100, 120))) < 0.001
    assert queries.mann_whitney_p([1, 2], [3, 4]) is None
