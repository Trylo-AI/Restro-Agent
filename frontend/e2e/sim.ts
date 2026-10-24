// A synthetic WhatsApp customer driven through the local simulator API (/dev/sim/*), taps only (no LLM).
const API = () => process.env.E2E_API_URL ?? "http://localhost:8000";

async function sim<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API()}/dev/sim${path}`, {
    ...init,
    headers: { "content-type": "application/json", "x-sim-token": process.env.E2E_SIM_TOKEN ?? "", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`sim ${path}: ${res.status}`);
  return (await res.json()) as T;
}

export interface Out { dir: "out"; id: string; payload: unknown; status: string; error_code: string | null }
interface Item { dir: "in" | "out"; payload: unknown; at: string }

export type Fault = "send_timeout" | "send_429" | "llm_fail" | "llm_invalid" | "turn_crash";

/** Arms one simulator fault; each fires once, on the next matching step of any conversation. */
export async function arm(fault: Fault, times = 1): Promise<void> {
  for (let i = 0; i < times; i++) await sim("/faults", { method: "POST", body: JSON.stringify({ fault }) });
}

export async function armedFaults(): Promise<Record<string, string[]>> {
  return sim("/faults");
}

/** Dish names in a cart or order summary ("2 × Cold Coffee ₹ 380"). */
export function summaryNames(payload: string): string[] {
  const body = (JSON.parse(payload) as { interactive?: { body?: { text?: string } } }).interactive?.body?.text ?? "";
  return [...body.matchAll(/\d+ × (.+?) ₹/g)].map((m) => m[1]!);
}

export class Customer {
  private seen = 0;
  /** Set by placeOrder: the id carried by the "Edit order" button. */
  orderId = "";
  constructor(public waId: string) {}

  static async create(): Promise<Customer> {
    const { wa_id } = await sim<{ wa_id: string }>("/customers", { method: "POST" });
    return new Customer(wa_id);
  }

  /** Every outbound row for this customer, oldest first. */
  async outItems(): Promise<Out[]> {
    const t = await sim<{ items: (Item | Out)[] }>(`/thread?wa_id=${this.waId}`);
    return t.items.filter((i): i is Out => i.dir === "out");
  }

  private async outs(): Promise<string[]> {
    return (await this.outItems()).map((i) => JSON.stringify(i.payload));
  }

  /** Waits for a new outbound message matching `re` and returns it. */
  async expectReply(re: RegExp, timeoutMs = 30_000): Promise<string> {
    const until = Date.now() + timeoutMs;
    while (Date.now() < until) {
      const outs = await this.outs();
      // By position, not indexOf: the same message (a category list) can arrive twice.
      const at = outs.findIndex((p, i) => i >= this.seen && re.test(p));
      if (at >= 0) {
        this.seen = at + 1;
        return outs[at]!;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error(`no reply matching ${re} for the simulated customer`);
  }

  async say(text: string): Promise<void> {
    await sim("/send", { method: "POST", body: JSON.stringify({ wa_id: this.waId, kind: "text", text }) });
  }

  async tap(id: string, list = false): Promise<void> {
    await sim("/send", {
      method: "POST",
      body: JSON.stringify({ wa_id: this.waId, kind: list ? "list" : "button", reply_id: id, reply_title: id.slice(0, 20) }),
    });
  }

  /** QR → name, up to the Menu button. */
  async seat(table: number, name: string): Promise<void> {
    await this.say(`Hi, I'm at Table ${table}`);
    await this.expectReply(/What should we call you\?/);
    await this.say(name);
    await this.expectReply(/"id":\s*"menu"/);
  }

  /** Menu → first category → the dish at `index`. Returns the reply that follows the tap (a summary). */
  async addDish(index = 0, until: RegExp = /"id":\s*"confirm:\d+"/): Promise<string> {
    await this.tap("menu");
    const cats = await this.expectReply(/"id":\s*"cat:/);
    await this.tap(cats.match(/"id":\s*"(cat:[^"]+)"/)![1]!, true);
    const dishes = await this.expectReply(/"id":\s*"item:/);
    const ids = [...dishes.matchAll(/"id":\s*"(item:[^"]+)"/g)].map((m) => m[1]!);
    await this.tap(ids[index]!, true);
    return this.expectReply(until);
  }

  /** Taps Confirm on `summary` and returns the order number. */
  async confirm(summary: string): Promise<number> {
    await this.tap(summary.match(/"id":\s*"(confirm:\d+)"/)![1]!);
    const received = await this.expectReply(/Order #\d+ received/);
    this.orderId = received.match(/"id":\s*"edit_order:([^"]+)"/)![1]!;
    return Number(received.match(/Order #(\d+) received/)![1]);
  }

  /** QR → name → menu → first category → first dish → Confirm. Returns the order number. */
  async placeOrder(table: number, name: string): Promise<number> {
    await this.seat(table, name);
    return this.confirm(await this.addDish(0));
  }
}
