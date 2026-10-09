# Backend — Area Context (L1)

> Loaded when working under `backend/`. Inherits every invariant in [/CLAUDE.md](../CLAUDE.md).
> Full design: [docs/backend/](../docs/backend/README.md) · [docs/ai-agent/](../docs/ai-agent/README.md) · [docs/whatsapp/](../docs/whatsapp/README.md) · [docs/api/](../docs/api/README.md)

## What lives here

One Python 3.12 + FastAPI codebase, **deployed twice** on Cloud Run:

| Service | Entry point (planned) | Mounts | Ingress |
|---|---|---|---|
| `api` | `app/main_api.py` | `webhooks`, `staff/*`, `admin/*`, `platform/*` | Public, `min-instances=1` in prod |
| `worker` | `app/main_worker.py` | `internal/*` (Pub/Sub push, Cloud Tasks, Scheduler) | Internal only, OIDC from `invoker-sa` |

Domain services **don't know which service they run in**.

## Layout and dependency direction

```text
app/
├── core/          config, logging, JWT/JWKS auth + RBAC, webhook signature, internal OIDC   → core/CLAUDE.md
├── api/           thin routers: validate → call domain → map errors                        → api/CLAUDE.md
├── domain/        all business rules (conversation, ai_agent, menu, cart, orders, customers,
│                  handoff, consent, campaigns, feedback, analytics, outbound, audit)       → domain/CLAUDE.md
├── integrations/  whatsapp (client, parser, builders, error codes), llm (adapter), gcp     → integrations/whatsapp/CLAUDE.md
└── db/            session (asyncpg via Supavisor), repositories, models                    → db/CLAUDE.md
tests/             unit · integration · conversations · fixtures                            → tests/CLAUDE.md
```

Allowed direction: `api → domain → (integrations | db)`, and `core` may be used by anyone.

- Routers hold **no business logic**.
- Domain modules call each other only through their public service functions.
- Integrations hold **no business rules**: they're thin, typed adapters.
- **Only `domain/outbound` calls `integrations/whatsapp` to send.** Nothing else sends messages.

Detail: [docs/backend/module-structure.md](../docs/backend/module-structure.md).

## Backend invariants

1. Webhook handler: verify HMAC on the **raw** body → store `webhook_events` → publish to `wa-inbound` → `200` in under 1 s. No DB-heavy work, no LLM calls. → [webhooks](../docs/whatsapp/webhooks.md)
2. Worker processing per inbound message: dedupe on `wamid` → resolve café by `phone_number_id` → upsert customer → `pg_advisory_xact_lock(conversation)` → handle. → [inbound-pipeline](../docs/backend/inbound-pipeline.md)
3. Conversation and cart state live in Postgres, never in process memory, so any instance can take the next message.
4. Money, order state and consent are deterministic code paths. The LLM is reached only via `domain/ai_agent`.
5. Every repository method takes `cafe_id` as a required argument.
6. Every state change made by staff writes `audit_log`. Every order transition writes `order_status_events`.
7. Timeouts: AI turn about 12 s, then a fixed fallback with buttons. Outbound retries 429/5xx at most 3 times with backoff.
8. Logs are structured JSON with `cafe_id`, `conversation_id`, `wamid`, `order_id` and the request ID. Never log tokens or full phone numbers. → [logging](../docs/observability/logging-and-metrics.md)

## Task → doc

| Task | Doc |
|---|---|
| Inbound pipeline, retries, DLQ | [inbound-pipeline.md](../docs/backend/inbound-pipeline.md) |
| Routing a message, button IDs | [conversation-router.md](../docs/backend/conversation-router.md) |
| Stages and bot/human mode | [conversation-state-machine.md](../docs/backend/conversation-state-machine.md) |
| Cart, confirm, order create | [cart-and-confirmation.md](../docs/backend/cart-and-confirmation.md) |
| Order statuses and overdue | [order-lifecycle.md](../docs/backend/order-lifecycle.md) |
| Call waiter, bill, human mode | [human-handoff.md](../docs/backend/human-handoff.md) |
| Feedback | [feedback.md](../docs/backend/feedback.md) |
| Menu and sold-out | [menu-management.md](../docs/backend/menu-management.md) |
| Customers, greeting, reorder | [customers.md](../docs/backend/customers.md) |
| Scheduled jobs | [background-jobs.md](../docs/backend/background-jobs.md) |
| Endpoints | [docs/api/](../docs/api/README.md) |
| Tables | [docs/database/data-model.md](../docs/database/data-model.md) |

## Not decided yet (don't pick silently)

LLM provider · GST inclusive/exclusive · burst coalescing (P2). Tracked in [open-decisions.md](../docs/delivery/open-decisions.md).
