"""Postgres work queue (docs/architecture.md#message-pipeline, contracts/state-machines.md#work-queue).

One `pending` row per (conversation, kind). Postgres decides order; signals only wake the worker.
"""

from dataclasses import dataclass
from datetime import datetime

import asyncpg

MAX_RETRIES = 3          # 3 retries after the first failure; the 4th failure dead-letters
BACKOFF_SECONDS = (2, 4, 8)
DEBOUNCE_SECONDS = 1.0


@dataclass(frozen=True)
class Work:
    id: str
    cafe_id: str
    conversation_id: str
    status: str
    attempts: int
    next_attempt_at: datetime


def _work(row: asyncpg.Record | None) -> Work | None:
    return Work(**dict(row)) if row else None




async def upsert_turn(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, *, debounce: bool) -> Work:
    """Text debounces (due = now + 1 s, moved again by each new text); a tap is due now."""
    delay = DEBOUNCE_SECONDS if debounce else 0.0
    row = await conn.fetchrow(
        """insert into pending_work (cafe_id, conversation_id, kind, status, next_attempt_at)
            values ($1, $2, 'turn', 'pending', now() + make_interval(secs => $3))
            on conflict (conversation_id, kind) where status = 'pending'
            do update set next_attempt_at = case when $4 then excluded.next_attempt_at
                                                 else least(pending_work.next_attempt_at, excluded.next_attempt_at) end
            returning id::text, cafe_id::text, conversation_id::text, status, attempts, next_attempt_at""",
        cafe_id, conversation_id, delay, debounce,
    )
    assert row is not None
    return Work(**dict(row))


async def next_pending(conn: asyncpg.Connection, cafe_id: str, conversation_id: str) -> Work | None:
    return _work(await conn.fetchrow(
        """select id::text, cafe_id::text, conversation_id::text, status, attempts, next_attempt_at from pending_work
            where cafe_id = $1 and conversation_id = $2 and kind = 'turn' and status = 'pending'""",
        cafe_id, conversation_id,
    ))


async def claim_due(conn: asyncpg.Connection, cafe_id: str, work_id: str) -> Work | None:
    """pending → running, only once due. Caller holds the conversation lease."""
    return _work(await conn.fetchrow(
        """update pending_work set status = 'running'
            where id = $2 and cafe_id = $1 and status = 'pending' and next_attempt_at <= now()
            returning id::text, cafe_id::text, conversation_id::text, status, attempts, next_attempt_at""",
        cafe_id, work_id,
    ))


async def mark_done(conn: asyncpg.Connection, cafe_id: str, work_id: str) -> None:
    await conn.execute(
        "update pending_work set status = 'done', last_error = null where id = $2 and cafe_id = $1 and status = 'running'",
        cafe_id, work_id,
    )


@dataclass(frozen=True)
class FailResult:
    dead_lettered: bool
    retry_in: float | None
    attempts: int


async def fail(conn: asyncpg.Connection, cafe_id: str, work_id: str, error: str) -> FailResult:
    """running → pending with backoff, or dead_lettered on the 4th failure.

    If a new message already created the next `pending` row, this row is closed and its attempt count moves
    onto that row, so retries keep counting and the unique-pending rule holds.
    """
    error = error[:200]
    row = await conn.fetchrow(
        "select conversation_id::text, attempts from pending_work where id = $2 and cafe_id = $1 for update",
        cafe_id, work_id,
    )
    if row is None:
        return FailResult(dead_lettered=False, retry_in=None, attempts=0)
    attempts = row["attempts"] + 1
    if attempts > MAX_RETRIES:
        await conn.execute(
            """update pending_work set status = 'dead_lettered', attempts = $3, last_error = $4, dead_lettered_at = now()
               where id = $2 and cafe_id = $1""",
            cafe_id, work_id, attempts, error,
        )
        # A newer pending row for the same messages would only fail again; fold it into the dead letter.
        await conn.execute(
            """update pending_work set status = 'done', last_error = 'merged_into_dead_letter'
               where cafe_id = $1 and conversation_id = $2 and status = 'pending'""",
            cafe_id, row["conversation_id"],
        )
        return FailResult(dead_lettered=True, retry_in=None, attempts=attempts)

    delay = float(BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1])
    other = await conn.fetchval(
        """select id::text from pending_work
           where cafe_id = $1 and conversation_id = $2 and kind = 'turn' and status = 'pending'""",
        cafe_id, row["conversation_id"],
    )
    if other:
        await conn.execute(
            "update pending_work set status = 'done', last_error = $3 where id = $2 and cafe_id = $1",
            cafe_id, work_id, f"merged:{error}"[:200],
        )
        await conn.execute(
            """update pending_work set attempts = greatest(attempts, $3), last_error = $4,
                      next_attempt_at = greatest(next_attempt_at, now() + make_interval(secs => $5))
               where id = $2 and cafe_id = $1""",
            cafe_id, other, attempts, error, delay,
        )
    else:
        await conn.execute(
            """update pending_work set status = 'pending', attempts = $3, last_error = $4,
                      next_attempt_at = now() + make_interval(secs => $5)
               where id = $2 and cafe_id = $1""",
            cafe_id, work_id, attempts, error, delay,
        )
    return FailResult(dead_lettered=False, retry_in=delay, attempts=attempts)
