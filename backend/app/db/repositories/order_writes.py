"""Order writes behind transition(), undo, delay and flags. Every function takes cafe_id."""

from datetime import datetime
from typing import Any

import asyncpg

from app.db.repositories.orders import COLS, Order

STAMP_COLUMNS = {"ACCEPTED": "accepted_at", "READY": "ready_at", "SERVED": "served_at", "CANCELLED": "cancelled_at"}
EXTRA_COLUMNS = {"cancel_reason_code", "cancel_reason_text", "cancelled_by", "flags", "expected_minutes",
                 "total_paise", "allergy_note"}


async def set_status(conn: asyncpg.Connection, cafe_id: str, order_id: str, to: str, expected_version: int,
                     extra: dict[str, Any] | None = None) -> Order | None:
    """status + version + 1 + the status timestamp, only if the version still matches. None = stale version."""
    sets = ["status = $3", "version = version + 1"]
    args: list[Any] = [cafe_id, order_id, to, expected_version]
    if to in STAMP_COLUMNS:
        sets.append(f"{STAMP_COLUMNS[to]} = now()")
    for col, value in (extra or {}).items():
        if col not in EXTRA_COLUMNS:
            raise ValueError(col)
        args.append(value)
        sets.append(f"{col} = ${len(args)}")
    row = await conn.fetchrow(
        f"update orders set {', '.join(sets)} where cafe_id = $1 and id = $2 and version = $4 returning {COLS}", *args)
    return Order(**dict(row)) if row else None


async def revert_status(conn: asyncpg.Connection, cafe_id: str, order_id: str, back_to: str, from_status: str,
                        expected_version: int) -> Order | None:
    clear = STAMP_COLUMNS.get(from_status)
    row = await conn.fetchrow(
        f"""update orders set status = $3, version = version + 1{f', {clear} = null' if clear else ''}
            where cafe_id = $1 and id = $2 and version = $4 returning {COLS}""",
        cafe_id, order_id, back_to, expected_version,
    )
    return Order(**dict(row)) if row else None


async def insert_event(conn: asyncpg.Connection, cafe_id: str, order_id: str, from_status: str | None, to_status: str,
                       actor_type: str, actor_id: str | None, reason: str | None) -> None:
    await conn.execute(
        """insert into order_events (cafe_id, order_id, from_status, to_status, actor_type, actor_id, reason)
           values ($1, $2, $3, $4, $5, $6, $7)""",
        cafe_id, order_id, from_status, to_status, actor_type, actor_id, reason,
    )


async def last_event(conn: asyncpg.Connection, cafe_id: str, order_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """select from_status, to_status, actor_id::text, reason, created_at from order_events
           where cafe_id = $1 and order_id = $2 order by created_at desc limit 1""",
        cafe_id, order_id,
    )
    return dict(row) if row else None


async def apply_delay(conn: asyncpg.Connection, cafe_id: str, order_id: str, minutes: int) -> Order | None:
    """+N minutes, flag `delayed`, clear `overdue`. Only NEW or ACCEPTED."""
    row = await conn.fetchrow(
        "update orders set delay_minutes = delay_minutes + $3, version = version + 1, "
        "flags = array_append(array_remove(array_remove(flags, 'overdue'), 'delayed'), 'delayed') "
        "where cafe_id = $1 and id = $2 and status in ('NEW', 'ACCEPTED') returning " + COLS,
        cafe_id, order_id, minutes,
    )
    return Order(**dict(row)) if row else None


async def flag_open_orders_with_item(conn: asyncpg.Connection, cafe_id: str, item_id: str, flag: str) -> int:
    rows = await conn.fetch(
        """update orders o set flags = array_append(o.flags, $3)
           where o.cafe_id = $1 and o.status in ('NEW', 'ACCEPTED') and not ($3 = any(o.flags))
             and exists (select 1 from order_items oi where oi.order_id = o.id and oi.item_id = $2)
           returning o.id""",
        cafe_id, item_id, flag,
    )
    return len(rows)


async def insert_escalation(conn: asyncpg.Connection, cafe_id: str, subject_type: str, subject_id: str, level: int,
                            target: str) -> bool:
    return bool(await conn.fetchval(
        """insert into escalations (cafe_id, subject_type, subject_id, level, target) values ($1, $2, $3, $4, $5)
           on conflict (subject_type, subject_id, level) do nothing returning id""",
        cafe_id, subject_type, subject_id, level, target,
    ))


async def insert_notifications(conn: asyncpg.Connection, cafe_id: str, user_ids: list[str], kind: str,
                               subject_type: str, subject_id: str, payload: dict[str, Any]) -> None:
    await conn.executemany(
        """insert into notifications (cafe_id, user_id, kind, subject_type, subject_id, payload)
           values ($1, $2, $3, $4, $5, $6)""",
        [(cafe_id, uid, kind, subject_type, subject_id, payload) for uid in user_ids],
    )


def is_recent(at: datetime, now: datetime, seconds: int) -> bool:
    return (now - at).total_seconds() <= seconds
