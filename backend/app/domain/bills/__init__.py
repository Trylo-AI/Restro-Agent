"""Bill sheet and code redemption (docs/backend.md#bill-sheet, #redemption). Money is computed here, in integer
paise, from what code already priced (order totals); the console only shows it.

Subtotal = the session's non-cancelled orders. Discount = the campaign's `discount_percent` of the subtotal,
rounded down to the paisa (open-decision default: percentage of subtotal). One code per bill, one redemption per
code across the table bill and the counter, never across cafés. Applying the code already on this bill is a no-op.
Eligibility is `campaigns.redemption.check_code`, the same verdict as the counter. Lock order: the bill (table
session), then the customer row, then the code; the counter takes the last two in the same order and never locks a
session, so the two paths never deadlock. The bill is read after the locks.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from app.core.auth import Principal
from app.core.errors import BadRequest, NotFound
from app.db.repositories import redemptions
from app.db.repositories import tables as tables_repo
from app.db.tx import conn, tx
from app.domain import audit
from app.domain.campaigns.redemption import (
    CodeAlreadyUsed,
    InvalidCode,
    OneCodePerBill,
    check_code,
    discount_paise,
    explain,
    local_date,
    normalise_code,
    offer_redeem_change,
    raise_for,
    redemption_details,
)

__all__ = ["CodeAlreadyUsed", "InvalidCode", "apply_code", "bill", "compute"]


async def compute(c: asyncpg.Connection, cafe_id: str, table_session_id: str) -> dict[str, Any]:
    if not await tables_repo.session_exists(c, cafe_id, table_session_id):
        raise NotFound()
    orders = await tables_repo.bill_orders(c, cafe_id, table_session_id)
    lines = await redemptions.bill_lines(c, cafe_id, [i for i, _, _ in orders])
    redemption = await redemptions.for_session(c, cafe_id, table_session_id)
    subtotal = sum(t for _, _, t in orders)
    discount = min(redemption.discount_paise, subtotal) if redemption else 0
    closed_at = await c.fetchval("select closed_at from table_sessions where cafe_id = $1 and id = $2",
                                 cafe_id, table_session_id)
    return {
        "table_session_id": table_session_id, "closed_at": closed_at.isoformat() if closed_at else None,
        "orders": [{"id": i, "number": n, "total_paise": t,
                    "items": [{"name_snapshot": ln["name_snapshot"], "qty": ln["qty"], "line_paise": ln["line_paise"]}
                              for ln in lines if ln["order_id"] == i]} for i, n, t in orders],
        "subtotal_paise": subtotal, "code": redemption.code if redemption else None,
        "discount_paise": discount, "total_paise": subtotal - discount,
    }


async def bill(p: Principal, table_session_id: str) -> dict[str, Any]:
    async with conn() as c:
        return await compute(c, p.cafe_id, table_session_id)


async def apply_code(p: Principal, table_session_id: str, code_text: str) -> dict[str, Any]:
    code_text = normalise_code(code_text)
    async with tx() as c:
        # Locks first (the bill, the customer row, then the code), then the bill is read: every check sees what a
        # concurrent tap committed, so a double tap returns the discounted bill and a second code gets "one per bill".
        if not await redemptions.lock_session(c, p.cafe_id, table_session_id):
            raise NotFound()
        found = await redemptions.code(c, p.cafe_id, code_text)
        if found is None:
            raise InvalidCode(details={"verdict": "not_found"})
        await redemptions.lock_customer(c, p.cafe_id, found.customer_id)
        info, used, v = await check_code(c, p.cafe_id, code_text, lock=True)
        now = datetime.now(UTC)
        if info is None:
            raise InvalidCode(details={"verdict": "not_found"})
        current = await compute(c, p.cafe_id, table_session_id)
        if v == "redeemed" and used is not None:
            if used.table_session_id == table_session_id:
                return current  # already on this bill
            today = local_date(used.redeemed_at, info.timezone) == local_date(now, info.timezone)
            when = "today" if today else used.redeemed_at.astimezone(ZoneInfo(info.timezone)).strftime("on %d %b")
            message = (f"This code was already used {when} at the counter." if used.channel == "counter"
                       else f"This code was already used {when} on #{used.order_number}." if used.order_number
                       else f"This code was already used {when}.")
            raise CodeAlreadyUsed(message, redemption_details(used))
        if v != "eligible":
            raise_for(v, explain(v, info, used, now, info.timezone), used)
        if current["code"] is not None:
            raise OneCodePerBill(f"This bill already has a code ({current['code']}). One code per bill.")
        if not current["orders"]:
            raise BadRequest("There's nothing on this bill yet.")
        subtotal = current["subtotal_paise"]
        discount = discount_paise(subtotal, info.discount_percent)
        latest = current["orders"][-1]
        if await redemptions.insert(c, p.cafe_id, info.recipient_id, info.code, latest["id"], table_session_id,
                                    discount, p.user_id, channel="table_bill", bill_paise=subtotal) is None:
            raise CodeAlreadyUsed("This code was just used on another bill.")
        await redemptions.link_order(c, p.cafe_id, latest["id"], info.recipient_id)
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "offer.redeem",
                          offer_redeem_change(info, "table_bill", subtotal, discount, table_session_id=table_session_id,
                                              order_id=latest["id"], order_number=latest["number"]))
        return await compute(c, p.cafe_id, table_session_id)
