-- Per-café counters (docs/data-and-api.md: order number allocated inside the order transaction,
-- menu version from a café-wide counter). Row lock on cafe_counters serialises allocation per café;
-- gaps are allowed (a rolled-back transaction), duplicates never.

create or replace function create_cafe_counters() returns trigger language plpgsql as $$
begin
  insert into cafe_counters (cafe_id) values (new.id) on conflict do nothing;
  return new;
end $$;

create trigger cafes_counters after insert on cafes for each row execute function create_cafe_counters();

create or replace function next_order_number(cafe uuid) returns bigint language sql as $$
  update cafe_counters set order_number = order_number + 1 where cafe_id = cafe returning order_number;
$$;

create or replace function next_menu_version(cafe uuid) returns bigint language sql as $$
  update cafe_counters set menu_version = menu_version + 1 where cafe_id = cafe returning menu_version;
$$;

revoke all on function next_order_number(uuid), next_menu_version(uuid) from public, anon, authenticated;
