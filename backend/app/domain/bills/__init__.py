"""Bill sheet and code redemption (docs/backend.md#bill-sheet, #redemption). Money is computed here, in integer
paise, from what code already priced (order totals); the console only shows it.

Subtotal = the session's non-cancelled orders. Discount = the campaign's `discount_percent` of the subtotal,
rounded down to the paisa (open-decision default: percentage of subtotal). One code per bill, one redemption per
code, never across cafés. Applying the code already on this bill is a no-op.
"""

from datetime import UTC, datetime
from typing import Any

import asyncpg

from app.core.auth import Principal
from app.core.errors import AppError, BadRequest, NotFound
from app.db.repositories import redemptions
from app.db.repositories import tables as tables_repo
from app.db.tx import conn, tx
from app.domain import audit


class InvalidCode(AppError):
    status, code, default_message = 422, "invalid_code", "This isn't a valid code, or it has expired."


class CodeAlreadyUsed(AppError):
    status, code = 409, "code_already_used"


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
    code_text = code_text.strip().upper()
    async with tx() as c:
        current = await compute(c, p.cafe_id, table_session_id)
        info = await redemptions.code(c, p.cafe_id, code_text)
        if info is None or info.status not in ("sent", "delivered", "read", "held") \
                or info.campaign_status == "cancelled" \
                or (info.valid_until is not None and info.valid_until < datetime.now(UTC)):
            raise InvalidCode()
        used = await redemptions.by_recipient(c, p.cafe_id, info.recipient_id)
        if used is not None:
            if used.table_session_id == table_session_id:
                return current  # already on this bill
            when = "today" if used.redeemed_at.date() == datetime.now(UTC).date() else used.redeemed_at.strftime("on %d %b")
            raise CodeAlreadyUsed(f"This code was already used {when} on #{used.order_number}."
                                  if used.order_number else f"This code was already used {when}.")
        if current["code"] is not None:
            raise BadRequest(f"This bill already has a code ({current['code']}). One code per bill.")
        if not current["orders"]:
            raise BadRequest("There's nothing on this bill yet.")
        discount = current["subtotal_paise"] * (info.discount_percent or 0) // 100
        latest = current["orders"][-1]["id"]
        if not await redemptions.insert(c, p.cafe_id, info.recipient_id, code_text, latest, table_session_id,
                                        discount, p.user_id):
            raise CodeAlreadyUsed("This code was just used on another bill.")
        await redemptions.link_order(c, p.cafe_id, latest, info.recipient_id)
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "offer.redeem",
                          {"table_session_id": table_session_id, "campaign_id": info.campaign_id,
                           "discount_paise": discount})
        return await compute(c, p.cafe_id, table_session_id)
