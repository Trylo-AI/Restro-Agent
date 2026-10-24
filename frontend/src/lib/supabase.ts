// The only Supabase client in the console: login and reads (RLS + Realtime). Never used for writes.
import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

if (!url || !key) {
  // Fails loudly in dev; frontend/.env.local holds the public values.
  console.error("VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY must be set in frontend/.env.local");
}

export const supabase = createClient(url ?? "http://localhost", key ?? "missing", {
  auth: { persistSession: true, autoRefreshToken: true },
  realtime: { params: { eventsPerSecond: 20 } },
});
