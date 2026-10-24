"""Auth for /internal/* on worker: Pub/Sub push, Cloud Tasks, Cloud Scheduler.

dev_token (local shims): `Authorization: Bearer <INTERNAL_DEV_TOKEN>`.
oidc (Phase 3): Google-signed OIDC token for invoker-sa; not wired yet, so every call is refused.
"""

import hmac

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import Unauthenticated


async def require_internal(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if settings.internal_auth == "dev_token":
        expected = settings.internal_dev_token.get_secret_value() if settings.internal_dev_token else ""
        presented = (authorization or "").removeprefix("Bearer ").strip()
        if expected and presented and hmac.compare_digest(presented, expected):
            return
        raise Unauthenticated()
    raise Unauthenticated("OIDC verification arrives with Cloud Tasks in Phase 3")


def internal_headers() -> dict[str, str]:
    token = get_settings().internal_dev_token
    return {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
