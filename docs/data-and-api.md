# Data and API

## Schema

Contract: `contracts/schema.sql`. Implemented by `supabase/migrations/`.

- Every table has `id` (uuid), `created_at`, `updated_at` (UTC). Not repeated below.
- Every tenant table has `cafe_id`, child tables included, so RLS and Realtime filter without joins. Only `platform_admins` and `webhook_events` have none.
- Money is integer paise in `*_paise` columns. `llm_calls.cost_estimate` is the one fractional amount.
- Enum-like columns are text with a check constraint. Day and hour buckets use `cafes.timezone`.

### Café and access

| Table | Key columns | Keys and checks |
|---|---|---|
| `cafes` | `name`, `timezone`, `business_id`, `waba_id`, `phone_number_id`, `display_phone_number`, `onboarding_model` (`hosted`), `token_ref`, `pin_ref`, `status`, `opening_hours`, `cutoff_minutes`, `settings` (`paused`, `tone`, `feedback_delay_minutes`, `gst_mode`, `gst_rate`, `alert_phone`, `model`), `health`, `checklist` | unique `phone_number_id`; `status` `onboarding` / `live` / `disconnected` / `paused`; `tone` `warm` / `crisp` / `playful` |
| `memberships` | `user_id`, `cafe_id`, `role`, `active`, `alert_phone` | unique (`user_id`, `cafe_id`); `role` `shift_manager` / `manager` / `owner` |
| `platform_admins` | `user_id` | unique `user_id` |
| `tables` | `cafe_id`, `label`, `area`, `seats`, `qr_text` | unique (`cafe_id`, `label`) |

`token_ref` and `pin_ref` are Secret Manager references, never the secret.

### Menu

| Table | Key columns | Keys and checks |
|---|---|---|
| `menu_items` | `cafe_id`, `name`, `description`, `category`, `price_paise`, `prep_minutes`, `veg`, `spice`, `available`, `photo_path`, `version` | unique (`cafe_id`, `name`); index (`cafe_id`, `available`) |
| `menu_item_allergens` | `cafe_id`, `item_id`, `allergen`, `state` | unique (`item_id`, `allergen`); `state` `contains` / `free_from` / `unknown` |

`version` comes from a café-wide counter, so the highest `version` is the menu version.

### Customers and conversations

| Table | Key columns | Keys and checks |
|---|---|---|
| `customers` | `cafe_id`, `wa_id`, `name`, `first_seen_at`, `last_inbound_at`, `last_visit_at` | unique (`cafe_id`, `wa_id`) |
| `customer_preferences` | `cafe_id`, `customer_id`, `kind`, `value`, `source` | Stated preferences only |
| `consents` | `cafe_id`, `customer_id`, `type` (`marketing`), `status`, `wording_version`, `source` | `status` `opted_in` / `opted_out`; append-only; current = newest row |
| `data_deletion_requests` | `cafe_id`, `customer_id`, `wa_id`, `source` (`owner` / `meta_callback`), `status`, `completed_at` | — |
| `conversations` | `cafe_id`, `customer_id`, `mode`, `assigned_to`, `table_session_id`, `state` | unique (`cafe_id`, `customer_id`); `mode` `bot` / `human` |
| `inbound_messages` | `cafe_id`, `conversation_id`, `wamid`, `type`, `payload`, `received_at`, `processed_at` | unique `wamid`; index on unprocessed per conversation |
| `wa_messages` | `cafe_id`, `customer_id`, `wamid`, `idempotency_key`, `direction`, `type`, `template_name`, `status`, `error_code`, `pricing_category`, `billable`, status timestamps | unique `wamid`; unique `idempotency_key`; status forward-only |

### Orders and service

| Table | Key columns | Keys and checks |
|---|---|---|
| `table_sessions` | `cafe_id`, `table_id`, `opened_at`, `closed_at` | One open session per table |
| `orders` | `cafe_id`, `customer_id`, `table_session_id`, `number`, `status`, `flags`, `allergy_note`, `expected_minutes`, `delay_minutes`, `total_paise`, `source`, `campaign_recipient_id`, `idempotency_key`, `version`, status timestamps | unique (`cafe_id`, `number`); unique `idempotency_key`; `status` `NEW` / `ACCEPTED` / `READY` / `SERVED` / `CANCELLED`; `source` `qr` / `campaign` / `manual` |
| `order_items` | `cafe_id`, `order_id`, `item_id`, `name_snapshot`, `price_snapshot_paise`, `qty`, `note` | `qty` 1–20 |
| `order_events` | `cafe_id`, `order_id`, `from_status`, `to_status`, `actor_type`, `actor_id`, `reason` | `actor_type` `customer` / `staff` / `system`; append-only |
| `requests` | `cafe_id`, `table_session_id`, `conversation_id`, `order_id`, `kind`, `status`, `reason_tag`, `acknowledged_by`, `acknowledged_at`, `resolved_at` | `kind` `help` / `bill` / `allergy` / `low_rating`; one unresolved per (`conversation_id`, `kind`) |
| `escalations` | `cafe_id`, `subject_type`, `subject_id`, `level`, `target`, `sent_at`, `outcome` | unique (`subject_type`, `subject_id`, `level`) |
| `feedback` | `cafe_id`, `order_id`, `rating`, `text` | unique `order_id`; `rating` 1–5 |

- `number` is allocated inside the order-create transaction: gaps allowed, duplicates never, never resets.
- `idempotency_key` is `{conversation_id}:{cart_version}` for WhatsApp orders and the `Idempotency-Key` header for manual orders.
- Snapshots are copied at creation and never recomputed; revenue, bills and history read them.
- Undo writes a new `order_events` row; nothing is deleted.

### Retention, metering, audit

| Table | Key columns | Keys and checks |
|---|---|---|
| `templates` | `cafe_id`, `name`, `category`, `language`, `status`, `quality` | unique (`cafe_id`, `name`, `language`) |
| `campaigns` | `cafe_id`, `name`, `segment`, `template_name`, `offer_text`, `code_prefix`, `valid_until`, `scheduled_at`, `status` | `segment` `all_opted_in` / `lapsed` / `regulars` |
| `campaign_recipients` | `cafe_id`, `campaign_id`, `customer_id`, `code`, `status`, `wamid` | unique `code`; unique (`campaign_id`, `customer_id`) |
| `redemptions` | `cafe_id`, `campaign_recipient_id`, `code`, `order_id`, `table_session_id`, `discount_paise`, `redeemed_by`, `redeemed_at` | unique `campaign_recipient_id`; append-only |
| `llm_calls` | `cafe_id`, `conversation_id`, `model`, token counts, `latency_ms`, `outcome`, `cost_estimate` | append-only |
| `audit_log` | `actor_id`, `actor_role`, `support`, `cafe_id`, `action`, `change`, `at` | append-only; no secrets or full numbers in `change` |
| `webhook_events` | `received_at`, `signature_valid`, `payload`, `processed_at` | Deleted after 30 days |

**Schema rules**

- Append-only tables (`consents`, `order_events`, `redemptions`, `llm_calls`, `audit_log`): the backend role has no UPDATE or DELETE grant.
- Never key a customer on `wa_id` alone. Never weaken the unique `wamid`, `idempotency_key` or `code`.
- Migrations are timestamped and forward-only; an applied migration is never edited. Each new tenant table enables RLS and its read policy in the same migration. Expand then contract: the migration must work with the running backend.
- CI applies every migration to an empty DB. A schema change updates `contracts/schema.sql` in the same PR.
- `supabase/seed/` (from the seed generator, also used by MSW): demo café, menu with allergens, tables, one user per role, customers, orders in every status, a campaign. Never real data.

## Row-level security

| Path | Who | Guard |
|---|---|---|
| Supabase client (reads + Realtime) | Console with anon key + user JWT | RLS read policies |
| Pooler, server role | `api`, `worker` | `cafe_id` in every repository call (bypasses RLS) |

- A row is visible when its `cafe_id` is in the caller's active `memberships`, or the caller is in `platform_admins`.
- No INSERT, UPDATE or DELETE policy anywhere. Every write goes through `api`.

| Readable by | Tables / views |
|---|---|
| Every member | `cafes`, `tables`, `menu_items`, `menu_item_allergens`, `customers`, `conversations`, `inbound_messages`, `wa_messages`, `table_sessions`, `orders`, `order_items`, `requests` (not `low_rating`), `escalations`, `redemptions` |
| Own rows | `memberships` (owner sees every row of the café); `platform_admins` |
| `manager`+ | `low_rating` requests, `feedback`, `v_service_report`, `v_prep_times`, `v_feedback_summary` |
| `owner` | `consents`, `templates`, `campaigns`, `campaign_recipients`, `v_daily_revenue`, `v_top_items`, `v_customer_segments`, `v_customer_profile`, `v_campaign_funnel` |
| `platform_admins` | `llm_calls`, `audit_log`, `webhook_events`, `v_unit_costs` |
| Backend only | `customer_preferences`, `order_events`, `data_deletion_requests` |

`v_customer_profile` exposes the masked number, never `wa_id`. RLS is tested in CI with cross-café and role cases.

## Analytics views

`v_*` views over the operational tables; no separate analytics stack.

| View | Gives | Used by |
|---|---|---|
| `v_daily_revenue` | Revenue, orders, average order, distinct customers by day and hour | Today, Analytics, weekly report |
| `v_top_items` | Dishes by quantity and revenue | Analytics, weekly report |
| `v_customer_segments` | Segment per customer; returning % | Today, Analytics, Customers, campaign audiences |
| `v_customer_profile` | Masked number, visits, spend, top 3 dishes, rating, consent, campaigns, segment | Customers |
| `v_prep_times` | Median `created_at` → `ready_at` per order and dish | Service report |
| `v_service_report` | Median prep, % overdue, slowest dishes, peak overdue hour | Service report, Analytics |
| `v_feedback_summary` | Average rating, count, 1–3★ with text | Service report, Analytics, AI review |
| `v_campaign_funnel` | Sent → held → delivered → read → came back; spent, discount, WhatsApp cost | Campaigns, Today, Costs |
| `v_unit_costs` | Cost per order, WhatsApp 7 d, AI 7 d, fallback rate | Platform Costs, daily summary |

- Revenue = WhatsApp orders from snapshots; `CANCELLED` excluded from revenue, visits and spend. Anonymised customers stay as anonymous rows.
- AI cost per order = `llm_calls.cost_estimate` for the conversation that produced it.
- `refresh_views` runs nightly ("Updated nightly"). Today tiles and a `sending` campaign need same-day numbers, so those stay plain views **[verify]**. Plain views run as the caller so RLS applies; materialised views stay out of the exposed schema and are read through a filtered plain view **[verify]**.

## API

| Service | Routes | Called by | Auth |
|---|---|---|---|
| `api` | `/webhooks/*` | Meta | Signature on raw bytes, or signed request |
| `api` | Console routes, `/platform/*` | Restro Console | `Authorization: Bearer <Supabase JWT>` |
| `worker` | `/internal/*` | Cloud Tasks, Cloud Scheduler | OIDC for `invoker-sa` |

The console reads tables and views through Supabase (no custom read endpoints) and calls `api` for every write. Exceptions that are API reads: `GET /consents/export`, `GET /platform/audit/export`, `POST /campaigns/audience-preview`.

### Console request check

1. Verify the JWT (signature, `exp`, `aud`, `iss`) → 401 `unauthenticated`.
2. Café from the resource in the path, else `X-Cafe-Id`; never from the body → 404 `not_found` if missing or another café's.
3. Active membership for (user, café). `platform_admin` passes only inside an open support session and only for actions marked support → 403 `forbidden`.
4. Role ≥ the endpoint minimum → 403.
5. Domain call with the membership's `cafe_id` → 409 / 422.
6. One `audit_log` row in the same transaction.

`X-Cafe-Id` only picks among the caller's own memberships; disagreeing with the resource's café → 403.

### Errors and idempotency

Body: `error.code` (stable snake_case), `error.message` (safe to show), `error.details` (optional).

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | Malformed body, unknown field, out of range |
| 401 | `unauthenticated` | Missing or expired JWT; bad signature |
| 403 | `forbidden` | No membership, role too low, café mismatch, no support session |
| 404 | `not_found` | Missing or another café's |
| 409 | `version_conflict` | Row changed since read; response carries the current row |
| 409 | `code_already_used` | Code redeemed on another order |
| 422 | `invalid_transition` | Not allowed from current status, or undo after 5 s |
| 422 | `invalid_code` | Unknown, expired or another café's code |
| 429 | `rate_limited` | `Retry-After` set |
| 5xx | `internal` | Retry with backoff |

- Order transitions carry `expected_version`. Manual orders carry an `Idempotency-Key` header.
- Set-state calls (ack, resolve, availability, pause, takeover, handback, deliver) are no-ops when the value is already set: 200, no second audit row or message.
- Applying a code already on this table session returns 200.

### Endpoints

`shift_manager`+ means `shift_manager`, `manager` or `owner`. "Support" = also `platform_admin` in support mode.

| Endpoint | Body | Role | Does | Audit |
|---|---|---|---|---|
| `POST /orders/{id}/transition` | `{to, reason?, expected_version}` | `shift_manager`+, support | Accept, Mark ready, Mark served, cancel / decline (reason required); queues `status_message` | `order.transition` / `order.cancel` |
| `POST /orders/{id}/undo` | — | Same actor ≤ 5 s | Revert last transition | `order.undo` |
| `POST /orders/{id}/delay` | `{minutes, note?}` | `shift_manager`+, support | Delay and message | `order.delay` |
| `POST /orders/manual` | `{table_id, items, allergy_note?}` + `Idempotency-Key` | `shift_manager`+ | Manual order priced by code | `order.manual` |
| `POST /requests/{id}/ack` | — | `shift_manager`+ (`low_rating`: `manager`+), support | `acknowledged` | `request.ack` |
| `POST /requests/{id}/resolve` | — | same | `resolved`; help → chat back to bot | `request.resolve` |
| `POST /bills/{table_session_id}/apply-code` | `{code}` | `shift_manager`+, support | Redeem; returns bill totals | `offer.redeem` |
| `POST /bills/{table_session_id}/deliver` | — | `shift_manager`+, support | Resolve bill request, close session | `bill.deliver` |
| `POST /conversations/{id}/takeover` | — | `shift_manager`+, support | `mode = human`, assigned to caller | `chat.takeover` |
| `POST /conversations/{id}/reply` | `{text}` | `shift_manager`+, support | Send via `outbound`; takes over if in bot mode | `chat.reply` |
| `POST /conversations/{id}/handback` | — | `shift_manager`+, support | `mode = bot` | `chat.handback` |
| `POST /conversations/open` | `{table_session_id}` | `shift_manager`+, support | Open the table's chat in human mode | `chat.takeover` |
| `POST /menu/{id}/availability` | `{available}` | `shift_manager`+, support | Sold-out toggle | `menu.availability` |
| `POST /menu` · `PUT /menu/{id}` | Item + `allergens: [{allergen, state}]` (+ `version` on PUT) | `manager`+, support | Create / edit; past orders keep snapshots | `menu.update` |
| `POST /menu/import` | CSV (multipart) | `owner`, support | All-or-nothing import; returns row errors | `menu.import` |
| `POST /cafes/{id}/pause-ordering` | `{paused}` | `manager`+, support | Pause / resume WhatsApp ordering | `ordering.pause` / `ordering.resume` |
| `PUT /cafes/{id}/settings` | Hours, cutoff, tone, feedback delay, GST, alert number | `owner`, support | Update settings | `settings.update` |
| `POST /tables` · `PUT /tables/{id}` | `{label, area, seats}` | `owner`, support | Create / edit table and `qr_text` | `table.update` |
| `POST /team/invite` | `{email, role}` | `owner`, support | Auth invite + membership | `team.invite` |
| `PATCH /team/{membership_id}` | `{role?, active?}` | `owner`, support | Change role or deactivate; effective next request | `team.update` |
| `POST /customers/{id}/delete` | — | `owner`, support | [Delete customer data](architecture.md#data-protection) | `customer.delete` |
| `GET /consents/export` | — | `owner`, support | CSV of full consent history (masked numbers) | `consent.export` |
| `POST /campaigns` · `PUT /campaigns/{id}` | Name, segment, template, offer, code prefix, valid until | `owner`, support | Create / edit a draft | — |
| `POST /campaigns/audience-preview` | `{segment, template_name}` | `owner`, support | Counts, daily limit, quality, cost, days | — |
| `POST /campaigns/{id}/schedule` | `{scheduled_at}` | `owner`, support | `draft` → `scheduled` | `campaign.schedule` |
| `POST /campaigns/{id}/cancel` | — | `owner`, support | → `cancelled`; queued recipients not sent | `campaign.cancel` |
| `POST /platform/cafes` | Café, Meta ids, secret refs, owner email | `platform_admin` | Create café (`onboarding`), invite owner | `cafe.create` |
| `PATCH /platform/cafes/{id}/checklist` | `{item, done}` | `platform_admin` | Tick go-live item | `cafe.checklist` |
| `POST /platform/cafes/{id}/templates/sync` | — | `platform_admin` | Pull templates from Meta | `template.sync` |
| `POST /platform/support/{cafe_id}/open` · `POST /platform/support/exit` | — | `platform_admin` | Start / end support mode | `support.open` / `support.exit` |
| `GET /platform/audit/export` | Filters | `platform_admin` | CSV of `audit_log` | — |
| `POST /internal/tasks/{ack,process,timer,campaign-send}` | Task body | `invoker-sa` | [Queues](architecture.md#queues-timers-and-jobs) | — |
| `POST /internal/jobs/{job}` | — | `invoker-sa` | [Jobs](architecture.md#scheduled-jobs) | — |

**Endpoint rules**

- No endpoint accepts `cafe_id` in a body or query string.
- Every console and platform write writes exactly one `audit_log` row; `change` holds changed fields only. Webhook and internal calls are recorded in their own tables instead.
- Prices, totals and discounts in responses are computed from the DB, in paise.
- Rate limits never apply to signature-valid webhooks.

## Realtime

The console joins one channel per table, filtered `cafe_id = <active café>`, with the user's JWT; RLS applies to every event. Only these tables are in the `supabase_realtime` publication.

| Table | Events | Drives |
|---|---|---|
| `orders` | INSERT, UPDATE | Order cards, bill totals, Today, badges, chime, alarms |
| `requests` | INSERT, UPDATE | Calls and bills, Chats badges, Today low ratings, help alarm |
| `conversations` | UPDATE | Chats list, human-mode badge |
| `inbound_messages` | INSERT | Open chat thread |
| `wa_messages` | INSERT, UPDATE | Open chat thread, delivery ticks |
| `menu_items` | UPDATE | Sold-out toggles on every device |
| `cafes` | UPDATE | Paused and Disconnected banners |

- `order_items` isn't subscribed; on an order INSERT or an `edited` UPDATE the console reads the items.
- Apply an UPDATE only if its `version` (orders) or `updated_at` is newer than shown.
- Subscribe first, buffer, do the initial read, then apply the buffer. No gap.
- Never trust that every event arrived: refetch on reconnect, tab visible again and café switch.
- A table joins the publication only with an RLS read policy and an entry in `contracts/events.md`.
- The console is never the only alert path: escalation timers send `new_order_alert` whatever the browser state.
