"""Cloud Tasks and Pub/Sub adapters against a fake Google API (httpx.MockTransport): request shape, dedupe, errors."""

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import get_settings
from app.integrations.gcp.auth import GcpError
from app.integrations.gcp.cloud_tasks import CloudTaskQueue, task_id
from app.integrations.gcp.pubsub import PubSubSignal


@pytest.fixture(autouse=True)
def gcp_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gcp_project", "restro-staging")
    monkeypatch.setattr(s, "gcp_invoker_sa", "invoker-sa@restro-staging.iam.gserviceaccount.com")
    monkeypatch.setattr(s, "worker_url", "https://worker-abc.a.run.app")


async def _token() -> str:
    return "fake-access-token"


class FakeGoogle:
    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status, self.body = status, body or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


async def test_cloud_task_shape():
    google = FakeGoogle()
    q = CloudTaskQueue(tokens=_token, transport=httpx.MockTransport(google))
    await q.enqueue("timers", {"kind": "escalate", "cafe_id": "c1"}, delay_s=60, name="esc:order-1:1")

    req = google.requests[0]
    assert str(req.url) == "https://cloudtasks.googleapis.com/v2/projects/restro-staging/locations/asia-south1/queues/timers/tasks"
    assert req.headers["Authorization"] == "Bearer fake-access-token"
    task = json.loads(req.content)["task"]
    http = task["httpRequest"]
    assert http["url"] == "https://worker-abc.a.run.app/internal/tasks/timer"
    assert http["oidcToken"] == {"serviceAccountEmail": "invoker-sa@restro-staging.iam.gserviceaccount.com",
                                 "audience": "https://worker-abc.a.run.app"}
    assert json.loads(base64.b64decode(http["body"])) == {"kind": "escalate", "cafe_id": "c1"}
    assert task["name"].endswith("/queues/timers/tasks/" + task_id("esc:order-1:1"))
    due = datetime.fromisoformat(task["scheduleTime"].replace("Z", "+00:00"))
    assert 50 < (due - datetime.now(UTC)).total_seconds() <= 60


async def test_cloud_task_without_name_or_delay():
    google = FakeGoogle()
    await CloudTaskQueue(tokens=_token, transport=httpx.MockTransport(google)).enqueue("wa-ack", {"wamid": "w1"})
    task = json.loads(google.requests[0].content)["task"]
    assert "name" not in task and "scheduleTime" not in task  # Cloud Tasks names it; due now


async def test_already_exists_is_the_dedupe():
    google = FakeGoogle(409, {"error": {"code": 409, "status": "ALREADY_EXISTS"}})
    await CloudTaskQueue(tokens=_token, transport=httpx.MockTransport(google)).enqueue("wa-ack", {}, name="ack:w1")


async def test_other_errors_raise_without_the_body():
    google = FakeGoogle(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "secret detail"}})
    with pytest.raises(GcpError) as err:
        await CloudTaskQueue(tokens=_token, transport=httpx.MockTransport(google)).enqueue("timers", {})
    assert "PERMISSION_DENIED" in str(err.value) and "secret detail" not in str(err.value)


def test_task_ids_are_valid_and_distinct():
    a, b = task_id("ack:wamid.ABC=="), task_id("ack_wamid.ABC==")
    assert a != b
    for t in (a, b, task_id("x" * 600)):
        assert all(ch.isalnum() or ch in "-_" for ch in t) and len(t) <= 500


async def test_pubsub_publish_shape():
    google = FakeGoogle(200, {"messageIds": ["1"]})
    await PubSubSignal(tokens=_token, transport=httpx.MockTransport(google)).publish("cafe-1", "conv-1")
    req = google.requests[0]
    assert str(req.url) == "https://pubsub.googleapis.com/v1/projects/restro-staging/topics/wa-inbound:publish"
    msg = json.loads(req.content)["messages"][0]
    assert json.loads(base64.b64decode(msg["data"])) == {"cafe_id": "cafe-1", "conversation_id": "conv-1"}


async def test_pubsub_error_raises():
    google = FakeGoogle(503, {"error": {"status": "UNAVAILABLE"}})
    with pytest.raises(GcpError, match="UNAVAILABLE"):
        await PubSubSignal(tokens=_token, transport=httpx.MockTransport(google)).publish("c", "v")


def test_missing_config_refuses(monkeypatch):
    monkeypatch.setattr(get_settings(), "gcp_invoker_sa", None)
    with pytest.raises(RuntimeError, match="GCP_INVOKER_SA"):
        CloudTaskQueue(tokens=_token)
