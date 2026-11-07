-- Retention, audit log and recommendations (docs/retention-audit-recommend.md). Expand only.
-- Rollback: supabase/rollback/20261101000001_retention_audit_recommend_down.sql

-- ══ 1. Counter redemption (feature 1) ═══════════════════════════════════════
-- ALTER doesn't fire the append-only row trigger; existing rows are table-bill redemptions with a session.
alter table redemptions
  add column channel         text not null default 'table_bill' check (channel in ('table_bill', 'counter')),
  add column bill_paise      integer check (bill_paise >= 0),        -- before discount (counter: as entered; bill: subtotal then)
  add column idempotency_key text check (char_length(idempotency_key) between 8 and 80);

alter table redemptions add constraint redemptions_channel_shape check (
  (channel = 'table_bill' and table_session_id is not null)
  or (channel = 'counter' and table_session_id is null and order_id is null and bill_paise is not null));
alter table redemptions add constraint redemptions_discount_within_bill
  check (bill_paise is null or discount_paise <= bill_paise);

create unique index redemptions_idempotency on redemptions (cafe_id, idempotency_key) where idempotency_key is not null;
create index redemptions_cafe_at on redemptions (cafe_id, redeemed_at desc);
create index campaign_recipients_customer on campaign_recipients (customer_id);
create index inbound_messages_conversation on inbound_messages (conversation_id, received_at);

-- ══ 2. Audit log viewer (feature 2) ═════════════════════════════════════════
alter table audit_log
  add column severity        text check (severity in ('critical', 'warning', 'info')),
  add column order_id        uuid,
  add column customer_id     uuid,
  add column conversation_id uuid,
  add column actor_name      text;
comment on column audit_log.severity is 'From app/domain/audit/catalog.py classify() at write time; not null after 20261101000002';
comment on column audit_log.actor_name is 'Display name snapshot at write time (membership may be removed later)';

-- One-time backfill of the derived columns only (decision 19). Called again by 20261101000002, then dropped.
create or replace function audit_log_backfill_derived() returns integer language plpgsql as $$
declare n integer;
begin
  alter table audit_log disable trigger audit_log_append_only;
  update audit_log a set
    order_id        = case when a.change->>'order_id' ~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then (a.change->>'order_id')::uuid end,
    customer_id     = case when a.change->>'customer_id' ~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then (a.change->>'customer_id')::uuid end,
    conversation_id = case when a.change->>'conversation_id' ~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then (a.change->>'conversation_id')::uuid end,
    actor_name      = (select m.display_name from memberships m where m.cafe_id = a.cafe_id and m.user_id = a.actor_id),
    severity = case
      when a.action in ('order.cancel', 'customer.delete', 'consent.export') then 'critical'
      when a.action = 'customer.profile_update' and a.change->'ops' @> '[{"op": "remove", "kind": "allergy"}]' then 'critical'
      when a.action = 'team.invite' and a.change->>'role' = 'owner' then 'critical'
      when a.support or a.actor_role = 'platform_admin' then 'warning'
      when a.action in ('order.undo', 'ordering.pause', 'campaign.schedule', 'campaign.cancel', 'campaign.export',
                        'menu.import', 'team.update') then 'warning'
      when a.action = 'menu.availability' and a.change->>'available' = 'false'
           and coalesce((a.change->>'orders_flagged')::int, 0) > 0 then 'warning'
      when a.action = 'order.delay' and coalesce((a.change->>'minutes')::int, 0) >= 15 then 'warning'
      when a.action = 'offer.redeem' and coalesce((a.change->>'discount_paise')::int, 0) >= 50000 then 'warning'
      when a.action = 'settings.update' and a.change->'fields' ? 'alert_phone' then 'warning'
      else 'info' end
  where a.severity is null;
  get diagnostics n = row_count;
  alter table audit_log enable trigger audit_log_append_only;
  return n;
end $$;
-- Functions get EXECUTE for PUBLIC by default and public-schema functions are exposed as PostgREST RPC: lock it down
-- like next_order_number (20261024000004). Only the migration role (owner) runs it.
revoke all on function audit_log_backfill_derived() from public, anon, authenticated;
select audit_log_backfill_derived();

-- Keyset paging (at desc, id desc) replaces (cafe_id, at desc).
create index audit_log_cafe_at_id on audit_log (cafe_id, at desc, id desc);
drop index audit_log_cafe_at;
create index audit_log_cafe_order        on audit_log (cafe_id, order_id, at desc)        where order_id is not null;
create index audit_log_cafe_customer     on audit_log (cafe_id, customer_id, at desc)     where customer_id is not null;
create index audit_log_cafe_conversation on audit_log (cafe_id, conversation_id, at desc) where conversation_id is not null;
create index audit_log_cafe_actor        on audit_log (cafe_id, actor_id, at desc);
create index audit_log_cafe_flagged      on audit_log (cafe_id, severity, at desc)        where severity <> 'info';
-- RLS unchanged: audit_log_platform_read only. The owner reads through the API (decision 19). Not in Realtime.

-- ══ 3. LLM purpose 'recommend' (feature 3) ══════════════════════════════════
alter table llm_calls drop constraint llm_calls_purpose_check;
alter table llm_calls add constraint llm_calls_purpose_check
  check (purpose in ('turn', 'cancel_draft', 'profile_note', 'recommend'));

-- ══ 4. Campaign views (feature 1) ═══════════════════════════════════════════
-- v_campaign_recipients: one row per recipient with everything after the send. security_invoker; owner-only through
-- campaign_recipients RLS (inner joins). No has_role() so the backend (server role) reuses it for the CSV, always with cafe_id.
create view v_campaign_recipients with (security_invoker = true) as
  select r.id as recipient_id, r.cafe_id, r.campaign_id, r.customer_id, r.code, r.status,
         cu.name,
         case when cu.wa_id like '000%' then null
              when length(cu.wa_id) >= 10
              then '+' || left(cu.wa_id, 2) || ' ' || substr(cu.wa_id, 3, 2) || '••• ••' || right(cu.wa_id, 3)
              else '••••' end as masked_phone,
         (cu.wa_id like '000%') as deleted,
         r.created_at as queued_at,
         w.sent_at, w.delivered_at, w.read_at, w.failed_at, w.error_code,
         coalesce(w.cost, 0)::numeric(12, 6) as whatsapp_cost,
         win.window_start, win.window_end,
         rep.replied_at,
         coalesce(ord.orders, 0)::int as orders,
         coalesce(ord.spent_paise, 0)::bigint as spent_paise,
         ord.first_order_at,
         d.id as redemption_id, d.redeemed_at, d.channel as redemption_channel,
         coalesce(d.bill_paise, sess.subtotal_paise)::bigint as bill_paise,
         d.discount_paise, d.redeemed_by, m.display_name as redeemed_by_name,
         oo.opted_out_at, oo.source as opted_out_source,
         (coalesce(ord.orders, 0) > 0 or d.id is not null) as came_back
  from campaign_recipients r
  join campaigns ca on ca.id = r.campaign_id
  join customers cu on cu.id = r.customer_id
  left join wa_messages w on w.idempotency_key = 'campaign:' || r.id::text
  cross join lateral (
    select case when r.status in ('sent', 'delivered', 'read', 'held')
                then coalesce(w.sent_at, w.attempted_at, w.created_at, r.updated_at) end as window_start) s
  cross join lateral (
    select s.window_start,
           least(now(), coalesce(ca.valid_until, s.window_start + interval '30 days')) as window_end) win
  left join lateral (
    select min(i.received_at) as replied_at
    from conversations cv join inbound_messages i on i.conversation_id = cv.id
    where cv.cafe_id = r.cafe_id and cv.customer_id = r.customer_id
      and i.received_at > win.window_start and i.received_at <= win.window_end) rep on true
  left join lateral (
    select count(*) as orders, sum(o.total_paise) as spent_paise, min(o.created_at) as first_order_at
    from orders o
    where o.cafe_id = r.cafe_id and o.customer_id = r.customer_id and o.status <> 'CANCELLED'
      and o.created_at > win.window_start and o.created_at <= win.window_end) ord on true
  left join redemptions d on d.campaign_recipient_id = r.id
  left join memberships m on m.cafe_id = d.cafe_id and m.user_id = d.redeemed_by
  left join lateral (
    select sum(o2.total_paise) as subtotal_paise from orders o2
    where d.bill_paise is null and d.table_session_id is not null
      and o2.table_session_id = d.table_session_id and o2.status <> 'CANCELLED') sess on true
  left join lateral (
    select sc.created_at as opted_out_at, sc.source from consents sc
    where sc.customer_id = r.customer_id and sc.status = 'opted_out'
      and sc.created_at > win.window_start and sc.created_at <= win.window_end
    order by sc.created_at limit 1) oo on true;

-- v_campaign_insights: per-campaign funnel and money. Same visibility. queued = queued + deferred; came_back = ordered in
-- the window or redeemed.
create view v_campaign_insights with (security_invoker = true) as
  select ca.id as campaign_id, ca.cafe_id, ca.name, ca.status, ca.segment, ca.template_name, ca.offer_text,
         ca.discount_percent, ca.code_prefix, ca.valid_until, ca.scheduled_at, ca.created_at,
         count(x.recipient_id)::int as recipients,
         count(x.recipient_id) filter (where x.status in ('queued', 'deferred'))::int as queued,
         count(x.recipient_id) filter (where x.status = 'deferred')::int as deferred,
         count(x.recipient_id) filter (where x.status in ('sent', 'delivered', 'read', 'held'))::int as sent,
         count(x.recipient_id) filter (where x.status = 'held')::int as held,
         count(x.recipient_id) filter (where x.status in ('delivered', 'read'))::int as delivered,
         count(x.recipient_id) filter (where x.status = 'read')::int as read,
         count(x.recipient_id) filter (where x.status = 'failed')::int as failed,
         count(x.replied_at)::int as replied,
         count(x.recipient_id) filter (where x.orders > 0)::int as ordered,
         count(x.redemption_id)::int as redeemed,
         count(x.redemption_id) filter (where x.redemption_channel = 'counter')::int as redeemed_counter,
         count(x.redemption_id) filter (where x.redemption_channel = 'table_bill')::int as redeemed_table_bill,
         count(x.recipient_id) filter (where x.came_back)::int as came_back,
         count(x.opted_out_at)::int as opted_out,
         coalesce(sum(x.spent_paise), 0)::bigint as ordered_paise,
         coalesce(sum(x.bill_paise) filter (where x.redemption_channel = 'counter'), 0)::bigint as counter_bill_paise,
         (coalesce(sum(x.spent_paise), 0)
          + coalesce(sum(x.bill_paise) filter (where x.redemption_channel = 'counter'), 0))::bigint as came_back_paise,
         coalesce(sum(x.discount_paise), 0)::bigint as discount_paise,
         coalesce(sum(x.whatsapp_cost), 0)::numeric(12, 4) as whatsapp_cost
  from campaigns ca
  left join v_campaign_recipients x on x.campaign_id = ca.id
  group by ca.id;

-- v_redemptions: every redemption with names, for "Redeemed today" (every member) and owner insights. Definer view
-- (like v_staff_names) filtered by membership; masked number only, null when deleted.
create view v_redemptions as
  select d.id as redemption_id, d.cafe_id, r.campaign_id, ca.name as campaign_name, d.campaign_recipient_id,
         d.code, d.channel, d.redeemed_at, (d.redeemed_at at time zone cf.timezone)::date as day,
         r.customer_id, nullif(split_part(coalesce(cu.name, ''), ' ', 1), '') as first_name,
         case when cu.wa_id like '000%' then null
              when length(cu.wa_id) >= 10
              then '+' || left(cu.wa_id, 2) || ' ' || substr(cu.wa_id, 3, 2) || '••• ••' || right(cu.wa_id, 3)
              else '••••' end as masked_phone,
         coalesce(d.bill_paise, sess.subtotal_paise)::bigint as bill_paise, d.discount_paise,
         d.redeemed_by, m.display_name as redeemed_by_name,
         o.number as order_number, t.label as table_label, d.table_session_id
  from redemptions d
  join campaign_recipients r on r.id = d.campaign_recipient_id
  join campaigns ca on ca.id = r.campaign_id
  join customers cu on cu.id = r.customer_id
  join cafes cf on cf.id = d.cafe_id
  left join memberships m on m.cafe_id = d.cafe_id and m.user_id = d.redeemed_by
  left join orders o on o.id = d.order_id
  left join table_sessions ts on ts.id = d.table_session_id
  left join tables t on t.id = ts.table_id
  left join lateral (
    select sum(o2.total_paise) as subtotal_paise from orders o2
    where d.bill_paise is null and d.table_session_id is not null
      and o2.table_session_id = d.table_session_id and o2.status <> 'CANCELLED') sess on true
  where is_member(d.cafe_id);

-- ══ 5. Grants: views are read-only for clients ══════════════════════════════
do $$
declare v text;
begin
  foreach v in array array['v_campaign_recipients', 'v_campaign_insights', 'v_redemptions'] loop
    execute format('revoke all on %I from anon', v);
    execute format('revoke insert, update, delete, truncate on %I from authenticated', v);
    execute format('grant select on %I to authenticated', v);
  end loop;
end $$;
