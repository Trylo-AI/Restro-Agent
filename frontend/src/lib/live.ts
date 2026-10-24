// Live data (docs/data-and-api.md#realtime, contracts/events.md).
// Subscribe first, then read; any change event refetches the query (no gap, nothing patched by hand).
// Never trust that every event arrived: refetch on reconnect, tab visible again, café switch, and every 30 s.
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { supabase } from "./supabase";

export const REFETCH_MS = 30_000;

type Table =
  | "orders" | "requests" | "conversations" | "inbound_messages" | "wa_messages" | "menu_items" | "cafes"
  | "notifications" | "customer_notes" | "customer_preferences";

export interface LiveOptions<T> {
  key: unknown[];
  enabled: boolean;
  tables: Table[];
  cafeId: string | null;
  fetcher: () => Promise<T>;
  /** Extra Realtime filter, e.g. `customer_id=eq.<id>` (defaults to the active café). */
  filter?: string;
}

export function useLive<T>({ key, enabled, tables, cafeId, fetcher, filter }: LiveOptions<T>): UseQueryResult<T> {
  const qc = useQueryClient();
  const keyRef = useRef(key);
  keyRef.current = key;

  useEffect(() => {
    if (!enabled || !cafeId) return;
    const name = `live:${tables.join(",")}:${filter ?? cafeId}:${Math.random().toString(36).slice(2, 8)}`;
    let channel = supabase.channel(name);
    for (const table of tables) {
      channel = channel.on(
        "postgres_changes",
        { event: "*", schema: "public", table, filter: filter ?? `${table === "cafes" ? "id" : "cafe_id"}=eq.${cafeId}` },
        () => void qc.invalidateQueries({ queryKey: keyRef.current }),
      );
    }
    channel.subscribe((status) => {
      if (status === "SUBSCRIBED") void qc.invalidateQueries({ queryKey: keyRef.current }); // read after subscribing
    });
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [enabled, cafeId, filter, qc, tables.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

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
