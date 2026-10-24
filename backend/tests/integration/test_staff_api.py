"""Console REST endpoints over ASGI (auth dependency overridden; JWT checks are unit-tested in test_auth.py)."""

from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest

from app.core import auth
from app.core.auth import Principal
from app.main_api import create_app
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


def client_as(k: Kitchen, role: str, cafe_id: str | None = None) -> httpx.AsyncClient:
    app = create_app()
    p = Principal(user_id=k.staff[role], cafe_id=cafe_id or k.cafe_id, role=role, display_name="Ravi")
    for dep in (auth.shift_manager, auth.manager, auth.owner):
        rank = {"shift_manager": 1, "manager": 2, "owner": 3}
        need = {auth.shift_manager: 1, auth.manager: 2, auth.owner: 3}[dep]
        if rank[role] >= need:
            app.dependency_overrides[dep] = lambda p=p: p
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"X-Cafe-Id": p.cafe_id})


async def a_new_order(k: Kitchen) -> asyncpg.Record:
    async with client_as(k, "shift_manager") as api:
        r = await api.post("/orders/manual", json={"table_id": k.tables["T1"], "items": [{"item_id": k.items["Masala Chai"], "qty": 1}]},
                           headers={"Idempotency-Key": "manual-key-0001"})
        assert r.status_code == 200, r.text
    # A WhatsApp-style NEW order for the transition tests:
    await k.say("919999710001", "Hi, I'm at Table 5")
    await k.say("919999710001", "Sana")
    await k.say("919999710001", tap=f"item:{k.items['Cold Coffee']}")
    v = (await k.state("919999710001"))["cart"]["version"]
    await k.say("919999710001", tap=f"confirm:{v}")
    return next(o for o in await k.orders() if o["status"] == "NEW")


async def test_transition_cancel_and_validation(k: Kitchen) -> None:
    order = await a_new_order(k)
    async with client_as(k, "shift_manager") as api:
        r = await api.post(f"/orders/{order['id']}/transition", json={"to": "ACCEPTED", "expected_version": order["version"]})
        assert r.status_code == 200 and r.json()["status"] == "ACCEPTED" and r.json()["undo_until"]
        assert "idempotency_key" not in r.json()

        stale = await api.post(f"/orders/{order['id']}/transition", json={"to": "READY", "expected_version": order["version"]})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
        assert stale.json()["error"]["details"]["current"]["status"] == "ACCEPTED"

        skip = await api.post(f"/orders/{order['id']}/transition", json={"to": "SERVED", "expected_version": r.json()["version"]})
        assert skip.status_code == 422 and skip.json()["error"]["code"] == "invalid_transition"

        cancel = await api.post(f"/orders/{order['id']}/cancel",
                                json={"reason_code": "kitchen_issue", "expected_version": r.json()["version"]})
        assert cancel.status_code == 403  # shift_manager can't cancel after NEW

        unknown_field = await api.post(f"/orders/{order['id']}/transition",
                                       json={"to": "READY", "expected_version": 2, "price_paise": 1})
        assert unknown_field.status_code == 400 and unknown_field.json()["error"]["code"] == "bad_request"

        no_key = await api.post("/orders/manual", json={"table_id": k.tables["T1"], "items": [{"item_id": k.items["Masala Chai"], "qty": 1}]})
        assert no_key.status_code == 400

        pause = await api.post(f"/cafes/{k.cafe_id}/pause-ordering", json={"paused": True})
        assert pause.status_code in (401, 403)  # manager+ only

    async with client_as(k, "manager") as api:
        cancel = await api.post(f"/orders/{order['id']}/cancel",
                                json={"reason_code": "kitchen_issue", "expected_version": r.json()["version"]})
        assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
        paused = await api.post(f"/cafes/{k.cafe_id}/pause-ordering", json={"paused": True})
        assert paused.status_code == 200 and paused.json()["settings"]["paused"] is True
        again = await api.post(f"/cafes/{k.cafe_id}/pause-ordering", json={"paused": True})  # no-op
        assert again.status_code == 200
    audit = [a["action"] for a in await k.db.fetch("select action from audit_log where cafe_id = $1", k.cafe_id)]
    assert audit.count("ordering.pause") == 1


async def test_another_cafes_order_is_404(k: Kitchen, db: asyncpg.Pool) -> None:
    order = await a_new_order(k)
    async with db.acquire() as c:
        other = await create_test_cafe(c)
    try:
        async with client_as(k, "owner", cafe_id=other.cafe_id) as api:
            r = await api.post(f"/orders/{order['id']}/transition", json={"to": "ACCEPTED", "expected_version": order["version"]})
            assert r.status_code == 404
            r = await api.post(f"/cafes/{k.cafe_id}/pause-ordering", json={"paused": True})
            assert r.status_code == 403
    finally:
        async with db.acquire() as c:
            await drop_test_cafe(c, other.cafe_id)
