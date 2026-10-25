"""Pub/Sub signal (SIGNAL_BACKEND=pubsub): publishes {cafe_id, conversation_id} to topic wa-inbound, pushed to
`worker` /internal/pubsub/inbound as invoker-sa. Ids only, never message text: the work itself is in pending_work,
so a lost or duplicate signal changes nothing (the sweep re-signals)."""

import base64
import json

import httpx

from app.core.config import get_settings
from app.integrations.gcp.auth import AdcTokens, GcpError, TokenSource
from app.integrations.gcp.cloud_tasks import _status
from app.ops import tracing

API = "https://pubsub.googleapis.com/v1"


class PubSubSignal:
    def __init__(self, tokens: TokenSource | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        if not s.gcp_project:
            raise RuntimeError("SIGNAL_BACKEND=pubsub needs GCP_PROJECT")
        self.topic = f"projects/{s.gcp_project}/topics/{s.pubsub_topic}"
        self.tokens = tokens or AdcTokens()
        self.client = httpx.AsyncClient(timeout=10, transport=transport)

    def build(self, cafe_id: str, conversation_id: str) -> dict[str, object]:
        data = base64.b64encode(json.dumps({"cafe_id": cafe_id, "conversation_id": conversation_id}).encode()).decode()
        tp = tracing.traceparent()
        return {"messages": [{"data": data, "attributes": {"traceparent": tp} if tp else {}}]}

    async def publish(self, cafe_id: str, conversation_id: str) -> None:
        with tracing.span("queue.publish", target="wa-inbound"):
            r = await self.client.post(f"{API}/{self.topic}:publish", json=self.build(cafe_id, conversation_id),
                                       headers={"Authorization": f"Bearer {await self.tokens()}"})
            if r.status_code >= 300:
                raise GcpError(f"pubsub {r.status_code} {_status(r)}")
