"""Cross-café queries for scheduled jobs only (named exception to the cafe_id rule; read-mostly, no customer data)."""

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class StuckConversation:
    cafe_id: str
    conversation_id: str
    reason: str


async def stuck_work(conn: asyncpg.Connection, overdue_seconds: int = 30) -> list[StuckConversation]:
    """Due work nobody is processing, and running work whose lease expired.

    A `running` row with an expired lease goes back to `pending` here, so the next holder reruns it.
    """
    reclaimed = await conn.fetch(
        """update pending_work w set status = 'pending', next_attempt_at = now()
           from conversations c
           where c.id = w.conversation_id and w.status = 'running'
             and (c.lease_expires_at is null or c.lease_expires_at < now())  -- renewed every 10 s while alive
             and not exists (select 1 from pending_work p
                             where p.conversation_id = w.conversation_id and p.kind = w.kind and p.status = 'pending')
           returning w.cafe_id::text, w.conversation_id::text"""
    )
    overdue = await conn.fetch(
        """select w.cafe_id::text, w.conversation_id::text
           from pending_work w join conversations c on c.id = w.conversation_id
           where w.status = 'pending' and w.next_attempt_at < now() - make_interval(secs => $1)
             and (c.lease_expires_at is null or c.lease_expires_at < now())""",
        overdue_seconds,
    )
    out = [StuckConversation(r["cafe_id"], r["conversation_id"], "lease_expired") for r in reclaimed]
    seen = {s.conversation_id for s in out}
    out += [StuckConversation(r["cafe_id"], r["conversation_id"], "overdue") for r in overdue
            if r["conversation_id"] not in seen]
    return out
