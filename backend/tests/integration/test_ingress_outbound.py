"""Webhook ingest and the send layer against a real Postgres (docs/rules.md: Webhook and Send layer rows)."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import httpx
import pytest

from app.core.errors import WindowClosed
from app.core.signature import sign
from app.db.repositories.cafes import Cafe
from app.domain import outbound
from app.domain.outbound import Purpose, Recipient
from app.integrations import tasks
from app.integrations.whatsapp import client as wa_client
from app.integrations.whatsapp import messages as m
from app.integrations.whatsapp import simulated
from app.integrations.whatsapp.errors import SendClass, SendError
from app.main_api import create_app
from tests.integration.conftest import CafeFixture

pytestmark = pytest.mark.integration


class Recorder:
    def __init__(self) -> None:
        self.signals: list[tuple[str, str]] = []
        self.tasks: list[tuple[str, dict[str, Any], float]] = []

    async def publish(self, cafe_id: str, conversation_id: str) -> None:
        self.signals.append((cafe_id, conversation_id))

    async def enqueue(self, queue: str, body: dict[str, Any], *, delay_s: float = 0, name: str | None = None) -> None:
        self.tasks.append((queue, body, delay_s))


class FakeTransport:
    def __init__(self, script: list[str] | None = None) -> None:
        self.script = list(script or [])
        self.sent: list[dict[str, Any]] = []

    async def send(self, cafe: Cafe, to: str, body: dict[str, Any], callback_data: str) -> str:
        await asyncio.sleep(0.05)
        outcome = self.script.pop(0) if self.script else "ok"
        if outcome == "429":
            raise SendError(SendClass.NOT_SENT_RETRY, "130429")
        if outcome == "timeout":
            raise SendError(SendClass.AMBIGUOUS, "timeout")
        if outcome == "400":
            raise SendError(SendClass.FAILED, "131026")
        self.sent.append({"to": to, **body})
        return f"wamid.FAKE.{len(self.sent)}.{callback_data[:8]}"

    async def mark_read(self, cafe: Cafe, wamid: str, wa_id: str) -> None:
        return None


@pytest.fixture
async def rec() -> AsyncIterator[Recorder]:
    r = Recorder()
    tasks.set_backends(signal_impl=r, tasks_impl=r)
    yield r
    tasks.set_backends()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbound, "RETRY_DELAYS", (0.01, 0.01, 0.01))


async def pnid(db: asyncpg.Pool, cafe: CafeFixture) -> str:
    return str(await db.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id))


async def post(body: dict[str, Any], secret: str = "test-app-secret") -> httpx.Response:
    raw = json.dumps(body).encode()
    app = create_app()  # lifespan not run: the test pool is already open
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": sign(raw, secret)})


async def test_webhook_stores_queues_and_dedupes(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    phone = await pnid(db, cafe)
    body, wamid = simulated.message_payload(phone, "919999555001", "Test", "text", text="2 cold coffee")
    assert (await post(body)).status_code == 200
    assert (await post(body)).status_code == 200  # Meta retry: same wamid

    rows = await db.fetch("select i.wamid, i.type from inbound_messages i where i.cafe_id = $1", cafe.cafe_id)
    assert [(r["wamid"], r["type"]) for r in rows] == [(wamid, "text")]
    work = await db.fetch("select status, next_attempt_at > now() as debounced from pending_work where cafe_id = $1",
                          cafe.cafe_id)
    assert len(work) == 1 and work[0]["status"] == "pending"
    assert len(rec.signals) == 1 and [t[0] for t in rec.tasks] == ["wa-ack"]
    customer = await db.fetchrow("select last_inbound_at from customers where cafe_id = $1 and wa_id = '919999555001'",
                                 cafe.cafe_id)
    assert customer["last_inbound_at"] is not None
    assert await db.fetchval("select count(*) from webhook_events where payload::text like $1", f"%{wamid}%") == 2


async def test_bad_signature_stores_nothing(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    phone = await pnid(db, cafe)
    body, wamid = simulated.message_payload(phone, "919999555002", None, "text", text="hi")
    assert (await post(body, secret="wrong")).status_code == 401
    assert await db.fetchval("select count(*) from webhook_events where payload::text like $1", f"%{wamid}%") == 0
    assert await db.fetchval("select count(*) from inbound_messages where wamid = $1", wamid) == 0
    assert rec.signals == []


async def test_unknown_phone_number_id_is_stored_but_not_processed(db: asyncpg.Pool, rec: Recorder) -> None:
    body, wamid = simulated.message_payload("no-such-number", "919999555003", None, "text", text="hi")
    assert (await post(body)).status_code == 200
    assert await db.fetchval("select count(*) from inbound_messages where wamid = $1", wamid) == 0
    assert rec.signals == []
    await db.execute("delete from webhook_events where payload::text like $1", f"%{wamid}%")


async def test_window_rules(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    async with db.acquire() as c, c.transaction():
        step = await outbound.prepare(c, cafe.cafe_id, Recipient(cafe.customer_id), m.Text("hi"), "t:1", Purpose.REPLY)
        assert step is None  # the fixture customer never wrote: window closed, reply dropped
        step = await outbound.prepare(c, cafe.cafe_id, Recipient(cafe.customer_id), m.Text("Your order is being made"),
                                      "t:2", Purpose.ORDER_STATUS,
                                      fallback_template=m.Template("order_update", ["1001", "being made"]))
        assert step is not None and step.type == "template" and step.template_name == "order_update"
        with pytest.raises(WindowClosed):
            await outbound.prepare(c, cafe.cafe_id, Recipient(cafe.customer_id), m.Text("On its way"), "t:3",
                                   Purpose.STAFF_REPLY)
        await c.execute("update customers set last_inbound_at = now() where id = $1", cafe.customer_id)
        step = await outbound.prepare(c, cafe.cafe_id, Recipient(cafe.customer_id), m.Text("hi"), "t:4", Purpose.REPLY)
        assert step is not None and step.type == "text" and step.status == "pending"
        again = await outbound.prepare(c, cafe.cafe_id, Recipient(cafe.customer_id), m.Text("hi"), "t:4", Purpose.REPLY)
        assert again is not None and again.id == step.id  # same key, same row


async def _open_window(db: asyncpg.Pool, cafe: CafeFixture) -> None:
    await db.execute("update customers set last_inbound_at = now() where id = $1", cafe.customer_id)


async def test_send_once_then_statuses_only_move_forward(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    await _open_window(db, cafe)
    fake = FakeTransport()
    wa_client.set_transport(fake)
    try:
        assert await outbound.send(cafe.cafe_id, Recipient(cafe.customer_id), m.Text("hello"), "k:1", Purpose.REPLY) == "sent"
        assert await outbound.send(cafe.cafe_id, Recipient(cafe.customer_id), m.Text("hello"), "k:1", Purpose.REPLY) == "sent"
        assert len(fake.sent) == 1
        row = await db.fetchrow("select id::text, wamid, status from wa_messages where idempotency_key = 'k:1'")
        phone = await pnid(db, cafe)
        for status in ("read", "delivered", "sent"):  # out of order
            await post(simulated.status_payload(phone, row["wamid"], status, "x"))
        assert await db.fetchval("select status from wa_messages where id = $1", row["id"]) == "read"
    finally:
        wa_client.set_transport(None)


async def test_two_concurrent_deliveries_send_once(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    await _open_window(db, cafe)
    fake = FakeTransport()
    wa_client.set_transport(fake)
    try:
        async with db.acquire() as c, c.transaction():
            step = await outbound.prepare(c, cafe.cafe_id, Recipient(cafe.customer_id), m.Text("x"), "k:2", Purpose.REPLY)
        assert step is not None
        results = await asyncio.gather(*[outbound.deliver(cafe.cafe_id, step.id) for _ in range(4)])
        assert len(fake.sent) == 1 and results.count("sent") >= 1
    finally:
        wa_client.set_transport(None)


async def test_429_is_retried_and_sent_once(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    await _open_window(db, cafe)
    fake = FakeTransport(["429", "429", "ok"])
    wa_client.set_transport(fake)
    try:
        assert await outbound.send(cafe.cafe_id, Recipient(cafe.customer_id), m.Text("x"), "k:3", Purpose.REPLY) == "sent"
        assert len(fake.sent) == 1
    finally:
        wa_client.set_transport(None)


async def test_timeout_is_ambiguous_and_never_resent(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    await _open_window(db, cafe)
    fake = FakeTransport(["timeout"])
    wa_client.set_transport(fake)
    try:
        assert await outbound.send(cafe.cafe_id, Recipient(cafe.customer_id), m.Text("x"), "k:4", Purpose.REPLY) == "ambiguous"
        assert await outbound.send(cafe.cafe_id, Recipient(cafe.customer_id), m.Text("x"), "k:4", Purpose.REPLY) == "ambiguous"
        assert fake.sent == []
        assert [(q, b["kind"]) for q, b, _ in rec.tasks] == [("timers", "send_check")]
        row = await db.fetchrow("select id::text, status from wa_messages where idempotency_key = 'k:4'")
        # The status webhook names our row (biz_opaque_callback_data): ambiguous → sent, no resend.
        phone = await pnid(db, cafe)
        await post(simulated.status_payload(phone, "wamid.LATE", "sent", "x", callback_data=row["id"]))
        assert await db.fetchval("select status from wa_messages where id = $1", row["id"]) == "sent"
    finally:
        wa_client.set_transport(None)


async def test_send_check_fails_an_unconfirmed_send(db: asyncpg.Pool, cafe: CafeFixture, rec: Recorder) -> None:
    await _open_window(db, cafe)
    wa_client.set_transport(FakeTransport(["timeout"]))
    try:
        await outbound.send(cafe.cafe_id, Recipient(cafe.customer_id), m.Text("x"), "k:5", Purpose.REPLY)
        step_id = await db.fetchval("select id::text from wa_messages where idempotency_key = 'k:5'")
        await outbound.send_check(cafe.cafe_id, step_id, {})
        assert await db.fetchval("select status from wa_messages where id = $1", step_id) == "failed"
    finally:
        wa_client.set_transport(None)
