"""Campaigns (docs/backend.md#campaigns): draft → preview → schedule → start → send within the daily limit →
deferred → resume → completed; consent re-checked at each send and by the sender; cancel; status webhooks."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.domain import campaigns
from app.domain.ai_agent import prompt
from app.domain.conversation import ingress
from app.integrations.whatsapp.parser import StatusEvent
from tests.integration.conftest import CafeFixture, drop_test_cafe
from tests.integration.conversation_kit import (
    Kitchen,
    RecordingTransport,
    TaskRecorder,
    build_kitchen,
    create_staff,
    drop_staff,
    install,
    uninstall,
)
from tests.integration.test_staff_api import client_as

pytestmark = pytest.mark.integration


@pytest.fixture
async def k(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Kitchen]:
    async with db.acquire() as c:
        items, tables = await build_kitchen(c, cafe.cafe_id)
        staff = await create_staff(c, cafe.cafe_id)
        pnid = await c.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id)
        await c.execute("insert into templates (cafe_id, name, category, status) values ($1, 'weekly_offer', 'marketing', 'approved'), "
                        "($1, 'winback', 'marketing', 'pending')", cafe.cafe_id)
        # Four past customers with an order each; three opted in, one opted out.
        for i, status in enumerate(("opted_in", "opted_in", "opted_in", "opted_out")):
            cid = await c.fetchval("insert into customers (cafe_id, wa_id, name, last_inbound_at, last_visit_at) "
                                   "values ($1, $2, $3, now() - interval '3 days', now() - interval '3 days') returning id",
                                   cafe.cafe_id, f"91999979000{i}", f"Guest{i} X")
            await c.execute("""insert into orders (cafe_id, customer_id, number, status, expected_minutes, total_paise, source,
                                                   idempotency_key, created_at)
                               values ($1, $2, $3, 'SERVED', 5, 19000, 'qr', $4, now() - interval '3 days')""",
                            cafe.cafe_id, cid, 9000 + i, f"camp-test-{cafe.cafe_id}-{i}")
            await c.execute("insert into consents (cafe_id, customer_id, status, source, wording_version) values ($1, $2, $3, 'feedback_flow', 1)",
                            cafe.cafe_id, cid, status)
    transport, rec = RecordingTransport(), TaskRecorder()
    install(transport, rec)
    prompt._cache.clear()
    try:
        yield Kitchen(db, cafe.cafe_id, pnid, items, tables, transport, rec, staff)
    finally:
        uninstall()
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


DRAFT = {"name": "Back soon", "segment": "all_opted_in", "template_name": "weekly_offer", "offer_text": "20% off your next visit",
         "discount_percent": 20, "code_prefix": "BACK20"}


async def make_due(k: Kitchen, campaign_id: str) -> None:
    await k.db.execute("update campaigns set scheduled_at = now() - interval '1 minute' where id = $1", campaign_id)


async def send_queued(k: Kitchen) -> list[str]:
    out = []
    for _q, body, _ in [t for t in k.rec.tasks if t[0] == "campaign-send"]:
        out.append(await campaigns.send_one(body["cafe_id"], body["recipient_id"]))
    k.rec.tasks = [t for t in k.rec.tasks if t[0] != "campaign-send"]
    return out


async def test_draft_preview_schedule_rules(k: Kitchen) -> None:
    async with client_as(k, "owner") as api:
        r = await api.post("/campaigns", json=DRAFT)
        assert r.status_code == 200 and r.json()["status"] == "draft"
        cid = r.json()["id"]
        prev = (await api.post("/campaigns/audience-preview", json={"segment": "all_opted_in", "template_name": "weekly_offer"})).json()
        assert (prev["in_segment"], prev["excluded"], prev["will_receive"], prev["daily_limit"], prev["days_needed"]) == (4, 1, 3, 250, 1)
        assert prev["template_status"] == "approved" and prev["estimated_cost"] is None
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        assert (await api.post(f"/campaigns/{cid}/schedule", json={"scheduled_at": past})).status_code == 422
        pending = (await api.post("/campaigns", json={**DRAFT, "template_name": "winback", "segment": "lapsed"})).json()["id"]
        soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        r = await api.post(f"/campaigns/{pending}/schedule", json={"scheduled_at": soon})
        assert r.status_code == 422 and "isn't approved" in r.json()["error"]["message"]
        r = await api.post(f"/campaigns/{cid}/schedule", json={"scheduled_at": soon})
        assert r.status_code == 200 and r.json()["status"] == "scheduled"
        assert (await api.put(f"/campaigns/{cid}", json=DRAFT)).status_code == 422  # only drafts are edited
    async with client_as(k, "manager") as api:
        assert (await api.post("/campaigns", json=DRAFT)).status_code in (401, 403)
    assert await k.db.fetchval("select count(*) from audit_log where cafe_id = $1 and action = 'campaign.schedule'", k.cafe_id) == 1


async def test_start_send_limit_resume_complete_and_statuses(k: Kitchen, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(campaigns, "DEFAULT_DAILY_LIMIT", 2)  # a tiny tier: one recipient waits for tomorrow
    async with client_as(k, "owner") as api:
        cid = (await api.post("/campaigns", json=DRAFT)).json()["id"]
        await api.post(f"/campaigns/{cid}/schedule", json={"scheduled_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()})
    await make_due(k, cid)
    assert await campaigns.start_due() == {"started": 1, "queued": 2}
    recipients = await k.db.fetch("select code, status from campaign_recipients where campaign_id = $1", cid)
    assert len(recipients) == 3 and len({r["code"] for r in recipients}) == 3
    assert all(r["code"].startswith("BACK20-") and len(r["code"]) == 10 for r in recipients)
    assert sorted(r["status"] for r in recipients) == ["deferred", "queued", "queued"]
    assert await send_queued(k) == ["sent", "sent"]
    templates = [s.body["template"] for s in k.transport.sent]
    assert [t["name"] for t in templates] == ["weekly_offer", "weekly_offer"]
    params = [p["text"] for p in templates[0]["components"][0]["parameters"]]
    assert params[1] == "20% off your next visit" and params[2].startswith("BACK20-")

    # Another day (quota back): the deferred one goes; the campaign completes.
    await k.db.execute("update wa_messages set created_at = now() - interval '2 days' where cafe_id = $1 and idempotency_key like 'campaign:%'", k.cafe_id)
    assert (await campaigns.resume_deferred())["queued"] == 1
    assert await send_queued(k) == ["sent"]
    assert await k.db.fetchval("select status from campaigns where id = $1", cid) == "completed"

    wamid = await k.db.fetchval("select wamid from campaign_recipients where campaign_id = $1 and wamid is not null limit 1", cid)
    for status in ("delivered", "read", "delivered"):  # forward only: read never goes back to delivered
        await ingress.ingest_status(StatusEvent(phone_number_id=k.phone_number_id, wamid=wamid, status=status, timestamp=datetime.now(UTC),
                                                callback_data=None, error_code=None, pricing_category="marketing", billable=True))
    assert await k.db.fetchval("select status from campaign_recipients where wamid = $1", wamid) == "read"


async def test_opt_out_between_snapshot_and_send_and_cancel(k: Kitchen) -> None:
    async with client_as(k, "owner") as api:
        cid = (await api.post("/campaigns", json=DRAFT)).json()["id"]
        await api.post(f"/campaigns/{cid}/schedule", json={"scheduled_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()})
        await make_due(k, cid)
        await campaigns.start_due()
        first = await k.db.fetchval("select customer_id from campaign_recipients where campaign_id = $1 order by created_at limit 1", cid)
        await k.db.execute("insert into consents (cafe_id, customer_id, status, source) values ($1, $2, 'opted_out', 'keyword')", k.cafe_id, first)
        tasks_before = [b for q, b, _ in k.rec.tasks if q == "campaign-send"]
        r = await api.post(f"/campaigns/{cid}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
    results = [await campaigns.send_one(b["cafe_id"], b["recipient_id"]) for b in tasks_before]
    assert results.count("skipped") == len(results)  # cancelled: queued recipients are not sent
    assert k.transport.sent == []
    # And the sender itself refuses marketing without consent, whatever the caller did:
    from app.domain import outbound
    from app.integrations.whatsapp import messages as m
    assert await outbound.send(k.cafe_id, outbound.Recipient(customer_id=str(first)), m.Template("weekly_offer", ["a", "b", "c", "d"]),
                               "campaign:direct-test", outbound.Purpose.MARKETING) is None
