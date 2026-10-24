from typing import Any


class CloudTaskQueue:
    """Cloud Tasks with OIDC for invoker-sa; task name = dedupe key. Phase 3."""

    async def enqueue(self, queue: str, body: dict[str, Any], *, delay_s: float = 0, name: str | None = None) -> None:
        raise NotImplementedError("TASKS_BACKEND=cloud_tasks arrives in Phase 3; use TASKS_BACKEND=local")
