from typing import Any

from fastapi import Body

from app.api.internal import router
from app.domain import timers


@router.post("/tasks/timer")
async def timer(body: dict[str, Any] = Body(...)) -> dict[str, str]:
    # 2xx only when the work is done or deliberately skipped; an exception makes the queue retry.
    await timers.dispatch(body)
    return {"status": "ok"}
