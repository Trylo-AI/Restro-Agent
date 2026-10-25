"""Pub/Sub push endpoint: ack the signal at once, then run the conversation's due work.

The signal carries only ids; the work itself is in pending_work, so a lost or duplicate signal changes nothing.
"""

import asyncio
import base64
import json
from typing import Any

from fastapi import Body

from app.api.internal import router
from app.core.logging import get_logger
from app.domain.conversation import pipeline
from app.ops import tracing

log = get_logger(__name__)
_running: set[asyncio.Task[None]] = set()


async def _run(cafe_id: str, conversation_id: str) -> None:
    try:
        await pipeline.run(cafe_id, conversation_id)
    except Exception as exc:  # the row stays recoverable; the sweep re-signals
        log.error("pipeline_crashed", cafe_id=cafe_id, conversation_id=conversation_id, error_class=type(exc).__name__)


@router.post("/pubsub/inbound")
async def inbound_signal(envelope: dict[str, Any] = Body(...)) -> dict[str, str]:
    try:
        data = json.loads(base64.b64decode(envelope["message"]["data"]))
        cafe_id, conversation_id = str(data["cafe_id"]), str(data["conversation_id"])
    except (KeyError, ValueError, TypeError):
        log.warning("pubsub_bad_envelope")
        return {"status": "ignored"}  # 2xx: a malformed signal must not be redelivered forever
    # The turn runs started in this task link back to the run that published the signal (traceparent attribute).
    token = tracing.set_upstream((envelope["message"].get("attributes") or {}).get("traceparent"), "api")
    try:
        task = asyncio.create_task(_run(cafe_id, conversation_id))
    finally:
        tracing.reset_upstream(token)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return {"status": "accepted"}
