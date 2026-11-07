// Audit log reads (owner, docs/console.md#audit-log). Through the API, not Supabase: labels, sentences and severity
// reasons come from the backend catalogue and audit_log has no member read policy (decision 19). No Realtime: the
// list refetches every 60 s and on focus. The Customer filter's typeahead is the one Supabase read (the customer list).
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { ApiError, api, cafeHeader, call } from "./api/client";
import type { components, paths } from "./api/schema";
import { rows } from "./live";
import { useSession } from "./session";
import { supabase } from "./supabase";

export type AuditQuery = NonNullable<paths["/audit/entries"]["get"]["parameters"]["query"]>;
export type AuditEntry = components["schemas"]["AuditEntry"];
export type AuditPage = components["schemas"]["AuditPage"];
export type AuditFacets = components["schemas"]["AuditFacets"];
export type AuditCatalog = components["schemas"]["AuditCatalog"];
export type OrderHistory = components["schemas"]["OrderHistory"];
export type HistoryEvent = OrderHistory["events"][number];

const PAGE_SIZE = 50;

// 400 (bad filters), 403 (not the owner) and 404 won't change on a retry.
const retry = (n: number, e: unknown) => !(e instanceof ApiError && [400, 403, 404].includes(e.status)) && n < 2;

/** Labels, categories and roles. Static per deploy. */
export function useAuditCatalog(enabled = true) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["audit-catalog", cafeId],
    enabled: enabled && !!cafeId,
    staleTime: Infinity,
    retry,
    queryFn: () => call(api.GET("/audit/catalog", { params: cafeHeader() })),
  });
}

/** Newest first, 50 a page; "Show older" passes the previous page's `next` as `before`. */
export function useAuditEntries(q: AuditQuery, enabled = true) {
  const { cafeId } = useSession();
  return useInfiniteQuery({
    queryKey: ["audit-entries", cafeId, q],
    enabled: enabled && !!cafeId,
    initialPageParam: null as string | null,
    getNextPageParam: (last: AuditPage) => last.next,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    retry,
    queryFn: ({ pageParam }) => call(api.GET("/audit/entries", {
      params: { ...cafeHeader(), query: { ...q, limit: PAGE_SIZE, ...(pageParam ? { before: pageParam } : {}) } },
    })),
  });
}

/** Counts for the summary strip and the filter lists, for one date range (ISO strings). */
export function useAuditFacets(from: string, to: string, enabled = true) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["audit-facets", cafeId, from, to],
    enabled: enabled && !!cafeId,
    refetchInterval: 60_000,
    retry,
    queryFn: () => call(api.GET("/audit/facets", { params: { ...cafeHeader(), query: { from, to } } })),
  });
}

/** One order's merged timeline: customer, staff, automatic events, escalations, bill and code. */
export function useOrderHistory(orderId: string | null) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["order-history", cafeId, orderId],
    enabled: !!cafeId && !!orderId,
    refetchInterval: 60_000,
    retry,
    queryFn: () => call(api.GET("/orders/{id}/history", { params: { ...cafeHeader(), path: { id: orderId! } } })),
  });
}

export interface CustomerMatch { customer_id: string; name: string | null; visits: number }

/** Customer filter typeahead: up to 8 customers by name, most recent visit first. One query, no segment counts. */
export function useCustomerMatches(search: string) {
  const { cafeId } = useSession();
  const term = search.trim().replace(/[%_]/g, "");
  return useQuery({
    queryKey: ["audit-customer-matches", cafeId, term],
    enabled: !!cafeId && term.length >= 2,
    staleTime: 60_000,
    queryFn: () => rows<CustomerMatch[]>(supabase.from("v_customer_profile").select("customer_id, name, visits")
      .eq("cafe_id", cafeId!).gt("visits", 0).ilike("name", `%${term}%`)
      .order("last_visit_at", { ascending: false, nullsFirst: false }).limit(8)),
  });
}

/** Records in a CSV: line breaks inside quoted cells (the details column) don't count; the header doesn't either. */
export function csvRowCount(csv: string): number {
  let lines = 0;
  let quoted = false;
  for (let i = 0; i < csv.length; i++) {
    const c = csv[i];
    if (c === '"') quoted = !quoted;
    else if (c === "\n" && !quoted) lines++;
  }
  if (csv.length && !csv.endsWith("\n")) lines++;
  return Math.max(0, lines - 1);
}

function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Downloads the filtered log as "audit-log-YYYY-MM-DD.csv" (≤ 10 000 rows; the API writes `audit.export`). Returns the row count. */
export async function exportAuditCsv(q: AuditQuery): Promise<number> {
  const csv = (await call(api.GET("/audit/export", { params: { ...cafeHeader(), query: q }, parseAs: "text" }))) as unknown as string;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `audit-log-${localDate(new Date())}.csv` });
  a.click();
  URL.revokeObjectURL(url);
  return csvRowCount(csv);
}
