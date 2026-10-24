"""Token prices per model, USD per 1M tokens: (input, cached input, output). **[verify]** against the Gemini
pricing page before relying on them; the cost model is refreshed from pilot data."""

PRICING_VERSION = "gemini-2025-paid-tier-verify"
CURRENCY = "USD"

PRICES: dict[str, tuple[float, float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.025, 0.40),
    "gemini-2.5-flash": (0.30, 0.075, 2.50),
    # gemini-3.5-flash / -lite: not in this table yet; DEFAULT (Flash rates) applies until verified.
}
DEFAULT = (0.30, 0.075, 2.50)  # unknown model: price as Flash so costs are never under-reported


def cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    price_in, price_cached, price_out = PRICES.get(model, DEFAULT)
    uncached = max(input_tokens - cached_tokens, 0)
    return round((uncached * price_in + cached_tokens * price_cached + output_tokens * price_out) / 1_000_000, 6)
