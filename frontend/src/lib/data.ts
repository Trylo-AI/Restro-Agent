// Read hooks (Supabase + RLS + Realtime). Screens never create their own client; they use these.
// Never select customers.wa_id: masked numbers come from v_customer_card.
import { useQuery } from "@tanstack/react-query";

import { useLive, rows } from "./live";
import { useSession } from "./session";
import { api, cafeHeader, call } from "./api/client";
import type { components } from "./api/schema";
import { supabase } from "./supabase";
import type {
  Cafe, Conversation, CustomerCard, CustomerNote, Escalation, MenuItem, Order, OrderItem, Preference, Request,
} from "./types";

export interface LiveOrder extends Order {
  items: OrderItem[];
  table: string | null;
  customerName: string | null;
  visits: number;
  profileAllergies: string[];
  escalations: Escalation[];
}

export interface LiveRequest extends Request {
  table: string | null;
  customerName: string | null;
  customerId: string | null;
  escalations: Escalation[];
  ackName: string | null;
  words: string | null; // the customer's last message
}

export interface ChatRow extends Conversation {
  customerName: string | null;
  maskedPhone: string;
  table: string | null;
  lastInAt: string | null;
  lastOutAt: string | null;
  lastText: string | null;
}

async function cards(ids: string[]): Promise<Map<string, CustomerCard>> {
  if (!ids.length) return new Map();
  const data = await rows<CustomerCard[]>(supabase.from("v_customer_card").select("*").in("customer_id", ids));
  return new Map(data.map((c) => [c.customer_id, c]));
}

/** Teammates' display names (`v_staff_names`: every member of the café sees them; memberships RLS shows own row only). */
async function staffNames(cafeId: string): Promise<Map<string, string>> {
  const data = await rows<{ user_id: string; display_name: string | null }[]>(
    supabase.from("v_staff_names").select("user_id, display_name").eq("cafe_id", cafeId));
  return new Map(data.map((m) => [m.user_id, m.display_name ?? "a teammate"]));
}

export interface AppNotification {
  id: string; kind: "order_cancelled"; subject_id: string; read_at: string | null; created_at: string;
  payload: { order_number?: number; table?: string | null; reason_code?: string; reason_text?: string | null; actor_name?: string | null };
}

/** The caller's own in-app notifications (manager+), newest first. */
export function useNotifications(enabled: boolean) {
  const { cafeId, userId } = useSession();
  return useLive<AppNotification[]>({
    key: ["notifications", cafeId, userId], enabled: enabled && !!cafeId, cafeId, tables: ["notifications"],
    filter: userId ? `user_id=eq.${userId}` : undefined,
    fetcher: () => rows(supabase.from("notifications").select("id, kind, subject_id, read_at, created_at, payload")
      .eq("cafe_id", cafeId!).order("created_at", { ascending: false }).limit(50)),
  });
}

export function useCafe() {
  const { cafeId } = useSession();
  return useLive<Cafe | null>({
    key: ["cafe", cafeId], enabled: !!cafeId, cafeId, tables: ["cafes"],
    fetcher: async () => (await rows<Cafe[]>(supabase.from("cafes").select("id, name, status, settings, updated_at, opening_hours, cutoff_minutes, display_phone_number").eq("id", cafeId!)))[0] ?? null,
  });
}

export function useLiveOrders() {
  const { cafeId } = useSession();
  return useLive<LiveOrder[]>({
    key: ["orders", cafeId], enabled: !!cafeId, cafeId, tables: ["orders", "customer_preferences"],
    fetcher: async () => {
      type Row = Order & { order_items: OrderItem[]; table_sessions: { tables: { label: string } | null } | null };
      const data = await rows<Row[]>(supabase.from("orders")
        .select("*, order_items(*), table_sessions(tables(label))")
        .eq("cafe_id", cafeId!).in("status", ["NEW", "ACCEPTED", "READY"]).order("created_at"));
      const customerIds = [...new Set(data.map((o) => o.customer_id).filter((x): x is string => !!x))];
      const ids = data.map((o) => o.id);
      const [cardMap, allergies, escalations] = await Promise.all([
        cards(customerIds),
        customerIds.length ? rows<Preference[]>(supabase.from("customer_preferences").select("customer_id, kind, value")
          .eq("cafe_id", cafeId!).eq("kind", "allergy").in("customer_id", customerIds)) : Promise.resolve([] as Preference[]),
        ids.length ? rows<Escalation[]>(supabase.from("escalations").select("*").eq("subject_type", "order").in("subject_id", ids))
          : Promise.resolve([] as Escalation[]),
      ]);
      return data.map((o) => ({
        ...o,
        items: o.order_items ?? [],
        table: o.table_sessions?.tables?.label ?? null,
        customerName: o.customer_id ? cardMap.get(o.customer_id)?.first_name || null : null,
        visits: o.customer_id ? cardMap.get(o.customer_id)?.visits ?? 0 : 0,
        profileAllergies: allergies.filter((a) => a.customer_id === o.customer_id).map((a) => a.value),
        escalations: escalations.filter((e) => e.subject_id === o.id).sort((a, b) => a.level - b.level),
      }));
    },
  });
}

export function useOpenRequests() {
  const { cafeId, userId } = useSession();
  return useLive<LiveRequest[]>({
    key: ["requests", cafeId], enabled: !!cafeId, cafeId, tables: ["requests"],
    fetcher: async () => {
      type Row = Request & { table_sessions: { tables: { label: string } | null } | null;
        conversations: { customer_id: string } | null };
      const data = await rows<Row[]>(supabase.from("requests")
        .select("*, table_sessions(tables(label)), conversations(customer_id)")
        .eq("cafe_id", cafeId!).neq("status", "resolved").order("created_at"));
      const customerIds = [...new Set(data.map((r) => r.conversations?.customer_id).filter((x): x is string => !!x))];
      const convIds = data.map((r) => r.conversation_id).filter((x): x is string => !!x);
      const ratedOrders = data.filter((r) => r.kind === "low_rating" && r.order_id).map((r) => r.order_id!);
      const [cardMap, names, escalations, words, ratings] = await Promise.all([
        cards(customerIds),
        staffNames(cafeId!),
        data.length ? rows<Escalation[]>(supabase.from("escalations").select("*").eq("subject_type", "request")
          .in("subject_id", data.map((r) => r.id))) : Promise.resolve([] as Escalation[]),
        convIds.length ? rows<{ conversation_id: string; payload: { text?: { body?: string } }; received_at: string }[]>(
          supabase.from("inbound_messages").select("conversation_id, payload, received_at").in("conversation_id", convIds)
            .eq("type", "text").order("received_at", { ascending: false }).limit(200)) : Promise.resolve([]),
        ratedOrders.length ? rows<{ order_id: string; rating: number; text: string | null }[]>(
          supabase.from("feedback").select("order_id, rating, text").in("order_id", ratedOrders)) : Promise.resolve([]),
      ]);
      return data.map((r) => {
        const customerId = r.conversations?.customer_id ?? null;
        const last = words.find((w) => w.conversation_id === r.conversation_id && Date.parse(w.received_at) <= Date.parse(r.created_at) + 5_000);
        return {
          ...r,
          table: r.table_sessions?.tables?.label ?? null,
          customerId,
          customerName: customerId ? cardMap.get(customerId)?.first_name || null : null,
          escalations: escalations.filter((e) => e.subject_id === r.id),
          ackName: r.acknowledged_by ? (r.acknowledged_by === userId ? "You" : names.get(r.acknowledged_by) ?? "a teammate") : null,
          // A low rating shows the stars and what they wrote, not the chat's last line.
          words: r.kind === "low_rating"
            ? (() => { const f = ratings.find((x) => x.order_id === r.order_id); return f ? `${f.rating}★${f.text ? ` · ${f.text}` : ""}` : null; })()
            : last?.payload?.text?.body ?? null,
        };
      });
    },
  });
}

export function useChats() {
  const { cafeId } = useSession();
  return useLive<ChatRow[]>({
    key: ["chats", cafeId], enabled: !!cafeId, cafeId, tables: ["conversations", "inbound_messages", "wa_messages"],
    fetcher: async () => {
      const since = new Date(Date.now() - 24 * 3600_000).toISOString();
      type Row = Conversation & { table_sessions: { tables: { label: string } | null } | null };
      const [convs, ins, outs] = await Promise.all([
        rows<Row[]>(supabase.from("conversations").select("id, cafe_id, customer_id, mode, assigned_to, table_session_id, updated_at, table_sessions(tables(label))")
          .eq("cafe_id", cafeId!).gte("updated_at", since).order("updated_at", { ascending: false }).limit(100)),
        rows<{ conversation_id: string; received_at: string; type: string; payload: { text?: { body?: string } } }[]>(
          supabase.from("inbound_messages").select("conversation_id, received_at, type, payload").eq("cafe_id", cafeId!)
            .gte("received_at", since).order("received_at", { ascending: false }).limit(1000)),
        rows<{ customer_id: string | null; created_at: string }[]>(supabase.from("wa_messages").select("customer_id, created_at")
          .eq("cafe_id", cafeId!).gte("created_at", since).order("created_at", { ascending: false }).limit(1000)),
      ]);
      const cardMap = await cards([...new Set(convs.map((c) => c.customer_id))]);
      return convs.map((c) => {
        const lastIn = ins.find((m) => m.conversation_id === c.id);
        const lastOut = outs.find((m) => m.customer_id === c.customer_id);
        const card = cardMap.get(c.customer_id);
        return {
          ...c,
          table: c.table_sessions?.tables?.label ?? null,
          customerName: card?.first_name || null,
          maskedPhone: card?.masked_phone ?? "••••",
          lastInAt: lastIn?.received_at ?? null,
          lastOutAt: lastOut?.created_at ?? null,
          lastText: lastIn ? messageText(lastIn.type, lastIn.payload as Payload).text : null,
        };
      });
    },
  });
}

export interface CustomerProfile {
  card: CustomerCard | null;
  prefs: Preference[];
  notes: (CustomerNote & { author: string })[];
  feedback: { id: string; rating: number; text: string | null; created_at: string; number: number }[];
  complaints: { id: string; at: string; label: string; words: string | null }[];
  recent: { id: string; number: number; status: Order["status"]; total_paise: number; created_at: string;
    items: { name_snapshot: string; qty: number }[] }[];
}

/** One customer's profile panel (docs/console.md#customer-profile). */
export function useCustomerProfile(customerId: string | null) {
  const { cafeId, userId } = useSession();
  return useLive<CustomerProfile>({
    key: ["profile", cafeId, customerId], enabled: !!customerId, cafeId,
    tables: ["customer_notes", "customer_preferences"],
    fetcher: async () => {
      const cid = customerId!;
      type FeedbackRow = { id: string; rating: number; text: string | null; created_at: string; orders: { number: number } };
      type ComplaintRow = { id: string; created_at: string; reason_tag: string | null; conversation_id: string };
      const [cardMap, prefs, notes, names, feedback, complaints, recent] = await Promise.all([
        cards([cid]),
        rows<Preference[]>(supabase.from("customer_preferences").select("id, customer_id, kind, value, source, note_id")
          .eq("customer_id", cid).order("created_at")),
        rows<CustomerNote[]>(supabase.from("customer_notes").select("*").eq("customer_id", cid)
          .order("created_at", { ascending: false }).limit(30)),
        staffNames(cafeId!),
        rows<FeedbackRow[]>(supabase.from("feedback").select("id, rating, text, created_at, orders!inner(number, customer_id)")
          .eq("orders.customer_id", cid).order("created_at", { ascending: false }).limit(20)),
        rows<ComplaintRow[]>(supabase.from("requests").select("id, created_at, reason_tag, conversation_id, conversations!inner(customer_id)")
          .eq("conversations.customer_id", cid).in("reason_tag", ["complaint", "frustration"])
          .order("created_at", { ascending: false }).limit(10)),
        rows<CustomerProfile["recent"]>(supabase.from("orders").select("id, number, status, total_paise, created_at, order_items(name_snapshot, qty)")
          .eq("customer_id", cid).order("created_at", { ascending: false }).limit(5))
          .then((r) => r.map((o) => ({ ...o, items: (o as unknown as { order_items: CustomerProfile["recent"][0]["items"] }).order_items ?? [] }))),
      ]);
      const convIds = [...new Set(complaints.map((c) => c.conversation_id))];
      const words = convIds.length
        ? await rows<{ conversation_id: string; payload: { text?: { body?: string } }; received_at: string }[]>(
          supabase.from("inbound_messages").select("conversation_id, payload, received_at").in("conversation_id", convIds)
            .eq("type", "text").order("received_at", { ascending: false }).limit(100))
        : [];
      const nameOf = (uid: string) => (uid === userId ? "You" : names.get(uid) ?? "A teammate");
      return {
        card: cardMap.get(cid) ?? null,
        prefs,
        notes: notes.map((n) => ({ ...n, author: nameOf(n.author_id) })),
        feedback: feedback.map((f) => ({ id: f.id, rating: f.rating, text: f.text, created_at: f.created_at, number: f.orders.number })),
        complaints: [
          ...feedback.filter((f) => f.rating <= 2).map((f) => ({
            id: f.id, at: f.created_at, label: `${f.rating}★ on #${f.orders.number}`, words: f.text,
          })),
          ...complaints.map((c) => ({
            id: c.id, at: c.created_at, label: c.reason_tag === "frustration" ? "Frustrated in chat" : "Complaint in chat",
            words: words.find((w) => w.conversation_id === c.conversation_id
              && Date.parse(w.received_at) <= Date.parse(c.created_at) + 5_000)?.payload?.text?.body ?? null,
          })),
        ].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)),
        recent,
      };
    },
  });
}

export function useMenu() {
  const { cafeId } = useSession();
  return useLive<MenuItem[]>({
    key: ["menu", cafeId], enabled: !!cafeId, cafeId, tables: ["menu_items"],
    fetcher: () => rows<MenuItem[]>(supabase.from("menu_items")
      .select("id, name, description, category, price_paise, prep_minutes, veg, spice, available, version, updated_at")
      .eq("cafe_id", cafeId!).order("category").order("name")),
  });
}

export type AllergenState = "contains" | "free_from" | "unknown";

/** Allergen states of one dish; no row = unknown. */
export function useItemAllergens(itemId: string | null) {
  const { cafeId } = useSession();
  return useLive<Record<string, AllergenState>>({
    key: ["menu-allergens", cafeId, itemId], enabled: !!cafeId && !!itemId, cafeId, tables: [],
    fetcher: async () => {
      const list = await rows<{ allergen: string; state: AllergenState }[]>(supabase.from("menu_item_allergens")
        .select("allergen, state").eq("cafe_id", cafeId!).eq("item_id", itemId!));
      return Object.fromEntries(list.map((a) => [a.allergen, a.state]));
    },
  });
}

export interface TableRow { id: string; label: string; area: string | null; seats: number | null; qr_text: string }

export function useTableList() {
  const { cafeId } = useSession();
  return useLive<TableRow[]>({
    key: ["table-list", cafeId], enabled: !!cafeId, cafeId, tables: [],
    fetcher: async () => (await rows<TableRow[]>(supabase.from("tables").select("id, label, area, seats, qr_text").eq("cafe_id", cafeId!)))
      .sort((a, b) => a.label.localeCompare(b.label, "en", { numeric: true })), // T2 before T10
  });
}

export interface TeamMember { id: string; user_id: string; role: "shift_manager" | "manager" | "owner"; active: boolean; display_name: string | null }

/** The owner sees every membership of the café (RLS); others see only their own row. */
export function useTeam() {
  const { cafeId } = useSession();
  return useLive<TeamMember[]>({
    key: ["team", cafeId], enabled: !!cafeId, cafeId, tables: [],
    fetcher: () => rows(supabase.from("memberships").select("id, user_id, role, active, display_name")
      .eq("cafe_id", cafeId!).order("role").order("display_name")),
  });
}

export function useTables() {
  const { cafeId } = useSession();
  return useLive<{ id: string; label: string }[]>({
    key: ["tables", cafeId], enabled: !!cafeId, cafeId, tables: [],
    fetcher: () => rows(supabase.from("tables").select("id, label").eq("cafe_id", cafeId!).order("label")),
  });
}

export interface ThreadItem {
  id: string;
  dir: "in" | "out";
  by: "customer" | "agent" | "staff";
  at: string;
  text: string;
  buttons: string[];
  status: string | null; // outbound only
}

export interface Thread {
  conversation: Conversation;
  customerName: string | null;
  maskedPhone: string;
  table: string | null;
  windowOpen: boolean;
  assignedName: string | null;
  items: ThreadItem[];
}

type Payload = Record<string, unknown> & {
  text?: { body?: string };
  interactive?: {
    body?: { text?: string };
    button_reply?: { title?: string };
    list_reply?: { title?: string };
    action?: { buttons?: { reply?: { title?: string } }[]; button?: string; sections?: { rows?: { title?: string }[] }[] };
  };
  template?: { name?: string };
};

/** Customer text and taps, agent and staff messages, as staff read them. Never raw JSON. */
export function messageText(type: string, p: Payload): { text: string; buttons: string[] } {
  if (p.text?.body) return { text: p.text.body, buttons: [] };
  const i = p.interactive;
  if (i?.button_reply?.title) return { text: `Tapped “${i.button_reply.title}”`, buttons: [] };
  if (i?.list_reply?.title) return { text: `Picked “${i.list_reply.title}”`, buttons: [] };
  if (i?.body?.text) {
    const buttons = i.action?.buttons?.map((b) => b.reply?.title ?? "").filter(Boolean)
      ?? i.action?.sections?.flatMap((s) => s.rows?.map((r) => r.title ?? "") ?? []).filter(Boolean) ?? [];
    return { text: i.body.text, buttons };
  }
  if (type === "template" || p.template) return { text: `Template · ${p.template?.name ?? ""}`.trim(), buttons: [] };
  return { text: `(${type})`, buttons: [] };
}

/** One chat (docs/console.md#chats). */
export function useThread(conversationId: string | null) {
  const { cafeId, userId } = useSession();
  return useLive<Thread | null>({
    key: ["thread", cafeId, conversationId], enabled: !!conversationId, cafeId,
    tables: ["conversations", "inbound_messages", "wa_messages"],
    fetcher: async () => {
      type Row = Conversation & { created_at: string; table_sessions: { tables: { label: string } | null } | null;
        customers: { last_inbound_at: string | null } | null };
      const conv = (await rows<Row[]>(supabase.from("conversations")
        .select("id, cafe_id, customer_id, mode, assigned_to, table_session_id, updated_at, created_at, table_sessions(tables(label)), customers(last_inbound_at)")
        .eq("id", conversationId!)))[0];
      if (!conv) return null;
      const since = new Date(Date.now() - 7 * 24 * 3600_000).toISOString();
      const [ins, outs, cardMap, names] = await Promise.all([
        rows<{ id: string; type: string; payload: Payload; received_at: string }[]>(supabase.from("inbound_messages")
          .select("id, type, payload, received_at").eq("conversation_id", conv.id).gte("received_at", since)
          .order("received_at").limit(300)),
        rows<{ id: string; type: string; payload: Payload; status: string; idempotency_key: string; created_at: string }[]>(
          supabase.from("wa_messages").select("id, type, payload, status, idempotency_key, created_at")
            .eq("customer_id", conv.customer_id).gte("created_at", since).order("created_at").limit(300)),
        cards([conv.customer_id]),
        staffNames(cafeId!),
      ]);
      const items: ThreadItem[] = [
        ...ins.map((m) => ({ id: m.id, dir: "in" as const, by: "customer" as const, at: m.received_at, status: null,
          ...messageText(m.type, m.payload) })),
        ...outs.map((m) => ({ id: m.id, dir: "out" as const, by: m.idempotency_key.startsWith("staff:") ? "staff" as const : "agent" as const,
          at: m.created_at, status: m.status, ...messageText(m.type, m.payload) })),
      ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
      const card = cardMap.get(conv.customer_id);
      const lastIn = conv.customers?.last_inbound_at;
      return {
        conversation: conv,
        customerName: card?.first_name || null,
        maskedPhone: card?.masked_phone ?? "••••",
        table: conv.table_sessions?.tables?.label ?? null,
        windowOpen: !!lastIn && Date.now() - Date.parse(lastIn) < 24 * 3600_000,
        assignedName: conv.assigned_to ? (conv.assigned_to === userId ? "You" : names.get(conv.assigned_to) ?? "A teammate") : null,
        items,
      };
    },
  });
}

export type Bill = components["schemas"]["Bill"];

/** The bill as the backend computes it (orders, lines, subtotal, code, discount, total). Money only from the API. */
export function useBill(tableSessionId: string | null) {
  const { cafeId } = useSession();
  return useLive<Bill>({
    key: ["bill", cafeId, tableSessionId], enabled: !!tableSessionId, cafeId, tables: ["orders"],
    fetcher: () => call(api.GET("/bills/{table_session_id}", {
      params: { ...cafeHeader(), path: { table_session_id: tableSessionId! } },
    })),
  });
}

/** Open table sessions, for "Message a table". */
export function useOpenSessions() {
  const { cafeId } = useSession();
  return useLive<{ id: string; label: string; opened_at: string }[]>({
    key: ["sessions", cafeId], enabled: !!cafeId, cafeId, tables: [],
    fetcher: async () => {
      const data = await rows<{ id: string; opened_at: string; tables: { label: string } | null }[]>(supabase.from("table_sessions")
        .select("id, opened_at, tables(label)").eq("cafe_id", cafeId!).is("closed_at", null).order("opened_at"));
      return data.map((s) => ({ id: s.id, opened_at: s.opened_at, label: s.tables?.label ?? "—" }));
    },
  });
}

// ── Customers (owner): v_customer_profile, consents ───────────────────────

export interface CustomerProfileRow {
  customer_id: string; name: string | null; masked_phone: string; visits: number; spend_paise: number;
  first_seen_at: string; last_visit_at: string | null; top_items: string[] | null; avg_rating: number | null; rating_count: number;
  consent_status: "opted_in" | "opted_out" | "not_asked"; consent_at: string | null; consent_source: string | null;
  campaigns_received: number; campaigns_redeemed: number; segment: "regular" | "new" | "lapsed" | null;
}

export const PAGE = 50;

/** Server-side search, segment filter and paging (50 a page), plus whole-café counts per segment. */
export function useCustomerList(search: string, segment: string | null, page: number) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["customers", cafeId, search, segment, page],
    enabled: !!cafeId,
    queryFn: async () => {
      let q = supabase.from("v_customer_profile").select("*", { count: "exact" }).eq("cafe_id", cafeId!)
        .gt("visits", 0).order("last_visit_at", { ascending: false, nullsFirst: false }).range(page * PAGE, page * PAGE + PAGE - 1);
      if (search.trim()) q = q.ilike("name", `%${search.trim().replace(/[%_]/g, "")}%`);
      if (segment) q = q.eq("segment", segment);
      const { data, error, count } = await q;
      if (error) throw new Error(error.message);
      const counts = await Promise.all([null, "regular", "new", "lapsed"].map(async (seg) => {
        let c = supabase.from("v_customer_profile").select("customer_id", { count: "exact", head: true }).eq("cafe_id", cafeId!).gt("visits", 0);
        if (seg) c = c.eq("segment", seg);
        const r = await c;
        return r.count ?? 0;
      }));
      return { rows: (data ?? []) as CustomerProfileRow[], total: count ?? 0, counts: { all: counts[0]!, regular: counts[1]!, new: counts[2]!, lapsed: counts[3]! } };
    },
  });
}

export function useCustomerProfileRow(customerId: string | null) {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["customer-row", cafeId, customerId],
    enabled: !!cafeId && !!customerId,
    queryFn: async () => (await rows<CustomerProfileRow[]>(supabase.from("v_customer_profile").select("*").eq("customer_id", customerId!)))[0] ?? null,
  });
}

export interface ConsentRow { customer_id: string; status: string; wording_version: number | null; source: string; created_at: string; masked_phone: string; name: string | null }

/** Latest consent row per customer (read-only register). */
export function useConsentRegister() {
  const { cafeId } = useSession();
  return useQuery({
    queryKey: ["consents", cafeId],
    enabled: !!cafeId,
    queryFn: async () => {
      const list = await rows<{ customer_id: string; status: string; wording_version: number | null; source: string; created_at: string }[]>(
        supabase.from("consents").select("customer_id, status, wording_version, source, created_at").eq("cafe_id", cafeId!)
          .order("created_at", { ascending: false }).limit(2000));
      const latest = new Map<string, (typeof list)[number]>();
      for (const r of list) if (!latest.has(r.customer_id)) latest.set(r.customer_id, r);
      const ids = [...latest.keys()];
      const people = new Map<string, { masked_phone: string; name: string | null }>();
      for (let i = 0; i < ids.length; i += 200) {
        const chunk = await rows<{ customer_id: string; masked_phone: string; name: string | null }[]>(
          supabase.from("v_customer_profile").select("customer_id, masked_phone, name").in("customer_id", ids.slice(i, i + 200)));
        for (const c of chunk) people.set(c.customer_id, c);
      }
      return [...latest.values()].map((r) => ({ ...r, masked_phone: people.get(r.customer_id)?.masked_phone ?? "••••", name: people.get(r.customer_id)?.name ?? null }));
    },
  });
}
