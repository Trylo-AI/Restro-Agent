"""Conversation lease (docs/architecture.md#message-pipeline).

Claim and release are single statements. During a turn the lease is renewed every 10 s; every commit in the
turn starts with `fence()`, which locks the conversation row and checks we still own it. If another worker
took over after our lease expired, the commit is refused (LeaseLost) and that worker reruns the turn.
No transaction is open between those short statements, so pooled connections are never held across the
LLM call or a WhatsApp send.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncpg

from app.core.ids import lease_token
from app.core.logging import get_logger
from app.db.repositories import leases
from app.db.tx import conn as db_conn

log = get_logger(__name__)

LEASE_SECONDS = 30
RENEW_SECONDS = 10


class LeaseLost(Exception):
    pass


@dataclass
class Lease:
    cafe_id: str
    conversation_id: str
    token: str = field(default_factory=lease_token)
    lost: bool = False


async def claim(c: asyncpg.Connection, cafe_id: str, conversation_id: str) -> Lease | None:
    lease = Lease(cafe_id, conversation_id)
    return lease if await leases.claim(c, cafe_id, conversation_id, lease.token, LEASE_SECONDS) else None


async def renew(c: asyncpg.Connection, lease: Lease) -> bool:
    ok = await leases.renew(c, lease.cafe_id, lease.conversation_id, lease.token, LEASE_SECONDS)
    if not ok:
        lease.lost = True
    return ok


async def fence(tx_conn: asyncpg.Connection, lease: Lease) -> None:
    """Call first inside every commit transaction of a turn. Row lock holds until that transaction ends."""
    if await leases.owner_for_update(tx_conn, lease.cafe_id, lease.conversation_id) != lease.token:
        lease.lost = True
        raise LeaseLost(lease.conversation_id)


async def release(c: asyncpg.Connection, lease: Lease) -> None:
    await leases.release(c, lease.cafe_id, lease.conversation_id, lease.token)


@asynccontextmanager
async def hold(cafe_id: str, conversation_id: str) -> AsyncIterator[Lease | None]:
    """Claim, renew in the background, release on exit. Yields None when someone else holds it."""
    async with db_conn() as c:
        lease = await claim(c, cafe_id, conversation_id)
    if lease is None:
        yield None
        return

    async def renewer() -> None:
        while True:
            await asyncio.sleep(RENEW_SECONDS)
            async with db_conn() as rc:
                if not await renew(rc, lease):
                    log.warning("lease_lost", conversation_id=conversation_id)
                    return

    task = asyncio.create_task(renewer())
    try:
        yield lease
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        async with db_conn() as c:
            await release(c, lease)
