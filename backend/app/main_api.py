"""Cloud Run `api` (public): Meta webhooks, console REST, and /dev/* when the simulator is on.

Never mounts /internal/* (docs/rules.md#backend).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import staff, webhooks
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.db.pool import close_pool, open_pool
from app.ops.tracing import recorder
from app.ops.tracing.hooks.http_in import OpsMiddleware

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await open_pool()
    await recorder.heartbeat_now()
    try:
        yield
    finally:
        await recorder.flush()
        await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, service="api",
                      log_dir=(settings.ops_log_dir or str(LOG_DIR)) if settings.app_env == "local" else None)
    recorder.configure("api")
    app = FastAPI(title="Restro api", lifespan=lifespan,
                  docs_url="/docs" if settings.is_local else None, redoc_url=None)
    install_error_handlers(app)
    app.add_middleware(OpsMiddleware)  # one ops run per request (docs/OPS_PLAN.md)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH"],
            allow_headers=["Authorization", "Content-Type", "X-Cafe-Id", "Idempotency-Key", "X-Sim-Token", "X-Ops-Token"],
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "api"}

    app.include_router(webhooks.router)
    app.include_router(staff.router)
    if settings.simulator_on:
        from app.api.dev import sim

        app.include_router(sim.router)

    return app


app = create_app()
