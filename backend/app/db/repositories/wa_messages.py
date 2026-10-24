"""Outbound step records (decision 14, contracts/state-machines.md#outbound-step).

One row per outbound step, written before the request, unique on idempotency_key. `attempted_at` is claimed
atomically just before the request, so two senders can never both send the same row.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

STATUS_RANK = {"pending": 0, "ambiguous": 1, "sent": 2, "delivered": 3, "read": 4, "failed": 5}


@dataclass(frozen=True)
class Step:
    id: str
    cafe_id: str
    customer_id: str | None
    idempotency_key: str
    type: str
    template_name: str | None
    payload: dict[str, Any]
    status: str
    wamid: str | None
    attempted_at: datetime | None


_COLS = """id::text, cafe_id::text, customer_id::text, idempotency_key, type, template_name, payload, status, wamid,
           attempted_at"""


def _step(row: asyncpg.Record | None) -> Step | None:
    return Step(**dict(row)) if row else None


async def insert_step(conn: asyncpg.Connection, cafe_id: str, customer_id: str | None, key: str, type_: str,
                      payload: dict[str, Any], template_name: str | None = None) -> tuple[Step, bool]:
    """Returns (step, created). An existing key returns the existing row: a re-run never makes a second step."""
    row = await conn.fetchrow(
        "insert into wa_messages (cafe_id, customer_id, idempotency_key, type, template_name, payload)"
        " values ($1, $2, $3, $4, $5, $6) on conflict (idempotency_key) do nothing returning " + _COLS,
        cafe_id, customer_id, key, type_, template_name, payload,
    )
    if row:
        return Step(**dict(row)), True
    existing = await conn.fetchrow("select " + _COLS + " from wa_messages where cafe_id = $1 and idempotency_key = $2",
                                   cafe_id, key)
    if existing is None:  # the key belongs to another café: never reuse it
        raise ValueError("idempotency key collision across cafés")
    return Step(**dict(existing)), False


async def claim_attempt(conn: asyncpg.Connection, cafe_id: str, step_id: str) -> bool:
    """pending + never attempted → set attempted_at. Only one caller wins."""
    return bool(await conn.fetchval(
        """update wa_messages set attempted_at = now()
           where cafe_id = $1 and id = $2 and status = 'pending' and attempted_at is null returning id""",
        cafe_id, step_id,
    ))


async def claim_for_delivery(conn: asyncpg.Connection, cafe_id: str, step_id: str) -> tuple[Step, dict[str, Any]] | None:
    """claim_attempt + the step, its café and the customer's wa_id in one round trip (hot path)."""
    row = await conn.fetchrow(
        """with s as (
             update wa_messages set attempted_at = now()
             where cafe_id = $1 and id = $2 and status = 'pending' and attempted_at is null
             returning *)
           select s.id::text, s.cafe_id::text, s.customer_id::text, s.idempotency_key, s.type, s.template_name,
                  s.payload, s.status, s.wamid, s.attempted_at, cu.wa_id,
                  c.name as cafe_name, c.status as cafe_status, c.phone_number_id, c.display_phone_number,
                  c.token_ref, c.timezone, c.opening_hours, c.cutoff_minutes, c.settings
           from s join cafes c on c.id = s.cafe_id left join customers cu on cu.id = s.customer_id""",
        cafe_id, step_id,
    )
    if row is None:
        return None
    d = dict(row)
    step = Step(**{k: d[k] for k in ("id", "cafe_id", "customer_id", "idempotency_key", "type", "template_name",
                                      "payload", "status", "wamid", "attempted_at")})
    return step, d


async def release_attempt(conn: asyncpg.Connection, cafe_id: str, step_id: str, error_code: str | None) -> None:
    """The request certainly didn't go (connection refused, 429): back to never-attempted for a retry."""
    await conn.execute(
        "update wa_messages set attempted_at = null, error_code = $3 where cafe_id = $1 and id = $2 and status = 'pending'",
        cafe_id, step_id, error_code,
    )


async def mark_sent(conn: asyncpg.Connection, cafe_id: str, step_id: str, wamid: str) -> None:
    await conn.execute(
        """update wa_messages set status = 'sent', wamid = $3, sent_at = coalesce(sent_at, now()), error_code = null
           where cafe_id = $1 and id = $2 and status in ('pending', 'ambiguous')""",
        cafe_id, step_id, wamid,
    )


async def mark_ambiguous(conn: asyncpg.Connection, cafe_id: str, step_id: str, error_code: str | None) -> None:
    await conn.execute(
        "update wa_messages set status = 'ambiguous', error_code = $3 where cafe_id = $1 and id = $2 and status = 'pending'",
        cafe_id, step_id, error_code,
    )


async def mark_failed(conn: asyncpg.Connection, cafe_id: str, step_id: str, error_code: str | None) -> None:
    await conn.execute(
        """update wa_messages set status = 'failed', error_code = $3, failed_at = now()
           where cafe_id = $1 and id = $2 and status in ('pending', 'ambiguous')""",
        cafe_id, step_id, error_code,
    )


async def get(conn: asyncpg.Connection, cafe_id: str, step_id: str) -> Step | None:
    return _step(await conn.fetchrow("select " + _COLS + " from wa_messages where cafe_id = $1 and id = $2",
                                     cafe_id, step_id))


async def apply_status(conn: asyncpg.Connection, cafe_id: str, *, wamid: str | None, step_id: str | None,
                       status: str, error_code: str | None, pricing_category: str | None, billable: bool | None,
                       cost: float | None, currency: str | None, pricing_version: str | None) -> bool:
    """Forward-only status from a webhook. `step_id` (from biz_opaque_callback_data) resolves an ambiguous row."""
    if status not in ("sent", "delivered", "read", "failed"):
        return False
    stamp = {"sent": "sent_at", "delivered": "delivered_at", "read": "read_at", "failed": "failed_at"}[status]
    allowed_from = [s for s, rank in STATUS_RANK.items() if rank < STATUS_RANK[status] and s != "failed"]
    if status == "failed":
        allowed_from = ["pending", "ambiguous", "sent", "delivered"]
    row = await conn.fetchval(
        f"""update wa_messages set status = $3, {stamp} = coalesce({stamp}, now()),
                   wamid = coalesce(wamid, $4), error_code = coalesce($5, error_code),
                   pricing_category = coalesce($6, pricing_category), billable = coalesce($7, billable),
                   cost = coalesce($8, cost), currency = coalesce($9, currency),
                   pricing_version = coalesce($10, pricing_version),
                   cost_basis = case when $8::numeric is null then cost_basis else 'estimated' end
            where cafe_id = $1 and (wamid = $4 or id = $2::uuid) and status = any($11::text[])
            returning id""",  # noqa: S608  (column name from a fixed map)
        cafe_id, step_id, status, wamid, error_code, pricing_category, billable, cost, currency, pricing_version,
        allowed_from,
    )
    return row is not None


async def cafe_for_wamid(conn: asyncpg.Connection, wamid: str) -> str | None:
    """Status webhooks carry phone_number_id too; this is only a cross-check."""
    return await conn.fetchval("select cafe_id::text from wa_messages where wamid = $1", wamid)  # type: ignore[no-any-return]


async def never_attempted(conn: asyncpg.Connection, older_than_seconds: int = 30) -> list[tuple[str, str]]:
    """Recovery after a crash: steps written but never attempted (cross-café job query)."""
    rows = await conn.fetch(
        """select cafe_id::text, id::text from wa_messages
           where status = 'pending' and attempted_at is null and created_at < now() - make_interval(secs => $1)""",
        older_than_seconds,
    )
    return [(r["cafe_id"], r["id"]) for r in rows]
