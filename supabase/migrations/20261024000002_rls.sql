-- Row-level security: read policies only (docs/data-and-api.md#row-level-security).
-- No INSERT / UPDATE / DELETE policy anywhere; every write goes through `api` (server role, bypasses RLS).

create or replace function is_platform_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from platform_admins where user_id = auth.uid());
$$;

-- Role rank: shift_manager 1 < manager 2 < owner 3.
create or replace function role_rank(role text) returns integer
language sql immutable as $$
  select case role when 'shift_manager' then 1 when 'manager' then 2 when 'owner' then 3 else 0 end;
$$;

create or replace function has_role(cafe uuid, min_role text) returns boolean
language sql stable security definer set search_path = public as $$
  select is_platform_admin() or exists (
    select 1 from memberships m
    where m.user_id = auth.uid() and m.cafe_id = cafe and m.active
      and role_rank(m.role) >= role_rank(min_role)
  );
$$;

create or replace function is_member(cafe uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select has_role(cafe, 'shift_manager');
$$;

revoke all on function is_platform_admin(), has_role(uuid, text), is_member(uuid) from public;
grant execute on function is_platform_admin(), has_role(uuid, text), is_member(uuid), role_rank(text) to authenticated;

-- Enable RLS on every table and take away client write privileges (belt and braces: no write policies exist).
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table %I enable row level security', t);
    execute format('revoke insert, update, delete, truncate on %I from anon, authenticated', t);
    execute format('revoke all on %I from anon', t);
  end loop;
end $$;

-- Every member of the café.
do $$
declare t text;
begin
  foreach t in array array[
    'tables', 'menu_items', 'menu_item_allergens', 'customers', 'customer_preferences', 'customer_notes',
    'feedback', 'conversations', 'inbound_messages', 'wa_messages', 'table_sessions', 'orders', 'order_items',
    'escalations', 'redemptions'
  ] loop
    execute format('create policy %I on %I for select to authenticated using (is_member(cafe_id))', t || '_member_read', t);
  end loop;
end $$;

create policy cafes_member_read on cafes for select to authenticated using (is_member(id));

-- Requests: low ratings only for manager+.
create policy requests_member_read on requests for select to authenticated
  using (case when kind = 'low_rating' then has_role(cafe_id, 'manager') else is_member(cafe_id) end);

-- Own rows.
create policy memberships_read on memberships for select to authenticated
  using (user_id = auth.uid() or has_role(cafe_id, 'owner'));
create policy platform_admins_read on platform_admins for select to authenticated
  using (user_id = auth.uid());
create policy notifications_read on notifications for select to authenticated
  using (user_id = auth.uid() and has_role(cafe_id, 'manager'));

-- Owner.
do $$
declare t text;
begin
  foreach t in array array['consents', 'templates', 'campaigns', 'campaign_recipients'] loop
    execute format('create policy %I on %I for select to authenticated using (has_role(cafe_id, ''owner''))', t || '_owner_read', t);
  end loop;
end $$;

-- Platform admins.
create policy llm_calls_platform_read on llm_calls for select to authenticated using (is_platform_admin());
create policy audit_log_platform_read on audit_log for select to authenticated using (is_platform_admin());
create policy webhook_events_platform_read on webhook_events for select to authenticated using (is_platform_admin());
create policy pending_work_platform_read on pending_work for select to authenticated using (is_platform_admin());

-- Backend only (RLS on, no policy): order_events, data_deletion_requests, cafe_counters.

-- Views run as the caller (security_invoker), so the policies above apply.
revoke all on v_customer_card from anon;
grant select on v_customer_card to authenticated;
