# Restaurant Agentic System — Project Context (L0)

> **Always loaded.** This is the top of the context tree. It holds only what every task needs: what we're building, the rules nobody may break, and where to look next.
> Deeper context loads automatically from the `CLAUDE.md` in whichever folder you work in (L1/L2), and the full design lives in [`docs/`](docs/README.md) (L3). See [docs/CONTEXT-MAP.md](docs/CONTEXT-MAP.md).

## 1. What this is

A WhatsApp ordering and retention system for cafés (MVP). Two jobs:

1. **Ordering (the hook).** A customer scans a table QR, chats with an AI agent on WhatsApp, orders, gets status updates and can call a waiter.
2. **Retention (the value).** Every customer who orders becomes an opted-in WhatsApp contact the café owns. The owner sends an offer on a slow day and sees who came back and what they spent.

**Reliability beats features.** One lost or wrong order costs more trust than ten missing features.

**Status:** design phase. The repo has folders and docs only, no code yet. Architecture v0.1 (2026-10-05), owner: Aman.

## 2. Stack (one line each)

| Layer | Choice |
|---|---|
| Backend | Python 3.12 + FastAPI. One codebase, two Cloud Run services: `api` (public) and `worker` (internal) |
| Frontend | React + Vite + TypeScript SPA (staff panel, owner console, platform admin) on Firebase Hosting |
| Data | Supabase: Postgres + RLS, Auth (staff only), Realtime, Storage |
| Async | GCP Pub/Sub (`wa-inbound` + DLQ), Cloud Tasks, Cloud Scheduler |
| Secrets | GCP Secret Manager |
| Messaging | Meta WhatsApp Cloud API, direct |
| LLM | **Open decision**: see [docs/ai-agent/llm-provider.md](docs/ai-agent/llm-provider.md) |
| Region | GCP `asia-south1` + Supabase Mumbai |

## 3. Non-negotiable invariants

These apply everywhere. Area context files add their own, but never relax these.

1. **AI talks, code decides.** The AI picks words and suggests dishes. Prices, availability, totals, order creation, status changes and consent are deterministic code reading the DB. The AI never states a price.
2. **The menu DB is the only source of truth.** The AI sees only `is_available = true` items. Dish names and prices shown to customers are rendered by code from the DB.
3. **Acknowledge fast, process later.** The webhook verifies the signature, stores the raw event, publishes to Pub/Sub and returns `200`. All real work runs in the worker.
4. **One message at a time per customer.** Pub/Sub ordering key `{phone_number_id}:{wa_id}` plus a Postgres advisory lock per conversation.
5. **Tenant comes from the infrastructure, never from input.** Inbound: `cafe_id` from `phone_number_id`. Panel: from `staff_memberships` behind the JWT. Every repository method requires `cafe_id`.
6. **Every write goes through FastAPI.** The SPA uses Supabase directly only for login and Realtime reads. No RLS write policies.
7. **Idempotent everything.** `wamid` is unique, there's one order per `(cart_id, cart_version)`, and message status only moves forward.
8. **All outbound goes through one sender**, which enforces the 24-hour window and marketing consent.
9. **A human is always one tap away.** Any AI failure, low-confidence turn or explicit request reaches staff within a minute.
10. **Secrets never leave Secret Manager and the backend.** The DB stores only references, and logs hold no tokens or phone numbers.

## 4. Repository map

```text
Restro-Agent/
├── CLAUDE.md                  ← you are here (L0)
├── docs/                      ← L3 reference: full design, split by area
├── backend/      CLAUDE.md    ← L1: FastAPI api + worker
│   ├── app/core/       CLAUDE.md   auth, signature, OIDC, config
│   ├── app/api/        CLAUDE.md   routers: webhooks, staff, admin, platform, internal
│   ├── app/domain/     CLAUDE.md   business logic (L2 files per risky module)
│   ├── app/integrations/whatsapp/  CLAUDE.md
│   ├── app/db/         CLAUDE.md   repositories, models, session
│   └── tests/          CLAUDE.md
├── frontend/     CLAUDE.md    ← L1: React SPA
│   └── src/features/   CLAUDE.md
├── supabase/     CLAUDE.md    ← L1: migrations, RLS, views, seed
├── infra/        CLAUDE.md    ← L1: GCP resources per environment
└── .github/workflows/         CI/CD
```

## 5. Where to look (task → context)

| If you're working on… | Read first | Then |
|---|---|---|
| Webhook, Meta payloads, sending messages | [backend/app/integrations/whatsapp/CLAUDE.md](backend/app/integrations/whatsapp/CLAUDE.md) | [docs/whatsapp/](docs/whatsapp/README.md) |
| Conversation flow, buttons, stages | [backend/app/domain/conversation/CLAUDE.md](backend/app/domain/conversation/CLAUDE.md) | [docs/backend/conversation-router.md](docs/backend/conversation-router.md) |
| AI prompts, tools, guardrails | [backend/app/domain/ai_agent/CLAUDE.md](backend/app/domain/ai_agent/CLAUDE.md) | [docs/ai-agent/](docs/ai-agent/README.md) |
| Cart, confirmation, orders | [cart/CLAUDE.md](backend/app/domain/cart/CLAUDE.md) · [orders/CLAUDE.md](backend/app/domain/orders/CLAUDE.md) | [docs/backend/cart-and-confirmation.md](docs/backend/cart-and-confirmation.md) |
| Consent, campaigns, redemption | [consent/CLAUDE.md](backend/app/domain/consent/CLAUDE.md) · [campaigns/CLAUDE.md](backend/app/domain/campaigns/CLAUDE.md) | [docs/retention/](docs/retention/README.md) |
| REST endpoints, auth, RBAC | [backend/app/api/CLAUDE.md](backend/app/api/CLAUDE.md) | [docs/api/](docs/api/README.md) · [docs/security/](docs/security/README.md) |
| Schema, RLS, views | [supabase/CLAUDE.md](supabase/CLAUDE.md) | [docs/database/](docs/database/README.md) |
| Staff panel / owner console / platform admin | [frontend/CLAUDE.md](frontend/CLAUDE.md) | [docs/frontend/](docs/frontend/README.md) |
| Cloud Run, Pub/Sub, Tasks, IAM, CI | [infra/CLAUDE.md](infra/CLAUDE.md) | [docs/infra/](docs/infra/README.md) |
| Logs, alerts, cost | — | [docs/observability/](docs/observability/README.md) |
| Scope, priorities, roles | — | [docs/product/](docs/product/README.md) · [docs/delivery/](docs/delivery/README.md) |

## 6. Roles (short)

`staff` ⊂ `manager` ⊂ `owner` within one café, and `platform_admin` (us) works across cafés with every action audited. Customers never log in: their identity is their `wa_id`. Full matrix: [docs/product/roles-and-permissions.md](docs/product/roles-and-permissions.md).

## 7. Out of scope (MVP)

Payment gateway and refunds, bookings and pre-orders, loyalty tiers, POS/printer integration, multi-location, Embedded Signup, full BI dashboards. Don't build these without an ADR.

## 8. Working rules for agents

- **Docs first.** Before changing behaviour, read the linked doc. After changing it, update the doc, and the `CLAUDE.md` too if an invariant moved. Real trade-offs get an ADR in [docs/adr/](docs/adr/README.md).
- **Don't edit** [docs/_source/](docs/_source/mvp-architecture-v0.1.md). It's the frozen v0.1 snapshot.
- Use the exact names from the docs (tables, endpoints, jobs, queues, templates). Shared vocabulary is in [docs/product/glossary.md](docs/product/glossary.md).
- Open decisions live in [docs/delivery/open-decisions.md](docs/delivery/open-decisions.md). Don't silently decide one in code. Ask, or write an ADR.
- Build order: [docs/delivery/build-order.md](docs/delivery/build-order.md) (step 0 Foundations → step 6 Pilot hardening).
