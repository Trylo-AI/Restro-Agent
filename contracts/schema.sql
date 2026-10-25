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
  status        text not null default 'draft' check (status in ('draft', 'scheduled', 'sending', 'completed', 'cancelled')),
  discount_percent integer check (discount_percent between 1 and 90)  -- % of the bill subtotal
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
  provider        text not null check (provider in ('vertex', 'developer_api', 'stub', 'dashscope')),  -- dashscope: testing only
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


-- ───────────────────────────────────────────── Staff names and analytics views (docs/data-and-api.md#analytics-views)

-- Staff display names for every member of the café ("Acknowledged by Ravi"). Memberships RLS shows a member only
-- their own row, so this view runs as its owner and filters by membership itself. Names only.
create view v_staff_names as
  select m.cafe_id, m.user_id, m.display_name, m.role, m.active
  from memberships m
  where is_member(m.cafe_id);

-- ── Owner: revenue, dishes, segments, profiles, campaigns ─────────────────

create view v_daily_revenue with (security_invoker = true) as
  with o as (
    select o.cafe_id, o.customer_id, o.total_paise,
           (o.created_at at time zone c.timezone)::date as day,
           extract(hour from o.created_at at time zone c.timezone)::int as hour
    from orders o join cafes c on c.id = o.cafe_id
    where o.status <> 'CANCELLED' and has_role(o.cafe_id, 'owner')
  ),
  d as (
    select cafe_id, day, sum(total_paise)::bigint as revenue_paise, count(*)::int as orders,
           (sum(total_paise) / count(*))::int as avg_order_paise, count(distinct customer_id)::int as customers
    from o group by cafe_id, day
  ),
  h as (
    select cafe_id, day, jsonb_object_agg(hour::text, n) as orders_by_hour
    from (select cafe_id, day, hour, count(*) as n from o group by cafe_id, day, hour) x
    group by cafe_id, day
  )
  select d.*, h.orders_by_hour from d join h using (cafe_id, day);

create view v_top_items with (security_invoker = true) as
  select o.cafe_id, (o.created_at at time zone c.timezone)::date as day, oi.name_snapshot as name,
         sum(oi.qty)::int as qty, sum(oi.qty * oi.price_snapshot_paise)::bigint as revenue_paise
  from order_items oi
  join orders o on o.id = oi.order_id
  join cafes c on c.id = o.cafe_id
  where o.status <> 'CANCELLED' and has_role(o.cafe_id, 'owner')
  group by o.cafe_id, (o.created_at at time zone c.timezone)::date, oi.name_snapshot;

-- Segments (docs/backend.md#customers-and-feedback), checked in order: lapsed, regular, new.
create view v_customer_segments with (security_invoker = true) as
  with v as (
    select cu.id as customer_id, cu.cafe_id, cu.last_visit_at,
           count(o.id) filter (where o.status <> 'CANCELLED') as visits,
           count(o.id) filter (where o.status <> 'CANCELLED' and o.created_at > now() - interval '60 days') as orders_60d
    from customers cu left join orders o on o.customer_id = cu.id
    group by cu.id
  )
  select customer_id, cafe_id, visits::int,
         case when visits = 0 then null
              when last_visit_at < now() - interval '30 days' then 'lapsed'
              when orders_60d >= 3 then 'regular'
              when visits = 1 then 'new' end as segment,
         (visits > 1) as returning
  from v
  where has_role(cafe_id, 'owner');

create view v_customer_profile with (security_invoker = true) as
  with spend as (
    select customer_id, count(*)::int as visits, coalesce(sum(total_paise), 0)::bigint as spend_paise
    from orders where status <> 'CANCELLED' and customer_id is not null group by customer_id
  ),
  latest_consent as (
    select distinct on (customer_id) customer_id, status, created_at, source
    from consents order by customer_id, created_at desc
  ),
  camp as (
    select r.customer_id, count(*)::int as received,
           count(d.id)::int as redeemed
    from campaign_recipients r left join redemptions d on d.campaign_recipient_id = r.id
    where r.status in ('sent', 'delivered', 'read', 'held') group by r.customer_id
  )
  select cu.id as customer_id, cu.cafe_id, cu.name,
         case when length(cu.wa_id) >= 10
              then '+' || left(cu.wa_id, 2) || ' ' || substr(cu.wa_id, 3, 2) || '••• ••' || right(cu.wa_id, 3)
              else '••••' end as masked_phone,
         coalesce(s.visits, 0) as visits, coalesce(s.spend_paise, 0) as spend_paise,
         cu.first_seen_at, cu.last_visit_at,
         card.top_items, card.avg_rating, card.rating_count,
         coalesce(lc.status, 'not_asked') as consent_status, lc.created_at as consent_at, lc.source as consent_source,
         coalesce(cp.received, 0) as campaigns_received, coalesce(cp.redeemed, 0) as campaigns_redeemed,
         seg.segment
  from customers cu
  left join spend s on s.customer_id = cu.id
  left join latest_consent lc on lc.customer_id = cu.id
  left join camp cp on cp.customer_id = cu.id
  left join v_customer_card card on card.customer_id = cu.id
  left join v_customer_segments seg on seg.customer_id = cu.id
  where has_role(cu.cafe_id, 'owner');

create view v_campaign_funnel with (security_invoker = true) as
  select ca.id as campaign_id, ca.cafe_id, ca.name, ca.status, ca.segment, ca.scheduled_at,
         count(r.id) filter (where r.status in ('sent', 'delivered', 'read', 'held'))::int as sent,
         count(r.id) filter (where r.status = 'held')::int as held,
         count(r.id) filter (where r.status in ('delivered', 'read'))::int as delivered,
         count(r.id) filter (where r.status = 'read')::int as read,
         count(r.id) filter (where r.status in ('queued', 'deferred'))::int as queued,
         count(d.id)::int as came_back,
         coalesce(sum(o.total_paise) filter (where o.status <> 'CANCELLED'), 0)::bigint as spent_paise,
         coalesce(sum(d.discount_paise), 0)::bigint as discount_paise,
         coalesce(sum(w.cost), 0)::numeric(12, 4) as whatsapp_cost
  from campaigns ca
  left join campaign_recipients r on r.campaign_id = ca.id
  left join redemptions d on d.campaign_recipient_id = r.id
  left join orders o on o.id = d.order_id
  left join wa_messages w on w.wamid = r.wamid and w.wamid is not null
  where has_role(ca.cafe_id, 'owner')
  group by ca.id;

-- ── Manager: service report ───────────────────────────────────────────────

-- Placed → ready per order and dish. Overdue = flagged overdue (the timer re-checks the promised time).
create view v_prep_times with (security_invoker = true) as
  select o.cafe_id, o.id as order_id, o.number, oi.name_snapshot as dish,
         o.created_at, o.ready_at,
         (o.created_at at time zone c.timezone)::date as day,
         extract(hour from o.created_at at time zone c.timezone)::int as hour,
         round(extract(epoch from o.ready_at - o.created_at) / 60.0, 1) as prep_minutes,
         o.expected_minutes + o.delay_minutes as promised_minutes,
         ('overdue' = any (o.flags)) as overdue
  from orders o
  join order_items oi on oi.order_id = o.id
  join cafes c on c.id = o.cafe_id
  where o.status <> 'CANCELLED' and o.ready_at is not null and has_role(o.cafe_id, 'manager');

create view v_service_report with (security_invoker = true) as
  with o as (
    select distinct on (order_id) cafe_id, order_id, prep_minutes, overdue, hour
    from v_prep_times where created_at > now() - interval '7 days'
  ),
  dish as (
    select cafe_id, dish, percentile_cont(0.5) within group (order by prep_minutes) as median_minutes, count(*) as n
    from v_prep_times where created_at > now() - interval '7 days' group by cafe_id, dish
  ),
  peak as (
    select distinct on (cafe_id) cafe_id, hour from o where overdue
    group by cafe_id, hour order by cafe_id, count(*) desc
  )
  select o.cafe_id,
         count(*)::int as orders,
         round(percentile_cont(0.5) within group (order by o.prep_minutes)::numeric, 1) as median_prep_minutes,
         round(avg(o.overdue::int) * 100, 1) as pct_overdue,
         (select p.hour from peak p where p.cafe_id = o.cafe_id) as peak_overdue_hour,
         (select jsonb_agg(jsonb_build_object('dish', d.dish, 'median_minutes', round(d.median_minutes::numeric, 1),
                                              'orders', d.n) order by d.median_minutes desc)
          from (select * from dish d2 where d2.cafe_id = o.cafe_id order by median_minutes desc limit 3) d) as slowest_dishes
  from o group by o.cafe_id;

create view v_feedback_summary with (security_invoker = true) as
  select f.cafe_id, f.order_id, o.number, f.rating, f.text, f.created_at,
         (f.created_at at time zone c.timezone)::date as day
  from feedback f
  join orders o on o.id = f.order_id
  join cafes c on c.id = f.cafe_id
  where has_role(f.cafe_id, 'manager');

-- ── Performance dashboard (docs/OPS_PLAN.md) ─────────────────────────────────────────────────
-- Written only by backend/app/ops/tracing; read only by /api/ops. RLS on, no policies, no client grants.
create table ops_runs (
  run_id              uuid primary key,
  parent_run_id       uuid,
  upstream_trace      varchar(64),
  upstream_service    varchar(100),
  service             varchar(100) not null,
  kind                varchar(20)  not null check (kind in ('http', 'job', 'cron', 'message', 'cli', 'ws')),
  name                varchar(150) not null,
  batch_type          varchar(50),
  batch_id            varchar(100),
  subject_type        varchar(50),
  subject_id          varchar(100),
  tenant_id           varchar(100),
  attempt             int not null default 1,
  instance            varchar(100),
  queued_at           timestamptz,
  started_at          timestamptz not null,
  finished_at         timestamptz,
  duration_ms         double precision,
  queue_wait_ms       double precision,
  current_stage       varchar(80),
  outcome             varchar(24) not null default 'RUNNING' check (outcome in
                        ('RUNNING', 'SUCCEEDED', 'SUCCEEDED_DEGRADED', 'REJECTED', 'RETRYING', 'FAILED', 'CANCELLED',
                         'SUPERSEDED', 'ABANDONED')),
  http_status         smallint,
  error_stage         varchar(80),
  error_code          varchar(80),
  error_type          varchar(200),
  failure_reason      text check (char_length(failure_reason) <= 500),
  failure_fingerprint varchar(16),
  degradations        jsonb not null default '[]',
  git_sha             varchar(40),
  app_version         varchar(50),
  config_hash         varchar(16),
  settings            jsonb not null default '{}',
  labels              jsonb not null default '{}',
  input_size          double precision,
  input_size_unit     varchar(20),
  counts              jsonb not null default '{}',
  stage_ms            jsonb not null default '{}',
  dep_ms              jsonb not null default '{}',
  dep_calls           jsonb not null default '{}',
  cost_usd            double precision,          -- null: no price tracking yet (OPS_PLAN decisions)
  bytes_in            bigint,
  bytes_out           bigint,
  cpu_ms              double precision,
  peak_mem_mb         int,
  gpu_peak_mb         int,
  sampled             boolean not null default true,
  span_count          int not null default 0
);
create index ix_ops_runs_started  on ops_runs (started_at desc);
create index ix_ops_runs_name     on ops_runs (kind, name, started_at desc);
create index ix_ops_runs_batch    on ops_runs (batch_id, started_at);
create index ix_ops_runs_subject  on ops_runs (subject_type, subject_id, started_at desc);
create index ix_ops_runs_failures on ops_runs (failure_fingerprint, started_at desc)
  where outcome in ('FAILED', 'RETRYING', 'ABANDONED');
create index ix_ops_runs_running  on ops_runs (started_at) where outcome = 'RUNNING';
create index ix_ops_runs_upstream on ops_runs (upstream_trace) where upstream_trace is not null;

create table ops_spans (
  run_id       uuid not null references ops_runs (run_id) on delete cascade,
  span_id      int  not null,
  parent_id    int,
  name         varchar(100) not null,
  level        smallint not null check (level between 1 and 3),
  kind         varchar(20) not null,
  start_ms     double precision not null,
  duration_ms  double precision not null,
  outcome      varchar(10) not null check (outcome in ('ok', 'failed', 'degraded')),
  target       varchar(150),
  attrs        jsonb not null default '{}',
  error        text,
  primary key (run_id, span_id)
);
create index ix_ops_spans_name   on ops_spans (name, level);
create index ix_ops_spans_target on ops_spans (kind, target);

create table ops_run_events (
  run_id     uuid not null references ops_runs (run_id) on delete cascade,
  seq        int  not null,
  at_ms      double precision not null,
  level      varchar(10) not null check (level in ('WARNING', 'ERROR', 'DEGRADED')),
  span_name  varchar(100),
  logger     varchar(150),
  message    text not null,
  detail     jsonb,
  primary key (run_id, seq)
);

-- Deploy markers: a row at startup when the git sha differs from the last one for the service.
create table ops_deploys (
  at          timestamptz not null,
  service     varchar(100) not null,
  git_sha     varchar(40),
  app_version varchar(50),
  primary key (service, at)
);

-- One row per running process, heartbeat every 15 s. The sweeper marks RUNNING runs of a silent instance ABANDONED.
create table ops_instances (
  instance    varchar(100) primary key,
  service     varchar(100) not null,
  git_sha     varchar(40),
  started_at  timestamptz not null,
  last_seen   timestamptz not null
);

-- Insights the team dismissed (the only write the ops API makes).
create table ops_insight_state (
  finding_key varchar(200) primary key,
  dismissed_at timestamptz not null default now(),
  note        text check (char_length(note) <= 500)
);
