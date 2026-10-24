-- Generated from contracts/schema.sql at S3. Forward-only: never edit after it is applied; add a new migration.
-- Restro schema contract. Source of truth: docs/data-and-api.md#schema.
-- Implemented by supabase/migrations/. Changing this file is a contract change (CLAUDE.md working rules).
--
-- Conventions
--   * Every table: id uuid, created_at, updated_at (UTC).
--   * Every tenant table carries cafe_id (only platform_admins and webhook_events don't).
--   * Money: integer paise in *_paise. Fractional amounts only in metering (llm_calls.cost, wa_messages.cost).
--   * Enum-like columns: text + check constraint.
--   * Append-only tables reject UPDATE and DELETE with a trigger (consents, order_events, redemptions,
--     llm_calls, audit_log), whatever role the backend connects as.

create extension if not exists pgcrypto;

create or replace function touch_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create or replace function forbid_mutation() returns trigger language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name using errcode = 'insufficient_privilege';
end $$;

-- ───────────────────────────────────────────── Café and access

create table cafes (
  id                   uuid primary key default gen_random_uuid(),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  name                 text not null check (char_length(name) between 1 and 80),
  timezone             text not null default 'Asia/Kolkata',
  business_id          text,
  waba_id              text,
  phone_number_id      text not null unique,
  display_phone_number text,
  onboarding_model     text not null default 'hosted' check (onboarding_model in ('hosted')),
  token_ref            text,   -- Secret Manager reference (or env:NAME locally), never the token
  pin_ref              text,
  status               text not null default 'onboarding'
                       check (status in ('onboarding', 'live', 'disconnected', 'paused')),
  -- {"mon": [["08:00","23:00"]], ...}; empty = always open
  opening_hours        jsonb not null default '{}'::jsonb,
  cutoff_minutes       integer not null default 30 check (cutoff_minutes between 0 and 180),
  -- paused, tone, feedback_delay_minutes, alert_phone, model. No GST setting: prices include GST.
  settings             jsonb not null default '{"paused": false, "tone": "warm", "feedback_delay_minutes": 30}'::jsonb
                       check ((settings ->> 'tone') is null or (settings ->> 'tone') in ('warm', 'crisp', 'playful'))
                       check ((settings ->> 'feedback_delay_minutes') is null
                              or (settings ->> 'feedback_delay_minutes') in ('15', '30', '60')),
  health               jsonb not null default '{}'::jsonb,
  checklist            jsonb not null default '{}'::jsonb
);

-- Per-café counters (backend only): order numbers and menu version only ever increase.
create table cafe_counters (
  cafe_id      uuid primary key references cafes (id) on delete cascade,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  order_number bigint not null default 1000,
  menu_version bigint not null default 0
);

create table memberships (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  cafe_id     uuid not null references cafes (id) on delete cascade,
  role        text not null check (role in ('shift_manager', 'manager', 'owner')),
  active      boolean not null default true,
  display_name text,
  alert_phone text,
  unique (user_id, cafe_id)
);

create table platform_admins (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  user_id    uuid not null unique references auth.users (id) on delete cascade
);

create table tables (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  cafe_id    uuid not null references cafes (id) on delete cascade,
  label      text not null check (char_length(label) between 1 and 20),
  area       text,
  seats      integer check (seats between 1 and 50),
  qr_text    text not null,
  unique (cafe_id, label),
  unique (cafe_id, qr_text)
);

-- ───────────────────────────────────────────── Menu

create table menu_items (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  cafe_id      uuid not null references cafes (id) on delete cascade,
  name         text not null check (char_length(name) between 1 and 60),
  description  text check (char_length(description) <= 200),
  category     text not null check (char_length(category) between 1 and 40),
  price_paise  integer not null check (price_paise > 0),
  prep_minutes integer not null check (prep_minutes between 1 and 120),
  veg          boolean not null,
  spice        smallint not null default 0 check (spice between 0 and 3),
  available    boolean not null default true,
  photo_path   text,
  version      bigint not null
);
create unique index menu_items_cafe_name_ci on menu_items (cafe_id, lower(name));
create index menu_items_cafe_available on menu_items (cafe_id, available);

create table menu_item_allergens (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  cafe_id    uuid not null references cafes (id) on delete cascade,
  item_id    uuid not null references menu_items (id) on delete cascade,
  allergen   text not null check (allergen in ('gluten', 'milk', 'eggs', 'peanuts', 'tree_nuts', 'soy',
                                               'sesame', 'fish', 'shellfish', 'mustard', 'sulphites')),
  state      text not null default 'unknown' check (state in ('contains', 'free_from', 'unknown')),
  unique (item_id, allergen)
);

-- ───────────────────────────────────────────── Customers and conversations

create table customers (
  id              uuid primary key default gen_random_uuid(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  cafe_id         uuid not null references cafes (id) on delete cascade,
  wa_id           text not null,
  name            text check (char_length(name) <= 60),
  first_seen_at   timestamptz not null default now(),
  last_inbound_at timestamptz,
  last_visit_at   timestamptz,
  unique (cafe_id, wa_id)
);

create table customer_notes (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  cafe_id     uuid not null references cafes (id) on delete cascade,
  customer_id uuid not null references customers (id) on delete cascade,
  author_id   uuid not null references auth.users (id),
  text        text not null check (char_length(text) between 1 and 500),
  ai_status   text not null default 'pending' check (ai_status in ('pending', 'applied', 'no_changes', 'failed')),
  ai_ops      jsonb not null default '[]'::jsonb,  -- applied ops: [{op, kind, value, previous}]
  ai_summary  text check (char_length(ai_summary) <= 200),
  undone_at   timestamptz,
  undone_by   uuid references auth.users (id)
);
create index customer_notes_customer on customer_notes (customer_id, created_at desc);

create table customer_preferences (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  cafe_id     uuid not null references cafes (id) on delete cascade,
  customer_id uuid not null references customers (id) on delete cascade,
  kind        text not null check (kind in ('diet', 'milk', 'sugar', 'spice', 'seating',
                                            'allergy', 'dislike', 'like', 'other')),
  value       text not null check (char_length(value) between 1 and 40),
  source      text not null check (source in ('chat', 'staff_note')),
  note_id     uuid references customer_notes (id) on delete set null,
  unique (customer_id, kind, value)
);

create table consents (
  id              uuid primary key default gen_random_uuid(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  cafe_id         uuid not null references cafes (id) on delete cascade,
  customer_id     uuid not null references customers (id),
  type            text not null default 'marketing' check (type in ('marketing')),
  status          text not null check (status in ('opted_in', 'opted_out')),
  wording_version integer,
  source          text not null check (source in ('feedback_flow', 'counter_qr', 'order_flow',
                                                  'keyword', 'button', 'meta_block'))
);
create index consents_latest on consents (customer_id, created_at desc);

create table data_deletion_requests (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  cafe_id      uuid not null references cafes (id) on delete cascade,
  customer_id  uuid references customers (id),
  wa_id        text,
  source       text not null check (source in ('owner', 'meta_callback')),
  status       text not null default 'pending' check (status in ('pending', 'completed', 'failed')),
  completed_at timestamptz
);

-- ───────────────────────────────────────────── Orders and service (table_sessions first: conversations point at it)

create table table_sessions (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  cafe_id    uuid not null references cafes (id) on delete cascade,
  table_id   uuid not null references tables (id),
  opened_at  timestamptz not null default now(),
  closed_at  timestamptz
);
create unique index table_sessions_one_open on table_sessions (table_id) where closed_at is null;

create table conversations (
  id               uuid primary key default gen_random_uuid(),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  cafe_id          uuid not null references cafes (id) on delete cascade,
  customer_id      uuid not null references customers (id) on delete cascade,
  mode             text not null default 'bot' check (mode in ('bot', 'human')),
  assigned_to      uuid references auth.users (id),
  table_session_id uuid references table_sessions (id),
  -- step, awaiting, cart {items, version}, order_edit, clarify_strikes, allergy_note, feedback_order_id
  state            jsonb not null default '{"step": "greeting", "awaiting": null, "cart": {"items": [], "version": 0}, "clarify_strikes": 0}'::jsonb,
  lease_owner      text,
  lease_expires_at timestamptz,
  unique (cafe_id, customer_id),
  check ((lease_owner is null) = (lease_expires_at is null))
);

create table inbound_messages (
  id              uuid primary key default gen_random_uuid(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  cafe_id         uuid not null references cafes (id) on delete cascade,
  conversation_id uuid not null references conversations (id) on delete cascade,
  wamid           text not null unique,
  type            text not null,  -- text, interactive, image, audio, sticker, ... (as Meta sends it)
  payload         jsonb not null,
  received_at     timestamptz not null default now(),
  processed_at    timestamptz
);
create index inbound_messages_unprocessed on inbound_messages (conversation_id, received_at) where processed_at is null;

create table pending_work (
  id               uuid primary key default gen_random_uuid(),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  cafe_id          uuid not null references cafes (id) on delete cascade,
  conversation_id  uuid not null references conversations (id) on delete cascade,
  kind             text not null default 'turn' check (kind in ('turn')),
  status           text not null default 'pending'
                   check (status in ('pending', 'running', 'done', 'dead_lettered', 'resolved')),
  attempts         integer not null default 0,
  next_attempt_at  timestamptz not null default now(),
  last_error       text check (char_length(last_error) <= 200),  -- error class + id, never message text
  dead_lettered_at timestamptz
);
create unique index pending_work_one_pending on pending_work (conversation_id, kind) where status = 'pending';
create index pending_work_due on pending_work (status, next_attempt_at);

create table wa_messages (
  id               uuid primary key default gen_random_uuid(),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  cafe_id          uuid not null references cafes (id) on delete cascade,
  customer_id      uuid references customers (id),  -- null for alerts to staff numbers
  wamid            text unique,                      -- null until Meta accepts the send
  idempotency_key  text not null unique,
  direction        text not null default 'outbound' check (direction in ('outbound')),
  type             text not null check (type in ('text', 'interactive', 'template')),
  template_name    text,
  payload          jsonb not null,                   -- rendered outbound message (G1)
  status           text not null default 'pending'
                   check (status in ('pending', 'sent', 'delivered', 'read', 'ambiguous', 'failed')),
  attempted_at     timestamptz,
  error_code       text,
  pricing_category text,
  billable         boolean,
  cost             numeric(12, 6),
  currency         text,
  pricing_version  text,
  cost_basis       text check (cost_basis in ('estimated', 'actual')),
  sent_at          timestamptz,
  delivered_at     timestamptz,
  read_at          timestamptz,
  failed_at        timestamptz
);
create index wa_messages_customer on wa_messages (customer_id, created_at);
create index wa_messages_recovery on wa_messages (status, attempted_at) where status in ('pending', 'ambiguous');

create table orders (
  id                    uuid primary key default gen_random_uuid(),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  cafe_id               uuid not null references cafes (id) on delete cascade,
  customer_id           uuid references customers (id),  -- null for manual orders
  table_session_id      uuid references table_sessions (id),
  number                bigint not null,
  status                text not null default 'NEW' check (status in ('NEW', 'ACCEPTED', 'READY', 'SERVED', 'CANCELLED')),
  flags                 text[] not null default '{}'
                        check (flags <@ array['escalated', 'overdue', 'delayed', 'edited', 'item_sold_out', 'near_closing']::text[]),
  allergy_note          text check (char_length(allergy_note) <= 200),
  expected_minutes      integer not null check (expected_minutes between 1 and 120),
  delay_minutes         integer not null default 0 check (delay_minutes between 0 and 600),
  total_paise           integer not null check (total_paise >= 0),  -- sum of lines; prices include GST
  source                text not null check (source in ('qr', 'campaign', 'manual')),
  campaign_recipient_id uuid,  -- FK added after campaign_recipients
  idempotency_key       text not null unique,
  version               integer not null default 1,
  cancel_reason_code    text check (cancel_reason_code in ('out_of_stock', 'kitchen_issue', 'customer_asked', 'other', 'customer')),
  cancel_reason_text    text check (char_length(cancel_reason_text) <= 120),
  cancelled_by          uuid references auth.users (id),
  accepted_at           timestamptz,
  ready_at              timestamptz,
  served_at             timestamptz,
  cancelled_at          timestamptz,
  unique (cafe_id, number),
  check (status = 'CANCELLED' or (cancel_reason_code is null and cancel_reason_text is null
                                  and cancelled_by is null and cancelled_at is null)),
  check (status <> 'CANCELLED' or cancel_reason_code is not null),
  check (cancel_reason_code is distinct from 'other' or cancel_reason_text is not null)
);
create index orders_live on orders (cafe_id, status) where status in ('NEW', 'ACCEPTED', 'READY');
create index orders_customer on orders (customer_id, created_at desc);
create index orders_session on orders (table_session_id);

create table order_items (
  id                   uuid primary key default gen_random_uuid(),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  cafe_id              uuid not null references cafes (id) on delete cascade,
  order_id             uuid not null references orders (id) on delete cascade,
  item_id              uuid not null references menu_items (id),
  name_snapshot        text not null,
  price_snapshot_paise integer not null check (price_snapshot_paise > 0),
  qty                  integer not null check (qty between 1 and 20),
  note                 text check (char_length(note) <= 120)
);
create index order_items_order on order_items (order_id);

create table order_events (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  cafe_id     uuid not null references cafes (id) on delete cascade,
  order_id    uuid not null references orders (id) on delete cascade,
  from_status text check (from_status in ('NEW', 'ACCEPTED', 'READY', 'SERVED', 'CANCELLED')),
  to_status   text not null check (to_status in ('NEW', 'ACCEPTED', 'READY', 'SERVED', 'CANCELLED')),
  actor_type  text not null check (actor_type in ('customer', 'staff', 'system')),
  actor_id    uuid,
  reason      text check (char_length(reason) <= 120)
);
create index order_events_order on order_events (order_id, created_at);

create table requests (
  id               uuid primary key default gen_random_uuid(),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  cafe_id          uuid not null references cafes (id) on delete cascade,
  table_session_id uuid references table_sessions (id),
  conversation_id  uuid references conversations (id) on delete cascade,
  order_id         uuid references orders (id),
  kind             text not null check (kind in ('help', 'bill', 'allergy', 'low_rating')),
  status           text not null default 'open' check (status in ('open', 'acknowledged', 'resolved')),
  reason_tag       text check (reason_tag in ('asked', 'repeated_unclear', 'complaint', 'frustration',
                                              'allergen_unknown', 'system_error')),
  acknowledged_by  uuid references auth.users (id),
  acknowledged_at  timestamptz,
  resolved_at      timestamptz
);
create unique index requests_one_unresolved on requests (conversation_id, kind) where status <> 'resolved';
create index requests_open on requests (cafe_id, status) where status <> 'resolved';

create table escalations (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  cafe_id      uuid not null references cafes (id) on delete cascade,
  subject_type text not null check (subject_type in ('order', 'request')),
  subject_id   uuid not null,
  level        smallint not null check (level in (1, 2)),
  target       text not null,  -- 'managers' or 'owner'; never a phone number
  sent_at      timestamptz not null default now(),
  outcome      text,
  unique (subject_type, subject_id, level)
);

create table feedback (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  cafe_id    uuid not null references cafes (id) on delete cascade,
  order_id   uuid not null unique references orders (id),
  rating     smallint not null check (rating between 1 and 5),
  text       text check (char_length(text) <= 500)
);

create table notifications (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  cafe_id      uuid not null references cafes (id) on delete cascade,
  user_id      uuid not null references auth.users (id) on delete cascade,
  kind         text not null check (kind in ('order_cancelled')),
  subject_type text not null check (subject_type in ('order')),
  subject_id   uuid not null,
  payload      jsonb not null default '{}'::jsonb,  -- display fields only, never a phone number
  read_at      timestamptz
);
create index notifications_user on notifications (user_id, created_at desc);

-- ───────────────────────────────────────────── Retention, metering, audit

create table templates (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  cafe_id    uuid not null references cafes (id) on delete cascade,
  name       text not null,
  category   text not null check (category in ('utility', 'marketing')),
  language   text not null default 'en',
  status     text not null default 'pending' check (status in ('approved', 'pending', 'rejected')),
  quality    text,
  unique (cafe_id, name, language)
);

create table campaigns (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  cafe_id       uuid not null references cafes (id) on delete cascade,
  name          text not null,
  segment       text not null check (segment in ('all_opted_in', 'lapsed', 'regulars')),
  template_name text not null,
  offer_text    text,
  code_prefix   text check (code_prefix ~ '^[A-Z0-9]{2,10}$'),
  valid_until   timestamptz,
  scheduled_at  timestamptz,
  status        text not null default 'draft' check (status in ('draft', 'scheduled', 'sending', 'completed', 'cancelled'))
);

create table campaign_recipients (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  cafe_id     uuid not null references cafes (id) on delete cascade,
  campaign_id uuid not null references campaigns (id) on delete cascade,
  customer_id uuid not null references customers (id),
  code        text not null unique,
  status      text not null default 'queued'
              check (status in ('queued', 'deferred', 'sent', 'delivered', 'read', 'held', 'failed')),
  wamid       text,
  unique (campaign_id, customer_id)
);

alter table orders add constraint orders_campaign_recipient_fk
  foreign key (campaign_recipient_id) references campaign_recipients (id);

create table redemptions (
  id                    uuid primary key default gen_random_uuid(),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  cafe_id               uuid not null references cafes (id) on delete cascade,
  campaign_recipient_id uuid not null unique references campaign_recipients (id),
  code                  text not null,
  order_id              uuid references orders (id),
  table_session_id      uuid references table_sessions (id),
  discount_paise        integer not null check (discount_paise >= 0),
  redeemed_by           uuid references auth.users (id),
  redeemed_at           timestamptz not null default now()
);

create table llm_calls (
  id              uuid primary key default gen_random_uuid(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  cafe_id         uuid not null references cafes (id) on delete cascade,
  provider        text not null check (provider in ('vertex', 'developer_api', 'stub')),
  model           text not null,
  purpose         text not null check (purpose in ('turn', 'cancel_draft', 'profile_note')),
  conversation_id uuid references conversations (id) on delete set null,
  message_id      uuid references inbound_messages (id) on delete set null,
  order_id        uuid references orders (id) on delete set null,
  note_id         uuid references customer_notes (id) on delete set null,
  input_tokens    integer not null default 0,
  cached_tokens   integer not null default 0,
  output_tokens   integer not null default 0,
  cost            numeric(12, 6) not null default 0,
  currency        text not null default 'USD',
  pricing_version text not null,
  latency_ms      integer not null,
  outcome         text not null check (outcome in ('ok', 'invalid_output', 'timeout', 'rate_limited', 'error'))
);
create index llm_calls_cafe_day on llm_calls (cafe_id, created_at);

create table audit_log (
  id         uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  actor_id   uuid,
  actor_role text not null,
  support    boolean not null default false,
  cafe_id    uuid references cafes (id) on delete cascade,
  action     text not null,
  change     jsonb not null default '{}'::jsonb,  -- changed fields only; no secrets or full numbers
  at         timestamptz not null default now()
);
create index audit_log_cafe_at on audit_log (cafe_id, at desc);

create table webhook_events (
  id              uuid primary key default gen_random_uuid(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  received_at     timestamptz not null default now(),
  signature_valid boolean not null,
  payload         jsonb,
  processed_at    timestamptz
);
create index webhook_events_received on webhook_events (received_at);

-- ───────────────────────────────────────────── Triggers

do $$
declare t text;
begin
  foreach t in array array[
    'cafes', 'cafe_counters', 'memberships', 'platform_admins', 'tables', 'menu_items', 'menu_item_allergens',
    'customers', 'customer_notes', 'customer_preferences', 'data_deletion_requests', 'table_sessions',
    'conversations', 'inbound_messages', 'pending_work', 'wa_messages', 'orders', 'order_items', 'requests',
    'escalations', 'feedback', 'notifications', 'templates', 'campaigns', 'campaign_recipients', 'webhook_events'
  ] loop
    execute format('create trigger %I_touch before update on %I for each row execute function touch_updated_at()', t, t);
  end loop;
  foreach t in array array['consents', 'order_events', 'redemptions', 'llm_calls', 'audit_log'] loop
    execute format('create trigger %I_append_only before update or delete on %I for each row execute function forbid_mutation()', t, t);
  end loop;
end $$;

-- ───────────────────────────────────────────── Views (plain views; security_invoker so RLS applies to the caller)

-- Customer profile top box (docs/console.md#customer-profile). No spend, no consent, masked number only.
create view v_customer_card with (security_invoker = true) as
with visits as (
  select o.customer_id,
         count(distinct coalesce(o.table_session_id, o.id)) as visits
  from orders o
  where o.status <> 'CANCELLED' and o.customer_id is not null
  group by o.customer_id
),
recent as (
  select o.customer_id, oi.name_snapshot, sum(oi.qty) as qty,
         row_number() over (partition by o.customer_id order by sum(oi.qty) desc, oi.name_snapshot) as rn
  from orders o
  join order_items oi on oi.order_id = o.id
  where o.status <> 'CANCELLED' and o.customer_id is not null and o.created_at > now() - interval '90 days'
  group by o.customer_id, oi.name_snapshot
),
ratings as (
  select o.customer_id, round(avg(f.rating)::numeric, 1) as avg_rating, count(*) as rating_count
  from feedback f join orders o on o.id = f.order_id
  group by o.customer_id
)
select c.id as customer_id,
       c.cafe_id,
       split_part(coalesce(c.name, ''), ' ', 1) as first_name,
       case when length(c.wa_id) >= 10
            then '+' || left(c.wa_id, 2) || ' ' || substr(c.wa_id, 3, 2) || '••• ••' || right(c.wa_id, 3)
            else '••••' end as masked_phone,
       coalesce(v.visits, 0) as visits,
       c.first_seen_at,
       c.last_visit_at,
       (select array_agg(r.name_snapshot order by r.rn) from recent r where r.customer_id = c.id and r.rn <= 3) as top_items,
       case
         when c.last_visit_at is not null and c.last_visit_at < now() - interval '30 days' then 'lapsed'
         when (select count(*) from orders o2 where o2.customer_id = c.id and o2.status <> 'CANCELLED'
               and o2.created_at > now() - interval '60 days') >= 3 then 'regular'
         when coalesce(v.visits, 0) = 1 then 'new'
         else null
       end as segment,
       rt.avg_rating,
       coalesce(rt.rating_count, 0) as rating_count
from customers c
left join visits v on v.customer_id = c.id
left join ratings rt on rt.customer_id = c.id;
