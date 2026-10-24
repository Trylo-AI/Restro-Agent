# Restro

WhatsApp ordering and retention for cafés (MVP). A customer scans a table QR, orders with an AI agent on WhatsApp, gets status updates and can call a person. Staff run service in the **Restro Console**. The owner sends offers to opted-in customers and sees who came back and what they spent.

Reliability beats features. Done = 30+ orders in one evening at a real café, zero lost or wrong.

**Status:** design phase — docs only, no code. Owner: Aman. Build order: [docs/plan.md](docs/plan.md#roadmap).

This is the only `CLAUDE.md`. All design lives in `docs/`.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 + FastAPI. One image, two Cloud Run services: `api` (public) and `worker` (internal) |
| Frontend | Restro Console: React + Vite + TypeScript SPA on Firebase Hosting |
| Data | Supabase: Postgres + RLS, Auth (console users only), Realtime, Storage |
| Async | Pub/Sub (wake-up signal) + Postgres work queue (`pending_work`) + Cloud Tasks (`wa-ack`, `timers`, `campaign-send`) + Cloud Scheduler |
| LLM | Gemini Flash-Lite / Flash on Vertex AI, global endpoint (no data-residency guarantee; accepted for the MVP). One structured call per free-text turn |
| Messaging | Meta WhatsApp Cloud API, direct |
| Secrets | GCP Secret Manager |
| Infra / CI | `gcloud` scripts (no Terraform); GitHub Actions → GCP via Workload Identity Federation (no keys) |
| Region | GCP `asia-south1` + Supabase Mumbai; Vertex AI global endpoint |

## Invariants

1. **AI talks, code decides.** The LLM interprets and drafts text. Prices, totals, availability, orders, status and consent are code reading the DB. The LLM never states a price.
2. **The menu DB is the only source of truth.** The LLM sees only `available = true` items. Names and prices shown to customers are rendered by code.
3. **Acknowledge fast, process later.** Webhook: verify signature → store inbound + `pending_work` → publish signal + enqueue `wa-ack` → `200`. Real work runs in `worker`.
4. **One turn at a time per conversation.** Recoverable Postgres work queue (`pending_work`) + conversation lease (`lease_owner`, `lease_expires_at`), 1 s debounce. Gemini calls and WhatsApp sends run outside short DB transactions. 3 retries, then dead letter → customer told how to reach a person.
5. **Tenant from infrastructure, never input.** Webhooks: `cafe_id` from `phone_number_id`. Console: from `memberships` behind the JWT. Every repository method requires `cafe_id`.
6. **Every write goes through FastAPI.** The console uses Supabase only for login and reads. No RLS write policies.
7. **Orders change only through `transition()`** with optimistic locking. Every change writes `order_events` and `audit_log`.
8. **Idempotent everything.** `wamid` unique; one order per Confirm tap (`orders.idempotency_key`); one `wa_messages` row per outbound step, written before the send; an ambiguous send is checked, never blindly resent; message status only moves forward; every timer re-checks state.
9. **Money is integer paise.** Never floats.
10. **All outbound goes through one sender** (`domain/outbound`), which enforces the 24-hour window and marketing consent.
11. **A human is always one tap away.** AI failure, unknown allergen, two failed clarifications or an explicit ask reaches staff within a minute; escalation to manager at 60 s, owner at +2 min.
12. **Secrets stay in Secret Manager and the backend.** The DB stores references only. Logs hold no tokens, phone numbers or message text (ids like `wamid` are fine).

## Repository

Planned layout; each folder is created with its first real file.

```text
backend/     FastAPI api + worker (app/core, api, domain, integrations, db; tests/)
frontend/    Restro Console (src/features/ one folder per screen)
supabase/    migrations, RLS, views, seed
contracts/   frozen interfaces: schema, OpenAPI, events, LLM schema, state machines
simulator/   local-only WhatsApp simulator (never deployed)
infra/       gcloud scripts per environment (no Terraform)
evals/       LLM eval set + runner
loadtest/    webhook simulator + scenarios
docs/        all design docs
.github/workflows/  CI and deploys (Workload Identity Federation, no keys)
```

Inputs, read-only: `Restro_Build_Plan.md` (build plan v1.0), `Restro Console v2-Updates.html` (console mock), `docs/_source/` (frozen v0.1 architecture).

## Docs

| Doc | Holds |
|---|---|
| [plan.md](docs/plan.md) | Product, roles, features and priority, roadmap, resources, work split, contracts, open decisions, risks |
| [architecture.md](docs/architecture.md) | System, message pipeline, queues / timers / jobs, backend layout, security and tenancy, data protection, infrastructure, monitoring, cost |
| [backend.md](docs/backend.md) | Conversation, AI agent, cart, orders, human help, menu, customers, WhatsApp, consent, campaigns |
| [data-and-api.md](docs/data-and-api.md) | Schema, RLS, analytics views, endpoints, errors, Realtime |
| [console.md](docs/console.md) | Shell, urgency and alerts, every screen |
| [rules.md](docs/rules.md) | Must / never per folder, required tests, evals, load tests, dry run |
| [decisions.md](docs/decisions.md) | Settled trade-offs |
| [ordering-slice.md](docs/ordering-slice.md) | Current build: simulator → AI order → shift manager console, local, mock data |
| [ordering-slice-checklist.md](docs/ordering-slice-checklist.md) | Progress tracker for the ordering slice |
| [OPS_PLAN.md](docs/OPS_PLAN.md) | Performance dashboard plan (tracing, read API, `/platform/ops` pages); awaiting approval |

## Roles

`shift_manager` ⊂ `manager` ⊂ `owner` within one café (`memberships`); `platform_admin` (us) works across cafés, every action audited. Customers never log in; identity is `wa_id`.

## Out of scope (MVP)

Payment gateway and refunds, bookings and pre-orders, voice notes, loyalty tiers, POS/printer integration, multi-location features (switching between cafés is fine; nothing is shared), Embedded Signup, full BI. Don't build these without a decision in [decisions.md](docs/decisions.md).

## Working rules

- **Docs first.** Read the relevant doc and its section in [rules.md](docs/rules.md) before changing code; update the doc in the same PR. A changed invariant updates this file. A real trade-off adds an entry to [decisions.md](docs/decisions.md).
- **Contracts are frozen.** Change `contracts/` only when the task says so, in one PR that updates backend and console together.
- **Critical core is the team's.** Don't change a critical-core module unless the task names the file and the change ([list](docs/plan.md#work-split)).
- **Exact names.** Use the table, column and status names in [data-and-api.md](docs/data-and-api.md#schema). Never invent synonyms.
- **Open means ask.** Undecided items are in [open decisions](docs/plan.md#open-decisions). Build against the default; never decide one silently.
- **Phase vs priority.** "Phase 0–7" = build phase; "P0–P3" = feature priority.
- **No real data.** No secrets, tokens or real customer data in code, fixtures or tests. Use the seed generator.
- Every task ends with tests and a short note listing assumptions.
- Don't edit `docs/_source/`, the build plan or the mock.
