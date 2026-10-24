"""/internal/* routes. Mounted on worker only, every route behind require_internal."""

from fastapi import APIRouter, Depends

from app.core.internal_auth import require_internal

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal)], include_in_schema=False)

from app.api.internal import jobs, pubsub, tasks  # noqa: E402,F401  (registers routes)
