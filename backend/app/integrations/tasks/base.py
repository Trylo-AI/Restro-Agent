from typing import Any, Protocol

# Queue name → worker path (docs/architecture.md#queues-timers-and-jobs).
QUEUE_PATHS = {
    "wa-ack": "/internal/tasks/ack",
    "timers": "/internal/tasks/timer",
    "campaign-send": "/internal/tasks/campaign-send",
    "profile-note": "/internal/tasks/profile-note",
}


class SignalPublisher(Protocol):
    async def publish(self, cafe_id: str, conversation_id: str) -> None: ...


class TaskQueue(Protocol):
    async def enqueue(self, queue: str, body: dict[str, Any], *, delay_s: float = 0, name: str | None = None) -> None:
        """`name` deduplicates like a Cloud Tasks task name: a second enqueue with the same name is dropped."""
        ...
