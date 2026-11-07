// Audit log filters (docs/console.md#audit-log): every filter lives in the URL, so a view can be shared and bookmarked.
// Presets are local calendar days; "all" exists only with an order, customer or chat filter (D23).
import type { AuditQuery } from "../../lib/audit";
import type { components } from "../../lib/api/schema";

type S = components["schemas"];
export type Severity = S["AuditSeverity"];
export type Category = S["AuditCategory"];
export type ActorRole = S["AuditActorRole"];
export type Preset = "today" | "yesterday" | "7d" | "30d" | "custom" | "all";

export interface AuditFilterState {
  range: Preset;
  from: string | null; // custom range, `datetime-local` value ("2026-10-25T09:00"), local time
  to: string | null;
  q: string;
  critical: boolean;
  severity: Severity[];
  category: Category[];
  action: string[];
  actor: string[];
  role: ActorRole[];
  order: number | null;
  order_id: string | null;
  customer: string | null;
  conversation: string | null;
}

export const PRESETS: { id: Preset; label: string; entityOnly?: boolean }[] = [
  { id: "today", label: "Today" }, { id: "yesterday", label: "Yesterday" }, { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" }, { id: "custom", label: "Custom" }, { id: "all", label: "All time", entityOnly: true },
];

const SEVERITIES: readonly Severity[] = ["critical", "warning", "info"];
const CATEGORIES: readonly Category[] = ["orders", "calls", "chats", "menu", "customers", "campaigns", "offers", "team", "settings", "platform", "other"];
const ROLES: readonly ActorRole[] = ["shift_manager", "manager", "owner", "platform_admin", "staff_via_ai", "system"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ACTION = /^[a-z_]+\.[a-z_]+$/;
const LOCAL = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/;

/** A search like "#42" or "42": the API reads it as the order-number filter (viewer.normalise). */
const ORDER_Q = /^#?\d{1,9}$/;
const isOrderQ = (q: string) => ORDER_Q.test(q.trim());

/** An order, customer or chat filter (an order-number search counts): the default range is then all time (D23). */
export function hasEntity(s: Pick<AuditFilterState, "q" | "order" | "order_id" | "customer" | "conversation">): boolean {
  return s.order !== null || !!s.order_id || !!s.customer || !!s.conversation || isOrderQ(s.q);
}

export const defaultRange = (s: AuditFilterState): Preset => (hasEntity(s) ? "all" : "7d");

export const EMPTY: AuditFilterState = {
  range: "7d", from: null, to: null, q: "", critical: false, severity: [], category: [], action: [], actor: [], role: [],
  order: null, order_id: null, customer: null, conversation: null,
};

function many<T extends string>(sp: URLSearchParams, key: string, ok: (v: string) => boolean): T[] {
  return [...new Set(sp.getAll(key).filter(ok))] as T[];
}

const uuid = (v: string | null): string | null => (v && UUID.test(v) ? v.toLowerCase() : null);

export function parseFilters(sp: URLSearchParams): AuditFilterState {
  const n = Number(sp.get("order"));
  const s: AuditFilterState = {
    ...EMPTY,
    q: (sp.get("q") ?? "").slice(0, 60),
    critical: sp.get("critical") === "1",
    severity: many(sp, "severity", (v) => SEVERITIES.includes(v as Severity)),
    category: many(sp, "category", (v) => CATEGORIES.includes(v as Category)),
    action: many(sp, "action", (v) => ACTION.test(v)).slice(0, 40),
    actor: many(sp, "actor", (v) => UUID.test(v)).slice(0, 20),
    role: many(sp, "role", (v) => ROLES.includes(v as ActorRole)),
    order: Number.isInteger(n) && n > 0 ? n : null,
    order_id: uuid(sp.get("order_id")),
    customer: uuid(sp.get("customer")),
    conversation: uuid(sp.get("conversation")),
  };
  const range = sp.get("range") as Preset | null;
  s.range = range && PRESETS.some((p) => p.id === range) && (range !== "all" || hasEntity(s)) ? range : defaultRange(s);
  if (s.range === "custom") {
    const from = sp.get("from");
    const to = sp.get("to");
    s.from = from && LOCAL.test(from) ? from : null;
    s.to = to && LOCAL.test(to) ? to : null;
  }
  return s;
}

/** The URL for a state; defaults are left out so a plain /audit stays plain. */
export function toSearch(s: AuditFilterState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.range !== defaultRange(s)) sp.set("range", s.range);
  if (s.range === "custom") {
    if (s.from) sp.set("from", s.from);
    if (s.to) sp.set("to", s.to);
  }
  if (s.q.trim()) sp.set("q", s.q.trim());
  if (s.critical) sp.set("critical", "1");
  for (const k of ["severity", "category", "action", "actor", "role"] as const) for (const v of s[k]) sp.append(k, v);
  if (s.order !== null) sp.set("order", String(s.order));
  if (s.order_id) sp.set("order_id", s.order_id);
  if (s.customer) sp.set("customer", s.customer);
  if (s.conversation) sp.set("conversation", s.conversation);
  return sp;
}

const startOfDay = (d: Date, plusDays = 0) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + plusDays);

/** [from, to) of a preset in the browser's local days; null = all time. `to` is the next midnight, so it stays stable. */
export function bounds(s: AuditFilterState, now = new Date()): { from: Date; to: Date } | null {
  const tomorrow = startOfDay(now, 1);
  switch (s.range) {
    case "all": return null;
    case "today": return { from: startOfDay(now), to: tomorrow };
    case "yesterday": return { from: startOfDay(now, -1), to: startOfDay(now) };
    case "30d": return { from: startOfDay(now, -29), to: tomorrow };
    case "custom": return {
      from: s.from ? new Date(s.from) : startOfDay(now, -6),
      to: s.to ? new Date(s.to) : tomorrow,
    };
    default: return { from: startOfDay(now, -6), to: tomorrow };
  }
}

/** The range the summary strip counts: the filter's range, or the last year for "all time" (the API caps at 366 days). */
export function facetBounds(s: AuditFilterState, now = new Date()): { from: Date; to: Date } {
  return bounds(s, now) ?? { from: startOfDay(now, -364), to: startOfDay(now, 1) };
}

/** The API query for /audit/entries and /audit/export. "Critical only" wins over the severity filter. */
export function toQuery(s: AuditFilterState, now = new Date()): AuditQuery {
  const q: AuditQuery = {};
  const b = bounds(s, now);
  if (b) {
    q.from = b.from.toISOString();
    q.to = b.to.toISOString();
  }
  const severity = s.critical ? ["critical" as const] : s.severity;
  if (severity.length) q.severity = severity;
  if (s.category.length) q.category = s.category;
  if (s.action.length) q.action = s.action;
  if (s.actor.length) q.actor_id = s.actor;
  if (s.role.length) q.role = s.role;
  if (s.order !== null) q.order_number = s.order;
  if (s.order_id) q.order_id = s.order_id;
  if (s.customer) q.customer_id = s.customer;
  if (s.conversation) q.conversation_id = s.conversation;
  if (s.q.trim()) q.q = s.q.trim().slice(0, 60);
  return q;
}

export interface ChipLabels {
  action(a: string): string;
  category(c: Category): string;
  role(r: ActorRole): string;
  actor(id: string): string;
  customer(id: string): string;
  orderId(id: string): string;
}

export interface Chip { key: string; label: string; without: AuditFilterState }

const SEVERITY_LABEL: Record<Severity, string> = { critical: "Critical", warning: "Warnings", info: "Info" };
const PRESET_LABEL = Object.fromEntries(PRESETS.map((p) => [p.id, p.label])) as Record<Preset, string>;

/** One removable chip per active filter; `without` is the state with that one filter removed. */
export function activeChips(s: AuditFilterState, l: ChipLabels): Chip[] {
  const chips: Chip[] = [];
  const drop = <K extends "severity" | "category" | "action" | "actor" | "role">(k: K, v: string): AuditFilterState =>
    ({ ...s, [k]: (s[k] as string[]).filter((x) => x !== v) });
  const unset = (patch: Partial<AuditFilterState>): AuditFilterState => {
    const next = { ...s, ...patch };
    // Removing the last order, customer or chat filter can't leave "all time" behind.
    if (next.range === "all" && !hasEntity(next)) next.range = "7d";
    return next;
  };
  if (s.range !== defaultRange(s)) chips.push({ key: "range", label: PRESET_LABEL[s.range], without: { ...s, range: defaultRange(s), from: null, to: null } });
  if (s.q.trim()) chips.push({ key: "q", label: `“${s.q.trim()}”`, without: unset({ q: "" }) });
  if (s.critical) chips.push({ key: "critical", label: "Critical only", without: { ...s, critical: false } });
  for (const v of s.severity) chips.push({ key: `severity:${v}`, label: SEVERITY_LABEL[v], without: drop("severity", v) });
  for (const v of s.category) chips.push({ key: `category:${v}`, label: l.category(v), without: drop("category", v) });
  for (const v of s.action) chips.push({ key: `action:${v}`, label: l.action(v), without: drop("action", v) });
  for (const v of s.actor) chips.push({ key: `actor:${v}`, label: l.actor(v), without: drop("actor", v) });
  for (const v of s.role) chips.push({ key: `role:${v}`, label: l.role(v), without: drop("role", v) });
  if (s.order !== null) chips.push({ key: "order", label: `Order #${s.order}`, without: unset({ order: null }) });
  if (s.order_id) chips.push({ key: "order_id", label: l.orderId(s.order_id), without: unset({ order_id: null }) });
  if (s.customer) chips.push({ key: "customer", label: l.customer(s.customer), without: unset({ customer: null }) });
  if (s.conversation) chips.push({ key: "conversation", label: "This chat", without: unset({ conversation: null }) });
  return chips;
}

/** Any filter beyond the date range: decides "no match" against "nothing recorded". */
export const isFiltered = (s: AuditFilterState): boolean =>
  !!s.q.trim() || s.critical || s.severity.length + s.category.length + s.action.length + s.actor.length + s.role.length > 0 || hasEntity(s);

/**
 * Applies a filter change. A range that was the default follows the new default: adding an order, customer or chat
 * filter searches all time, removing the last one goes back to 7 days. A range the owner picked stays.
 */
export function patchFilters(s: AuditFilterState, patch: Partial<AuditFilterState>): AuditFilterState {
  const next = { ...s, ...patch };
  if (!("range" in patch) && s.range === defaultRange(s)) next.range = defaultRange(next);
  if (next.range === "all" && !hasEntity(next)) next.range = "7d";
  if (next.range !== "custom") {
    next.from = null;
    next.to = null;
  }
  return next;
}

/** A Date as a `datetime-local` value in the browser's time zone. */
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

const DAY = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" });

/** "in the last 7 days", "today", "between 25 Oct and 1 Nov" (the end day is the last day inside the range). */
export function rangePhrase(s: AuditFilterState, now = new Date()): string {
  switch (s.range) {
    case "today": return "today";
    case "yesterday": return "yesterday";
    case "7d": return "in the last 7 days";
    case "30d": return "in the last 30 days";
    case "all": return "in the last year";
    default: {
      const b = bounds(s, now)!;
      return `between ${DAY.format(b.from)} and ${DAY.format(new Date(b.to.getTime() - 1))}`;
    }
  }
}

/** "25 Oct and 1 Nov" for the empty state; null for all time. */
export function rangeDays(s: AuditFilterState, now = new Date()): string | null {
  const b = bounds(s, now);
  return b ? `${DAY.format(b.from)} and ${DAY.format(new Date(b.to.getTime() - 1))}` : null;
}
