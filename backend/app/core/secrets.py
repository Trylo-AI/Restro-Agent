"""Secret references → values. The DB only ever holds references (CLAUDE.md invariant 12).

References: `env:NAME` (local: process env, then backend/.env) · `gcp:projects/.../secrets/...` (Phase 3).
Values are cached for 10 minutes and never logged.
"""

import os
import time
from functools import lru_cache

from app.core.config import ENV_FILE, get_settings

_TTL_SECONDS = 600
_cache: dict[str, tuple[float, str]] = {}


class SecretNotFound(Exception):
    pass


@lru_cache
def _dotenv() -> dict[str, str]:
    if os.environ.get("RESTRO_TEST") or not ENV_FILE.exists():
        return {}
    out = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def resolve(ref: str) -> str:
    hit = _cache.get(ref)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    value = _load(ref)
    _cache[ref] = (time.monotonic() + _TTL_SECONDS, value)
    return value


def forget(ref: str) -> None:
    """Drop a cached value (e.g. after Meta error 190, token invalid)."""
    _cache.pop(ref, None)


def _load(ref: str) -> str:
    scheme, _, name = ref.partition(":")
    if scheme == "env":
        if get_settings().secrets_backend != "env":
            raise SecretNotFound("env: references are only allowed with SECRETS_BACKEND=env")
        value = os.environ.get(name) or _dotenv().get(name)
        if not value:
            raise SecretNotFound(f"secret reference {ref!r} has no value")
        return value
    if scheme == "gcp":
        raise SecretNotFound("Secret Manager backend arrives in Phase 3 (integrations/gcp)")
    raise SecretNotFound(f"unknown secret reference scheme in {ref!r}")
