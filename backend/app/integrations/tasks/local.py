"""In-process shims: deliver to the local worker over HTTP, with Cloud Tasks-like retries."""

import asyncio
import base64
import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.internal_auth import internal_headers
from app.core.logging import get_logger
from app.integrations.tasks.base import QUEUE_PATHS

log = get_logger(__name__)

RETRY_DELAYS = (0.5, 1, 2, 4, 8)


async def _post(path: str, body: dict[str, Any], transport: httpx.AsyncBaseTransport | None) -> None:
    url = get_settings().worker_url.rstrip("/") + path
    async with httpx.AsyncClient(timeout=30, transport=transport) as client:
        for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                r = await client.post(url, json=body, headers=internal_headers())
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
        task = asyncio.create_task(coro)
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def drain(self) -> None:
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)


class LocalSignal(_Background):
    async def publish(self, cafe_id: str, conversation_id: str) -> None:
        data = base64.b64encode(json.dumps({"cafe_id": cafe_id, "conversation_id": conversation_id}).encode()).decode()
        envelope = {"message": {"data": data, "messageId": "local"}, "subscription": "local/wa-inbound"}
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

        async def run() -> None:
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            await _post(QUEUE_PATHS[queue], body, self.transport)

        self.spawn(run())
