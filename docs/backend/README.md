# Backend docs

> **Area:** Backend
> **Source:** MVP Architecture v0.1 §6.1–§6.4, §6.6–§6.7, §6.9–§6.10, §6.15–§6.17 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [`/CLAUDE.md`](../../CLAUDE.md), [`backend/CLAUDE.md`](../../backend/CLAUDE.md), [`backend/app/domain/CLAUDE.md`](../../backend/app/domain/CLAUDE.md)

## Summary

The backend is one Python 3.12 + FastAPI codebase deployed twice on Cloud Run: `api` (public: webhooks and panel REST) and `worker` (internal: conversation engine and jobs). This folder describes how that codebase is organised and how each domain area behaves. The AI agent has its own folder ([../ai-agent/](../ai-agent/README.md)); WhatsApp sending and webhooks live in [../whatsapp/](../whatsapp/README.md).

## Index

| Doc | One-liner |
|---|---|
| [module-structure.md](module-structure.md) | Folder layout of `backend/app`, what each module owns, and which way dependencies are allowed to point |
| [inbound-pipeline.md](inbound-pipeline.md) | Meta webhook → `api` → Pub/Sub `wa-inbound` → `worker` → reply; dedupe, retries, DLQ, burst coalescing |
| [conversation-router.md](conversation-router.md) | How each inbound message is routed: human mode, STOP, table QR, interactive IDs, stages, AI turn |
| [conversation-state-machine.md](conversation-state-machine.md) | The `stage` state machine and the separate `mode` (bot / human) overlay |
| [cart-and-confirmation.md](cart-and-confirmation.md) | Cart versioning, priced snapshot and the deterministic Confirm step that creates an order |
| [order-lifecycle.md](order-lifecycle.md) | `PLACED → PREPARING → READY → SERVED` / `CANCELLED`, who may change what, overdue flag and sweep |
| [human-handoff.md](human-handoff.md) | Call waiter, request bill, human mode, staff reply in chat, 60 s escalation, idle timeout |
| [feedback.md](feedback.md) | The 1–5 rating prompt after SERVED and how each rating is handled |
| [menu-management.md](menu-management.md) | Menu edits, CSV import, two-tap sold-out toggle and how it propagates to the AI and cart |
| [customers.md](customers.md) | Customer identity per café, greeting new vs welcome back, name capture, visits, profile |
| [background-jobs.md](background-jobs.md) | Every scheduled, queued and on-request job, its trigger and what it does |

## Which doc for which task

| If you are… | Start with | Then read |
|---|---|---|
| Adding a new module or wondering where code goes | [module-structure.md](module-structure.md) | [../adr/0007-one-codebase-two-services.md](../adr/0007-one-codebase-two-services.md) |
| Touching the webhook or Pub/Sub push handler | [inbound-pipeline.md](inbound-pipeline.md) | [../whatsapp/webhooks.md](../whatsapp/webhooks.md), [../adr/0002-ack-fast-process-async.md](../adr/0002-ack-fast-process-async.md), [../adr/0003-per-customer-ordering-and-locks.md](../adr/0003-per-customer-ordering-and-locks.md) |
| Adding a button, list row or keyword | [conversation-router.md](conversation-router.md) | [../whatsapp/interactive-messages.md](../whatsapp/interactive-messages.md) |
| Adding or changing a conversation stage | [conversation-state-machine.md](conversation-state-machine.md) | [conversation-router.md](conversation-router.md) |
| Changing totals, tax or the Confirm step | [cart-and-confirmation.md](cart-and-confirmation.md) | [../database/constraints-and-indexes.md](../database/constraints-and-indexes.md) |
| Adding an order status or transition | [order-lifecycle.md](order-lifecycle.md) | [../api/staff.md](../api/staff.md), [../frontend/staff-panel.md](../frontend/staff-panel.md) |
| Working on call waiter, bill or human mode | [human-handoff.md](human-handoff.md) | [../frontend/realtime-and-alerts.md](../frontend/realtime-and-alerts.md) |
| Working on ratings | [feedback.md](feedback.md) | [../retention/consent.md](../retention/consent.md) |
| Working on menu, sold-out or import | [menu-management.md](menu-management.md) | [../ai-agent/prompt-context.md](../ai-agent/prompt-context.md) |
| Working on greeting, names or customer profile | [customers.md](customers.md) | [../database/analytics-views.md](../database/analytics-views.md) |
| Adding a cron job or delayed task | [background-jobs.md](background-jobs.md) | [../infra/async-messaging.md](../infra/async-messaging.md) |
| Changing what the AI sees or may do | [../ai-agent/README.md](../ai-agent/README.md) | [../adr/0001-ai-talks-code-decides.md](../adr/0001-ai-talks-code-decides.md) |
| Sending any message to a customer | [../whatsapp/outbound-sender.md](../whatsapp/outbound-sender.md) | [../whatsapp/templates.md](../whatsapp/templates.md) |

## Rules / invariants

- Domain services don't know whether they run in `api` or `worker` (§6.1).
- Money, order state and consent are deterministic code paths reading the database; the AI only talks and recommends (§1, §5.3).
- Every message to a customer goes through the outbound service (§6.13).
- Every repository method takes `cafe_id` as a required argument (§6.14).
- Conversation and cart state live in Postgres, never in worker memory (§6.4).

## Open questions

Tracked centrally in [../delivery/open-decisions.md](../delivery/open-decisions.md). Per-area open questions are listed at the bottom of each doc above.

## Related

- [../architecture/container-view.md](../architecture/container-view.md) · [../architecture/end-to-end-flow.md](../architecture/end-to-end-flow.md) · [../architecture/design-principles.md](../architecture/design-principles.md)
- [../api/README.md](../api/README.md) · [../database/README.md](../database/README.md) · [../ai-agent/README.md](../ai-agent/README.md)
- Context file: [`backend/CLAUDE.md`](../../backend/CLAUDE.md)
