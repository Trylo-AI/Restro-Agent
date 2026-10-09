# Schema by domain

> **Area:** Database
> **Source:** MVP Architecture v0.1 §7, §6.3, §6.4, §6.6, §6.7, §6.9, §6.11, §6.12 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [`backend/app/db/CLAUDE.md`](../../backend/app/db/CLAUDE.md), [`supabase/CLAUDE.md`](../../supabase/CLAUDE.md), [`backend/app/domain/CLAUDE.md`](../../backend/app/domain/CLAUDE.md), [`backend/app/domain/conversation/CLAUDE.md`](../../backend/app/domain/conversation/CLAUDE.md), [`backend/app/domain/cart/CLAUDE.md`](../../backend/app/domain/cart/CLAUDE.md), [`backend/app/domain/orders/CLAUDE.md`](../../backend/app/domain/orders/CLAUDE.md), [`backend/app/domain/consent/CLAUDE.md`](../../backend/app/domain/consent/CLAUDE.md), [`backend/app/domain/campaigns/CLAUDE.md`](../../backend/app/domain/campaigns/CLAUDE.md)

## Summary

The §7 ER diagram split into five domain diagrams, each followed by the allowed values of its status-like columns. Entities from another domain appear without attributes, just to show the link. [data-model.md](data-model.md) holds the verbatim full diagram; if the two ever disagree, that file wins.

Values marked **(proposed)** are not spelled out in the source and need confirming before the first migration.

## 1. Tenancy and staff

```mermaid
erDiagram
    CAFES ||--o{ CAFE_TABLES : has
    CAFES ||--o{ STAFF_MEMBERSHIPS : employs
    AUTH_USERS ||--o{ STAFF_MEMBERSHIPS : holds

    CAFES {
        uuid id PK
        text name
        text timezone
        text business_id
        text waba_id
        text phone_number_id UK
        text display_phone_number
        text onboarding_model
        text token_secret_ref
        text status
        jsonb settings
    }
    CAFE_TABLES {
        uuid id PK
        uuid cafe_id FK
        text label
        text qr_url
        bool active
    }
    STAFF_MEMBERSHIPS {
        uuid id PK
        uuid user_id FK
        uuid cafe_id FK
        text role
        bool active
    }
```

`phone_number_id` is how inbound webhooks find the café. `STAFF_MEMBERSHIPS.user_id` points at the Supabase Auth user (shown as `AUTH_USERS`).

| Column | Values | Notes |
|---|---|---|
| `staff_memberships.role` | `staff`, `manager`, `owner`, `platform_admin` | §4. One person can belong to several cafés without schema changes |
| `staff_memberships.active` | true / false | The owner can deactivate staff instantly (§10) |
| `cafes.settings` | Hours, tone, feedback delay, tax | From `GET/PUT /admin/settings` (§8.3) |
| `cafes.token_secret_ref` | A Secret Manager reference | The token itself never lives in the DB (§10) |
| `cafes.status`, `cafes.onboarding_model` | Not listed in source | **(proposed)** define during café onboarding design |

## 2. Customers and conversations

```mermaid
erDiagram
    CAFES ||--o{ CUSTOMERS : owns
    CUSTOMERS ||--o{ CONSENTS : gives
    CUSTOMERS ||--o{ CONVERSATIONS : has
    CONVERSATIONS ||--o{ MESSAGES : contains
    CONVERSATIONS ||--o{ WAITER_REQUESTS : raises
    CONVERSATIONS ||--o{ AI_USAGE : costs

    CUSTOMERS {
        uuid id PK
        uuid cafe_id FK
        text wa_id
        text name
        text language_hint
        timestamptz first_seen_at
        timestamptz last_inbound_at
        timestamptz last_visit_at
        int visit_count
        timestamptz anonymised_at
    }
    CONSENTS {
        uuid id PK
        uuid customer_id FK
        text type
        text status
        text wording_version
        text source
        timestamptz created_at
    }
    CONVERSATIONS {
        uuid id PK
        uuid cafe_id FK
        uuid customer_id FK
        text mode
        text stage
        text table_label
        timestamptz opened_at
        timestamptz last_activity_at
    }
    MESSAGES {
        text wamid PK
        uuid cafe_id FK
        uuid conversation_id FK
        text direction
        text type
        jsonb payload
        text status
        text error_code
        text pricing_category
        bool billable
        timestamptz created_at
    }
    WAITER_REQUESTS {
        uuid id PK
        uuid cafe_id FK
        uuid conversation_id FK
        text table_label
        text reason
        text status
        uuid acknowledged_by
        timestamptz created_at
        timestamptz resolved_at
    }
    AI_USAGE {
        uuid id PK
        uuid conversation_id FK
        text model
        int input_tokens
        int output_tokens
        int latency_ms
        numeric cost_usd
        bool fallback_used
        timestamptz created_at
    }
```

A customer belongs to exactly one café. Messages, waiter calls and AI cost all hang off the conversation.

| Column | Values | Notes |
|---|---|---|
| `conversations.mode` | `bot`, `human` | Separate from stage; can switch at any stage (§6.4) |
| `conversations.stage` | `greeting`, `awaiting_name`, `browsing`, `cart_open`, `awaiting_confirmation`, `order_active`, `awaiting_feedback`, `awaiting_feedback_comment`, `closed` | States from §6.4. `awaiting_name` and `awaiting_feedback_comment` are spelled this way in §6.3; the snake_case spelling of the rest is **(proposed)** |
| `messages.direction` | `in`, `out` | |
| `messages.status` | `sent`, `delivered`, `read`, `failed` | Forward only: sent → delivered → read, or failed (§6.2) |
| `messages.pricing_category`, `billable` | From Meta's `pricing` object on status webhooks | Feeds `v_unit_costs` (§11) |
| `messages.type` | Not listed in source | **(proposed)** text, interactive, template, etc. |
| `waiter_requests.status` | `PENDING`, `ACKNOWLEDGED`, `RESOLVED` | §6.9 |
| `waiter_requests.reason` | Includes `BILL` | "Request bill" reuses this path. Other reasons come from "Call waiter", AI fallback and the `call_human(reason)` tool; their exact codes are **(proposed)** |
| `consents.status` | `Unknown`, `OptedIn`, `Declined`, `OptedOut` | §6.11. Latest row per customer and type is current |
| `consents.source` | `order_flow`, `counter_qr`, `feedback_flow` | §6.11 |
| `consents.type` | Not listed in source | **(proposed)** e.g. a single `marketing` type for the MVP |
| `consents.wording_version` | The exact text shown | Proof of what the customer agreed to |

## 3. Menu, cart and orders

```mermaid
erDiagram
    CAFES ||--o{ MENU_CATEGORIES : has
    MENU_CATEGORIES ||--o{ MENU_ITEMS : contains
    CONVERSATIONS ||--o| CARTS : holds
    CARTS ||--o{ CART_ITEMS : contains
    MENU_ITEMS ||--o{ CART_ITEMS : referenced_by
    CUSTOMERS ||--o{ ORDERS : places
    CARTS ||--o| ORDERS : becomes
    ORDERS ||--o{ ORDER_ITEMS : contains
    MENU_ITEMS ||--o{ ORDER_ITEMS : referenced_by
    ORDERS ||--o{ ORDER_STATUS_EVENTS : logs
    ORDERS ||--o| FEEDBACK : receives

    MENU_CATEGORIES {
        uuid id PK
        uuid cafe_id FK
        text name
        int sort_order
    }
    MENU_ITEMS {
        uuid id PK
        uuid cafe_id FK
        uuid category_id FK
        text name
        text description
        numeric price
        bool is_veg
        int spice_level
        text_array tags
        text_array allergens
        int protein_g
        int calories
        int prep_minutes
        text image_path
        bool is_available
    }
    CARTS {
        uuid id PK
        uuid conversation_id FK
        int version
        text status
        jsonb priced_snapshot
    }
    CART_ITEMS {
        uuid id PK
        uuid cart_id FK
        uuid menu_item_id FK
        int qty
        text note
    }
    ORDERS {
        uuid id PK
        uuid cafe_id FK
        uuid customer_id FK
        uuid cart_id FK
        int cart_version
        int order_number
        text table_label
        text status
        numeric subtotal
        numeric tax
        numeric total
        text source
        uuid campaign_recipient_id FK
        timestamptz expected_ready_at
        bool overdue
        timestamptz created_at
    }
    ORDER_ITEMS {
        uuid id PK
        uuid order_id FK
        uuid menu_item_id FK
        text name_snapshot
        numeric unit_price_snapshot
        int qty
        text note
    }
    ORDER_STATUS_EVENTS {
        uuid id PK
        uuid order_id FK
        text from_status
        text to_status
        text actor
        timestamptz at
    }
    FEEDBACK {
        uuid id PK
        uuid order_id FK
        int rating
        text comment
        timestamptz created_at
    }
```

The cart becomes an order exactly once per `(cart_id, cart_version)`; the order then accumulates status events and at most one feedback row.

| Column | Values | Notes |
|---|---|---|
| `orders.status` | `PLACED`, `PREPARING`, `READY`, `SERVED`, `CANCELLED` | §6.7. Allowed transitions in [../backend/order-lifecycle.md](../backend/order-lifecycle.md) |
| `orders.overdue` | true / false | A flag, not a state: now > `expected_ready_at` and not READY. Set by `overdue_sweep` |
| `orders.expected_ready_at` | now + max(`prep_minutes`) of the lines | Set at create (§6.6) |
| `order_status_events.from_status`, `to_status` | Same values as `orders.status` | `from_status` is empty for the initial PLACED event |
| `order_status_events.actor` | Customer, staff user, or sweep | Exact encoding **(proposed)** |
| `orders.source` | Not listed in source | **(proposed)** |
| `carts.status` | Not listed in source | **(proposed)** at least "open" and "ordered", so "cart not already ordered" (§6.6) is a column check |
| `carts.version` | Integer, +1 on every `request_confirmation` | Embedded in the Confirm button id `confirm:{cart_id}:{version}` |
| `cart_items.qty` | 1–20 | Guardrail on `add_to_cart` (§6.5) |
| `feedback.rating` | 1–5 | 4–5 thank you, 3 asks for a comment, 1–2 apologise and alert the manager (§6.10) |
| `menu_items.is_available` | true / false | Only `true` items reach the AI prompt |

### Snapshot columns

| Column | Captured when | Why |
|---|---|---|
| `carts.priced_snapshot` | `request_confirmation`, together with `version += 1` | Freezes the exact lines, prices and totals the customer is shown in the summary they confirm |
| `order_items.name_snapshot` | Order create | Historical orders keep the dish name even if the menu item is renamed or deleted |
| `order_items.unit_price_snapshot` | Order create | Revenue and receipts never change when a price is edited later |

## 4. Retention

```mermaid
erDiagram
    CAFES ||--o{ TEMPLATES : has
    TEMPLATES ||--o{ CAMPAIGNS : used_by
    CAMPAIGNS ||--o{ CAMPAIGN_RECIPIENTS : targets
    CUSTOMERS ||--o{ CAMPAIGN_RECIPIENTS : is
    CAMPAIGN_RECIPIENTS ||--o| ORDERS : redeemed_in

    TEMPLATES {
        uuid id PK
        uuid cafe_id FK
        text meta_template_id
        text name
        text language
        text category
        text status
        text quality
        jsonb components
    }
    CAMPAIGNS {
        uuid id PK
        uuid cafe_id FK
        uuid template_id FK
        jsonb segment_definition
        jsonb variables
        timestamptz scheduled_at
        text status
    }
    CAMPAIGN_RECIPIENTS {
        uuid id PK
        uuid campaign_id FK
        uuid customer_id FK
        text wamid
        text send_status
        text redemption_code UK
        timestamptz redeemed_at
        uuid order_id FK
    }
```

A recipient row links a campaign to a customer and, once its code is redeemed, to the order where it was used.

| Column | Values | Notes |
|---|---|---|
| `campaign_recipients.send_status` | `QUEUED`, `DEFERRED`, `SKIPPED`, `SENT`, `DELIVERED`, `READ`, `FAILED`, `REDEEMED` | §6.12 state diagram. `DEFERRED` when the daily messaging limit is reached, `SKIPPED` when opted out before send |
| `campaigns.status` | Draft, `SCHEDULED` from source; cancel exists (`/cancel`) | Full set **(proposed)** e.g. `DRAFT`, `SCHEDULED`, `SENDING`, `COMPLETED`, `CANCELLED` |
| `campaigns.segment_definition` | All opted-in, lapsed (no order in N days), regulars (≥ K orders in 60 days) | Only three MVP segments (§6.12) |
| `templates.category` | Marketing or utility | Marketing requires `OptedIn` at send (§6.13) |
| `templates.status`, `quality` | Synced from Meta | Kept in sync by `template_sync` |
| Template names | `weekly_offer`, `winback`, `order_update`, `new_order_alert` | Submit for approval early (§13) |

## 5. Platform and supporting tables

The source names these tables but only gives columns for `audit_log`. Attributes for `webhook_events` beyond the raw payload and `signature_valid` (§6.2), and all attributes of `data_deletion_requests`, are **(proposed)**.

```mermaid
erDiagram
    CAFES ||--o{ WEBHOOK_EVENTS : receives
    CAFES ||--o{ AUDIT_LOG : records
    CUSTOMERS ||--o{ DATA_DELETION_REQUESTS : files

    WEBHOOK_EVENTS {
        uuid id PK
        uuid cafe_id FK
        jsonb payload
        bool signature_valid
        timestamptz received_at
    }
    AUDIT_LOG {
        uuid id PK
        text actor
        uuid cafe_id FK
        text action
        text entity
        jsonb before
        jsonb after
        timestamptz at
    }
    DATA_DELETION_REQUESTS {
        uuid id PK
        uuid cafe_id FK
        uuid customer_id FK
        text source
        text status
        timestamptz requested_at
        timestamptz completed_at
    }
```

Source-backed columns: `webhook_events.payload` and `signature_valid`; all `audit_log` columns (`actor`, `cafe_id`, `action`, `entity`, `before`, `after`, `at`). Everything else here, including the surrogate `id` keys, is **(proposed)**.

| Column | Values | Notes |
|---|---|---|
| `data_deletion_requests.source` | Chat ("delete my data") or Meta deletion callback | **(proposed)** column; both paths are in §6.11 |
| `data_deletion_requests.status` | **(proposed)** e.g. received, completed | The `data_deletion` job anonymises the customer |
| `webhook_events.cafe_id` | **(proposed)** | May be null if the payload's `phone_number_id` matches no café |
| `audit_log.actor` | Staff user, owner, platform admin, or job | Every platform-admin action is audited (§4) |

## Rules / invariants

- Status values are stored as fixed codes and validated in code (and ideally by a check constraint); the visible WhatsApp button title is never stored as a status.
- `messages.status` only moves forward.
- `consents` rows are never updated; a change is a new row.
- Snapshot columns are written once and never recomputed.

## Open questions

- Spelling and case of stored enum values: §6.3 writes `opted_out` and `awaiting_name` in lowercase, §6.11 writes `OptedOut`. Pick one convention per column **(proposed: keep source spelling as listed above)**.
- Enum values not given in source: `cafes.status`, `cafes.onboarding_model`, `carts.status`, `orders.source`, `campaigns.status`, `consents.type`, `messages.type`, non-BILL `waiter_requests.reason` codes.
- `platform_admin` is listed as a role but is cross-café; whether it lives in `staff_memberships` (with which `cafe_id`) or elsewhere is undecided.
- Columns for `webhook_events` and `data_deletion_requests` are proposals.

## Related

- [data-model.md](data-model.md) · [constraints-and-indexes.md](constraints-and-indexes.md)
- [../backend/conversation-state-machine.md](../backend/conversation-state-machine.md) · [../backend/order-lifecycle.md](../backend/order-lifecycle.md) · [../retention/consent.md](../retention/consent.md) · [../retention/campaigns.md](../retention/campaigns.md)
- Context files: [`backend/app/db/CLAUDE.md`](../../backend/app/db/CLAUDE.md), [`supabase/CLAUDE.md`](../../supabase/CLAUDE.md)
