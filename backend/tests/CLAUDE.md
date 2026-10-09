# backend/tests — Module Context (L2)

> Parent: [backend/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../CLAUDE.md)
> Docs: [ai-agent/evaluation](../../docs/ai-agent/evaluation.md) · [delivery/build-order](../../docs/delivery/build-order.md) (step 6: pilot hardening)

## Layout

```text
tests/
├── unit/           cart totals and tax, order transitions, guardrails, 24h window guard, consent state, ID parsing
├── integration/    webhook signature, wamid dedupe, tenant isolation (cross-café attempts), idempotent confirm, RLS
├── conversations/  scripted end-to-end chats incl. Hinglish, confusing orders, sold-out mid-cart, stale buttons
└── fixtures/       Meta webhook payloads (message, status, template_update, quality_update, batches), seed menus
```

## Required coverage (these guard the invariants)

| Invariant | Test kind |
|---|---|
| Invalid or tampered signature → 401, nothing stored or published | integration |
| Same `wamid` twice → exactly one reply | integration |
| Double-tap Confirm / stale `confirm:{cart_id}:{version}` → one order, polite reject | integration |
| Sold-out item in cart → removed at confirm, customer told | conversations |
| A staff JWT for café A can't read or write café B | integration |
| Free text outside the 24h window is rejected. Marketing without OptedIn is rejected | unit |
| AI reply with a price or an unknown/unavailable item ID → retry, then fallback | unit |
| Illegal order transition (e.g. SERVED → PREPARING) is rejected | unit |
| STOP → OptedOut, then no campaign send | conversations |
| `/internal/*` without OIDC is rejected | integration |

## Conversation suite

Around 50 scripted conversations, also used to benchmark LLM providers. Track the fallback rate: the production alert fires above 15%/hour. See [evaluation.md](../../docs/ai-agent/evaluation.md).
