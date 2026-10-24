"""Cloud Run `worker` (internal): Pub/Sub push, Cloud Tasks, Cloud Scheduler jobs.

Mounts only /internal/* and /healthz; never a public router (docs/rules.md#backend).
"""

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, service="worker")
    app = FastAPI(title="Restro worker", docs_url="/docs" if settings.is_local else None, redoc_url=None)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "worker"}

    return app


app = create_app()
