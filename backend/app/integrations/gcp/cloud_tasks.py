"""Cloud Tasks queue (TASKS_BACKEND=cloud_tasks): one HTTP task per enqueue, delivered to `worker` with an OIDC token
as invoker-sa. The task name is the dedupe key, as with the local shim: ALREADY_EXISTS means it was enqueued before.
Retries are set per queue by infra/ (docs/architecture.md#queues-timers-and-jobs)."""

import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.gcp.auth import AdcTokens, GcpError, TokenSource
from app.integrations.tasks.base import QUEUE_PATHS
from app.ops import tracing

API = "https://cloudtasks.googleapis.com/v2"


def task_id(name: str) -> str:
    """Cloud Tasks ids allow [A-Za-z0-9_-] only. A hash prefix keeps distinct names distinct and spreads ids, which
    Cloud Tasks prefers over sequential ones; the readable tail helps when looking at a queue."""
    digest = hashlib.sha256(name.encode()).hexdigest()[:16]
    return f"{digest}-{re.sub(r'[^A-Za-z0-9_-]', '-', name)[:100]}"


class CloudTaskQueue:
    def __init__(self, tokens: TokenSource | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        if not (s.gcp_project and s.gcp_invoker_sa):
            raise RuntimeError("TASKS_BACKEND=cloud_tasks needs GCP_PROJECT and GCP_INVOKER_SA")
        self.parent = f"projects/{s.gcp_project}/locations/{s.gcp_tasks_location}/queues"
        self.worker_url = s.worker_url.rstrip("/")
        self.invoker = s.gcp_invoker_sa
        self.tokens = tokens or AdcTokens()
        self.client = httpx.AsyncClient(timeout=10, transport=transport)

    def build(self, queue: str, body: dict[str, Any], delay_s: float, name: str | None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if tp := tracing.traceparent():
            headers["traceparent"] = tp
        task: dict[str, Any] = {
            "httpRequest": {
                "httpMethod": "POST",
                "url": self.worker_url + QUEUE_PATHS[queue],
                "headers": headers,
                "body": base64.b64encode(json.dumps(body, separators=(",", ":")).encode()).decode(),
                "oidcToken": {"serviceAccountEmail": self.invoker, "audience": self.worker_url},
            },
        }
        if delay_s > 0:
            at = datetime.now(UTC) + timedelta(seconds=delay_s)
            task["scheduleTime"] = at.isoformat().replace("+00:00", "Z")
        if name:
            task["name"] = f"{self.parent}/{queue}/tasks/{task_id(name)}"
        return {"task": task}

    async def enqueue(self, queue: str, body: dict[str, Any], *, delay_s: float = 0, name: str | None = None) -> None:
        with tracing.span("queue.enqueue", target=queue, delay_s=delay_s):
            r = await self.client.post(f"{API}/{self.parent}/{queue}/tasks", json=self.build(queue, body, delay_s, name),
                                       headers={"Authorization": f"Bearer {await self.tokens()}"})
            if r.status_code == 409:  # ALREADY_EXISTS: the same name was enqueued before, which is the point
                tracing.annotate(deduped=True)
                return
            if r.status_code >= 300:
                raise GcpError(f"cloud_tasks {r.status_code} {_status(r)}")


def _status(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", {}).get("status", ""))
    except ValueError:
        return ""
