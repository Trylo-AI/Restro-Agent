"""Offer codes: the one eligibility rule, staff lookup and counter redemption (docs/backend.md#redemption,
docs/retention-audit-recommend.md#backend).

A code is redeemed once, on a table bill (`domain/bills.apply_code`) or at the counter (here); both ask `check_code`
for the verdict. Precedence: not_found → redeemed → campaign_cancelled → not_sent (queued / deferred) →
not_delivered (failed) → expired → eligible. `held` counts as sent (eligible, with a warning). A code is valid
through the café-local day of `valid_until` (`cafes.timezone`), the date the customer was told.

Counter: discount = `discount_percent` of the amount paid before the discount, rounded down to the paisa (0 without
a percentage); one offer per customer per 60 minutes across both channels; `Idempotency-Key` → one row. Lock order
on both paths: the customer row, then `for share` on the code (the table bill locks its session before both). Lookup
is read only and never audited. Logs carry ids only: never the query, a name, digits or a code; nor do traces, so the
errors here record only their code (`StaffMessage`).
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, NoReturn
from zoneinfo import ZoneInfo

import asyncpg

from app.core.auth import Principal
from app.core.errors import AppError, BadRequest
from app.core.logging import get_logger
from app.db.repositories import redemptions
from app.db.repositories.redemptions import CodeInfo, Redemption
from app.db.tx import conn, tx
from app.domain import audit
from app.domain.customers.privacy import masked

log = get_logger(__name__)

Verdict = Literal["eligible", "redeemed", "expired", "campaign_cancelled", "not_sent", "not_delivered", "not_found"]
QueryKind = Literal["name", "phone_last4", "code"]
SENT = ("sent", "delivered", "read", "held")
VISIT_WINDOW = timedelta(minutes=60)
MIN_BILL_PAISE, MAX_BILL_PAISE = 100, 10_000_000  # ₹ 1 to ₹ 1,00,000
MAX_CUSTOMERS, MAX_OFFERS = 20, 10
CODE_RE = re.compile(r"^[A-Z0-9]{2,10}-[A-Z0-9]{3}$")


class StaffMessage(AppError):
    """The message is for staff only and may hold a first name, a code or a staff name. `str()` is what the tracer
    records (`ops_runs.failure_reason`, the failure fingerprint), so it is the error code alone; the message still
    reaches the console in the response body."""

    def __str__(self) -> str:
        return self.code


class InvalidCode(StaffMessage):
    status, code, default_message = 422, "invalid_code", "This isn't a valid code, or it has expired."


class CodeAlreadyUsed(StaffMessage):
    status, code = 409, "code_already_used"


class AlreadyRedeemedThisVisit(StaffMessage):
    status, code = 409, "already_redeemed_this_visit"


class OneCodePerBill(StaffMessage, BadRequest):
    """400: this bill already carries a code (the message names it)."""


@dataclass(frozen=True)
class Check:
    verdict: Verdict
    reason: str
    warning: str | None


# ── pure rules ────────────────────────────────────────────────────────────

def normalise_code(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def classify_query(q: str) -> tuple[QueryKind, str]:
    """`code` when it looks like one (any case, spaces dropped); exactly 4 digits = the number's last 4; any other
    digits-only query is refused. Everything else is a name (stripped), which also finds a code typed without
    its dash."""
    text = q.strip()
    digits = re.sub(r"\s+", "", text)
    if len(digits) < 2:
        raise BadRequest("Type at least 2 characters.")
    if digits.isdigit():
        if len(digits) != 4:
            raise BadRequest("Type the last 4 digits of the number.")
        return "phone_last4", digits
    code = normalise_code(text)
    if CODE_RE.match(code):
        return "code", code
    return "name", text


def local_date(at: datetime, tz: str) -> date:
    return at.astimezone(ZoneInfo(tz)).date()


def _day(at: datetime, tz: str) -> str:
    d = at.astimezone(ZoneInfo(tz))
    return f"{d.day} {d:%b}"


def _clock(at: datetime, tz: str) -> str:
    return f"{at.astimezone(ZoneInfo(tz)):%H:%M}"


def first_name(name: str | None) -> str | None:
    return (name or "").split(" ")[0] or None


def verdict(info: CodeInfo | None, used: Redemption | None, now: datetime, tz: str) -> Verdict:
    if info is None:
        return "not_found"
    if used is not None:
        return "redeemed"
    if info.campaign_status == "cancelled":
        return "campaign_cancelled"
    if info.status in ("queued", "deferred"):
        return "not_sent"
    if info.status not in SENT:
        return "not_delivered"
    if info.valid_until is not None and local_date(now, tz) > local_date(info.valid_until, tz):
        return "expired"
    return "eligible"


def used_text(used: Redemption, now: datetime, tz: str) -> str:
    """"Used today at 12:41 at the counter by Ravi" / "Used on 12 Oct on the T7 bill (#1041) by Ravi"."""
    when = (f"today at {_clock(used.redeemed_at, tz)}" if local_date(used.redeemed_at, tz) == local_date(now, tz)
            else f"on {_day(used.redeemed_at, tz)}")
    if used.channel == "counter":
        where = "at the counter"
    else:
        where = f"on the {used.table_label} bill" if used.table_label else "on a table bill"
        if used.order_number:
            where += f" (#{used.order_number})"
    return f"Used {when} {where}" + (f" by {used.redeemed_by_name}" if used.redeemed_by_name else "")


def explain(v: Verdict, info: CodeInfo | None, used: Redemption | None, now: datetime, tz: str, *,
            recent: Redemption | None = None, name: str | None = None) -> Check:
    """The reason staff can read out, and the first warning that applies (eligible codes only). `recent` is the
    customer's latest redemption in the last 60 minutes, either channel."""
    if v == "not_found" or info is None:
        return Check("not_found", InvalidCode.default_message, None)
    if v == "redeemed" and used is not None:
        return Check(v, used_text(used, now, tz), None)
    if v == "campaign_cancelled":
        return Check(v, "The café cancelled this offer.", None)
    if v == "not_sent":
        return Check(v, "Not sent yet: the customer hasn't received this code.", None)
    if v == "not_delivered":
        return Check(v, "This message failed: the customer never got the code.", None)
    if v == "expired" and info.valid_until is not None:
        return Check(v, f"Expired on {_day(info.valid_until, tz)}.", None)
    reason = (f"{info.discount_percent} % off the bill" if info.discount_percent
              else f"Offer: {info.offer_text or info.campaign_name} · no bill discount")
    if info.valid_until is not None:
        reason += f" · valid till {_day(info.valid_until, tz)}"
    warning = None
    if info.status == "held":
        warning = "WhatsApp held this message; the customer may not have seen it."
    elif recent is not None and recent.code != info.code:
        warning = (f"{first_name(name) or 'This customer'} used {recent.code} at {_clock(recent.redeemed_at, tz)}. "
                   "One offer per visit at the counter.")
    elif info.consent == "opted_out":
        warning = "Opted out of offers since; this code still works."
    return Check("eligible", reason, warning)


def discount_paise(bill_paise: int, percent: int | None) -> int:
    return bill_paise * (percent or 0) // 100


def redemption_details(used: Redemption) -> dict[str, Any]:
    return {"redeemed_at": used.redeemed_at.isoformat(), "channel": used.channel,
            "redeemed_by_name": used.redeemed_by_name, "order_number": used.order_number,
            "table_label": used.table_label}


def raise_for(v: Verdict, check: Check, used: Redemption | None) -> NoReturn:
    if v == "redeemed" and used is not None:
        raise CodeAlreadyUsed(check.reason, redemption_details(used))
    if v in ("not_found", "expired"):
        raise InvalidCode(details={"verdict": v})  # the generic text: an expired code reads like a wrong one
    raise InvalidCode(check.reason, {"verdict": v})


async def check_code(c: asyncpg.Connection, cafe_id: str, code_text: str, *,
                     lock: bool = False) -> tuple[CodeInfo | None, Redemption | None, Verdict]:
    """The verdict for one code in this café, as both channels see it. `lock` (inside the caller's transaction,
    after the customer row lock) holds the code and its campaign until commit."""
    info = await redemptions.code(c, cafe_id, normalise_code(code_text), lock=lock)
    used = await redemptions.by_recipient(c, cafe_id, info.recipient_id) if info else None
    return info, used, verdict(info, used, datetime.now(UTC), info.timezone if info else "UTC")


# ── staff lookup ──────────────────────────────────────────────────────────

def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _redemption_out(used: Redemption) -> dict[str, Any]:
    return {"redeemed_at": used.redeemed_at.isoformat(), "channel": used.channel,
            "redeemed_by_name": used.redeemed_by_name, "order_number": used.order_number,
            "table_label": used.table_label, "bill_paise": used.bill_paise, "discount_paise": used.discount_paise}


async def lookup(p: Principal, q: str) -> dict[str, Any]:
    """`OfferLookup`: customers by name, last 4 digits or code, with every offer's verdict. Never a full number."""
    kind, value = classify_query(q)
    searched = normalise_code(q).replace("-", "") if kind != "phone_last4" else None
    now = datetime.now(UTC)
    async with conn() as c:
        rows = await redemptions.search_customers(c, p.cafe_id, kind, _like(value) if kind == "name" else value,
                                                  MAX_CUSTOMERS + 1)
        if not rows and kind == "code":  # a hyphenated name ("Jo-Ann") looks like a code
            kind, value = "name", q.strip()
            rows = await redemptions.search_customers(c, p.cafe_id, kind, _like(value), MAX_CUSTOMERS + 1)
        truncated, rows = len(rows) > MAX_CUSTOMERS, rows[:MAX_CUSTOMERS]
        ids = [r["customer_id"] for r in rows]
        offers = await redemptions.offers_for(c, p.cafe_id, ids, searched, MAX_OFFERS)
        bills = await redemptions.open_bills(c, p.cafe_id, ids)
        recent = await redemptions.recent_for_customers(c, p.cafe_id, ids, now - VISIT_WINDOW)
    customers = []
    for r in rows:
        name = None if r["deleted"] else r["name"]
        checks = []
        for info, used in (o for o in offers if o[0].customer_id == r["customer_id"]):
            v = verdict(info, used, now, info.timezone)
            check = explain(v, info, used, now, info.timezone, recent=recent.get(r["customer_id"]), name=name)
            checks.append({
                "recipient_id": info.recipient_id, "code": info.code, "campaign_id": info.campaign_id,
                "campaign_name": info.campaign_name, "offer_text": info.offer_text,
                "discount_percent": info.discount_percent,
                "valid_until": info.valid_until.isoformat() if info.valid_until else None,
                "sent_at": info.sent_at.isoformat() if info.sent_at else None,
                "recipient_status": info.status, "verdict": check.verdict, "reason": check.reason,
                "warning": check.warning, "matched": searched is not None and info.code.replace("-", "") == searched,
                "redemption": _redemption_out(used) if used else None,
            })
        bill = bills.get(r["customer_id"])
        customers.append({
            "customer_id": r["customer_id"], "name": name, "first_name": first_name(name),
            "masked_phone": None if r["deleted"] else masked(r["wa_id"]), "deleted": r["deleted"],
            "visits": r["visits"], "last_visit_at": r["last_visit_at"].isoformat() if r["last_visit_at"] else None,
            "open_bill": {"table_session_id": bill["table_session_id"], "table_label": bill["table_label"],
                          "subtotal_paise": bill["subtotal_paise"], "code": bill["code"]} if bill else None,
            "offers": checks,
        })
    # Code decides: customers with an offer that can be redeemed now come first (stable, so the SQL order stays).
    customers.sort(key=lambda cu: not any(o["verdict"] == "eligible" for o in cu["offers"]))
    log.info("offer_lookup", cafe_id=p.cafe_id, query_kind=kind, customers=len(customers), truncated=truncated)
    return {"query_kind": kind, "customers": customers, "truncated": truncated}


# ── counter redemption ────────────────────────────────────────────────────

def offer_redeem_change(info: CodeInfo, channel: str, bill_paise: int | None, discount: int, *,
                        table_session_id: str | None = None, order_id: str | None = None,
                        order_number: int | None = None) -> dict[str, Any]:
    """The `offer.redeem` audit payload, one shape for both channels (decision D28). Ids, the code and money only."""
    return {"table_session_id": table_session_id, "order_id": order_id, "order_number": order_number,
            "campaign_id": info.campaign_id, "recipient_id": info.recipient_id, "customer_id": info.customer_id,
            "code": info.code, "channel": channel, "discount_percent": info.discount_percent,
            "bill_paise": bill_paise, "discount_paise": discount}


def _counter_out(row: asyncpg.Record) -> dict[str, Any]:
    return {"redemption_id": row["redemption_id"], "code": row["code"], "channel": row["channel"],
            "campaign_id": row["campaign_id"], "campaign_name": row["campaign_name"], "offer_text": row["offer_text"],
            "discount_percent": row["discount_percent"], "customer_id": row["customer_id"],
            "first_name": first_name(None if row["deleted"] else row["name"]), "bill_paise": row["bill_paise"],
            "discount_paise": row["discount_paise"], "total_paise": row["bill_paise"] - row["discount_paise"],
            "redeemed_at": row["redeemed_at"].isoformat(), "redeemed_by_name": row["redeemed_by_name"]}


async def redeem_at_counter(p: Principal, code_text: str, bill_paise: int, key: str) -> dict[str, Any]:
    """`CounterRedemption`. One transaction, no LLM, no WhatsApp. A retry with the same key returns the same row."""
    if not MIN_BILL_PAISE <= bill_paise <= MAX_BILL_PAISE:
        raise BadRequest("Check the amount: between ₹ 1 and ₹ 1,00,000.")
    code_text = normalise_code(code_text)
    async with tx() as c:
        info = await redemptions.code(c, p.cafe_id, code_text)
        if info is None:
            raise InvalidCode(details={"verdict": "not_found"})
        name = await redemptions.lock_customer(c, p.cafe_id, info.customer_id)
        prior = await redemptions.by_idempotency_key(c, p.cafe_id, key)
        if prior is not None:  # after the lock: concurrent sends of one key see the committed row
            if prior["code"] != info.code or prior["bill_paise"] != bill_paise:
                raise BadRequest("This request was already used for another code or amount.")
            return _counter_out(prior)
        info, used, v = await check_code(c, p.cafe_id, code_text, lock=True)
        now = datetime.now(UTC)
        if info is None or v != "eligible":
            tz = info.timezone if info else "UTC"
            raise_for(v, explain(v, info, used, now, tz), used)
        recent = await redemptions.recent_for_customer(c, p.cafe_id, info.customer_id, now - VISIT_WINDOW)
        if recent is not None:
            raise AlreadyRedeemedThisVisit(
                f"{first_name(name) or 'This customer'} already used an offer at "
                f"{_clock(recent.redeemed_at, info.timezone)} ({recent.code}). One offer per visit.",
                {**redemption_details(recent), "code": recent.code})
        discount = discount_paise(bill_paise, info.discount_percent)
        created = await redemptions.insert(c, p.cafe_id, info.recipient_id, info.code, None, None, discount, p.user_id,
                                           channel="counter", bill_paise=bill_paise, idempotency_key=key)
        if created is None:  # the backstop: the unique code or key was taken despite the lock
            prior = await redemptions.by_idempotency_key(c, p.cafe_id, key)
            if prior is not None and prior["code"] == info.code and prior["bill_paise"] == bill_paise:
                return _counter_out(prior)
            raise CodeAlreadyUsed("This code was just used.")
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "offer.redeem",
                          offer_redeem_change(info, "counter", bill_paise, discount))
        row = await redemptions.by_idempotency_key(c, p.cafe_id, key)
    log.info("offer_redeemed", cafe_id=p.cafe_id, redemption_id=created, channel="counter")
    return _counter_out(row)
