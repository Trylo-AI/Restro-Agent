# app/db — Module Context (L2)

> Parent: [backend/CLAUDE.md](../../CLAUDE.md) · Root: [/CLAUDE.md](../../../CLAUDE.md) · Schema owner: [supabase/CLAUDE.md](../../../supabase/CLAUDE.md)
> Docs: [data-model](../../../docs/database/data-model.md) · [constraints-and-indexes](../../../docs/database/constraints-and-indexes.md) · [row-level-security](../../../docs/database/row-level-security.md)

## Responsibility

```text
db/
├── session.py      async connection pool (asyncpg) via the Supavisor pooler, transaction helpers, advisory locks
├── repositories/   one repository per aggregate; the only place SQL lives
└── models/         typed row models kept in sync with supabase/migrations
```

## Must

- **Every repository method takes `cafe_id` as a required argument** and filters by it. The one exception is the tenant lookup by `phone_number_id`.
- Use DB constraints for idempotency rather than read-then-write checks: `INSERT … ON CONFLICT DO NOTHING` on `wamid`, and `UNIQUE (cart_id, cart_version)` on orders.
- Run order creation (orders + order_items + order_status_events) in **one transaction**.
- Take the conversation lock with a transaction-scoped advisory lock keyed on the conversation.
- Make status updates forward-only (a conditional update on the current status).
- Keep models in step with migrations. The migrations are the truth.

## Must never

- Create tables, indexes or policies from application code. That's migrations only.
- Use the service-role key anywhere but the backend. Never ship it to the frontend.
- Return rows across cafés, except in `platform_admin` repositories, which are explicitly named and audited.
