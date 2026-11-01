// Live data (docs/data-and-api.md#realtime, contracts/events.md).
// Subscribe first, then read; any change event refetches the query (no gap, nothing patched by hand).
// Never trust that every event arrived: refetch on reconnect, tab visible again, café switch, and every 30 s.
// Hooks reading the same query share one channel, and a burst of events becomes one refetch now plus at most
// one more per second (a new order still shows at once; a chat burst no longer refetches per status update).
import { useQuery, useQueryClient, type QueryClient, type UseQueryResult } from "@tanstack/react-query";
import type { RealtimeChannel } from "@supabase/supabase-js";
import { useEffect } from "react";

import { supabase } from "./supabase";

export const REFETCH_MS = 30_000;
export const COALESCE_MS = 1_000;

type Table =
  | "orders" | "requests" | "conversations" | "inbound_messages" | "wa_messages" | "menu_items" | "cafes"
  | "notifications" | "customer_notes" | "customer_preferences";

/** A table, or a table and the one change type that matters (e.g. only new messages, not status updates). */
export type Watch = Table | { table: Table; event: "INSERT" | "UPDATE" | "DELETE" };

export interface LiveOptions<T> {
  key: unknown[];
  enabled: boolean;
  tables: Watch[];
  cafeId: string | null;
  fetcher: () => Promise<T>;
  /** Extra Realtime filter, e.g. `customer_id=eq.<id>` (defaults to the active café). */
  filter?: string;
}

interface Shared {
  channel: RealtimeChannel;
  refs: number;
  last: number;
  timer: number | null;
}

const shared = new Map<string, Shared>();

function refetchSoon(entry: Shared, qc: QueryClient, key: unknown[]): void {
  if (entry.timer !== null) return; // a trailing refetch is already due
  const run = () => {
    entry.timer = null;
    entry.last = Date.now();
    void qc.invalidateQueries({ queryKey: key });
  };
  const wait = entry.last + COALESCE_MS - Date.now();
  if (wait <= 0) run();
  else entry.timer = window.setTimeout(run, wait);
}

export function useLive<T>({ key, enabled, tables, cafeId, fetcher, filter }: LiveOptions<T>): UseQueryResult<T> {
  const qc = useQueryClient();
  const watch = tables.map((w) => (typeof w === "string" ? { table: w, event: "*" as const } : w));
  const id = JSON.stringify([key, watch, filter ?? cafeId]);

  useEffect(() => {
    if (!enabled || !cafeId || !watch.length) return;
    let entry = shared.get(id);
    if (!entry) {
      const name = `live:${watch.map((w) => w.table).join(",")}:${filter ?? cafeId}:${Math.random().toString(36).slice(2, 8)}`;
      let channel = supabase.channel(name);
      const e: Shared = { channel, refs: 0, last: 0, timer: null };
      for (const { table, event } of watch) {
        channel = channel.on(
          "postgres_changes",
          { event, schema: "public", table, filter: filter ?? `${table === "cafes" ? "id" : "cafe_id"}=eq.${cafeId}` },
          () => refetchSoon(e, qc, key),
        );
      }
      channel.subscribe((status) => {
        if (status === "SUBSCRIBED") void qc.invalidateQueries({ queryKey: key }); // read after subscribing
      });
      entry = e;
      shared.set(id, entry);
    }
    entry.refs++;
    const mine = entry;
    return () => {
      if (--mine.refs > 0) return;
      if (mine.timer !== null) window.clearTimeout(mine.timer);
      shared.delete(id);
      void supabase.removeChannel(mine.channel);
    };
  }, [enabled, cafeId, id, qc]); // eslint-disable-line react-hooks/exhaustive-deps

  return useQuery({
    queryKey: key,
    queryFn: fetcher,
    enabled: enabled && !!cafeId,
    refetchInterval: REFETCH_MS,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    staleTime: 2_000,
  });
}

/** Throw on a Supabase read error so react-query keeps the last good data and retries. */
export async function rows<T>(p: PromiseLike<{ data: unknown; error: { message: string } | null }>): Promise<T> {
  const { data, error } = await p;
  if (error) throw new Error(error.message);
  return data as T;
}
