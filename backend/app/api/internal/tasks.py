from typing import Any

from fastapi import Body

from app.api.internal import router
from app.core.logging import get_logger
from app.db.repositories import cafes, conversations, customers
from app.db.tx import conn
from app.domain import timers
from app.integrations.whatsapp import client

log = get_logger(__name__)


@router.post("/tasks/timer")
async def timer(body: dict[str, Any] = Body(...)) -> dict[str, str]:
    # 2xx only when the work is done or deliberately skipped; an exception makes the queue retry.
    await timers.dispatch(body)
    return {"status": "ok"}


@router.post("/tasks/ack")
async def ack(body: dict[str, Any] = Body(...)) -> dict[str, str]:
    """Mark read + typing right after the webhook, so the customer sees activity within a second."""
    cafe_id, conversation_id, wamid = str(body["cafe_id"]), str(body["conversation_id"]), str(body["wamid"])
    async with conn() as c:
        cafe = await cafes.get(c, cafe_id)
        convo = await conversations.get(c, cafe_id, conversation_id)
        customer = await customers.get(c, cafe_id, convo.customer_id) if convo else None
    if cafe is None or customer is None or cafe.status == "disconnected":
        return {"status": "skipped"}
    try:
        await client.transport().mark_read(cafe, wamid, customer.wa_id)
    except Exception as exc:  # best effort; never worth a retry storm
        log.warning("ack_failed", cafe_id=cafe_id, error_class=type(exc).__name__)
    return {"status": "ok"}
