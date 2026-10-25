"""Recorder against the real DB (PDS §17): rows written after the work, kept when the business transaction rolls
back, failures never reach the work, stale RUNNING runs become ABANDONED. Rows use a throwaway service name."""

import secrets

import asyncpg
import pytest

from app.db.tx import tx
from app.ops import tracing
from app.ops.tracing import recorder, trace


@pytest.fixture
async def service(db: asyncpg.Pool):  # type: ignore[no-untyped-def]
    from app.core.logging import configure_logging

    configure_logging("info", "test")  # the real processor chain: scrubber, then the ops event hook
    name = f"test-{secrets.token_hex(4)}"
    recorder.configure(name)
    yield name
    recorder.configure("app")
    async with db.acquire() as c:
        await c.execute("delete from ops_runs where service = $1", name)
        await c.execute("delete from ops_instances where service = $1", name)


async def test_run_spans_and_events_are_written(db: asyncpg.Pool, service: str) -> None:
    async with tracing.run("job", "turn", subject=("pending_work", "w1")) as r:
        with tracing.span("turn.load"):
            async with tx() as c:
                await c.fetchval("select 1")
        tracing.degraded("llm_fallback_model", model="m")
        from app.core.logging import get_logger

        get_logger("test").warning("something_odd", order_id="o1", text="never stored")
    await recorder.flush()
    async with db.acquire() as c:
        row = await c.fetchrow("select * from ops_runs where run_id = $1", r.run_id)
        spans = await c.fetch("select name, level, kind, parent_id from ops_spans where run_id = $1 order by span_id",
                              r.run_id)
        events = await c.fetch("select level, message, detail from ops_run_events where run_id = $1 order by seq",
                               r.run_id)
    assert row["outcome"] == "SUCCEEDED_DEGRADED" and row["service"] == service and row["subject_id"] == "w1"
    assert row["span_count"] == len(spans) and row["dep_calls"]["db"] >= 1
    names = [s["name"] for s in spans]
    assert names[:1] == ["turn.load"] and "db.tx" in names and "db.query" in names
    assert [e["level"] for e in events] == ["DEGRADED", "WARNING"]
    assert events[1]["detail"]["text"] == "[removed]"


async def test_a_rolled_back_business_transaction_still_has_its_run(db: asyncpg.Pool, service: str) -> None:
    with pytest.raises(RuntimeError):
        async with tracing.run("job", "turn") as r:
            with tracing.span("turn.commit"):
                async with tx() as c:
                    await c.execute("select 1")
                    raise RuntimeError("rolled back")
    await recorder.flush()
    async with db.acquire() as c:
        row = await c.fetchrow("select outcome, error_stage, failure_reason from ops_runs where run_id = $1", r.run_id)
    assert dict(row) == {"outcome": "FAILED", "error_stage": "turn.commit", "failure_reason": "RuntimeError: rolled back"}


async def test_write_failure_never_reaches_the_work_and_warns_once(db: asyncpg.Pool, service: str,
                                                                   monkeypatch: pytest.MonkeyPatch,
                                                                   capsys: pytest.CaptureFixture[str]) -> None:
    async def broken(_: object) -> None:
        raise ConnectionError("db down")

    monkeypatch.setitem(recorder._WRITES, "end", broken)
    trace._warned.discard("ops.write_failed")
    results = []
    for i in range(3):
        async with tracing.run("job", "turn"):
            results.append(i * 2)
    await recorder.flush()
    assert results == [0, 2, 4]
    assert capsys.readouterr().out.count("ops.write_failed") == 1


async def test_sweeper_abandons_runs_of_a_silent_instance(db: asyncpg.Pool, service: str) -> None:
    async with db.acquire() as c:
        run_id = await c.fetchval(
            """insert into ops_runs (run_id, service, kind, name, started_at, instance, current_stage)
               values (gen_random_uuid(), $1, 'job', 'turn', now() - interval '2 minutes', 'dead-instance', 'turn.route')
               returning run_id::text""", service)
    assert (await recorder.sweep())["abandoned"] >= 1
    async with db.acquire() as c:
        row = await c.fetchrow("select outcome, error_stage from ops_runs where run_id = $1", run_id)
    assert dict(row) == {"outcome": "ABANDONED", "error_stage": "turn.route"}
