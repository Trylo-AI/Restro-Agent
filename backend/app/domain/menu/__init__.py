"""Menu availability (docs/backend.md#menu). Critical core: the agent must never offer a sold-out dish.

The sold-out toggle is one transaction: `available`, a new menu version (the prompt cache key, so the agent stops
offering it on the next message), the audit row, and the `item_sold_out` flag on open orders containing it.
Carts lose the line at their next summary.
"""

from app.core.auth import Principal
from app.core.errors import NotFound
from app.db.repositories import menu as menu_repo
from app.db.repositories import order_writes
from app.db.repositories.menu import MenuItem
from app.db.tx import tx
from app.domain import audit


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
