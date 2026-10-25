"""Inbound HTTP hook (PDS §6.3): one run per request on `api`, named by route template.

Pure ASGI, so the route handler runs in this task's context and its spans land in the run. Status → outcome:
2xx/3xx SUCCEEDED, 4xx (not 408/429) REJECTED, 408/429/5xx FAILED. The Meta webhook is `kind=message`.
`/dev/*`, `/healthz`, `/docs`, `/api/ops/*` and preflights are not traced.
"""

import uuid
from typing import Any

from app.ops.tracing import trace

SKIP = ("/dev/", "/healthz", "/docs", "/openapi.json", "/api/ops", "/favicon")
Scope = dict[str, Any]


def outcome_for(status: int) -> str:
    if status < 400:
        return "SUCCEEDED"
    if status < 500 and status not in (408, 429):
        return "REJECTED"
    return "FAILED"


class OpsMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        path: str = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or path.startswith(SKIP) or not trace.enabled():
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        kind = "message" if path.startswith("/webhooks/") else "http"
        state = {"status": 500, "bytes_out": 0}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                state["status"] = int(message["status"])
            elif message["type"] == "http.response.body":
                state["bytes_out"] += len(message.get("body", b""))
            await send(message)

        async with trace.run(kind, f"{scope.get('method')} {path}", tenant=_uuid(headers.get("x-cafe-id")),
                             upstream=headers.get("traceparent"), insert_at_start=False) as r:
            try:
                if kind == "http":
                    with trace.span("request.handle"):
                        await self.app(scope, receive, send_wrapper)
                else:  # the webhook records its own stages
                    await self.app(scope, receive, send_wrapper)
            finally:
                route = scope.get("route")
                template = getattr(route, "path", None)
                r.name = f"{scope.get('method')} {template or _unmatched(path)}"[:150]
                status = state["status"]
                r.http_status, r.bytes_out = status, state["bytes_out"]
                if length := headers.get("content-length"):
                    r.bytes_in = int(length) if length.isdigit() else None
                if r.outcome is None:
                    r.outcome = outcome_for(status)
                    if r.outcome != "SUCCEEDED" and not r.error_code:
                        r.error_code = f"HTTP_{status}"


def _unmatched(path: str) -> str:
    return "(unmatched)" if path.count("/") > 1 else path


def _uuid(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(value)) if value else None
    except ValueError:
        return None
