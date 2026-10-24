"""Customer profile comments (S12): API → profile-note task → applied ops, undo, chip removal."""

from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from app.domain import customers
from app.integrations import llm
from app.integrations.llm import StubLlm
from tests.integration.conftest import CafeFixture, create_test_cafe, drop_test_cafe
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
    transport, rec = RecordingTransport(), TaskRecorder()
    install(transport, rec)
    try:
        yield Kitchen(db, cafe.cafe_id, pnid, items, tables, transport, rec, staff)
    finally:
        uninstall()
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


def model_says(ops: list[dict[str, Any]] | str, summary: str = "") -> StubLlm:
    stub = StubLlm(lambda s, u: ops if isinstance(ops, str) else {"ops": ops, "summary": summary})
    llm.set_client(stub)
    return stub


async def note(k: Kitchen, cust: str, text: str) -> dict[str, Any]:
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/customers/{cust}/notes", json={"text": text})
        assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_status"] == "pending"
    task = [b for q, b, _ in k.rec.tasks if q == "profile-note" and b["note_id"] == body["id"]]
    assert task, "profile-note task enqueued"
    await customers.process_note(k.cafe_id, body["id"])
    return dict(await k.db.fetchrow("select ai_status, ai_ops, ai_summary from customer_notes where id = $1", body["id"])) | {"id": body["id"]}


async def profile(k: Kitchen, cust: str) -> set[tuple[str, str]]:
    return {(r["kind"], r["value"]) for r in await k.db.fetch("select kind, value from customer_preferences where customer_id = $1", cust)}


async def a_customer(k: Kitchen) -> str:
    return str(await k.db.fetchval("insert into customers (cafe_id, wa_id, name) values ($1, '919999740001', 'Riya') returning id::text", k.cafe_id))


async def test_comment_updates_the_profile_and_undo_reverts(k: Kitchen) -> None:
    cust = await a_customer(k)
    stub = model_says([{"op": "set", "kind": "diet", "value": "vegan"}, {"op": "add", "kind": "dislike", "value": "mushrooms"}],
                      "Diet → vegan · Dislikes + mushrooms")
    n = await note(k, cust, "She's vegan now and hates mushrooms.")
    assert n["ai_status"] == "applied" and n["ai_summary"] == "Diet → vegan · Dislikes + mushrooms"
    assert await profile(k, cust) == {("diet", "vegan"), ("dislike", "mushrooms")}
    assert "919999740001" not in stub.calls[0]["prompt"]  # no number in the prompt
    llm_row = await k.db.fetchrow("select purpose, note_id::text from llm_calls where cafe_id = $1", k.cafe_id)
    assert llm_row["purpose"] == "profile_note" and llm_row["note_id"] == n["id"]

    model_says([{"op": "set", "kind": "diet", "value": "jain"}])
    n2 = await note(k, cust, "Actually jain, not vegan")
    assert await profile(k, cust) == {("diet", "jain"), ("dislike", "mushrooms")}
    assert n2["ai_ops"][0]["previous"] == "vegan"

    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/customer-notes/{n['id']}/undo")  # a later note changed the diet
        assert r.status_code == 409 and "Changed again since" in r.json()["error"]["message"]
        r = await api.post(f"/customer-notes/{n2['id']}/undo")
        assert r.status_code == 200 and r.json()["undone_at"]
        assert (await api.post(f"/customer-notes/{n2['id']}/undo")).status_code == 200  # no-op
    assert await profile(k, cust) == {("diet", "vegan"), ("dislike", "mushrooms")}
    audit = [r["action"] for r in await k.db.fetch("select action from audit_log where cafe_id = $1", k.cafe_id)]
    assert audit.count("customer.note") == 2 and audit.count("customer.profile_update") == 2 and audit.count("customer.profile_undo") == 1
    change = await k.db.fetchval("select change::text from audit_log where cafe_id = $1 and action = 'customer.note' limit 1", k.cafe_id)
    assert "vegan" not in change and "mushrooms" not in change  # comment text never in the audit log


async def test_allergy_changes_apply_automatically(k: Kitchen) -> None:
    cust = await a_customer(k)
    model_says([{"op": "add", "kind": "allergy", "value": "peanuts"}])
    await note(k, cust, "allergic to peanuts")
    assert ("allergy", "peanuts") in await profile(k, cust)
    model_says([{"op": "remove", "kind": "allergy", "value": "peanuts"}])
    n = await note(k, cust, "no more peanut allergy, she says")
    assert n["ai_status"] == "applied" and ("allergy", "peanuts") not in await profile(k, cust)


async def test_consent_and_junk_ops_change_nothing(k: Kitchen) -> None:
    cust = await a_customer(k)
    model_says([{"op": "add", "kind": "other", "value": "doesn't want offers"}, {"op": "add", "kind": "consent", "value": "no"}])
    n = await note(k, cust, "He doesn't want any offers")
    assert n["ai_status"] == "no_changes" and await profile(k, cust) == set()
    assert await k.db.fetchval("select count(*) from consents where customer_id = $1", cust) == 0


async def test_llm_failure_leaves_the_profile_alone(k: Kitchen) -> None:
    cust = await a_customer(k)
    model_says("error")
    n = await note(k, cust, "likes window seats")
    assert n["ai_status"] == "failed" and await profile(k, cust) == set()
    assert await customers.process_note(k.cafe_id, n["id"]) == "skipped"  # only pending notes are processed


async def test_chip_remove_and_cross_cafe(k: Kitchen, db: asyncpg.Pool) -> None:
    cust = await a_customer(k)
    model_says([{"op": "add", "kind": "like", "value": "cold brew"}])
    await note(k, cust, "loves cold brew")
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/customers/{cust}/preferences/remove", json={"kind": "like", "value": "cold brew"})
        assert r.status_code == 200 and r.json() == []
        bad = await api.post(f"/customers/{cust}/preferences/remove", json={"kind": "favourite", "value": "x"})
        assert bad.status_code == 400
    async with db.acquire() as c:
        other = await create_test_cafe(c)
    try:
        async with client_as(k, "owner", cafe_id=other.cafe_id) as api:
            r = await api.post(f"/customers/{cust}/notes", json={"text": "hi"})
            assert r.status_code == 404
    finally:
        async with db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)
