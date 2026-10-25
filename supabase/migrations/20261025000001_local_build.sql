-- Local build L1 (docs/local-build.md): staff names, campaign discount, analytics views.
-- Views use security_invoker, so the caller's RLS on the base tables applies, plus the role each view is for
-- (docs/data-and-api.md#row-level-security). Days are in the café's time zone. Cancelled orders never count.

-- Campaign discount: "% of subtotal" (open-decision default) needs a column.
alter table campaigns add column discount_percent integer check (discount_percent between 1 and 90);

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

-- Views are read-only for clients.
do $$
declare v text;
begin
  foreach v in array array['v_staff_names', 'v_daily_revenue', 'v_top_items', 'v_customer_segments', 'v_customer_profile',
                           'v_campaign_funnel', 'v_prep_times', 'v_service_report', 'v_feedback_summary'] loop
    execute format('revoke all on %I from anon', v);
    execute format('revoke insert, update, delete, truncate on %I from authenticated', v);
    execute format('grant select on %I to authenticated', v);
  end loop;
end $$;
