"""Customer message for a staff cancellation (docs/backend.md#transitions).

S8: the fixed fallback. S9 adds the AI-drafted apology (reason code only, never reason_text) with guardrails.
"""

from app.db.repositories.orders import Order
from app.domain.conversation.render import CALL_WAITER
from app.integrations.whatsapp import messages as m


def fallback(order: Order) -> m.Buttons:
    return m.Buttons(f"Sorry, order #{order.number} has been cancelled.", [CALL_WAITER])


async def message(cafe_id: str, order: Order) -> m.Buttons:
    return fallback(order)
