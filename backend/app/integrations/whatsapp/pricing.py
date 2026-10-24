"""WhatsApp cost estimate per billable message (docs/architecture.md#cost). Meta gives the category, not the amount.

India prices per message by category, **[verify]** against Meta's current rate card before relying on them.
Service conversations (replies inside the 24-hour window) are free.
"""

PRICING_VERSION = "meta-in-2025-07-verify"
CURRENCY = "INR"
PER_MESSAGE = {"marketing": 0.7846, "utility": 0.1150, "authentication": 0.1150, "service": 0.0}


def estimate(category: str | None, billable: bool | None) -> tuple[float | None, str | None, str | None]:
    if category is None:
        return None, None, None
    cost = PER_MESSAGE.get(category, 0.0) if billable else 0.0
    return cost, CURRENCY, PRICING_VERSION
