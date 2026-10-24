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

interface Item { dir: "in" | "out"; payload: unknown; at: string }

export class Customer {
  private seen = 0;
  constructor(public waId: string) {}

  static async create(): Promise<Customer> {
    const { wa_id } = await sim<{ wa_id: string }>("/customers", { method: "POST" });
    return new Customer(wa_id);
  }

  private async outs(): Promise<string[]> {
    const t = await sim<{ items: Item[] }>(`/thread?wa_id=${this.waId}`);
    return t.items.filter((i) => i.dir === "out").map((i) => JSON.stringify(i.payload));
  }

  /** Waits for a new outbound message matching `re` and returns it. */
  async expectReply(re: RegExp, timeoutMs = 30_000): Promise<string> {
    const until = Date.now() + timeoutMs;
    while (Date.now() < until) {
      const outs = await this.outs();
      const hit = outs.slice(this.seen).find((p) => re.test(p));
      if (hit) {
        this.seen = outs.indexOf(hit) + 1;
        return hit;
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

  /** QR → name → menu → first category → first dish → Confirm. Returns the order number. */
  async placeOrder(table: number, name: string): Promise<number> {
    await this.say(`Hi, I'm at Table ${table}`);
    await this.expectReply(/What should we call you\?/);
    await this.say(name);
    await this.expectReply(/"id":\s*"menu"/);
    await this.tap("menu");
    const cats = await this.expectReply(/"id":\s*"cat:/);
    await this.tap(cats.match(/"id":\s*"(cat:[^"]+)"/)![1]!, true);
    const dishes = await this.expectReply(/"id":\s*"item:/);
    await this.tap(dishes.match(/"id":\s*"(item:[^"]+)"/)![1]!, true);
    const summary = await this.expectReply(/"id":\s*"confirm:\d+"/);
    await this.tap(summary.match(/"id":\s*"(confirm:\d+)"/)![1]!);
    const received = await this.expectReply(/Order #\d+ received/);
    return Number(received.match(/Order #(\d+) received/)![1]);
  }
}
