"""Cloud Run `worker` (internal): Pub/Sub push, Cloud Tasks, Cloud Scheduler jobs.

Mounts only /internal/* and /healthz; never a public router (docs/rules.md#backend).
Locally it also runs the scheduled jobs on a loop (Cloud Scheduler does that in the cloud).
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.internal import router as internal_router
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.db.pool import close_pool, open_pool
from app.domain import campaigns, jobs
from app.domain import feedback as _feedback  # noqa: F401  (registers the feedback timer)
from app.domain import outbound as _outbound  # noqa: F401  (registers the send_check timer)
from app.domain.conversation import agent_turn, pipeline, router
from app.domain.conversation import recommend as recommend_flow
from app.domain.handoff import service as _help_timers  # noqa: F401  (registers help timers, dead-letter hook)
from app.domain.orders import service as _order_timers  # noqa: F401  (registers order timers)
from app.ops.tracing import recorder

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"

pipeline.set_handler(router.route)
router.set_free_text_handler(agent_turn.handle_text)
router.set_recommend_handler(recommend_flow.on_tap)
jobs.register("campaign_start", 60)(campaigns.start_due)  # every 5 min in the cloud (Cloud Scheduler)
jobs.register("campaign_resume", 600)(campaigns.resume_deferred)
jobs.register("ops_sweep", 60)(recorder.sweep)  # RUNNING runs of a dead process → ABANDONED
jobs.register("ops_retention", 86_400)(recorder.retention)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await open_pool()
    await recorder.heartbeat_now()
    stop = asyncio.Event()
    loop_task = asyncio.create_task(jobs.local_loop(stop)) if get_settings().tasks_backend == "local" else None
    try:
        yield
    finally:
        stop.set()
        if loop_task:
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
        await recorder.flush()
        await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, service="worker",
                      log_dir=(settings.ops_log_dir or str(LOG_DIR)) if settings.app_env == "local" else None)
    recorder.configure("worker")
    app = FastAPI(title="Restro worker", lifespan=lifespan,
                  docs_url="/docs" if settings.is_local else None, redoc_url=None)
    install_error_handlers(app)
    app.include_router(internal_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "worker"}

    return app


app = create_app()
