"""Timers: `schedule(kind, subject_id, delay)` and dispatch (docs/architecture.md#timers).

Each timer is a task on the `timers` queue. Handlers re-read state first and do nothing if the subject moved
on, so a timer can fire twice or late and nothing goes wrong. No timer polls the DB.
"""

from collections.abc import Awaitable, Callable

from app.core.logging import get_logger
from app.integrations import tasks

log = get_logger(__name__)

Handler = Callable[[str, str, dict[str, object]], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}

KINDS = {
    "accept_escalation_1", "accept_escalation_2", "help_escalation", "overdue", "auto_served", "status_message",
    "work_retry", "send_check", "feedback", "idle_handback",
}


def handler(kind: str) -> Callable[[Handler], Handler]:
    if kind not in KINDS:
        raise ValueError(f"unknown timer kind {kind}")

    def register(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return register


async def schedule(cafe_id: str, kind: str, subject_id: str, delay_s: float, *,
                   extra: dict[str, object] | None = None, dedupe: str | None = None) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown timer kind {kind}")
    body = {"kind": kind, "cafe_id": cafe_id, "subject_id": subject_id, **(extra or {})}
    await tasks.tasks().enqueue("timers", body, delay_s=delay_s, name=dedupe)


async def dispatch(body: dict[str, object]) -> None:
    kind = str(body.get("kind"))
    fn = _HANDLERS.get(kind)
    if fn is None:
        log.warning("timer_without_handler", kind=kind)
        return
    await fn(str(body["cafe_id"]), str(body["subject_id"]), body)
