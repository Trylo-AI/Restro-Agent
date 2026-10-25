"""Owner setup (docs/console.md#setup "Done when"): settings in one save, tables and QR text, team rules."""

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.domain import setup
from app.domain.ai_agent import prompt
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
    transport, rec = RecordingTransport(), TaskRecorder()
    install(transport, rec)
    prompt._cache.clear()
    try:
        yield Kitchen(db, cafe.cafe_id, pnid, items, tables, transport, rec, staff)
    finally:
        uninstall()
        setup.set_inviter(None)
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


async def test_settings_save_in_one_call_and_validate(k: Kitchen) -> None:
    body = {"opening_hours": {"mon": [["08:00", "23:00"]], "sat": [["18:00", "02:00"]]}, "cutoff_minutes": 20,
            "tone": "playful", "feedback_delay_minutes": 60, "alert_phone": "+919999000901"}
    async with client_as(k, "owner") as api:
        r = await api.put(f"/cafes/{k.cafe_id}/settings", json=body)
        assert r.status_code == 200, r.text
        assert r.json() == {**body, "alert_phone": "919999000901"}
        assert (await api.put(f"/cafes/{k.cafe_id}/settings", json=body)).status_code == 200  # no change: no audit
        for bad in ({"opening_hours": {"funday": []}}, {"opening_hours": {"mon": [["8am", "11pm"]]}},
                    {"feedback_delay_minutes": 45}, {"alert_phone": "12345"}, {"tone": "rude"}):
            assert (await api.put(f"/cafes/{k.cafe_id}/settings", json=bad)).status_code == 400, bad
        assert (await api.put(f"/cafes/{uuid.uuid4()}/settings", json=body)).status_code == 403
    async with client_as(k, "manager") as api:
        assert (await api.put(f"/cafes/{k.cafe_id}/settings", json=body)).status_code in (401, 403)
    row = await k.db.fetchrow("select opening_hours, cutoff_minutes, settings from cafes where id = $1", k.cafe_id)
    assert row["cutoff_minutes"] == 20 and row["settings"]["tone"] == "playful" and row["settings"]["paused"] is False
    audits = await k.db.fetch("select change from audit_log where cafe_id = $1 and action = 'settings.update'", k.cafe_id)
    assert len(audits) == 1 and "alert_phone" in audits[0]["change"]["fields"]
    assert "919999000901" not in str(audits[0]["change"])  # never a full number in the audit log


async def test_tables_get_qr_text_and_unique_labels(k: Kitchen) -> None:
    async with client_as(k, "owner") as api:
        r = await api.post("/tables", json={"label": "T12", "area": "Patio", "seats": 4})
        assert r.status_code == 200 and r.json()["qr_text"] == "Hi, I'm at Table 12"
        assert (await api.post("/tables", json={"label": "T12"})).status_code == 409
        edited = await api.put(f"/tables/{r.json()['id']}", json={"label": "Window 1", "seats": 2})
        assert edited.json()["qr_text"] == "Hi, I'm at Table Window 1" and edited.json()["area"] is None
    await k.say("919999770001", "Hi, I'm at Table Window 1")  # the QR text joins that table
    assert await k.db.fetchval("""select t.label from table_sessions s join tables t on t.id = s.table_id
                                  where s.cafe_id = $1 and s.closed_at is null""", k.cafe_id) == "Window 1"


async def test_team_invite_role_change_and_owner_rules(k: Kitchen, db: asyncpg.Pool) -> None:
    new_user = str(uuid.uuid4())
    async with db.acquire() as c:
        await c.execute("insert into auth.users (id, email, aud, role) values ($1, $2, 'authenticated', 'authenticated')",
                        new_user, f"test-{new_user[:12]}@restro.test")
    k.staff["invited"] = new_user  # dropped with the others

    async def fake_invite(email: str, name: str | None) -> str:
        return new_user
    setup.set_inviter(fake_invite)
    async with client_as(k, "owner") as api:
        r = await api.post("/team/invite", json={"email": "Sam@Example.com", "role": "shift_manager", "display_name": "Sam"})
        assert r.status_code == 200 and r.json()["role"] == "shift_manager" and r.json()["active"] is True
        member = r.json()["id"]
        assert (await api.post("/team/invite", json={"email": "sam@example.com", "role": "manager"})).status_code == 409
        assert (await api.post("/team/invite", json={"email": "not-an-email", "role": "manager"})).status_code == 400
        assert (await api.patch(f"/team/{member}", json={"role": "manager"})).json()["role"] == "manager"
        assert (await api.patch(f"/team/{member}", json={"active": False})).json()["active"] is False
        owner_row = await k.db.fetchval("select id::text from memberships where cafe_id = $1 and role = 'owner'", k.cafe_id)
        assert (await api.patch(f"/team/{owner_row}", json={"active": False})).status_code == 400
        last = await api.patch(f"/team/{owner_row}", json={"role": "manager"})
        assert last.status_code == 400 and "at least one active owner" in last.json()["error"]["message"]
    actions = [a["action"] for a in await k.db.fetch("select action from audit_log where cafe_id = $1 order by at", k.cafe_id)]
    assert actions.count("team.invite") == 1 and actions.count("team.update") == 2
    changes = " ".join(str(a["change"]) for a in await k.db.fetch("select change from audit_log where cafe_id = $1", k.cafe_id))
    assert "example.com" not in changes.lower()  # no email in the audit log
