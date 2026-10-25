"""Scheduled jobs (docs/architecture.md#scheduled-jobs). Cloud Scheduler calls /internal/jobs/{job} in the
cloud; locally the worker runs the ones this slice needs on a loop. Every job is safe to run twice.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger
from app.db.repositories import jobs as jobs_repo
from app.db.repositories import wa_messages
from app.db.tx import conn
from app.integrations import tasks

log = get_logger(__name__)


async def stuck_inbound_check() -> dict[str, int]:
    """Due work nobody picked up, or a running turn whose lease expired → re-signal (and alert)."""
    async with conn() as c:
        stuck = await jobs_repo.stuck_work(c)
        unsent = await wa_messages.never_attempted(c)
    for s in stuck:
        log.warning("stuck_inbound", cafe_id=s.cafe_id, conversation_id=s.conversation_id, reason=s.reason)
        await tasks.signal().publish(s.cafe_id, s.conversation_id)
    # Steps committed by a turn whose process died before sending: send them now (claimed atomically).
    from app.domain import outbound

    for cafe_id, step_id in unsent:
        log.warning("recovering_unsent_step", cafe_id=cafe_id, step_id=step_id)
        await outbound.deliver(cafe_id, step_id)
    return {"resignalled": len(stuck), "recovered_sends": len(unsent)}


async def llm_spend_check() -> dict[str, int]:
    """Today's LLM spend over LLM_DAILY_SPEND_LIMIT_USD → kill switch to the cheaper model (and alert)."""
    from app.core.config import get_settings
    from app.db.repositories import llm_calls
    from app.domain.ai_agent import calls

    async with conn() as c:
        spent = await llm_calls.spend_today_usd(c)
    over = spent > get_settings().llm_daily_spend_limit_usd
    calls.set_auto_kill("cheap" if over else None)
    if over:
        log.error("llm_spend_over_limit", spent_usd=round(spent, 4))
    return {"over_limit": int(over)}


JOBS: dict[str, Callable[[], Awaitable[dict[str, int]]]] = {
    "stuck_inbound_check": stuck_inbound_check,
    "llm_spend_check": llm_spend_check,
}

# Local loop intervals in seconds (Cloud Scheduler cron in the cloud).
LOCAL_SCHEDULE = {"stuck_inbound_check": 60, "llm_spend_check": 900}


def register(name: str, interval_s: int | None = None) -> Callable[[Callable[[], Awaitable[dict[str, int]]]], Callable[[], Awaitable[dict[str, int]]]]:
    def deco(fn: Callable[[], Awaitable[dict[str, int]]]) -> Callable[[], Awaitable[dict[str, int]]]:
        JOBS[name] = fn
        if interval_s:
            LOCAL_SCHEDULE[name] = interval_s
        return fn
    return deco


async def run_job(name: str) -> dict[str, int]:
    from app.ops import tracing

    async with tracing.run("cron", f"job.{name}"):
        with tracing.span("job.handle"):
            result = await JOBS[name]()
        tracing.count(**{k: v for k, v in result.items() if isinstance(v, int | float)})
    return result


async def local_loop(stop: asyncio.Event) -> None:
    """Runs each scheduled job on its interval until `stop` is set. Local only."""
    elapsed = 0
    while not stop.is_set():
        for name, every in LOCAL_SCHEDULE.items():
            if elapsed % every == 0:
                try:
                    await run_job(name)
                except Exception as exc:  # a failed run is retried next interval
                    log.error("job_failed", job=name, error_class=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)
        elapsed += 5
