"""Customer data deletion and the consent export (docs/architecture.md#data-protection)."""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.domain import recommend
from app.domain.ai_agent import prompt
from app.integrations import llm
from app.integrations.llm import StubLlm
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
WA = "919999780001"


@pytest.fixture
async def k(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Kitchen]:
    async with db.acquire() as c:
        items, tables = await build_kitchen(c, cafe.cafe_id)
        staff = await create_staff(c, cafe.cafe_id)
        pnid = await c.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id)
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


async def a_customer_with_history(k: Kitchen) -> str:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, "Priya Sharma")
    await k.say(WA, tap=f"item:{k.items['Cold Coffee']}")
    v = (await k.state(WA))["cart"]["version"]
    await k.say(WA, tap=f"confirm:{v}")
    async with k.db.acquire() as c:
        cid = str(await c.fetchval("select id from customers where cafe_id = $1 and wa_id = $2", k.cafe_id, WA))
        order = await c.fetchval("select id from orders where customer_id = $1", cid)
        note = await c.fetchval(
            """insert into customer_notes (cafe_id, customer_id, author_id, text, ai_status, ai_ops)
               values ($1, $2, $3, 'Allergic to peanuts', 'applied', '[{"op":"add","kind":"allergy","value":"peanuts"}]')
               returning id""", k.cafe_id, cid, k.staff["owner"])
        await c.execute("insert into customer_preferences (cafe_id, customer_id, kind, value, source, note_id) "
                        "values ($1, $2, 'allergy', 'peanuts', 'staff_note', $3)", k.cafe_id, cid, note)
        await c.execute("""insert into llm_calls (cafe_id, provider, model, purpose, note_id, pricing_version, latency_ms, outcome)
                           values ($1, 'stub', 'm', 'profile_note', $2, 'v', 1, 'ok')""", k.cafe_id, note)
        await c.execute("insert into feedback (cafe_id, order_id, rating, text) values ($1, $2, 2, 'Too sweet')", k.cafe_id, order)
        await c.execute("insert into consents (cafe_id, customer_id, status, source, wording_version) values ($1, $2, 'opted_in', 'feedback_flow', 1)",
                        k.cafe_id, cid)
    return cid


async def test_delete_removes_personal_data_and_keeps_anonymous_records(k: Kitchen) -> None:
    cid = await a_customer_with_history(k)
    async with client_as(k, "manager") as api:
        assert (await api.post(f"/customers/{cid}/delete")).status_code in (401, 403)
    async with client_as(k, "owner") as api:
        r = await api.post(f"/customers/{cid}/delete")
        assert r.status_code == 200 and r.json()["status"] == "completed"
        assert (await api.post(f"/customers/{cid}/delete")).json()["id"] == r.json()["id"]  # already done
    c = await k.db.fetchrow("select name, wa_id from customers where id = $1", cid)
    assert c["name"] is None and c["wa_id"].startswith("000") and c["wa_id"] != WA
    assert await k.db.fetchval("select count(*) from customer_preferences where customer_id = $1", cid) == 0
    assert await k.db.fetchval("select text from customer_notes where customer_id = $1", cid) == "(deleted)"
    assert await k.db.fetchval("""select count(*) from inbound_messages i join conversations v on v.id = i.conversation_id
                                  where v.customer_id = $1 and i.payload <> '{}'::jsonb""", cid) == 0
    assert await k.db.fetchval("select count(*) from wa_messages where customer_id = $1 and payload <> '{}'::jsonb", cid) == 0
    assert await k.db.fetchval("select count(*) from webhook_events where payload::text like '%' || $1 || '%'", WA) == 0
    fb = await k.db.fetchrow("select rating, text from feedback f join orders o on o.id = f.order_id where o.customer_id = $1", cid)
    assert dict(fb) == {"rating": 2, "text": None}  # the rating stays, the words go
    assert await k.db.fetchval("select count(*) from orders where customer_id = $1", cid) == 1  # orders stay, anonymous
    assert await k.db.fetchval("select count(*) from consents where customer_id = $1", cid) == 1
    assert await k.db.fetchval("select status from data_deletion_requests where customer_id = $1", cid) == "completed"
    assert await k.db.fetchval("select count(*) from audit_log where cafe_id = $1 and action = 'customer.delete'", k.cafe_id) == 1

    # The same number later is a new customer with no consent.
    await k.say(WA, "Hi, I'm at Table 2")
    new = await k.db.fetchval("select id::text from customers where cafe_id = $1 and wa_id = $2", k.cafe_id, WA)
    assert new and new != cid
    assert await k.db.fetchval("select count(*) from consents where customer_id = $1", new) == 0


async def test_consent_export_is_masked_and_audited(k: Kitchen) -> None:
    await a_customer_with_history(k)
    async with client_as(k, "owner") as api:
        r = await api.get("/consents/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "customer,first_name,type,status,wording_version,source,at"
    assert "+91 99••• ••001" in lines[1] and "Priya" in lines[1] and "opted_in,1,feedback_flow" in lines[1]
    assert WA not in r.text
    assert await k.db.fetchval("select count(*) from audit_log where cafe_id = $1 and action = 'consent.export'", k.cafe_id) == 1


async def test_recommend_prompt_has_no_phone_wa_id_or_allergy(k: Kitchen) -> None:
    await a_customer_with_history(k)  # a peanut allergy on the profile, one past order
    for item_id in k.items.values():  # every dish free from peanuts, so the model gets candidates
        await k.db.execute("insert into menu_item_allergens (cafe_id, item_id, allergen, state) "
                           "values ($1, $2, 'peanuts', 'free_from')", k.cafe_id, item_id)
    recommend._allergen_cache.clear()
    stub = StubLlm(lambda s, u: {"intro": "A few ideas", "picks": []})
    llm.set_client(stub)
    sent = await k.say(WA, tap="recommend")
    assert len(stub.calls) == 1 and "Picks for you" in str(sent[0].body)
    seen = stub.calls[0]["system"] + stub.calls[0]["prompt"]
    for secret in (WA, WA[-10:], "peanut", "Sharma", "₹"):
        assert secret not in seen
    assert "allerg" not in stub.calls[0]["prompt"].lower()  # the system rules name allergens only to forbid claims
    assert "CUSTOMER: Priya;" in stub.calls[0]["prompt"]  # first name only
