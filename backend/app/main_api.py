"""Cloud Run `api` (public): Meta webhooks, console REST, and /dev/* when the simulator is on.

Never mounts /internal/* (docs/rules.md#backend).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, service="api")
    app = FastAPI(title="Restro api", docs_url="/docs" if settings.is_local else None, redoc_url=None)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH"],
            allow_headers=["Authorization", "Content-Type", "X-Cafe-Id", "Idempotency-Key", "X-Sim-Token"],
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "api"}

    return app


app = create_app()
