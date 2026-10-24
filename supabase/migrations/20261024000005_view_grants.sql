-- Views are read-only for clients: take away the default write grants Supabase gives on new relations.
revoke insert, update, delete, truncate on v_customer_card from anon, authenticated;
