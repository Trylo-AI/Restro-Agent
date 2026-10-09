# Context map

> **Area:** Meta · **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../CLAUDE.md) and every other `CLAUDE.md`

## Summary

Context is layered so that an engineer or an AI coding agent working on one area loads only what that area needs. `CLAUDE.md` files hold the **rules and pointers** and stay short. `docs/` holds the **full design** and is read on demand. Claude Code loads the root `CLAUDE.md` at session start, and loads a folder's `CLAUDE.md` when it reads or edits files in that folder.

## Levels

| Level | Where | Loaded | Holds | Size target |
|---|---|---|---|---|
| **L0 — Project** | [`/CLAUDE.md`](../CLAUDE.md) | Always | Mission, stack, the 10 global invariants, repo map, task → doc router | ≤ 150 lines |
| **L1 — Area** | `backend/`, `frontend/`, `supabase/`, `infra/` `CLAUDE.md` | When working in that area | Area invariants, folder layout, boundaries, doc links | ≤ 100 lines |
| **L2 — Module** | e.g. `backend/app/domain/cart/CLAUDE.md` | When working in that module | Module contract, must / must-never, edge cases, tests to keep green | ≤ 60 lines |
| **L3 — Reference** | `docs/**` | On demand, via links | Flows, diagrams, schema, API tables, decisions | ~250 lines per file |
| **Frozen** | [`docs/_source/`](_source/mvp-architecture-v0.1.md) | Never edited | The original v0.1 architecture, for traceability | — |

## Context tree

The tree below shows which context file points at which docs.

```mermaid
flowchart TB
    L0["/CLAUDE.md<br/>L0 project"]

    subgraph L1["L1 — area"]
        BE["backend/CLAUDE.md"]
        FE["frontend/CLAUDE.md"]
        SB["supabase/CLAUDE.md"]
        IN["infra/CLAUDE.md"]
    end

    subgraph L2B["L2 — backend modules"]
        CORE["app/core"]
        API["app/api"]
        DOM["app/domain"]
        WA["app/integrations/whatsapp"]
        DB["app/db"]
        TST["tests"]
    end

    subgraph L2D["L2 — domain modules"]
        CONV["conversation"]
        AI["ai_agent"]
        CART["cart"]
        ORD["orders"]
        OUT["outbound"]
        CON["consent"]
        CAMP["campaigns"]
    end

    FEAT["frontend/src/features/CLAUDE.md"]

    subgraph L3["L3 — docs/"]
        D1["product · architecture · delivery · adr"]
        D2["backend · ai-agent"]
        D3["whatsapp · retention"]
        D4["frontend · api"]
        D5["database · infra · security · observability"]
    end

    L0 --> BE
    L0 --> FE
    L0 --> SB
    L0 --> IN
    BE --> CORE
    BE --> API
    BE --> DOM
    BE --> WA
    BE --> DB
    BE --> TST
    DOM --> CONV
    DOM --> AI
    DOM --> CART
    DOM --> ORD
    DOM --> OUT
    DOM --> CON
    DOM --> CAMP
    FE --> FEAT

    L0 -.-> D1
    BE -.-> D2
    WA -.-> D3
    OUT -.-> D3
    CON -.-> D3
    CAMP -.-> D3
    FE -.-> D4
    API -.-> D4
    SB -.-> D5
    IN -.-> D5
    CORE -.-> D5
```

Solid arrows mean the parent context file links to the child context file. Dotted arrows mean the context file links into those doc folders.

## How a task resolves context

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Engineer / agent
    participant L0 as /CLAUDE.md
    participant L1 as backend/CLAUDE.md
    participant L2 as domain/cart/CLAUDE.md
    participant L3 as docs/backend/cart-and-confirmation.md

    Dev->>L0: start session (auto-loaded)
    L0-->>Dev: invariants + "cart work → domain/cart"
    Dev->>L1: open a file under backend/ (auto-loaded)
    L1-->>Dev: backend layering + module boundaries
    Dev->>L2: open a file under domain/cart/ (auto-loaded)
    L2-->>Dev: cart contract, versioning rule, tests to keep green
    Dev->>L3: follow link when detail is needed
    L3-->>Dev: full confirmation sequence, edge cases, schema links
```

## Which file states which rule

| Rule | Stated in | Detailed in |
|---|---|---|
| AI talks, code decides | L0 §3, [ai_agent](../backend/app/domain/ai_agent/CLAUDE.md) | [ai-agent/guardrails.md](ai-agent/guardrails.md), [adr/0001](adr/0001-ai-talks-code-decides.md) |
| Ack fast, process later | L0 §3, [api](../backend/app/api/CLAUDE.md), [whatsapp](../backend/app/integrations/whatsapp/CLAUDE.md) | [whatsapp/webhooks.md](whatsapp/webhooks.md), [adr/0002](adr/0002-ack-fast-process-async.md) |
| Per-customer ordering + lock | L0 §3, [conversation](../backend/app/domain/conversation/CLAUDE.md) | [backend/inbound-pipeline.md](backend/inbound-pipeline.md), [adr/0003](adr/0003-per-customer-ordering-and-locks.md) |
| Tenant from infrastructure | L0 §3, [db](../backend/app/db/CLAUDE.md), [core](../backend/app/core/CLAUDE.md) | [security/auth-and-tenant-isolation.md](security/auth-and-tenant-isolation.md), [adr/0006](adr/0006-tenant-from-infrastructure.md) |
| Writes only via FastAPI | L0 §3, [frontend](../frontend/CLAUDE.md), [supabase](../supabase/CLAUDE.md) | [database/row-level-security.md](database/row-level-security.md), [adr/0004](adr/0004-all-writes-through-fastapi.md) |
| One outbound sender, 24h window | L0 §3, [outbound](../backend/app/domain/outbound/CLAUDE.md) | [whatsapp/outbound-sender.md](whatsapp/outbound-sender.md) |
| Cart versioning / idempotent confirm | [cart](../backend/app/domain/cart/CLAUDE.md), [orders](../backend/app/domain/orders/CLAUDE.md) | [backend/cart-and-confirmation.md](backend/cart-and-confirmation.md) |
| Marketing needs OptedIn | [consent](../backend/app/domain/consent/CLAUDE.md), [campaigns](../backend/app/domain/campaigns/CLAUDE.md) | [retention/consent.md](retention/consent.md) |

## Rules for maintaining context

1. **Rules go up, detail goes down.** If something must never be broken, state it in the nearest `CLAUDE.md`. If it explains *how*, put it in `docs/`.
2. **Don't duplicate detail.** A `CLAUDE.md` links to a doc. It doesn't copy its tables or diagrams.
3. **Add an L2 file only for risky modules,** where a mistake costs money, orders, consent or tenant isolation. Simple modules inherit from L1.
4. **Keep it current.** A PR that changes behaviour updates the doc, and the context file too if an invariant changed. Reviewers check this.
