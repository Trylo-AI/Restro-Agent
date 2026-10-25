"""In-process shims: deliver to the local worker over HTTP, with Cloud Tasks-like retries."""

import asyncio
import base64
import contextvars
import json
import time
import uuid
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.internal_auth import internal_headers
from app.core.logging import get_logger
from app.integrations.tasks.base import QUEUE_PATHS
from app.ops import tracing

log = get_logger(__name__)

RETRY_DELAYS = (0.5, 1, 2, 4, 8)


async def _post(path: str, body: dict[str, Any], transport: httpx.AsyncBaseTransport | None,
                trace: dict[str, str] | None = None, eta: float | None = None, task_name: str | None = None) -> None:
    """Delivers like Cloud Tasks: the same retry-count, ETA and traceparent headers reach the worker."""
    url = get_settings().worker_url.rstrip("/") + path
    async with httpx.AsyncClient(timeout=30, transport=transport) as client:
        for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            headers = {**internal_headers(), **(trace or {}), "X-CloudTasks-TaskRetryCount": str(attempt)}
            if eta is not None:
                headers["X-CloudTasks-TaskETA"] = f"{eta:.3f}"
            if task_name:
                headers["X-CloudTasks-TaskName"] = task_name
            try:
                r = await client.post(url, json=body, headers=headers)
                if r.status_code < 300:
                    return
                log.warning("local_delivery_retry", path=path, status=r.status_code, attempt=attempt)
            except httpx.HTTPError as exc:
                log.warning("local_delivery_retry", path=path, error_class=type(exc).__name__, attempt=attempt)
    log.error("local_delivery_gave_up", path=path)


class _Background:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport
        self._running: set[asyncio.Task[None]] = set()

    def spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro, context=contextvars.Context())  # not part of the caller's ops run
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def drain(self) -> None:
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)


def _trace_headers() -> dict[str, str]:
    tp = tracing.traceparent()
    return {"traceparent": tp} if tp else {}


class LocalSignal(_Background):
    async def publish(self, cafe_id: str, conversation_id: str) -> None:
        with tracing.span("queue.publish", target="wa-inbound"):
            data = base64.b64encode(json.dumps({"cafe_id": cafe_id, "conversation_id": conversation_id}).encode()).decode()
            message: dict[str, Any] = {"data": data, "messageId": "local", "attributes": _trace_headers()}
            envelope = {"message": message, "subscription": "local/wa-inbound"}
            self.spawn(_post("/internal/pubsub/inbound", envelope, self.transport))


class LocalTaskQueue(_Background):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(transport)
        self._names: set[str] = set()

    async def enqueue(self, queue: str, body: dict[str, Any], *, delay_s: float = 0, name: str | None = None) -> None:
        if name:
            if name in self._names:
                return
            self._names.add(name)
        with tracing.span("queue.enqueue", target=queue, delay_s=delay_s):
            trace, eta = _trace_headers(), time.time() + delay_s
            task_name = name or f"{queue}-{uuid.uuid4().hex}"  # Cloud Tasks names every task too

        async def run() -> None:
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            await _post(QUEUE_PATHS[queue], body, self.transport, trace, eta, task_name)

        self.spawn(run())
