// Campaign results for the owner (docs/console.md#campaigns): v_campaign_insights per campaign, v_campaign_recipients
// per recipient, v_redemptions per redemption. Every read filters cafe_id (RLS returns every café the user belongs to).
// The ladder (decision D5): Read → Replied → Came back → Redeemed, inside the offer window (D6). Money stays in paise.
import { useQuery } from "@tanstack/react-query";

import { api, cafeHeader, call } from "./api/client";
import { rows } from "./live";
import { REDEMPTION_COLUMNS, staffLabel, type RedemptionRow } from "./offers";
import { useSession } from "./session";
import { supabase } from "./supabase";

/** One row of v_campaign_insights. queued = queued + deferred; delivered = delivered + read; came_back = ordered or redeemed. */
export interface CampaignInsight {
  campaign_id: string; cafe_id: string; name: string; status: "draft" | "scheduled" | "sending" | "completed" | "cancelled";
  segment: "all_opted_in" | "lapsed" | "regulars"; template_name: string; offer_text: string | null; discount_percent: number | null;
  code_prefix: string | null; valid_until: string | null; scheduled_at: string | null; created_at: string;
  recipients: number; queued: number; deferred: number; sent: number; held: number; delivered: number; read: number; failed: number;
  replied: number; ordered: number; redeemed: number; redeemed_counter: number; redeemed_table_bill: number; came_back: number;
  opted_out: number; ordered_paise: number; counter_bill_paise: number; came_back_paise: number; discount_paise: number;
  whatsapp_cost: number;
}

export type RecipientStatus = "queued" | "deferred" | "sent" | "delivered" | "read" | "held" | "failed";

/** One row of v_campaign_recipients (masked number only; null when the customer was deleted). */
export interface RecipientRow {
  recipient_id: string; cafe_id: string; campaign_id: string; customer_id: string; code: string; status: RecipientStatus;
  name: string | null; masked_phone: string | null; deleted: boolean; queued_at: string; sent_at: string | null;
  delivered_at: string | null; read_at: string | null; failed_at: string | null; error_code: string | null;
  window_start: string | null; window_end: string | null; replied_at: string | null; orders: number; spent_paise: number;
  first_order_at: string | null; redemption_id: string | null; redeemed_at: string | null;
  redemption_channel: "table_bill" | "counter" | null; bill_paise: number | null; discount_paise: number | null;
  redeemed_by: string | null; redeemed_by_name: string | null; opted_out_at: string | null; opted_out_source: string | null;
  came_back: boolean;
}

/** Status chips: "Not sent" covers queued and deferred. */
export type StatusFilter = "not_sent" | "sent" | "delivered" | "read" | "held" | "failed";
/** Outcome chips, combined with AND. */
export type Outcome = "redeemed" | "not_redeemed" | "came_back" | "replied" | "opted_out";
export interface RecipientFilters { status: StatusFilter | null; outcomes: Outcome[]; name: string }

export const RECIPIENT_PAGE = 50;

/** placeholderData that keeps the last page on screen while the next filter or page loads, only within the same café
 * and campaign (key positions 1 and 2), so another campaign's rows never show. */
function keepWithin<T>(cafeId: string | null | undefined, campaignId: string | null) {
  return (prev: T | undefined, prevQuery?: { queryKey: readonly unknown[] }) =>
    prevQuery?.queryKey[1] === cafeId && prevQuery?.queryKey[2] === campaignId ? prev : undefined;
}

const NUMERIC: (keyof CampaignInsight)[] = ["ordered_paise", "counter_bill_paise", "came_back_paise", "discount_paise", "whatsapp_cost"];

export function useCampaignInsights() {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["campaign-insights", cafeId],
    enabled: !!cafeId,
    refetchInterval: 60_000, // a `sending` campaign moves while it is open
    queryFn: async () => (await rows<CampaignInsight[]>(supabase.from("v_campaign_insights").select("*").eq("cafe_id", cafeId!)
      .order("created_at", { ascending: false })))
      // bigint and numeric arrive as numbers or strings depending on size: normalise once
      .map((r) => ({ ...r, ...Object.fromEntries(NUMERIC.map((k) => [k, Number(r[k] ?? 0)])) }) as CampaignInsight),
  });
}

/** One page (50) of a campaign's recipients, filtered on the server, with the exact match count. */
export function useCampaignRecipients(campaignId: string, filters: RecipientFilters, page: number) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["campaign-recipients", cafeId, campaignId, filters, page],
    enabled: !!cafeId,
    refetchInterval: 60_000,
    placeholderData: keepWithin<{ rows: RecipientRow[]; total: number }>(cafeId, campaignId),
    queryFn: async () => {
      let q = supabase.from("v_campaign_recipients").select("*", { count: "exact" }).eq("cafe_id", cafeId!).eq("campaign_id", campaignId);
      const s = filters.status;
      if (s === "not_sent") q = q.in("status", ["queued", "deferred"]);
      else if (s) q = q.eq("status", s);
      for (const o of filters.outcomes) {
        if (o === "redeemed") q = q.not("redemption_id", "is", null);
        else if (o === "not_redeemed") q = q.is("redemption_id", null);
        else if (o === "came_back") q = q.eq("came_back", true);
        else if (o === "replied") q = q.not("replied_at", "is", null);
        else q = q.not("opted_out_at", "is", null);
      }
      const name = filters.name.trim().replace(/[%_]/g, "");
      if (name) q = q.ilike("name", `%${name}%`);
      const { data, error, count } = await q.order("sent_at", { ascending: false, nullsFirst: false }).order("recipient_id")
        .range(page * RECIPIENT_PAGE, page * RECIPIENT_PAGE + RECIPIENT_PAGE - 1);
      if (error) throw new Error(error.message);
      const list = ((data ?? []) as RecipientRow[]).map((r) => ({
        ...r, spent_paise: Number(r.spent_paise ?? 0), bill_paise: r.bill_paise === null ? null : Number(r.bill_paise),
        discount_paise: r.discount_paise === null ? null : Number(r.discount_paise),
      }));
      return { rows: list, total: count ?? 0 };
    },
  });
}

export const REDEMPTION_PAGE = 1000; // PostgREST's max-rows: one request never returns more
export const REDEMPTION_CAP = 10_000; // the most the panels add up; a note says so when reached

/** Redemptions from v_redemptions, newest first: one campaign, or every campaign since a café-local day. Pages through
 * 1000 rows at a time up to `limit`; a result of exactly `limit` rows may be missing older ones. */
export function useRedemptions({ campaignId, sinceDay, limit = REDEMPTION_CAP }: { campaignId?: string; sinceDay?: string | null; limit?: number }) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["redemptions", cafeId, campaignId ?? null, sinceDay ?? null, limit],
    enabled: !!cafeId,
    refetchInterval: 60_000,
    placeholderData: keepWithin<RedemptionRow[]>(cafeId, campaignId ?? null), // Insights' period chips keep the panels
    queryFn: async () => {
      const out: RedemptionRow[] = [];
      while (out.length < limit) {
        let q = supabase.from("v_redemptions").select(REDEMPTION_COLUMNS).eq("cafe_id", cafeId!);
        if (campaignId) q = q.eq("campaign_id", campaignId);
        if (sinceDay) q = q.gte("day", sinceDay);
        const size = Math.min(REDEMPTION_PAGE, limit - out.length);
        const page = await rows<RedemptionRow[]>(q.order("redeemed_at", { ascending: false }).order("redemption_id")
          .range(out.length, out.length + size - 1));
        out.push(...page);
        if (page.length < size) break;
      }
      return out.map((r) => ({ ...r, bill_paise: r.bill_paise === null ? null : Number(r.bill_paise), discount_paise: Number(r.discount_paise) }));
    },
  });
}

/** GET /campaigns/{id}/recipients/export (masked numbers; audited `campaign.export`) saved as a file. Returns the row count. */
export async function exportRecipients(campaignId: string): Promise<number> {
  const csv = (await call(api.GET("/campaigns/{id}/recipients/export", {
    params: { ...cafeHeader(), path: { id: campaignId } }, parseAs: "text",
  }))) as unknown as string;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: "campaign-recipients.csv" });
  a.click();
  URL.revokeObjectURL(url);
  return Math.max(0, csv.split(/\r?\n/).filter((l) => l.length > 0).length - 1); // minus the header
}

/** The highest came_back / sent among campaigns with ≥ `minDelivered` delivered; ties go to more redemptions. */
export function bestCampaign(list: CampaignInsight[], minDelivered = 10): CampaignInsight | null {
  let best: CampaignInsight | null = null;
  for (const c of list) {
    if (c.delivered < minDelivered || !c.sent) continue;
    if (!best) { best = c; continue; }
    const a = c.came_back / c.sent, b = best.came_back / best.sent;
    if (a > b || (a === b && c.redeemed > best.redeemed)) best = c;
  }
  return best;
}

export interface StaffRow { key: string; name: string; total: number; counter: number; tableBill: number; discount_paise: number; last: string }

/** Redemptions per staff member, most first ("Unknown" when redeemed_by is null). */
export function byStaff(list: RedemptionRow[]): StaffRow[] {
  const out = new Map<string, StaffRow>();
  for (const r of list) {
    const key = r.redeemed_by ?? "unknown";
    const s = out.get(key) ?? { key, name: staffLabel(r), total: 0, counter: 0,
      tableBill: 0, discount_paise: 0, last: r.redeemed_at };
    s.total++;
    if (r.channel === "counter") s.counter++;
    else s.tableBill++;
    s.discount_paise += Number(r.discount_paise);
    if (r.redeemed_at > s.last) s.last = r.redeemed_at;
    out.set(key, s);
  }
  return [...out.values()].sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
}

export interface DayRow { t: number; day: string; counter: number; table_bill: number }

/** Redemptions per café-local day, counter vs table bill, oldest first, with empty days filled in between. */
export function byDay(list: RedemptionRow[]): DayRow[] {
  const days = new Map<string, DayRow>();
  const noon = (d: string) => Date.parse(`${d}T12:00:00Z`); // a day's bar sits at its noon; labels show the date
  for (const r of list) {
    const d = days.get(r.day) ?? { t: noon(r.day), day: r.day, counter: 0, table_bill: 0 };
    if (r.channel === "counter") d.counter++;
    else d.table_bill++;
    days.set(r.day, d);
  }
  const sorted = [...days.values()].sort((a, b) => a.t - b.t);
  if (sorted.length < 2) return sorted;
  const out: DayRow[] = [];
  for (let t = sorted[0]!.t; t <= sorted[sorted.length - 1]!.t; t += 86_400_000) {
    const day = new Date(t).toISOString().slice(0, 10);
    out.push(days.get(day) ?? { t, day, counter: 0, table_bill: 0 });
  }
  return out;
}

export interface Totals { sent: number; delivered: number; read: number; redeemed: number; came_back: number;
  came_back_paise: number; discount_paise: number; opted_out: number; whatsapp_cost: number }

/** Sums across campaigns. A visit can count for more than one campaign, so revenue is summed per campaign. */
export function totals(list: CampaignInsight[]): Totals {
  const t: Totals = { sent: 0, delivered: 0, read: 0, redeemed: 0, came_back: 0, came_back_paise: 0, discount_paise: 0, opted_out: 0, whatsapp_cost: 0 };
  for (const c of list) for (const k of Object.keys(t) as (keyof Totals)[]) t[k] += Number(c[k] ?? 0);
  return t;
}
