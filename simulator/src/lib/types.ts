// Shapes returned by the api's /dev/sim/* routes (backend/app/api/dev/sim.py).

export interface ReplyButton {
  type: "reply";
  reply: { id: string; title: string };
}
export interface ListRow {
  id: string;
  title: string;
  description?: string;
}
export interface ListSection {
  title: string;
  rows: ListRow[];
}

export interface OutPayload {
  type: "text" | "interactive" | "template";
  text?: { body: string };
  interactive?: {
    type: "button" | "list";
    header?: { text: string };
    body: { text: string };
    footer?: { text: string };
    action: { buttons?: ReplyButton[]; button?: string; sections?: ListSection[] };
  };
  template?: { name: string; language: { code: string }; components: { parameters: { text: string }[] }[] };
}

export interface InPayload {
  type: string;
  text?: { body: string };
  interactive?: { type: string; button_reply?: { id: string; title: string }; list_reply?: { id: string; title: string } };
}

export type ThreadItem =
  | { dir: "in"; id: string; wamid: string; type: string; payload: InPayload; at: string; processed: boolean }
  | {
      dir: "out";
      id: string;
      wamid: string | null;
      type: string;
      template_name: string | null;
      payload: OutPayload;
      status: "pending" | "sent" | "delivered" | "read" | "ambiguous" | "failed";
      error_code: string | null;
      at: string;
      updated_at: string;
      violations: string[];
    };

export interface Thread {
  cafe: string;
  items: ThreadItem[];
  typing: boolean;
  customer: { id: string; name: string | null; window_open: boolean } | null;
  server_time?: string;
}

export interface CustomersResponse {
  cafe: string;
  customers: { wa_id: string; name: string | null }[];
  tables: { label: string; qr_text: string }[];
}

export interface Debug {
  conversation: {
    id: string;
    mode: "bot" | "human";
    assigned_to: string | null;
    state: Record<string, unknown> & {
      step?: string;
      awaiting?: string | null;
      clarify_strikes?: number;
      allergy_note?: string | null;
      cart?: { items: { item_id: string; qty: number; note: string | null; name?: string }[]; version: number };
    };
    table: string | null;
    lease: { owner: boolean; expires_at: string | null };
  } | null;
  work?: { status: string; attempts: number; next_attempt_at: string; last_error: string | null; dead_lettered_at: string | null }[];
  llm_calls?: {
    id: string; purpose: string; provider: string; model: string; outcome: string; latency_ms: number;
    input_tokens: number; cached_tokens: number; output_tokens: number; cost: string; currency: string; created_at: string;
  }[];
  traces?: { llm_call_id: string; purpose: string; prompt_chars: number; suffix: string; raw_output: string; parsed: unknown; guardrails: unknown; created_at: string }[];
  order_events?: { number: number; from_status: string | null; to_status: string; actor_type: string; reason: string | null; created_at: string }[];
  events: { at: string; kind: string; [k: string]: unknown }[];
}

export interface Alerts {
  alerts: { id: string; template_name: string | null; payload: OutPayload & { _to_ref?: string }; status: string; created_at: string }[];
}
