"""Customer message for a staff cancellation (docs/backend.md#transitions).

The model drafts an apology in the café's tone from the reason CODE only (never `reason_text`); code adds the
order number and the Call waiter button. Amounts, refund or compensation wording, or any LLM failure → the
fixed fallback.
"""

from app.db.repositories import cafes, customers
from app.db.repositories.orders import Order
from app.db.tx import conn
from app.domain.ai_agent import calls, guardrails
from app.domain.conversation.render import CALL_WAITER
from app.integrations.whatsapp import messages as m

REASONS = {
    "out_of_stock": "an item ran out",
    "kitchen_issue": "a problem in the kitchen",
    "customer_asked": "the customer asked us to cancel",
    "other": "something came up at the café",
}

SYSTEM = """You write one short WhatsApp message (max 2 sentences) from {cafe} to a customer whose order was cancelled.
Apologise, say clearly the order is cancelled, and say they can tap Call waiter for help. Tone: {tone}.
Never mention prices, money, refunds, compensation, discounts or anything free. Don't invent details.
Return JSON {{"reply": "..."}}."""


def fallback(order: Order) -> m.Buttons:
    return m.Buttons(f"Sorry, order #{order.number} has been cancelled.", [CALL_WAITER])


async def message(cafe_id: str, order: Order) -> m.Buttons:
    if calls.kill_switch() == "handoff":
        return fallback(order)
    async with conn() as c:
        cafe = await cafes.get(c, cafe_id)
        customer = await customers.get(c, cafe_id, order.customer_id) if order.customer_id else None
    if cafe is None:
        return fallback(order)
    first = (customer.name or "").split(" ")[0] if customer else ""
    reason = REASONS.get(order.cancel_reason_code or "", "something came up at the café")
    attempted = await calls.call(
        cafe_id=cafe_id, cafe_model=cafe.settings.get("model"), purpose="cancel_draft",
        system=SYSTEM.format(cafe=cafe.name, tone=cafe.settings.get("tone") or "warm"),
        prompt=f"Customer first name: {first or 'unknown'}\nReason: {reason}", schema_name="cancel_draft",
        max_output_tokens=150, order_id=order.id,
    )
    reply = (attempted.result.data or {}).get("reply") if attempted else None
    if not isinstance(reply, str) or guardrails.has_refund_promise(reply):
        return fallback(order)
    cleaned = guardrails.clean_reply(reply, 400)
    if cleaned is None:
        return fallback(order)
    return m.Buttons(f"{cleaned}\nOrder #{order.number}", [CALL_WAITER])
