// Read-only reports from the analytics views (docs/data-and-api.md#analytics-views): service report (manager+) and
// analytics (owner). Days are café-local dates from the views; cancelled orders are already excluded there.
import { useQuery } from "@tanstack/react-query";

import { useSession } from "./session";
import { supabase } from "./supabase";

const TZ = "Asia/Kolkata"; // cafes.timezone default; the views bucket days in the café's own zone

export function isoDay(d: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

async function rows<T>(q: PromiseLike<{ data: unknown; error: { message: string } | null }>): Promise<T[]> {
  const { data, error } = await q;
  if (error) throw new Error(error.message);
  return (data as T[] | null) ?? [];
}

export interface ServiceReport {
  orders: number; median_prep_minutes: number | null; pct_overdue: number | null; peak_overdue_hour: number | null;
  slowest_dishes: { dish: string; median_minutes: number; orders: number }[] | null;
}
export interface FeedbackRow { order_id: string; number: number; rating: number; text: string | null; created_at: string; day: string }
export interface PrepRow { order_id: string; dish: string; prep_minutes: number; overdue: boolean; hour: number; day: string }

export function useServiceReport() {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["service-report", cafeId],
    enabled: !!cafeId,
    refetchInterval: 5 * 60_000,
    queryFn: async () => {
      const since = isoDay(new Date(Date.now() - 7 * 86_400_000));
      const [report, feedback] = await Promise.all([
        rows<ServiceReport>(supabase.from("v_service_report").select("*").eq("cafe_id", cafeId!)),
        rows<FeedbackRow>(supabase.from("v_feedback_summary").select("order_id, number, rating, text, created_at, day")
          .eq("cafe_id", cafeId!).gte("day", since).order("created_at", { ascending: false })),
      ]);
      return { report: report[0] ?? null, feedback };
    },
  });
}

export interface PrepSummary { median: number | null; pctOverdue: number | null; peakHour: number | null; slowest: { dish: string; median: number; orders: number }[] }

/** Median prep, % overdue, worst hour and slowest dishes from per-dish prep rows (Analytics' own period). */
export function summarisePrep(prep: PrepRow[]): PrepSummary {
  const median = (xs: number[]): number | null => {
    if (!xs.length) return null;
    const s = [...xs].sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
  };
  const byOrder = new Map<string, PrepRow>();
  for (const p of prep) byOrder.set(p.order_id, p);
  const orders = [...byOrder.values()];
  const overdue = orders.filter((o) => o.overdue);
  const hours = new Map<number, number>();
  for (const o of overdue) hours.set(o.hour, (hours.get(o.hour) ?? 0) + 1);
  const dishes = new Map<string, number[]>();
  for (const p of prep) dishes.set(p.dish, [...(dishes.get(p.dish) ?? []), Number(p.prep_minutes)]);
  return {
    median: median(orders.map((o) => Number(o.prep_minutes))),
    pctOverdue: orders.length ? Math.round((overdue.length / orders.length) * 1000) / 10 : null,
    peakHour: hours.size ? [...hours.entries()].sort((a, b) => b[1] - a[1])[0]![0] : null,
    slowest: [...dishes.entries()].map(([dish, xs]) => ({ dish, median: median(xs)!, orders: xs.length }))
      .sort((a, b) => b.median - a.median).slice(0, 3),
  };
}

export interface DayRevenue { day: string; revenue_paise: number; orders: number; customers: number }
export interface TopItem { day: string; name: string; qty: number; revenue_paise: number }
export interface Period { revenue: number; orders: number; avgOrder: number | null; avgRating: number | null; returningPct: number | null; customers: number }

/** Returning = a customer of the period first seen before the period started. */
export function summarisePeriod(days: DayRevenue[], ratings: FeedbackRow[], firstSeen: Map<string, string>,
  customers: Set<string>, start: string): Period {
  const revenue = days.reduce((s, d) => s + Number(d.revenue_paise), 0);
  const orders = days.reduce((s, d) => s + d.orders, 0);
  const returning = [...customers].filter((c) => (firstSeen.get(c) ?? "9999") < start).length;
  return {
    revenue, orders, avgOrder: orders ? Math.round(revenue / orders / 100) * 100 : null, // whole rupees
    avgRating: ratings.length ? Math.round((ratings.reduce((s, r) => s + r.rating, 0) / ratings.length) * 10) / 10 : null,
    returningPct: customers.size ? Math.round((returning / customers.size) * 1000) / 10 : null,
    customers: customers.size,
  };
}

export function useAnalytics(days: 7 | 30 | 90) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["analytics", cafeId, days],
    enabled: !!cafeId,
    refetchInterval: 5 * 60_000,
    queryFn: async () => {
      const now = Date.now();
      const start = isoDay(new Date(now - (days - 1) * 86_400_000));
      const prevStart = isoDay(new Date(now - (2 * days - 1) * 86_400_000));
      const fromIso = new Date(now - 2 * days * 86_400_000).toISOString();
      const [revenue, top, feedback, prep, orders] = await Promise.all([
        rows<DayRevenue>(supabase.from("v_daily_revenue").select("day, revenue_paise, orders, customers").eq("cafe_id", cafeId!).gte("day", prevStart).order("day")),
        rows<TopItem>(supabase.from("v_top_items").select("day, name, qty, revenue_paise").eq("cafe_id", cafeId!).gte("day", start)),
        rows<FeedbackRow>(supabase.from("v_feedback_summary").select("order_id, number, rating, text, created_at, day").eq("cafe_id", cafeId!).gte("day", prevStart)),
        rows<PrepRow>(supabase.from("v_prep_times").select("order_id, dish, prep_minutes, overdue, hour, day").eq("cafe_id", cafeId!).gte("day", start)),
        rows<{ customer_id: string; created_at: string }>(supabase.from("orders").select("customer_id, created_at")
          .eq("cafe_id", cafeId!).neq("status", "CANCELLED").gte("created_at", fromIso).not("customer_id", "is", null)),
      ]);
      const ids = [...new Set(orders.map((o) => o.customer_id))];
      const firstSeen = new Map<string, string>();
      for (let i = 0; i < ids.length; i += 200) {
        const chunk = await rows<{ id: string; first_seen_at: string }>(supabase.from("customers").select("id, first_seen_at").in("id", ids.slice(i, i + 200)));
        for (const c of chunk) firstSeen.set(c.id, isoDay(new Date(c.first_seen_at)));
      }
      const between = (day: string, from: string, to?: string) => day >= from && (!to || day < to);
      const customersIn = (from: string, to?: string) =>
        new Set(orders.filter((o) => between(isoDay(new Date(o.created_at)), from, to)).map((o) => o.customer_id));
      const dishes = new Map<string, { qty: number; revenue: number }>();
      for (const t of top) {
        const d = dishes.get(t.name) ?? { qty: 0, revenue: 0 };
        dishes.set(t.name, { qty: d.qty + t.qty, revenue: d.revenue + Number(t.revenue_paise) });
      }
      return {
        start,
        current: summarisePeriod(revenue.filter((d) => between(d.day, start)), feedback.filter((f) => between(f.day, start)), firstSeen, customersIn(start), start),
        previous: summarisePeriod(revenue.filter((d) => between(d.day, prevStart, start)), feedback.filter((f) => between(f.day, prevStart, start)),
          firstSeen, customersIn(prevStart, start), prevStart),
        series: revenue.filter((d) => between(d.day, start)),
        top5: [...dishes.entries()].map(([name, v]) => ({ name, ...v })).sort((a, b) => b.revenue - a.revenue).slice(0, 5),
        service: summarisePrep(prep),
        lowRatings: feedback.filter((f) => between(f.day, start) && f.rating <= 3),
      };
    },
  });
}
