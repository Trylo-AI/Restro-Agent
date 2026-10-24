-- Realtime publication: only the tables in contracts/events.md, each with an RLS read policy.
alter publication supabase_realtime add table
  orders, requests, conversations, inbound_messages, wa_messages, menu_items, cafes, notifications,
  customer_notes, customer_preferences;

-- DELETE events need the old row's cafe_id for the channel filter and the console's chip removal.
alter table customer_preferences replica identity full;
