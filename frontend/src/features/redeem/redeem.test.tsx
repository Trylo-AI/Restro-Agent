// Redeem an offer at the counter (docs/console.md#redeem-offer, "Done when"). The API (mocked call()) decides every
// verdict, discount and amount to collect; Redeemed today reads v_redemptions (a fake Supabase builder).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ToastProvider } from "../../app/toasts";
import { api, ApiError } from "../../lib/api/client";
import { bills } from "../../lib/mutations";
import type { CounterRedemption, OfferCheck, OfferLookup } from "../../lib/offers";
import { parseAmount } from "./RedeemDialog";
import { RedeemScreen } from "./RedeemScreen";

const h = vi.hoisted(() => {
  type Call = [string, unknown[]];
  const state = {
    offline: false,
    tables: {} as Record<string, unknown[]>,
    failing: [] as string[], // tables whose read returns an error
    log: [] as { table: string; calls: Call[] }[],
    lookup: null as unknown,
    redeem: (async () => ({})) as (opts: unknown) => Promise<unknown>,
  };
  function from(table: string) {
    const entry = { table, calls: [] as Call[] };
    state.log.push(entry);
    const b: Record<string | symbol, unknown> = new Proxy({}, {
      get(_t, prop) {
        if (prop === "then") {
          const data = state.tables[table] ?? [];
          const error = state.failing.includes(table) ? { message: "down" } : null;
          return (ok: (v: unknown) => unknown, err: (e: unknown) => unknown) =>
            Promise.resolve({ data: error ? null : data, error, count: data.length }).then(ok, err);
        }
        return (...args: unknown[]) => { entry.calls.push([String(prop), args]); return b; };
      },
    });
    return b;
  }
  return { state, from };
});

vi.mock("../../lib/session", async (orig) => ({
  ...(await orig<typeof import("../../lib/session")>()),
  useSession: () => ({ cafeId: "cafe-1", role: "shift_manager", userId: "u-1" }),
}));
vi.mock("../../lib/supabase", () => ({ supabase: { from: h.from } }));
vi.mock("../../app/App", () => ({ useOffline: () => h.state.offline }));
vi.mock("../../lib/api/client", async (orig) => {
  const real = await orig<typeof import("../../lib/api/client")>();
  return {
    ...real,
    api: { POST: vi.fn(async (path: string, opts: unknown) => ({ path, opts })), GET: vi.fn() },
    call: vi.fn(async (p: Promise<{ path: string; opts: unknown }>) => {
      const { path, opts } = await p;
      if (path === "/offers/lookup") return h.state.lookup;
      if (path === "/offers/redeem") return h.state.redeem(opts);
      throw new Error(`unexpected ${path}`);
    }),
  };
});
vi.mock("../../lib/mutations", async (orig) => ({
  ...(await orig<typeof import("../../lib/mutations")>()),
  bills: { applyCode: vi.fn(), deliver: vi.fn() },
}));

function offer(over: Partial<OfferCheck>): OfferCheck {
  return {
    recipient_id: "r-1", code: "BACK20-7KQ", campaign_id: "c-1", campaign_name: "Weekend comeback", offer_text: "20% off your next visit",
    discount_percent: 20, valid_until: "2026-10-12T10:12:00Z", sent_at: "2026-10-05T05:00:00Z", recipient_status: "read",
    verdict: "eligible", reason: "20 % off the bill · valid till 12 Oct", warning: null, matched: false, redemption: null, ...over,
  };
}

const LOOKUP: OfferLookup = {
  query_kind: "name", truncated: false,
  customers: [{
    customer_id: "cu-1", name: "Riya Sharma", first_name: "Riya", masked_phone: "+91 98••• ••421", deleted: false, visits: 6,
    last_visit_at: "2026-10-12T10:00:00Z",
    open_bill: { table_session_id: "ts-1", table_label: "T7", subtotal_paise: 64000, code: null },
    offers: [
      offer({ warning: "Opted out of offers since; this code still works." }),
      offer({
        recipient_id: "r-2", code: "WINBACK-ABC", campaign_name: "Winback", verdict: "redeemed", reason: "Used today at 12:41 at the counter by Ravi",
        redemption: { redeemed_at: "2026-10-12T07:11:00Z", channel: "counter", redeemed_by_name: "Ravi", order_number: null, table_label: null,
          bill_paise: 50000, discount_paise: 10000 },
      }),
      offer({ recipient_id: "r-3", code: "OLD10-XYZ", campaign_name: "Old", verdict: "expired", reason: "Expired on 1 Oct." }),
    ],
  }],
};

const DONE: CounterRedemption = {
  redemption_id: "d-1", code: "BACK20-7KQ", channel: "counter", campaign_id: "c-1", campaign_name: "Weekend comeback",
  offer_text: "20% off your next visit", discount_percent: 20, customer_id: "cu-1", first_name: "Riya", bill_paise: 124000,
  discount_paise: 24800, total_paise: 99200, redeemed_at: "2026-10-12T07:11:00Z", redeemed_by_name: "Asha",
};

const POST = () => vi.mocked(api.POST).mock.calls as unknown as [string, { body?: Record<string, unknown>; params: { header: Record<string, string> } }][];
const posts = (path: string) => POST().filter(([p]) => p === path);

beforeEach(() => {
  h.state.offline = false;
  h.state.tables = { cafes: [{ timezone: "Asia/Kolkata" }], v_redemptions: [] };
  h.state.failing = [];
  h.state.log = [];
  h.state.lookup = LOOKUP;
  h.state.redeem = async () => DONE;
  vi.mocked(api.POST).mockClear();
  vi.mocked(bills.applyCode).mockReset();
});

function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ToastProvider><MemoryRouter><RedeemScreen /></MemoryRouter></ToastProvider>
    </QueryClientProvider>,
  );
}

async function search(text: string) {
  fireEvent.change(screen.getByLabelText("Search offers"), { target: { value: text } });
  return screen.findByRole("article", { name: "Riya" });
}

async function openDialog() {
  const card = await search("Riya");
  fireEvent.click(within(card).getByRole("button", { name: "Redeem at counter" }));
  return screen.getByRole("dialog", { name: "Redeem BACK20-7KQ" });
}

function amount(dialog: HTMLElement, text: string) {
  fireEvent.change(within(dialog).getByLabelText("Amount paid (₹, before discount)"), { target: { value: text } });
}

test("search shows verdict chips and the API reason", async () => {
  show();
  const card = await search("Riya");
  expect(posts("/offers/lookup")[0]![1].body).toEqual({ q: "Riya" });
  expect(within(card).getByText("Eligible")).toBeInTheDocument();
  expect(within(card).getByText("Used")).toBeInTheDocument();
  expect(within(card).getByText("Expired")).toBeInTheDocument();
  expect(within(card).getByText("20 % off the bill · valid till 12 Oct")).toBeInTheDocument();
  expect(within(card).getByText("Expired on 1 Oct.")).toBeInTheDocument();
  expect(within(card).getByText("Opted out of offers since; this code still works.")).toBeInTheDocument();
  expect(within(card).getByText("+91 98••• ••421")).toBeInTheDocument();
  expect(within(card).getByText(/6th visit · last 12 Oct/)).toBeInTheDocument();
  expect(within(card).getByText("At T7 · bill open · ₹ 640")).toBeInTheDocument();
});

test("1 to 3 digits shows the hint and makes no call", async () => {
  show();
  fireEvent.change(screen.getByLabelText("Search offers"), { target: { value: "421" } });
  expect(screen.getByText("Type the last 4 digits of the number")).toBeInTheDocument();
  await act(() => new Promise((r) => setTimeout(r, 450)));
  expect(posts("/offers/lookup")).toHaveLength(0);
});

test("only eligible offers have Redeem; a used offer shows who, when and where", async () => {
  show();
  const card = await search("Riya");
  expect(within(card).getAllByRole("button", { name: "Redeem at counter" })).toHaveLength(1);
  expect(within(card).getAllByRole("button", { name: "Apply to T7 bill" })).toHaveLength(1);
  expect(within(card).getByText("Used today at 12:41 at the counter by Ravi")).toBeInTheDocument();
  expect(within(card).getByText(/^Counter · .* · by Ravi · ₹ 100 off$/)).toBeInTheDocument();
});

test("redeem sends paise and a key, then shows Collect from the API", async () => {
  show();
  const dialog = await openDialog();
  amount(dialog, "1,240");
  await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 1,240" })));
  const [, opts] = posts("/offers/redeem")[0]!;
  expect(opts.body).toEqual({ code: "BACK20-7KQ", bill_paise: 124000 });
  expect(opts.params.header["Idempotency-Key"]).toMatch(/.{8,}/);
  expect(await screen.findByText("Collect ₹ 992")).toBeInTheDocument();
  expect(screen.getByText(/Bill ₹ 1,240 · Discount −₹ 248 · Redeemed by you at/)).toBeInTheDocument();
  expect(screen.getByText("BACK20-7KQ redeemed · ₹ 248 off")).toBeInTheDocument();
});

test("a retry after a network error reuses the key", async () => {
  let n = 0;
  h.state.redeem = async () => { if (n++ === 0) throw new ApiError(0, "network", "Can't reach Restro. Check the connection and try again."); return DONE; };
  show();
  const dialog = await openDialog();
  amount(dialog, "1240");
  const button = within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 1,240" });
  await act(async () => fireEvent.click(button));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(/Can't reach Restro/);
  expect(within(dialog).getByLabelText("Amount paid (₹, before discount)")).toHaveValue("1240");
  await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 1,240" })));
  const keys = posts("/offers/redeem").map(([, o]) => o.params.header["Idempotency-Key"]);
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  expect(await screen.findByText("Collect ₹ 992")).toBeInTheDocument();
});

test("a network error, then the same amount retyped, still reuses the key; the amount is locked while sending", async () => {
  let n = 0;
  let release = () => {};
  h.state.redeem = async () => {
    if (n++ === 0) throw new ApiError(0, "network", "Can't reach Restro. Check the connection and try again.");
    await new Promise<void>((r) => { release = r; });
    return DONE;
  };
  show();
  const dialog = await openDialog();
  const input = within(dialog).getByLabelText("Amount paid (₹, before discount)");
  amount(dialog, "1240");
  await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 1,240" })));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(/Can't reach Restro/);
  amount(dialog, "");
  amount(dialog, "124");
  amount(dialog, "1240.00");
  await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 1,240" })));
  expect(input).toBeDisabled();
  await act(async () => release());
  const keys = posts("/offers/redeem").map(([, o]) => o.params.header["Idempotency-Key"]);
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  expect(await screen.findByText("Collect ₹ 992")).toBeInTheDocument();
});

test("409 shows the API message and refetches", async () => {
  h.state.redeem = async () => { throw new ApiError(409, "code_already_used", "Used today at 12:41 at the counter by Ravi"); };
  show();
  const dialog = await openDialog();
  const before = posts("/offers/lookup").length;
  amount(dialog, "500");
  await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 500" })));
  const alert = await within(dialog).findByRole("alert");
  expect(alert).toHaveTextContent("Already used.");
  expect(alert).toHaveTextContent("Used today at 12:41 at the counter by Ravi");
  await waitFor(() => expect(posts("/offers/lookup").length).toBeGreaterThan(before));
});

test("open bill shows Apply and calls apply-code", async () => {
  vi.mocked(bills.applyCode).mockResolvedValue({ table_session_id: "ts-1", orders: [], total_paise: 51200, discount_paise: 12800 } as never);
  show();
  const card = await search("Riya");
  await act(async () => fireEvent.click(within(card).getByRole("button", { name: "Apply to T7 bill" })));
  expect(bills.applyCode).toHaveBeenCalledWith("ts-1", "BACK20-7KQ");
  expect(await within(card).findByText("Applied to T7 bill · ₹ 128 off")).toBeInTheDocument();
});

test("Redeem disabled offline", async () => {
  h.state.offline = true;
  show();
  const card = await search("Riya");
  expect(within(card).getByRole("button", { name: "Redeem at counter" })).toBeDisabled();
  expect(within(card).getByRole("button", { name: "Apply to T7 bill" })).toBeDisabled();
});

test("amount rejects 0, 3 decimals and over ₹ 1,00,000", async () => {
  expect(parseAmount("0").paise).toBeNull();
  expect(parseAmount("12.345").paise).toBeNull();
  expect(parseAmount("100000.01").error).toBe("Check the amount: over ₹ 1,00,000");
  expect(parseAmount("100000").paise).toBe(10_000_000);
  expect(parseAmount("1240.5").paise).toBe(124050);
  show();
  const dialog = await openDialog();
  for (const bad of ["0", "12.345", "150000"]) {
    amount(dialog, bad);
    expect(within(dialog).getByRole("button", { name: "Redeem" })).toBeDisabled();
  }
  expect(within(dialog).getByText("Check the amount: over ₹ 1,00,000")).toBeInTheDocument();
  expect(posts("/offers/redeem")).toHaveLength(0);
});

test("changing the amount after a failed attempt sends a new key", async () => {
  let n = 0;
  h.state.redeem = async () => { if (n++ === 0) throw new ApiError(400, "bad_request", "Check the amount."); return DONE; };
  show();
  const dialog = await openDialog();
  amount(dialog, "1240");
  await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 1,240" })));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Check the amount.");
  amount(dialog, "1250");
  await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Redeem 20 % off ₹ 1,250" })));
  const calls = posts("/offers/redeem");
  expect(calls.map(([, o]) => o.body!.bill_paise)).toEqual([124000, 125000]);
  expect(calls[1]![1].params.header["Idempotency-Key"]).not.toBe(calls[0]![1].params.header["Idempotency-Key"]);
});

test("Redeemed today queries v_redemptions with cafe_id and the café-local date", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-12T12:00:00Z")); // 13 Oct 02:00 in the café's zone; still 12 Oct in UTC and IST
  try {
    h.state.tables = {
      cafes: [{ timezone: "Pacific/Kiritimati" }],
      v_redemptions: [{
        redemption_id: "d-1", cafe_id: "cafe-1", campaign_id: "c-1", campaign_name: "Weekend comeback", campaign_recipient_id: "r-1",
        code: "BACK20-7KQ", channel: "table_bill", redeemed_at: "2026-10-12T11:00:00Z", day: "2026-10-13", customer_id: "cu-1",
        first_name: "Riya", masked_phone: "+91 98••• ••421", bill_paise: 124000, discount_paise: 24800, redeemed_by: "u-2",
        redeemed_by_name: "Ravi", order_number: 1041, table_label: "T7", table_session_id: "ts-1",
      }],
    };
    show();
    const today = await screen.findByRole("region", { name: "Redeemed today" });
    expect(await within(today).findByText("BACK20-7KQ")).toBeInTheDocument();
    expect(within(today).getByText("T7 bill")).toBeInTheDocument();
    expect(within(today).getByText("−₹ 248")).toBeInTheDocument();
    expect(within(today).getByText("by Ravi")).toBeInTheDocument();
    const read = h.state.log.find((e) => e.table === "v_redemptions")!;
    expect(read.calls).toContainEqual(["eq", ["cafe_id", "cafe-1"]]);
    expect(read.calls).toContainEqual(["eq", ["day", "2026-10-13"]]);
  } finally {
    vi.useRealTimers();
  }
});

test("Redeemed today falls back to the default zone when the café timezone can't be read", async () => {
  h.state.failing = ["cafes"];
  show();
  const today = await screen.findByRole("region", { name: "Redeemed today" });
  expect(await within(today).findByText("Nothing redeemed yet today.")).toBeInTheDocument();
  const read = h.state.log.find((e) => e.table === "v_redemptions")!;
  expect(read.calls).toContainEqual(["eq", ["day", new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date())]]);
});

test("Redeemed today says Former staff when the redeemer's membership is gone, Unknown with no staff", async () => {
  const row = {
    cafe_id: "cafe-1", campaign_id: "c-1", campaign_name: "Weekend comeback", campaign_recipient_id: "r-1", channel: "counter",
    redeemed_at: new Date().toISOString(), day: "2026-10-12", customer_id: "cu-1", first_name: "Riya", masked_phone: "+91 98••• ••421",
    bill_paise: 124000, discount_paise: 24800, order_number: null, table_label: null, table_session_id: null,
  };
  h.state.tables = {
    cafes: [{ timezone: "Asia/Kolkata" }],
    v_redemptions: [
      { ...row, redemption_id: "d-1", code: "BACK20-7KQ", redeemed_by: "u-9", redeemed_by_name: null },
      { ...row, redemption_id: "d-2", code: "BACK20-8MN", redeemed_by: null, redeemed_by_name: null },
    ],
  };
  show();
  const today = await screen.findByRole("region", { name: "Redeemed today" });
  expect(await within(today).findByText("by Former staff")).toBeInTheDocument();
  expect(within(today).getByText("by Unknown")).toBeInTheDocument();
});
