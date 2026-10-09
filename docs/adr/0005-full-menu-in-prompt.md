# ADR 0005: Full available menu in the prompt

> **Area:** AI agent
> **Source:** MVP Architecture v0.1 §5.3 (principle 4), §6.5, §6.15, §14 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Accepted · **Date:** 2026-10-05 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/app/domain/ai_agent/CLAUDE.md](../../backend/app/domain/ai_agent/CLAUDE.md)

## Summary

Each AI turn gets the café's whole available menu as compact JSON instead of retrieving dishes through a `search_menu` tool and pgvector.

## Context

Cafés typically have tens to low hundreds of items, so the whole available menu fits in the prompt. Search adds a retrieval step that can miss relevant dishes, plus an embedding pipeline to keep in sync with sold-out toggles.

## Decision

- The prompt builder reads `is_available = true` items on every turn (no long-lived cache) and includes id, name, category, veg flag, spice level, tags, protein/calories if present, allergens and short description.
- `search_menu(query, veg?, max_spice?, tags?)` is defined but used only for large menus.
- Switch to `search_menu` with pgvector only when the menu exceeds a token budget.
- If a menu cache is ever added, it must be invalidated on every sold-out toggle.

## Consequences

- **Good:** more reliable than search for small menus; sold-out takes effect on the next AI turn; no vector index to maintain.
- **Costs:** more input tokens per turn, so higher AI cost per order; does not scale to very large menus.
- **Follow-ups:** track tokens per turn in `ai_usage`; define the token budget that triggers the switch.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| `search_menu` tool with pgvector from day one | Less reliable for small menus; extra pipeline to keep in sync with availability |
| Cached menu in the prompt | Sold-out items could be recommended until the cache refreshes |

## Status

Accepted. The "Menu size" topic stays open in [open-decisions.md](../delivery/open-decisions.md) for the token-budget threshold.

## Date

2026-10-05

## Related

- [../ai-agent/prompt-context.md](../ai-agent/prompt-context.md), [../ai-agent/tools.md](../ai-agent/tools.md)
- [../backend/menu-management.md](../backend/menu-management.md)
- [../observability/cost-tracking.md](../observability/cost-tracking.md)
