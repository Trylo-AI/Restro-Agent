# ADR 0001: AI talks, code decides

> **Area:** AI agent
> **Source:** MVP Architecture v0.1 §1, §5.3 (principle 3), §6.5, §6.6 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Accepted · **Date:** 2026-10-05 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/app/domain/ai_agent/CLAUDE.md](../../backend/app/domain/ai_agent/CLAUDE.md), [backend/app/domain/cart/CLAUDE.md](../../backend/app/domain/cart/CLAUDE.md), [backend/app/domain/orders/CLAUDE.md](../../backend/app/domain/orders/CLAUDE.md)

## Summary

The AI handles conversation and recommendation only. Everything involving money, order state or consent is deterministic code reading the database.

## Context

One lost or wrong order costs more trust than ten missing features. LLMs can hallucinate dish names and prices, misread intent and be steered by prompt injection. The pilot café runs live on busy evenings without us in the kitchen.

## Decision

- The AI chooses what to say and which dishes to suggest, and returns structured output: `reply_text`, `suggested_item_ids`, `intent`.
- Prices, availability, totals, order creation, status changes and consent are fixed code paths.
- The AI's tools are scoped to the current conversation. Its only route to an order is `request_confirmation()`, which hands off to the deterministic confirmation step.
- Suggested dishes are rendered by code from the database as a WhatsApp list; guardrails reject unknown or unavailable ids and currency amounts in free text.

## Consequences

- **Good:** a hallucinated name or price cannot reach the customer; confirmation is testable without an LLM; prompt injection cannot create orders or send templates.
- **Costs:** more code paths to build (router, cart service, confirmation); the AI cannot "just handle" edge cases such as price questions.
- **Follow-ups:** guardrail and tool tests in `backend/tests/unit`; scripted conversations in `backend/tests/conversations`.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Let the AI quote prices and create orders through a tool | One wrong total or phantom order breaks the café's trust |
| AI renders the dish list itself | Hallucinated names or prices could reach the customer |

## Status

Accepted

## Date

2026-10-05

## Related

- [../architecture/design-principles.md](../architecture/design-principles.md)
- [../ai-agent/tools.md](../ai-agent/tools.md), [../ai-agent/guardrails.md](../ai-agent/guardrails.md)
- [../backend/cart-and-confirmation.md](../backend/cart-and-confirmation.md)
