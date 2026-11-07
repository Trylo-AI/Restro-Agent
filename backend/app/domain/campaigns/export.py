"""Campaign recipients CSV (docs/backend.md#campaigns). Owner only; masked numbers and first names only.

Money in rupees with integer maths (never a float). Times in the café's timezone, ISO with the offset. A cell that a
spreadsheet would read as a formula (`= + - @`, tab, carriage return) gets a leading `'`. The export itself is
audited (`campaign.export`), in its own transaction after the read.
"""

import csv
import io
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.auth import Principal
from app.core.errors import NotFound
from app.db.repositories import campaign_insights
from app.db.repositories import campaigns as campaigns_repo
from app.db.tx import conn, tx
from app.domain import audit

HEADER = ["first_name", "masked_phone", "code", "status", "sent_at", "delivered_at", "read_at", "replied_at",
          "orders_in_window", "spent_inr", "redeemed_at", "redemption_channel", "bill_inr", "discount_inr",
          "redeemed_by", "opted_out_at", "opted_out_source"]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def rupees(paise: int | None) -> str:
    if paise is None:
        return ""
    sign, p = ("-", -paise) if paise < 0 else ("", paise)
    return f"{sign}{p // 100}.{p % 100:02d}"


def cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def _at(value: datetime | None, tz: ZoneInfo) -> str:
    return value.astimezone(tz).isoformat(timespec="seconds") if value else ""


def rows_csv(rows: list[Any], tz: str) -> str:
    zone = ZoneInfo(tz)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEADER)
    for r in rows:
        redeemed = r["redeemed_at"] is not None
        w.writerow([cell(v) for v in (
            "" if r["deleted"] else (r["name"] or "").split(" ")[0],
            "deleted" if r["deleted"] else r["masked_phone"],
            r["code"], r["status"], _at(r["sent_at"], zone), _at(r["delivered_at"], zone), _at(r["read_at"], zone),
            _at(r["replied_at"], zone), r["orders"], rupees(r["spent_paise"]), _at(r["redeemed_at"], zone),
            r["redemption_channel"] or "", rupees(r["bill_paise"]) if redeemed else "",
            rupees(r["discount_paise"]) if redeemed else "", r["redeemed_by_name"] or "",
            _at(r["opted_out_at"], zone), r["opted_out_source"] or "")])
    return out.getvalue()


async def recipients_csv(p: Principal, campaign_id: str) -> str:
    async with conn() as c:
        if await campaigns_repo.get(c, p.cafe_id, campaign_id) is None:
            raise NotFound()
        rows = await campaign_insights.recipient_rows(c, p.cafe_id, campaign_id)
        tz = await c.fetchval("select timezone from cafes where id = $1", p.cafe_id)
    body = rows_csv(rows, tz)
    async with tx() as c:
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "campaign.export",
                          {"campaign_id": campaign_id, "rows": len(rows)})
    return body
