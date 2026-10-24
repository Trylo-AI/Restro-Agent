"""Requests and human mode (docs/backend.md#human-help).

`help`, `allergy`, `bill`, `low_rating`; one unresolved request per (conversation, kind). Every handoff sets
`mode = human` (a bill never does). Nothing — guardrail, pause, closed — can block `help`.
"""

from dataclasses import dataclass

import asyncpg

from app.db.repositories import conversations
from app.db.repositories import requests as requests_repo

KINDS = ("help", "bill", "allergy", "low_rating")
HANDOFF_KINDS = ("help", "allergy")


@dataclass(frozen=True)
class Opened:
    request_id: str
    created: bool


async def open_request(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, kind: str, *,
                       reason_tag: str | None = None, table_session_id: str | None = None,
                       order_id: str | None = None) -> Opened:
    if kind not in KINDS:
        raise ValueError(kind)
    request_id, created = await requests_repo.insert_if_none_open(conn, cafe_id, conversation_id, kind, reason_tag,
                                                                  table_session_id, order_id)
    return Opened(request_id, created)


async def handoff(conn: asyncpg.Connection, cafe_id: str, conversation_id: str, reason_tag: str, *,
                  table_session_id: str | None = None, order_id: str | None = None) -> Opened:
    """Open the request for this reason and switch the chat to human mode (the bot goes silent)."""
    kind = "allergy" if reason_tag == "allergen_unknown" else "help"
    opened = await open_request(conn, cafe_id, conversation_id, kind, reason_tag=reason_tag,
                                table_session_id=table_session_id, order_id=order_id)
    await conversations.set_mode(conn, cafe_id, conversation_id, "human", None)
    return opened
