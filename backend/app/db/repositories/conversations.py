from dataclasses import dataclass
from typing import Any

import asyncpg

DEFAULT_STATE: dict[str, Any] = {
    "step": "greeting", "awaiting": None, "cart": {"items": [], "version": 0}, "clarify_strikes": 0,
}


@dataclass
class Conversation:
    id: str
    cafe_id: str
    customer_id: str
    mode: str
    assigned_to: str | None
    table_session_id: str | None
    state: dict[str, Any]


_COLS = "id::text, cafe_id::text, customer_id::text, mode, assigned_to::text, table_session_id::text, state"


async def get_or_create(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> str:
    return str(await conn.fetchval(
        """insert into conversations (cafe_id, customer_id) values ($1, $2)
           on conflict (cafe_id, customer_id) do update set updated_at = conversations.updated_at
           returning id::text""",
        cafe_id, customer_id,
    ))


async def get(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, *, for_update: bool = False) -> Conversation | None:
    sql = "select " + _COLS + " from conversations where cafe_id = $1 and id = $2" + (" for update" if for_update else "")
    row = await conn.fetchrow(sql, cafe_id, conversation_id)
    return Conversation(**dict(row)) if row else None


async def by_customer(conn: asyncpg.Connection, cafe_id: str, customer_id: str) -> Conversation | None:
    row = await conn.fetchrow("select " + _COLS + " from conversations where cafe_id = $1 and customer_id = $2",
                              cafe_id, customer_id)
    return Conversation(**dict(row)) if row else None


async def turn_context(conn: asyncpg.Connection, cafe_id: str, conversation_id: str) -> tuple[Any, Any, Any] | None:
    """Café + conversation + customer for one turn, in one round trip."""
    from app.db.repositories.cafes import Cafe
    from app.db.repositories.customers import Customer

    r = await conn.fetchrow(
        """select c.id::text as cafe_id, c.name, c.status, c.phone_number_id, c.display_phone_number, c.token_ref,
                  c.timezone, c.opening_hours, c.cutoff_minutes, c.settings,
                  cv.id::text as conversation_id, cv.customer_id::text, cv.mode, cv.assigned_to::text,
                  cv.table_session_id::text, cv.state,
                  cu.wa_id, cu.name as customer_name, cu.last_inbound_at, cu.last_visit_at
           from conversations cv join cafes c on c.id = cv.cafe_id join customers cu on cu.id = cv.customer_id
           where cv.cafe_id = $1 and cv.id = $2""",
        cafe_id, conversation_id,
    )
    if r is None:
        return None
    cafe = Cafe(id=r["cafe_id"], name=r["name"], status=r["status"], phone_number_id=r["phone_number_id"],
                display_phone_number=r["display_phone_number"], token_ref=r["token_ref"], timezone=r["timezone"],
                opening_hours=r["opening_hours"], cutoff_minutes=r["cutoff_minutes"], settings=r["settings"])
    convo = Conversation(id=r["conversation_id"], cafe_id=r["cafe_id"], customer_id=r["customer_id"], mode=r["mode"],
                         assigned_to=r["assigned_to"], table_session_id=r["table_session_id"], state=r["state"])
    customer = Customer(id=r["customer_id"], cafe_id=r["cafe_id"], wa_id=r["wa_id"], name=r["customer_name"],
                        last_inbound_at=r["last_inbound_at"], last_visit_at=r["last_visit_at"])
    return cafe, convo, customer


async def save_state(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, state: dict[str, Any]) -> None:
    await conn.execute("update conversations set state = $3 where cafe_id = $1 and id = $2",
                       cafe_id, conversation_id, state)


async def set_mode(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, mode: str,
                   assigned_to: str | None) -> bool:
    """Returns True when something changed (set-state calls are no-ops when already set)."""
    changed = await conn.fetchval(
        """update conversations set mode = $3, assigned_to = $4
           where cafe_id = $1 and id = $2 and (mode <> $3 or assigned_to is distinct from $4::uuid)
           returning id""",
        cafe_id, conversation_id, mode, assigned_to,
    )
    return bool(changed)


async def set_table_session(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, session_id: str | None) -> None:
    await conn.execute("update conversations set table_session_id = $3 where cafe_id = $1 and id = $2",
                       cafe_id, conversation_id, session_id)


async def latest_in_session(conn: asyncpg.Connection, cafe_id: str, table_session_id: str) -> Conversation | None:
    row = await conn.fetchrow(
        "select " + _COLS + " from conversations where cafe_id = $1 and table_session_id = $2 order by updated_at desc limit 1",
        cafe_id, table_session_id,
    )
    return Conversation(**dict(row)) if row else None


async def last_activity(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, customer_id: str) -> tuple[Any, Any]:
    """(last customer message at, last outbound message at) for idle hand-back."""
    row = await conn.fetchrow(
        """select (select max(received_at) from inbound_messages where cafe_id = $1 and conversation_id = $2) as last_in,
                  (select max(created_at) from wa_messages where cafe_id = $1 and customer_id = $3) as last_out""",
        cafe_id, conversation_id, customer_id,
    )
    return (row["last_in"], row["last_out"]) if row else (None, None)
