"""Cloud Run `worker` (internal): Pub/Sub push, Cloud Tasks, Cloud Scheduler jobs.

Mounts only /internal/* and /healthz; never a public router (docs/rules.md#backend).
Locally it also runs the scheduled jobs on a loop (Cloud Scheduler does that in the cloud).
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.internal import router as internal_router
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.db.pool import close_pool, open_pool
from app.domain import jobs
from app.domain import outbound as _outbound  # noqa: F401  (registers the send_check timer)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await open_pool()
    stop = asyncio.Event()
    loop_task = asyncio.create_task(jobs.local_loop(stop)) if get_settings().tasks_backend == "local" else None
    try:
        yield
    finally:
        stop.set()
        if loop_task:
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
        await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, service="worker")
    app = FastAPI(title="Restro worker", lifespan=lifespan,
                  docs_url="/docs" if settings.is_local else None, redoc_url=None)
    install_error_handlers(app)
    app.include_router(internal_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "worker"}

    return app


app = create_app()
