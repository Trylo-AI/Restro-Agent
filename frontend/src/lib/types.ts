// Row shapes read through Supabase (contracts/schema.sql names, exactly).

export type Role = "shift_manager" | "manager" | "owner";
export type OrderStatus = "NEW" | "ACCEPTED" | "READY" | "SERVED" | "CANCELLED";

export interface Membership {
  cafe_id: string;
  role: Role;
  display_name: string | null;
  cafes: { id: string; name: string; status: string; settings: CafeSettings } | null;
}

export interface CafeSettings {
  paused?: boolean;
  tone?: string;
  feedback_delay_minutes?: number;
  alert_phone?: string;
  model?: string | null;
}

export interface Cafe {
  id: string;
  name: string;
  status: "onboarding" | "live" | "disconnected" | "paused";
  settings: CafeSettings;
  updated_at: string;
  opening_hours?: Record<string, [string, string][]>;
  cutoff_minutes?: number;
  display_phone_number?: string | null;
}

export interface OrderItem {
  id: string;
  order_id: string;
  item_id: string;
  name_snapshot: string;
  price_snapshot_paise: number;
  qty: number;
  note: string | null;
}

export interface Order {
  id: string;
  cafe_id: string;
  customer_id: string | null;
  table_session_id: string | null;
  number: number;
  status: OrderStatus;
  flags: string[];
  allergy_note: string | null;
  expected_minutes: number;
  delay_minutes: number;
  total_paise: number;
  source: "qr" | "campaign" | "manual";
  version: number;
  cancel_reason_code: string | null;
  created_at: string;
  accepted_at: string | null;
  ready_at: string | null;
  served_at: string | null;
  cancelled_at: string | null;
  updated_at: string;
}

export interface Request {
  id: string;
  cafe_id: string;
  table_session_id: string | null;
  conversation_id: string | null;
  order_id: string | null;
  kind: "help" | "bill" | "allergy" | "low_rating";
  status: "open" | "acknowledged" | "resolved";
  reason_tag: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  cafe_id: string;
  customer_id: string;
  mode: "bot" | "human";
  assigned_to: string | null;
  table_session_id: string | null;
  updated_at: string;
}

export interface Escalation {
  id: string;
  subject_type: "order" | "request";
  subject_id: string;
  level: number;
  target: string;
  sent_at: string;
}

export interface MenuItem {
  id: string;
  name: string;
  description?: string | null;
  category: string;
  price_paise: number;
  prep_minutes: number;
  veg: boolean;
  spice: number;
  available: boolean;
  version: number;
  updated_at: string;
}

export interface Preference {
  id: string;
  customer_id: string;
  kind: "diet" | "milk" | "sugar" | "spice" | "seating" | "allergy" | "dislike" | "like" | "other";
  value: string;
  source: "chat" | "staff_note";
  note_id: string | null;
}

export interface CustomerNote {
  id: string;
  customer_id: string;
  author_id: string;
  text: string;
  ai_status: "pending" | "applied" | "no_changes" | "failed";
  ai_ops: { op: string; kind: string; value: string; previous?: string | null }[];
  ai_summary: string | null;
  undone_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CustomerCard {
  customer_id: string;
  first_name: string;
  masked_phone: string;
  visits: number;
  first_seen_at: string;
  last_visit_at: string | null;
  top_items: string[] | null;
  segment: "new" | "regular" | "lapsed" | null;
  avg_rating: number | null;
  rating_count: number;
}
