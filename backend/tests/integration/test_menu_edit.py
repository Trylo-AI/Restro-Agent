"""Menu edit, new item and CSV import through the API (docs/backend.md#menu, console.md#menu "Done when")."""

from collections.abc import AsyncIterator

import asyncpg
import pytest

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
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


ITEM = {"name": "Filter Coffee", "category": "Hot coffee", "price_paise": 9000, "prep_minutes": 4, "veg": True,
        "spice": 0, "description": "South Indian, strong", "allergens": [{"allergen": "milk", "state": "contains"},
                                                                         {"allergen": "gluten", "state": "free_from"}]}


async def test_new_and_edit_with_allergens_version_and_roles(k: Kitchen) -> None:
    async with client_as(k, "shift_manager") as api:
        assert (await api.post("/menu", json=ITEM)).status_code in (401, 403)  # not allowed (test client has no JWT)
    async with client_as(k, "manager") as api:
        r = await api.post("/menu", json=ITEM)
        assert r.status_code == 200, r.text
        item = r.json()
        states = {a["allergen"]: a["state"] for a in item["allergens"]}
        assert states["milk"] == "contains" and states["gluten"] == "free_from" and states["peanuts"] == "unknown"
        assert (await api.post("/menu", json={**ITEM, "name": "filter coffee"})).status_code == 400  # name clash

        edit = {**ITEM, "price_paise": 9500, "spice": 1, "allergens": [{"allergen": "milk", "state": "free_from"}],
                "version": item["version"]}
        r = await api.put(f"/menu/{item['id']}", json=edit)
        assert r.status_code == 200 and r.json()["price_paise"] == 9500 and r.json()["version"] > item["version"]
        stale = await api.put(f"/menu/{item['id']}", json={**edit, "price_paise": 9900})
        assert stale.status_code == 409 and "Someone else changed this item" in stale.json()["error"]["message"]
        bad = await api.put(f"/menu/{item['id']}", json={**edit, "prep_minutes": 121, "version": r.json()["version"]})
        assert bad.status_code == 400
    rows = await k.db.fetch("select allergen, state from menu_item_allergens where item_id = $1", item["id"])
    assert {(x["allergen"], x["state"]) for x in rows} == {("milk", "free_from")}  # gluten back to unknown (no row)
    audits = await k.db.fetch("select change from audit_log where cafe_id = $1 and action = 'menu.update' order by at", k.cafe_id)
    assert [a["change"]["new"] for a in audits] == [True, False]
    assert "price_paise" in audits[1]["change"]["fields"]


async def test_edit_to_unavailable_flags_open_orders_and_reaches_the_prompt(k: Kitchen) -> None:
    await k.say("919999740001", "Hi, I'm at Table 5")
    await k.say("919999740001", "Asha")
    await k.say("919999740001", tap=f"item:{k.items['Cold Coffee']}")
    v = (await k.state("919999740001"))["cart"]["version"]
    await k.say("919999740001", tap=f"confirm:{v}")
    row = await k.db.fetchrow("select * from menu_items where id = $1", k.items["Cold Coffee"])
    body = {"name": "Cold Coffee", "category": row["category"], "price_paise": row["price_paise"],
            "prep_minutes": row["prep_minutes"], "veg": row["veg"], "available": False, "version": row["version"]}
    async with client_as(k, "manager") as api:
        assert (await api.put(f"/menu/{k.items['Cold Coffee']}", json=body)).status_code == 200
    assert "item_sold_out" in (await k.orders())[0]["flags"]


CSV = ("name,category,price,prep_minutes,veg,description,spice,contains,free_from,available\n"
       "Cold Coffee,Cold coffee,200,5,yes,Now with jaggery,0,milk,gluten,yes\n"
       "Sabudana Vada,Snacks,140,10,yes,,1,peanuts,,yes\n")


async def test_csv_import_updates_inserts_and_is_all_or_nothing(k: Kitchen) -> None:
    async with client_as(k, "manager") as api:
        assert (await api.post("/menu/import", content=CSV, headers={"content-type": "text/csv"})).status_code in (401, 403)
    async with client_as(k, "owner") as api:
        r = await api.post("/menu/import", content=CSV, headers={"content-type": "text/csv"})
        assert r.status_code == 200 and r.json() == {"created": 1, "updated": 1}
        before = await k.db.fetchval("select count(*) from menu_items where cafe_id = $1", k.cafe_id)
        bad = CSV + "Broken,Snacks,abc,5,yes,,,,,\n"
        r = await api.post("/menu/import", content=bad, headers={"content-type": "text/csv"})
        assert r.status_code == 400
        assert r.json()["error"]["details"]["rows"] == [
            {"row": 4, "column": "price", "message": "Price must be in rupees, more than 0, at most 2 decimals."}]
    assert await k.db.fetchval("select count(*) from menu_items where cafe_id = $1", k.cafe_id) == before
    coffee = await k.db.fetchrow("select price_paise, description from menu_items where id = $1", k.items["Cold Coffee"])
    assert dict(coffee) == {"price_paise": 20000, "description": "Now with jaggery"}
    vada = await k.db.fetchval("select id from menu_items where cafe_id = $1 and name = 'Sabudana Vada'", k.cafe_id)
    assert await k.db.fetchval("select state from menu_item_allergens where item_id = $1 and allergen = 'peanuts'", vada) == "contains"
