// Offer codes at the counter (docs/console.md#redeem-offer). Lookup and redeem go through the API: eligibility is the
// backend's one rule, shared with the table bill (decision 18); the console never decides it. "Redeemed today" reads
// v_redemptions. Never a full number; money only as paise from the API or the view.
import { useQuery } from "@tanstack/react-query";

import { api, cafeHeader, call, newIdempotencyKey } from "./api/client";
import type { components } from "./api/schema";
import { rows } from "./live";
import { useSession } from "./session";
import { supabase } from "./supabase";

export type OfferLookup = components["schemas"]["OfferLookup"];
export type OfferCustomer = components["schemas"]["OfferCustomer"];
export type OfferCheck = components["schemas"]["OfferCheck"];
export type OfferVerdict = components["schemas"]["OfferVerdict"];
export type OfferOpenBill = components["schemas"]["OfferOpenBill"];
export type CounterRedemption = components["schemas"]["CounterRedemption"];

/** One row of v_redemptions (column names from the migration). */
export interface RedemptionRow {
  redemption_id: string; cafe_id: string; campaign_id: string; campaign_name: string; campaign_recipient_id: string;
  code: string; channel: "table_bill" | "counter"; redeemed_at: string; day: string; customer_id: string;
  first_name: string | null; masked_phone: string | null; bill_paise: number | null; discount_paise: number;
  redeemed_by: string | null; redeemed_by_name: string | null; order_number: number | null; table_label: string | null;
  table_session_id: string | null;
}

export const REDEMPTION_COLUMNS = "redemption_id, cafe_id, campaign_id, campaign_name, campaign_recipient_id, code, channel, "
  + "redeemed_at, day, customer_id, first_name, masked_phone, bill_paise, discount_paise, redeemed_by, redeemed_by_name, "
  + "order_number, table_label, table_session_id";

export const DEFAULT_TZ = "Asia/Kolkata"; // cafes.timezone default

/** "Type the last 4 digits of the number" for exactly 1–3 digits (no call is made). */
export function digitsHint(q: string): string | null {
  return /^\d{1,3}$/.test(q.trim()) ? "Type the last 4 digits of the number" : null;
}

/** A search the API is asked about: at least 2 characters and not 1–3 bare digits. */
export function searchable(q: string): boolean {
  const t = q.trim();
  return t.length >= 2 && !digitsHint(t);
}

/** The café-local calendar date ("2026-10-12") of an instant, never the browser's. */
export function cafeDay(tz: string, at: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(at);
}

/** "12:41" in the café's zone. */
export function cafeClock(iso: string, tz: string): string {
  return new Intl.DateTimeFormat("en-IN", { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

/** "12 Oct" in the café's zone. */
export function cafeDayMonth(iso: string, tz: string): string {
  return new Intl.DateTimeFormat("en-IN", { timeZone: tz, day: "numeric", month: "short" }).format(new Date(iso));
}

/** "Counter" / "T7 bill" / "T7 bill #1041". */
export function channelLabel(r: { channel: string; table_label?: string | null; order_number?: number | null }, withOrder = false): string {
  if (r.channel === "counter") return "Counter";
  const bill = r.table_label ? `${r.table_label} bill` : "Table bill";
  return withOrder && r.order_number ? `${bill} #${r.order_number}` : bill;
}

/** Who redeemed: the name, "Former staff" when the membership is gone, "Unknown" when no staff is recorded. */
export function staffLabel(r: { redeemed_by: string | null; redeemed_by_name: string | null }): string {
  return r.redeemed_by ? r.redeemed_by_name ?? "Former staff" : "Unknown";
}

/** The café's timezone (cafes.timezone); it changes almost never. */
export function useCafeTimezone() {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["cafe-tz", cafeId],
    enabled: !!cafeId,
    staleTime: Infinity,
    queryFn: async () => (await rows<{ timezone: string | null }[]>(supabase.from("cafes").select("timezone").eq("id", cafeId!)))[0]?.timezone
      ?? DEFAULT_TZ,
  });
}

/** POST /offers/lookup: the search text goes in the body, never the URL. Pass the debounced text. */
export function useOfferLookup(q: string) {
  const { cafeId } = useSession();
  const term = q.trim();
  return useQuery({
    queryKey: ["offer-lookup", cafeId, term],
    enabled: !!cafeId && searchable(term),
    retry: false,
    queryFn: () => call(api.POST("/offers/lookup", { params: cafeHeader(), body: { q: term } })),
  });
}

/** Today's redemptions in this café, newest first; "today" is the café-local date. Refetches every 30 s. */
export function useRedemptionsToday() {
  const { cafeId } = useSession();
  const tz = useCafeTimezone();
  const zone = tz.data ?? (tz.isError ? DEFAULT_TZ : undefined); // the same fallback as the screen, so a failed read never stalls
  return useQuery({
    queryKey: ["redemptions-today", cafeId, zone],
    enabled: !!cafeId && !!zone,
    refetchInterval: 30_000,
    queryFn: () => rows<RedemptionRow[]>(supabase.from("v_redemptions").select(REDEMPTION_COLUMNS)
      .eq("cafe_id", cafeId!).eq("day", cafeDay(zone!)).order("redeemed_at", { ascending: false }).limit(200)),
  });
}

/** POST /offers/redeem. Reuse `key` for every retry of one attempt; a new amount needs a new key. */
export function redeemAtCounter(key: string, code: string, billPaise: number): Promise<CounterRedemption> {
  return call(api.POST("/offers/redeem", {
    params: { header: { ...cafeHeader().header, "Idempotency-Key": key } },
    body: { code, bill_paise: billPaise },
  }));
}

export { newIdempotencyKey };
