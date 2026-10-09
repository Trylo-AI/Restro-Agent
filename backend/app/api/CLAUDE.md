# app/api — Module Context (L2)

> Parent: [backend/CLAUDE.md](../../CLAUDE.md) · Root: [/CLAUDE.md](../../../CLAUDE.md)
> Docs: [docs/api/](../../../docs/api/README.md) · [roles-and-permissions](../../../docs/product/roles-and-permissions.md)

## Responsibility

Thin HTTP routers: authenticate → validate input → call **one** domain service → map domain errors to HTTP. No SQL and no business rules here.

```text
api/
├── webhooks.py   GET verify (hub.verify_token) · POST events · Meta data-deletion callback   [api service]
├── staff/        orders, waiter requests, conversations (chat/takeover), menu availability, customers, redeem
├── admin/        menu, tables + QR, staff, settings, templates, campaigns, customers, consents, analytics, reports
├── platform/     cafés, templates, health, costs, conversations review, audit
└── internal/     pubsub inbound push, tasks (campaign-send, feedback-prompt), jobs/{job_name}   [worker service]
```

## Route group → auth → doc

| Group | Service | Auth | Min role | Doc |
|---|---|---|---|---|
| `/webhooks/*` | api | `X-Hub-Signature-256` / verify token / signed request | — | [public.md](../../../docs/api/public.md) |
| `/staff/*` | api | Supabase JWT + `X-Cafe-Id` | `staff` (cancel: `manager`) | [staff.md](../../../docs/api/staff.md) |
| `/admin/*` | api | Supabase JWT + `X-Cafe-Id` | `owner` (menu/analytics: `manager`) | [admin.md](../../../docs/api/admin.md) |
| `/platform/*` | api | Supabase JWT | `platform_admin` | [platform.md](../../../docs/api/platform.md) |
| `/internal/*` | worker | OIDC from `invoker-sa` | — | [internal.md](../../../docs/api/internal.md) |

## Must

- The webhook POST does only this: verify, insert `webhook_events`, publish with ordering key `{phone_number_id}:{wa_id}`, return `200`. Target under 1 s.
- `/internal/*` handlers are idempotent. Pub/Sub and Tasks will redeliver. Return 2xx only once the work is durably done or deliberately skipped.
- State-changing staff/admin/platform calls write `audit_log`, through the domain service.
- Order status changes go through `PATCH /staff/orders/{id}/status`, which validates the transition and sends the customer message.

## Must never

- Mount `internal/*` on the `api` service, or public routers on the `worker`.
- Accept `cafe_id`, `role`, prices or totals from the client as truth.
- Call the LLM or the WhatsApp client directly from a router.
