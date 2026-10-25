from datetime import UTC, datetime
from typing import Any

from fastapi import Body, Request

from app.api.internal import router
from app.core.logging import get_logger
from app.db.repositories import cafes, conversations, customers
from app.db.tx import conn
from app.domain import timers
from app.integrations.whatsapp import client
from app.ops import tracing

log = get_logger(__name__)

# What each timer's subject_id names (for the ops Runs page).
TIMER_SUBJECT = {"help_escalation": "request", "idle_handback": "conversation", "work_retry": "conversation",
                 "send_check": "wa_message"}


def _task_run(request: Request, name: str, cafe_id: str | None) -> tracing.run:
    """One ops run per task attempt; the task (its Cloud Tasks name) is the unit, so retries group together.
    Cloud Tasks headers (the local shim sends the same ones) give the name, the attempt, the scheduled time and
    the traceparent of the run that enqueued it."""
    h = request.headers
    task = h.get("x-cloudtasks-taskname")
    subject = ("task", task[-100:]) if task else None
    retries = h.get("x-cloudtasks-taskretrycount", "0")
    eta = h.get("x-cloudtasks-tasketa")
    try:
        queued_at = datetime.fromtimestamp(float(eta), UTC) if eta else None
    except ValueError:
        queued_at = None
    attempt = int(retries) + 1 if retries.isdigit() else 1
    return tracing.run("job", name, subject=subject, attempt=attempt, queued_at=queued_at, tenant=cafe_id,
                       upstream=h.get("traceparent"))


def _retry_outcome(run: Any, exc: Exception) -> None:
    from app.core.config import get_settings

    last = run.attempt >= get_settings().ops_task_max_attempts
    run.finish("FAILED" if last else "RETRYING", error=exc)


@router.post("/tasks/timer")
async def timer(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, str]:
    # 2xx only when the work is done or deliberately skipped; an exception makes the queue retry.
    kind = str(body.get("kind"))
    subject_type = TIMER_SUBJECT.get(kind, "order")
    async with _task_run(request, f"timer.{kind}", str(body.get("cafe_id"))) as run:
        try:
            with tracing.span("task.handle", timer=kind, subject_type=subject_type, subject_id=str(body.get("subject_id"))):
                await timers.dispatch(body)
        except Exception as exc:
            _retry_outcome(run, exc)
            raise
    return {"status": "ok"}


@router.post("/tasks/ack")
async def ack(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, str]:
    """Mark read + typing right after the webhook, so the customer sees activity within a second."""
    cafe_id, conversation_id, wamid = str(body["cafe_id"]), str(body["conversation_id"]), str(body["wamid"])
    async with _task_run(request, "task.ack", cafe_id):
        with tracing.span("task.handle", conversation_id=conversation_id, wamid=wamid):
            async with conn() as c:
                cafe = await cafes.get(c, cafe_id)
                convo = await conversations.get(c, cafe_id, conversation_id)
                customer = await customers.get(c, cafe_id, convo.customer_id) if convo else None
            if cafe is None or customer is None or cafe.status == "disconnected":
                tracing.count(skipped=1)
                return {"status": "skipped"}
            try:
                with tracing.span("wa.mark_read", target=client.transport().__class__.__name__):
                    await client.transport().mark_read(cafe, wamid, customer.wa_id)
            except Exception as exc:  # best effort; never worth a retry storm
                log.warning("ack_failed", cafe_id=cafe_id, error_class=type(exc).__name__)
                tracing.degraded("ack_failed", error_class=type(exc).__name__)
    return {"status": "ok"}


@router.post("/tasks/profile-note")
async def profile_note(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, str]:
    from app.domain import customers

    cafe_id, note_id = str(body["cafe_id"]), str(body["note_id"])
    async with _task_run(request, "task.profile_note", cafe_id) as run:
        try:
            with tracing.span("task.handle", note_id=note_id):
                status = await customers.process_note(cafe_id, note_id)
        except Exception as exc:
            _retry_outcome(run, exc)
            raise
        if status == "failed":
            tracing.degraded("profile_note_failed")
        tracing.label(note_status=status)
    return {"status": status}


@router.post("/tasks/campaign-send")
async def campaign_send(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, str]:
    from app.domain import campaigns

    cafe_id, recipient_id = str(body["cafe_id"]), str(body["recipient_id"])
    async with _task_run(request, "task.campaign_send", cafe_id) as run:
        try:
            with tracing.span("task.handle", recipient_id=recipient_id):
                status = await campaigns.send_one(cafe_id, recipient_id)
        except Exception as exc:
            _retry_outcome(run, exc)
            raise
        tracing.label(send_status=status)
    return {"status": status}
