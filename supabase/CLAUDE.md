# Supabase — Area Context (L1)

> Loaded when working under `supabase/`. Inherits every invariant in [/CLAUDE.md](../CLAUDE.md).
> Full design: [docs/database/](../docs/database/README.md)

## What lives here

```text
supabase/
├── migrations/   source of truth: tables, indexes, sequences, RLS policies, views (Supabase CLI)
└── seed/         dev-only seed data: demo café, tables, menu, staff memberships
```

SQLAlchemy/Pydantic models in `backend/app/db/models` follow these migrations, never the other way round.

## Database invariants

1. **Every business table carries `cafe_id`.** The same person at two cafés is two `customers` rows (`UNIQUE (cafe_id, wa_id)`).
2. **Idempotency constraints are load-bearing.** Never drop or weaken them:
   - `messages.wamid` primary key (dedupes Meta retries)
   - `orders UNIQUE (cart_id, cart_version)` (a double-tapped Confirm can't create two orders)
   - `orders UNIQUE (cafe_id, order_number)` via a per-café sequence
   - `campaign_recipients UNIQUE (redemption_code)` and `UNIQUE (campaign_id, customer_id)`
   - `cafes UNIQUE (phone_number_id)` (tenant resolution)
3. **RLS is enabled on every table.** `SELECT` policies exist **only** on `orders`, `order_items`, `waiter_requests` and `messages`, limited to the user's member cafés. There are **no** INSERT/UPDATE/DELETE policies for `authenticated`.
4. `consents` is **append-only**. The latest row per `(customer_id, type)` is the current state.
5. Order and price history is **snapshotted** (`order_items.name_snapshot`, `unit_price_snapshot`, `carts.priced_snapshot`). Menu edits never rewrite history.
6. Anonymisation (DPDP) replaces the name and `wa_id` and purges messages, but **keeps orders** as anonymous rows.
7. Raw `webhook_events` are pruned after 30 days.
8. Migrations are forward-only and reviewed. A change to schema, RLS or a view also updates the matching doc.

## Task → doc

| Task | Doc |
|---|---|
| Full ER diagram, table list | [data-model.md](../docs/database/data-model.md) |
| Per-domain ER and enum values | [schema-by-domain.md](../docs/database/schema-by-domain.md) |
| Unique keys, indexes, locks | [constraints-and-indexes.md](../docs/database/constraints-and-indexes.md) |
| RLS policies | [row-level-security.md](../docs/database/row-level-security.md) |
| Migration order and workflow | [migrations.md](../docs/database/migrations.md) |
| Analytics views | [analytics-views.md](../docs/database/analytics-views.md) |
| PII and retention | [data-protection.md](../docs/security/data-protection.md) |
