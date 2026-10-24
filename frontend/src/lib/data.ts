// Read hooks (Supabase + RLS + Realtime). Screens never create their own client; they use these.
// Never select customers.wa_id: masked numbers come from v_customer_card.
import { useLive, rows } from "./live";
import { useSession } from "./session";
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

async function staffNames(cafeId: string): Promise<Map<string, string>> {
  const data = await rows<{ user_id: string; display_name: string | null }[]>(
    supabase.from("memberships").select("user_id, display_name").eq("cafe_id", cafeId));
  return new Map(data.map((m) => [m.user_id, m.display_name ?? "Staff"]));
}

export function useCafe() {
  const { cafeId } = useSession();
  return useLive<Cafe | null>({
    key: ["cafe", cafeId], enabled: !!cafeId, cafeId, tables: ["cafes"],
    fetcher: async () => (await rows<Cafe[]>(supabase.from("cafes").select("id, name, status, settings, updated_at").eq("id", cafeId!)))[0] ?? null,
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
  const { cafeId } = useSession();
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
      const [cardMap, names, escalations, words] = await Promise.all([
        cards(customerIds),
        staffNames(cafeId!),
        data.length ? rows<Escalation[]>(supabase.from("escalations").select("*").eq("subject_type", "request")
          .in("subject_id", data.map((r) => r.id))) : Promise.resolve([] as Escalation[]),
        convIds.length ? rows<{ conversation_id: string; payload: { text?: { body?: string } }; received_at: string }[]>(
          supabase.from("inbound_messages").select("conversation_id, payload, received_at").in("conversation_id", convIds)
            .eq("type", "text").order("received_at", { ascending: false }).limit(200)) : Promise.resolve([]),
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
          ackName: r.acknowledged_by ? names.get(r.acknowledged_by) ?? "Staff" : null,
          words: last?.payload?.text?.body ?? null,
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
          lastText: lastIn ? (lastIn.type === "text" ? lastIn.payload?.text?.body ?? "" : `(${lastIn.type})`) : null,
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
      .select("id, name, category, price_paise, prep_minutes, veg, spice, available, version, updated_at")
      .eq("cafe_id", cafeId!).order("category").order("name")),
  });
}

export function useTables() {
  const { cafeId } = useSession();
  return useLive<{ id: string; label: string }[]>({
    key: ["tables", cafeId], enabled: !!cafeId, cafeId, tables: [],
    fetcher: () => rows(supabase.from("tables").select("id, label").eq("cafe_id", cafeId!).order("label")),
  });
}
