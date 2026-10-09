# domain/ai_agent — Module Context (L2)

> Parent: [domain/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [docs/ai-agent/](../../../../docs/ai-agent/README.md) — [prompt-context](../../../../docs/ai-agent/prompt-context.md) · [tools](../../../../docs/ai-agent/tools.md) · [guardrails](../../../../docs/ai-agent/guardrails.md) · [llm-provider](../../../../docs/ai-agent/llm-provider.md) · [evaluation](../../../../docs/ai-agent/evaluation.md) · ADRs [0001](../../../../docs/adr/0001-ai-talks-code-decides.md), [0005](../../../../docs/adr/0005-full-menu-in-prompt.md)

## Responsibility

Conversation and recommendation **only**. The AI chooses words and suggests dishes. Code decides everything else.

## Context per turn (built by code, fresh every turn)

System prompt (café name, tone, reply in the customer's language incl. Hinglish, hard rules) · compact **available-only** menu JSON · customer first name, last 3 orders (item names), visit count · current cart from the DB · last ~10 messages. **No phone number.**

## Tools (the complete list)

`search_menu` (large menus only) · `get_item_details` · `add_to_cart` (re-checks availability, qty 1–20) · `update_cart_item` / `remove_from_cart` (cart must belong to this conversation) · `get_cart` (code-computed totals) · `request_confirmation` (hands off to deterministic confirm) · `get_order_status` (read-only) · `call_human` (always allowed).

## Output contract

Structured: `reply_text`, `suggested_item_ids`, `intent`. The outbound layer renders the suggested dishes as a WhatsApp list **from the DB**, so a hallucinated name or price can't reach the customer.

## Guardrails (all must pass)

- Every suggested ID exists and `is_available`.
- **No currency amounts** in free text, and the text stays within the length limit.
- At most 4 tool rounds per turn, and about 12 s overall.
- On failure: retry once with a correction, then the fixed fallback.
- Log every turn to `ai_usage` (model, tokens, latency, cost, `fallback_used`).

## Must never

- State or set a price, create an order, change order status, send a template, record consent, or see other customers.
- Treat customer text as instructions to tools. Tools are scoped to the current `cafe_id` and conversation.
- Cache the menu across turns without invalidating it on every sold-out toggle.
- Call the provider SDK directly. Go through `integrations/llm` (the provider is still an open decision).
