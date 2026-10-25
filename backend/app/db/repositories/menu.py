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


async def all_items(conn: asyncpg.Connection, cafe_id: str) -> list[MenuItem]:
    """Every dish, sold out included (to tell "sold out" from "not on the menu")."""
    rows = await conn.fetch("select " + _COLS + " from menu_items where cafe_id = $1 order by category, name", cafe_id)
    return [MenuItem(**dict(r)) for r in rows]


async def set_available(conn: asyncpg.Connection, cafe_id: str, item_id: str, available: bool, version: int) -> MenuItem | None:
    """Returns the item when the value changed; None when it was already set."""
    row = await conn.fetchrow(
        "update menu_items set available = $3, version = $4 where cafe_id = $1 and id = $2 and available <> $3 returning " + _COLS,
        cafe_id, item_id, available, version,
    )
    return MenuItem(**dict(row)) if row else None


async def next_version(conn: asyncpg.Connection, cafe_id: str) -> int:
    return int(await conn.fetchval("select next_menu_version($1)", cafe_id))


_ITEM_FIELDS = ("name", "description", "category", "price_paise", "prep_minutes", "veg", "spice", "available")


async def by_name(conn: asyncpg.Connection, cafe_id: str, name: str) -> MenuItem | None:
    row = await conn.fetchrow("select " + _COLS + " from menu_items where cafe_id = $1 and lower(name) = lower($2)",
                              cafe_id, name)
    return MenuItem(**dict(row)) if row else None


async def insert(conn: asyncpg.Connection, cafe_id: str, fields: dict[str, object], version: int) -> MenuItem:
    row = await conn.fetchrow(
        """insert into menu_items (cafe_id, name, description, category, price_paise, prep_minutes, veg, spice,
                                   available, version)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) returning """ + _COLS,
        cafe_id, *(fields[f] for f in _ITEM_FIELDS), version,
    )
    return MenuItem(**dict(row))


async def update(conn: asyncpg.Connection, cafe_id: str, item_id: str, fields: dict[str, object], expected_version: int | None,
                 version: int) -> MenuItem | None:
    """None when the item changed since `expected_version` (optimistic lock); no version check when None."""
    row = await conn.fetchrow(
        """update menu_items set name = $3, description = $4, category = $5, price_paise = $6, prep_minutes = $7,
                  veg = $8, spice = $9, available = $10, version = $11
           where cafe_id = $1 and id = $2 and ($12::bigint is null or version = $12) returning """ + _COLS,
        cafe_id, item_id, *(fields[f] for f in _ITEM_FIELDS), version, expected_version,
    )
    return MenuItem(**dict(row)) if row else None


async def replace_allergens(conn: asyncpg.Connection, cafe_id: str, item_id: str, states: dict[str, str]) -> None:
    """Exactly these states; any allergen not given is `unknown` (no row)."""
    await conn.execute("delete from menu_item_allergens where cafe_id = $1 and item_id = $2", cafe_id, item_id)
    known = [(a, s) for a, s in states.items() if s != "unknown"]
    if known:
        await conn.executemany(
            "insert into menu_item_allergens (cafe_id, item_id, allergen, state) values ($1, $2, $3, $4)",
            [(cafe_id, item_id, a, s) for a, s in known],
        )
