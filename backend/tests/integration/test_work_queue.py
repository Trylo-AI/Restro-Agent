"""Lease and work queue against a real Postgres (docs/rules.md: Work queue row)."""

import asyncio

import asyncpg
import pytest

from app.db.repositories import jobs as jobs_repo
from app.db.repositories import pending_work as pw
from app.domain.conversation import lease as lease_mod
from tests.integration.conftest import CafeFixture

pytestmark = pytest.mark.integration


async def test_two_concurrent_claims_give_one_holder(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    async def try_claim() -> lease_mod.Lease | None:
        async with db.acquire() as c:
            return await lease_mod.claim(c, cafe.cafe_id, cafe.conversation_id)

    results = await asyncio.gather(*[try_claim() for _ in range(5)])
    assert sum(r is not None for r in results) == 1


async def test_expired_lease_is_reclaimed_and_old_holder_commit_refused(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    async with db.acquire() as c:
        old = await lease_mod.claim(c, cafe.cafe_id, cafe.conversation_id)
        assert old is not None
        assert await lease_mod.claim(c, cafe.cafe_id, cafe.conversation_id) is None  # still held
        await c.execute("update conversations set lease_expires_at = now() - interval '1 second' where id = $1",
                        cafe.conversation_id)
        new = await lease_mod.claim(c, cafe.cafe_id, cafe.conversation_id)
        assert new is not None
        with pytest.raises(lease_mod.LeaseLost):
            async with c.transaction():
                await lease_mod.fence(c, old)
                await c.execute("update conversations set state = state || '{\"step\":\"browsing\"}' where id = $1",
                                cafe.conversation_id)
        assert old.lost
        async with c.transaction():
            await lease_mod.fence(c, new)  # the new holder commits fine
        assert not await lease_mod.renew(c, old)
        assert await lease_mod.renew(c, new)
        await lease_mod.release(c, new)
        assert await c.fetchval("select lease_owner from conversations where id = $1", cafe.conversation_id) is None


async def test_lease_in_other_cafe_is_never_claimed(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    async with db.acquire() as c:
        other_cafe = await c.fetchval("select id::text from cafes where id <> $1 limit 1", cafe.cafe_id)
        if other_cafe:
            assert await lease_mod.claim(c, other_cafe, cafe.conversation_id) is None


async def test_text_debounces_and_tap_is_due_now(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    async with db.acquire() as c:
        first = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=True)
        await asyncio.sleep(0.3)
        second = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=True)
        assert second.id == first.id and second.next_attempt_at > first.next_attempt_at
        # Round trips to a hosted DB can outlast the 1 s debounce, so push the due time out explicitly.
        await c.execute("update pending_work set next_attempt_at = now() + interval '1 minute' where id = $1", first.id)
        assert await pw.claim_due(c, cafe.cafe_id, first.id) is None  # not due yet
        tap = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=False)
        assert tap.id == first.id
        claimed = await pw.claim_due(c, cafe.cafe_id, first.id)
        assert claimed is not None and claimed.status == "running"
        assert await pw.claim_due(c, cafe.cafe_id, first.id) is None  # only once
        # A message during the running turn creates the next pending row.
        nxt = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=False)
        assert nxt.id != first.id
        await pw.mark_done(c, cafe.cafe_id, first.id)
        assert await c.fetchval("select status from pending_work where id = $1", first.id) == "done"


async def test_three_retries_then_dead_letter(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    async with db.acquire() as c:
        work = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=False)
        delays = []
        for _ in range(3):
            await c.execute("update pending_work set next_attempt_at = now() where id = $1", work.id)
            assert await pw.claim_due(c, cafe.cafe_id, work.id) is not None
            async with c.transaction():
                result = await pw.fail(c, cafe.cafe_id, work.id, "RuntimeError")
            assert not result.dead_lettered
            delays.append(result.retry_in)
        assert delays == [2.0, 4.0, 8.0]
        await c.execute("update pending_work set next_attempt_at = now() where id = $1", work.id)
        assert await pw.claim_due(c, cafe.cafe_id, work.id) is not None
        async with c.transaction():
            result = await pw.fail(c, cafe.cafe_id, work.id, "RuntimeError")
        assert result.dead_lettered and result.attempts == 4
        row = await c.fetchrow("select status, dead_lettered_at, last_error from pending_work where id = $1", work.id)
        assert row["status"] == "dead_lettered" and row["dead_lettered_at"] is not None
        assert row["last_error"] == "RuntimeError"


async def test_failure_merges_into_a_newer_pending_row(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    async with db.acquire() as c:
        work = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=False)
        assert await pw.claim_due(c, cafe.cafe_id, work.id)
        newer = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=False)
        async with c.transaction():
            result = await pw.fail(c, cafe.cafe_id, work.id, "TimeoutError")
        assert not result.dead_lettered
        rows = await c.fetch("select id::text, status, attempts from pending_work where conversation_id = $1",
                             cafe.conversation_id)
        status = {r["id"]: (r["status"], r["attempts"]) for r in rows}
        assert status[work.id][0] == "done"
        assert status[newer.id] == ("pending", 1)


async def test_stuck_work_is_found(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    async with db.acquire() as c:
        work = await pw.upsert_turn(c, cafe.cafe_id, cafe.conversation_id, debounce=False)
        await c.execute("update pending_work set next_attempt_at = now() - interval '2 minutes' where id = $1", work.id)
        stuck = [s for s in await jobs_repo.stuck_work(c) if s.cafe_id == cafe.cafe_id]
        assert [(s.conversation_id, s.reason) for s in stuck] == [(cafe.conversation_id, "overdue")]

        # A running turn whose holder died: lease expired → back to pending, re-signalled.
        assert await pw.claim_due(c, cafe.cafe_id, work.id)
        await c.execute("""update conversations set lease_owner = 'dead', lease_expires_at = now() - interval '1 second'
                           where id = $1""", cafe.conversation_id)
        stuck = [s for s in await jobs_repo.stuck_work(c) if s.cafe_id == cafe.cafe_id]
        assert [(s.conversation_id, s.reason) for s in stuck] == [(cafe.conversation_id, "lease_expired")]
        assert await c.fetchval("select status from pending_work where id = $1", work.id) == "pending"
