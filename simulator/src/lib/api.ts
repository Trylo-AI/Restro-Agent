// Client for the api's /dev/sim/* routes (local only). Synthetic data only: never type real names or numbers.
import type { Alerts, Debug, Thread, CustomersResponse } from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const TOKEN = import.meta.env.VITE_SIMULATOR_TOKEN ?? "";

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}/dev/sim${path}`, {
    method,
    headers: { "X-Sim-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`);
  return (await res.json()) as T;
}

export type SendKind = "text" | "button" | "list" | "image" | "audio" | "sticker";

export const api = {
  customers: () => call<CustomersResponse>("GET", "/customers"),
  newCustomer: () => call<{ wa_id: string }>("POST", "/customers"),
  send: (wa_id: string, kind: SendKind, extra: { text?: string; reply_id?: string; reply_title?: string; name?: string } = {}) =>
    call<{ wamid: string; webhook_status: number }>("POST", "/send", { wa_id, kind, ...extra }),
  thread: (wa_id: string) => call<Thread>("GET", `/thread?wa_id=${encodeURIComponent(wa_id)}`),
  read: (wa_id: string) => call<{ read: number }>("POST", "/read", { wa_id }),
  debug: (wa_id: string) => call<Debug>("GET", `/debug?wa_id=${encodeURIComponent(wa_id)}`),
  reset: (wa_id: string) => call<{ reset: boolean }>("POST", `/customers/${wa_id}/reset`),
  closeWindow: (wa_id: string) => call<{ closed: boolean }>("POST", `/customers/${wa_id}/close-window`),
  armFault: (fault: string) => call<Record<string, string[]>>("POST", "/faults", { fault }),
  faults: () => call<Record<string, string[]>>("GET", "/faults"),
  alerts: () => call<Alerts>("GET", "/alerts"),
};
