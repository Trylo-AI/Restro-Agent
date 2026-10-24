import asyncio
import base64
import json

import httpx

from app.domain import timers
from app.integrations import tasks
from app.integrations.tasks.local import LocalSignal, LocalTaskQueue


class Recorder:
    def __init__(self, fail_first: int = 0) -> None:
        self.calls: list[tuple[str, dict[str, object], str | None]] = []
        self.fail_first = fail_first

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if self.fail_first:
                self.fail_first -= 1
                return httpx.Response(503)
            self.calls.append((request.url.path, json.loads(request.content), request.headers.get("authorization")))
            return httpx.Response(200, json={})
        return httpx.MockTransport(handle)


async def test_delayed_task_delivered_once_with_internal_token() -> None:
    rec = Recorder()
    q = LocalTaskQueue(rec.transport())
    await q.enqueue("timers", {"kind": "overdue"}, delay_s=0.05, name="order:1:overdue")
    await q.enqueue("timers", {"kind": "overdue"}, delay_s=0.05, name="order:1:overdue")  # same name → dropped
    assert rec.calls == []
    await q.drain()
    assert len(rec.calls) == 1
    path, body, auth = rec.calls[0]
    assert path == "/internal/tasks/timer" and body == {"kind": "overdue"} and auth == "Bearer test-internal-token"


async def test_delivery_retries_until_the_worker_answers() -> None:
    rec = Recorder(fail_first=2)
    q = LocalTaskQueue(rec.transport())
    await q.enqueue("wa-ack", {"wamid": "w1"})
    await q.drain()
    assert len(rec.calls) == 1


async def test_signal_uses_a_pubsub_shaped_envelope() -> None:
    rec = Recorder()
    s = LocalSignal(rec.transport())
    await s.publish("cafe-1", "conv-1")
    await s.drain()
    path, body, _ = rec.calls[0]
    assert path == "/internal/pubsub/inbound"
    data = json.loads(base64.b64decode(body["message"]["data"]))  # type: ignore[index]
    assert data == {"cafe_id": "cafe-1", "conversation_id": "conv-1"}


async def test_timer_schedule_and_dispatch() -> None:
    sent: list[tuple[str, dict[str, object], float]] = []

    class Q:
        async def enqueue(self, queue: str, body: dict[str, object], *, delay_s: float = 0, name: str | None = None) -> None:
            sent.append((queue, body, delay_s))

    tasks.set_backends(tasks_impl=Q())
    try:
        fired: list[str] = []

        @timers.handler("overdue")
        async def on_overdue(cafe_id: str, subject_id: str, body: dict[str, object]) -> None:
            fired.append(f"{cafe_id}/{subject_id}")

        await timers.schedule("cafe-1", "overdue", "order-9", 600)
        queue, body, delay = sent[0]
        assert queue == "timers" and delay == 600
        await timers.dispatch(body)
        assert fired == ["cafe-1/order-9"]
    finally:
        tasks.set_backends()
    await asyncio.sleep(0)
