"""Provenance for every run (PDS §8.1): git sha, app version, and a hash of the performance-relevant settings."""

import hashlib
import json
import os
import socket
import subprocess  # noqa: S404
import uuid
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from app.core.config import get_settings


@lru_cache
def git_sha() -> str | None:
    if sha := os.environ.get("GIT_SHA") or os.environ.get("K_REVISION"):
        return sha[:40]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3,  # noqa: S607
                             cwd=Path(__file__).resolve().parents[3], check=False)
        return out.stdout.strip()[:40] or None
    except (OSError, subprocess.SubprocessError):
        return None


@lru_cache
def app_version() -> str | None:
    try:
        return metadata.version("restro-backend")
    except metadata.PackageNotFoundError:
        return None


@lru_cache
def settings_snapshot() -> dict[str, Any]:
    """Only settings that change performance; never a secret or URL."""
    from app.db.pool import POOL_MAX_SIZE

    s = get_settings()
    return {
        "llm_backend": s.llm_backend, "llm_model_primary": s.llm_model_primary,
        "llm_model_fallback": s.llm_model_fallback, "llm_timeout_seconds": s.llm_timeout_seconds,
        "llm_kill_switch": s.llm_kill_switch, "wa_transport": s.wa_transport, "tasks_backend": s.tasks_backend,
        "signal_backend": s.signal_backend, "db_pool_max": POOL_MAX_SIZE,
    }


@lru_cache
def config_hash() -> str:
    return hashlib.sha1(json.dumps(settings_snapshot(), sort_keys=True).encode()).hexdigest()[:16]  # noqa: S324


@lru_cache
def instance_id(service: str) -> str:
    return f"{service}-{socket.gethostname()[:40]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"[:100]
