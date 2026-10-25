"""Menu availability (docs/backend.md#menu). Critical core: the agent must never offer a sold-out dish.

The sold-out toggle is one transaction: `available`, a new menu version (the prompt cache key, so the agent stops
offering it on the next message), the audit row, and the `item_sold_out` flag on open orders containing it.
Carts lose the line at their next summary.
"""

import asyncpg

from app.core.auth import Principal
from app.core.errors import BadRequest, NotFound, VersionConflict
from app.db.repositories import menu as menu_repo
from app.db.repositories import order_writes
from app.db.repositories.menu import MenuItem
from app.db.tx import tx
from app.domain import audit
from app.domain.menu import csv_import


async def set_availability(p: Principal, item_id: str, available: bool) -> MenuItem:
    async with tx() as c:
        item = await menu_repo.get(c, p.cafe_id, item_id)
        if item is None:
            raise NotFound()
        if item.available == available:
            return item  # no-op: no second audit row
        version = await menu_repo.next_version(c, p.cafe_id)
        updated = await menu_repo.set_available(c, p.cafe_id, item_id, available, version)
        if updated is None:
            current = await menu_repo.get(c, p.cafe_id, item_id)
            assert current is not None
            return current
        flagged = 0
        if not available:
            flagged = await order_writes.flag_open_orders_with_item(c, p.cafe_id, item_id, "item_sold_out")
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "menu.availability",
                          {"item_id": item_id, "name": item.name, "available": available, "orders_flagged": flagged})
    return updated


# ── Edit, new, import (manager+ / owner; docs/backend.md#menu, #csv-import) ─────────────────────────────────

ALLERGEN_STATES = ("contains", "free_from", "unknown")


def _duplicate(name: str) -> BadRequest:
    return BadRequest(f"A dish called “{name}” is already on the menu.")


async def save_item(p: Principal, item_id: str | None, fields: dict[str, object], allergens: dict[str, str],
                    expected_version: int | None) -> tuple[MenuItem, dict[str, str]]:
    """New (item_id None) or edit with optimistic locking. One transaction: the item, its allergen states, a new
    menu version (the agent sees it on the next message), the audit row, and — when an edit makes it unavailable —
    the `item_sold_out` flag on open orders, exactly like the sold-out toggle."""
    try:
        async with tx() as c:
            version = await menu_repo.next_version(c, p.cafe_id)
            if item_id is None:
                item = await menu_repo.insert(c, p.cafe_id, fields, version)
                before = None
            else:
                before = await menu_repo.get(c, p.cafe_id, item_id)
                if before is None:
                    raise NotFound()
                updated = await menu_repo.update(c, p.cafe_id, item_id, fields, expected_version, version)
                if updated is None:
                    raise VersionConflict("Someone else changed this item. Reload to see their change.")
                item = updated
            await menu_repo.replace_allergens(c, p.cafe_id, item.id, allergens)
            flagged = 0
            if before is not None and before.available and not item.available:
                flagged = await order_writes.flag_open_orders_with_item(c, p.cafe_id, item.id, "item_sold_out")
            changed = sorted(k for k, v in fields.items() if before is None or getattr(before, k) != v)
            await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "menu.update",
                              {"item_id": item.id, "name": item.name, "new": before is None, "fields": changed,
                               "allergens": len([s for s in allergens.values() if s != "unknown"]),
                               "orders_flagged": flagged})
    except asyncpg.UniqueViolationError as exc:
        raise _duplicate(str(fields["name"])) from exc
    return item, {a: allergens.get(a, "unknown") for a in csv_import.ALLERGENS}


async def import_csv(p: Principal, text: str) -> dict[str, int]:
    parsed = csv_import.parse(text)
    if parsed.errors:
        raise BadRequest(f"The file has {len(parsed.errors)} problem{'s' if len(parsed.errors) != 1 else ''}; "
                         "nothing was imported.", details={"rows": parsed.errors[:200]})
    created = updated = 0
    async with tx() as c:
        for row in parsed.rows:
            version = await menu_repo.next_version(c, p.cafe_id)
            existing = await menu_repo.by_name(c, p.cafe_id, row.name)
            if existing is None:
                item = await menu_repo.insert(c, p.cafe_id, row.fields(), version)
                created += 1
            else:
                item = await menu_repo.update(c, p.cafe_id, existing.id, row.fields(), None, version)  # type: ignore[assignment]
                updated += 1
                if existing.available and not row.available:
                    await order_writes.flag_open_orders_with_item(c, p.cafe_id, existing.id, "item_sold_out")
            await menu_repo.replace_allergens(c, p.cafe_id, item.id, row.allergens)
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "menu.import",
                          {"created": created, "updated": updated, "rows": len(parsed.rows)})
    return {"created": created, "updated": updated}
