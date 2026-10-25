"""The eval runner's Alibaba client: the app's adapter (app/integrations/llm/dashscope.py) with the key read from
DASHSCOPE_API_KEY in the environment or backend/.env. Synthetic cases only; the key is never printed."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.integrations.llm.dashscope import DashScopeClient as _AppClient
from app.integrations.llm.dashscope import extract_json

ENV_FILE = Path(__file__).resolve().parents[1] / "backend" / ".env"

__all__ = ["DashScopeClient", "api_key", "extract_json"]


def api_key() -> str:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("DASHSCOPE_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("DASHSCOPE_API_KEY is missing in backend/.env")
    return key


def DashScopeClient(**kw: Any) -> _AppClient:  # noqa: N802 (keeps the eval runner's call site)
    if kw.get("transport") is None:
        kw.setdefault("api_key", api_key())
    return _AppClient(**kw)
