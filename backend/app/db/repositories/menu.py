from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class MenuItem:
    id: str
    name: str
    description: str | None
    category: str
    price_paise: int
    prep_minutes: int
    veg: bool
    spice: int
    available: bool
    version: int


_COLS = "id::text, name, description, category, price_paise, prep_minutes, veg, spice, available, version"


async def available_items(conn: asyncpg.Connection, cafe_id: str) -> list[MenuItem]:
    rows = await conn.fetch("select " + _COLS + " from menu_items where cafe_id = $1 and available order by category, name",
                            cafe_id)
    return [MenuItem(**dict(r)) for r in rows]


async def items_by_ids(conn: asyncpg.Connection, cafe_id: str, ids: list[str]) -> dict[str, MenuItem]:
    """Only this café's items: an id from another café simply isn't found."""
    if not ids:
        return {}
    rows = await conn.fetch("select " + _COLS + " from menu_items where cafe_id = $1 and id = any($2::uuid[])",
                            cafe_id, ids)
    return {r["id"]: MenuItem(**dict(r)) for r in rows}


async def get(conn: asyncpg.Connection, cafe_id: str, item_id: str) -> MenuItem | None:
    return (await items_by_ids(conn, cafe_id, [item_id])).get(item_id)


async def menu_version(conn: asyncpg.Connection, cafe_id: str) -> int:
    return int(await conn.fetchval("select menu_version from cafe_counters where cafe_id = $1", cafe_id) or 0)


async def allergens(conn: asyncpg.Connection, cafe_id: str, item_ids: list[str]) -> dict[str, dict[str, str]]:
    rows = await conn.fetch(
        "select item_id::text, allergen, state from menu_item_allergens where cafe_id = $1 and item_id = any($2::uuid[])",
        cafe_id, item_ids,
    )
    out: dict[str, dict[str, str]] = {i: {} for i in item_ids}
    for r in rows:
        out[r["item_id"]][r["allergen"]] = r["state"]
    return out
