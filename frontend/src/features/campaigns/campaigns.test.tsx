// Campaigns for the owner (docs/console.md#campaigns, "Done when"): list, detail tabs, recipients filters and paging,
// the recipients CSV and Insights. Reads go through a fake Supabase builder that records every filter.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ToastProvider } from "../../app/toasts";
import { api } from "../../lib/api/client";
import type { CampaignInsight, RecipientRow } from "../../lib/campaignInsights";
import type { RedemptionRow } from "../../lib/offers";
import { CampaignsScreen } from "./CampaignsScreen";

const h = vi.hoisted(() => {
  type Call = [string, unknown[]];
  const state = {
    role: "owner",
    tables: {} as Record<string, unknown[]>,
    counts: {} as Record<string, number>,
    pending: [] as string[], // tables whose read never answers
    log: [] as { table: string; calls: Call[] }[],
    csv: "",
  };
  function from(table: string) {
    const entry = { table, calls: [] as Call[] };
    state.log.push(entry);
    const b: Record<string | symbol, unknown> = new Proxy({}, {
      get(_t, prop) {
        if (prop === "then") {
          const data = state.tables[table] ?? [];
          if (state.pending.includes(table)) return () => new Promise(() => {});
          return (ok: (v: unknown) => unknown, err: (e: unknown) => unknown) =>
            Promise.resolve({ data, error: null, count: state.counts[table] ?? data.length }).then(ok, err);
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
  useSession: () => ({ cafeId: "cafe-1", role: h.state.role, userId: "u-1" }),
}));
vi.mock("../../lib/supabase", () => ({ supabase: { from: h.from } }));
vi.mock("../../app/App", () => ({ useOffline: () => false }));
vi.mock("../../lib/api/client", async (orig) => {
  const real = await orig<typeof import("../../lib/api/client")>();
  return {
    ...real,
    api: { GET: vi.fn(async (path: string, opts: unknown) => ({ path, opts })), POST: vi.fn() },
    call: vi.fn(async (p: Promise<{ path: string }>) => {
      const { path } = await p;
      if (path === "/campaigns/{id}/recipients/export") return h.state.csv;
      throw new Error(`unexpected ${path}`);
    }),
  };
});

const ID = "11111111-1111-4111-8111-111111111111";
const ID2 = "22222222-2222-4222-8222-222222222222";

function insight(over: Partial<CampaignInsight>): CampaignInsight {
  return {
    campaign_id: ID, cafe_id: "cafe-1", name: "Weekend comeback", status: "completed", segment: "lapsed", template_name: "winback",
    offer_text: "20% off your next visit", discount_percent: 20, code_prefix: "BACK20", valid_until: "2026-10-12T10:12:00Z",
    scheduled_at: new Date(Date.now() - 5 * 86_400_000).toISOString(), created_at: new Date(Date.now() - 6 * 86_400_000).toISOString(),
    recipients: 412, queued: 2, deferred: 1, sent: 40, held: 3, delivered: 30, read: 20, failed: 4, replied: 8, ordered: 7, redeemed: 6,
    redeemed_counter: 4, redeemed_table_bill: 2, came_back: 10, opted_out: 1, ordered_paise: 300000, counter_bill_paise: 150000,
    came_back_paise: 450000, discount_paise: 60000, whatsapp_cost: 0, ...over,
  };
}

function redemption(over: Partial<RedemptionRow>): RedemptionRow {
  return {
    redemption_id: "d-1", cafe_id: "cafe-1", campaign_id: ID, campaign_name: "Weekend comeback", campaign_recipient_id: "r-1",
    code: "BACK20-7KQ", channel: "counter", redeemed_at: new Date(Date.now() - 86_400_000).toISOString(),
    day: new Date(Date.now() - 86_400_000).toISOString().slice(0, 10), customer_id: "cu-1", first_name: "Riya",
    masked_phone: "+91 98••• ••421", bill_paise: 124000, discount_paise: 24800, redeemed_by: "u-2", redeemed_by_name: "Ravi",
    order_number: null, table_label: null, table_session_id: null, ...over,
  };
}

const recipient = (over: Partial<RecipientRow>): RecipientRow => ({
  recipient_id: "r-1", cafe_id: "cafe-1", campaign_id: ID, customer_id: "cu-1", code: "BACK20-7KQ", status: "read", name: "Riya Sharma",
  masked_phone: "+91 98••• ••421", deleted: false, queued_at: "2026-10-05T04:00:00Z", sent_at: "2026-10-05T05:00:00Z",
  delivered_at: "2026-10-05T05:00:05Z", read_at: "2026-10-05T06:00:00Z", failed_at: null, error_code: null,
  window_start: "2026-10-05T05:00:00Z", window_end: "2026-10-12T10:12:00Z", replied_at: null, orders: 1, spent_paise: 64000,
  first_order_at: "2026-10-06T08:00:00Z", redemption_id: null, redeemed_at: null, redemption_channel: null, bill_paise: null,
  discount_paise: null, redeemed_by: null, redeemed_by_name: null, opted_out_at: null, opted_out_source: null, came_back: true, ...over,
});

beforeEach(() => {
  h.state.role = "owner";
  h.state.counts = {};
  h.state.pending = [];
  h.state.log = [];
  h.state.csv = "";
  h.state.tables = {
    cafes: [{ timezone: "Asia/Kolkata" }],
    campaigns: [{ id: ID, name: "Weekend comeback", segment: "lapsed", template_name: "winback", offer_text: "20% off your next visit",
      discount_percent: 20, code_prefix: "BACK20", valid_until: "2026-10-12T10:12:00Z", scheduled_at: "2026-10-05T04:30:00Z",
      status: "completed", created_at: "2026-10-04T10:00:00Z" }],
    v_campaign_funnel: [],
    templates: [{ name: "winback", category: "marketing", status: "approved", quality: null }],
    v_campaign_insights: [insight({})],
    v_campaign_recipients: [recipient({})],
    v_redemptions: [],
  };
});

function at(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/campaigns/:campaignId?" element={<CampaignsScreen />} />
            <Route path="/orders" element={<p>Orders screen</p>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const reads = (table: string) => h.state.log.filter((e) => e.table === table);

test("list numbers from v_campaign_insights", async () => {
  at("/campaigns");
  const row = (await screen.findByRole("button", { name: "Weekend comeback" })).closest("tr")!;
  const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
  expect(cells.slice(3)).toEqual(["40", "6 · 20 %", "10 · 25 %"]); // sent · redeemed of delivered · came back of sent
  expect(reads("v_campaign_insights")[0]!.calls).toContainEqual(["eq", ["cafe_id", "cafe-1"]]);
  expect(screen.getByText("winback", { selector: ".plist .mono" })).toBeInTheDocument(); // templates unchanged
});

test("detail funnel matches the view row", async () => {
  at(`/campaigns/${ID}`);
  const funnel = await screen.findByRole("list", { name: "Funnel" });
  const steps = within(funnel).getAllByRole("listitem").map((li) => li.textContent);
  expect(steps).toEqual([
    "Sent40 · 100 %", "Delivered30 · 75 %", "Read20 · 50 %", "Replied8 · 20 %", "Came back10 · 25 %", "Redeemed6 · 15 %",
  ]);
  const side = screen.getByRole("list", { name: "Other counts" });
  expect(side).toHaveTextContent("Held by WhatsApp ⓘ 3");
  expect(side).toHaveTextContent("Failed 4");
  expect(side).toHaveTextContent("Still to send 2");
  expect(side).toHaveTextContent("Opted out after 1");
  expect(screen.getByText("₹ 4,500")).toBeInTheDocument(); // came_back_paise
  expect(screen.getByText("4 at the counter · 2 on a table bill")).toBeInTheDocument();
  expect(screen.getByText(/Codes valid till 12 Oct/)).toBeInTheDocument();
});

test("recipient chips map to filters and paging", async () => {
  h.state.counts.v_campaign_recipients = 120;
  at(`/campaigns/${ID}?tab=recipients`);
  expect(await screen.findByText("412 recipients · 120 match")).toBeInTheDocument();
  const last = () => reads("v_campaign_recipients").at(-1)!.calls;
  expect(last()).toContainEqual(["eq", ["cafe_id", "cafe-1"]]);
  expect(last()).toContainEqual(["eq", ["campaign_id", ID]]);
  expect(last()).toContainEqual(["range", [0, 49]]);

  fireEvent.click(within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name: "Not sent" }));
  await waitFor(() => expect(last()).toContainEqual(["in", ["status", ["queued", "deferred"]]]));
  const outcome = screen.getByRole("group", { name: "Outcome" });
  fireEvent.click(within(outcome).getByRole("button", { name: "Redeemed" }));
  fireEvent.click(within(outcome).getByRole("button", { name: "Came back" }));
  await waitFor(() => expect(last()).toContainEqual(["eq", ["came_back", true]]));
  expect(last()).toContainEqual(["not", ["redemption_id", "is", null]]);
  expect(last()).toContainEqual(["in", ["status", ["queued", "deferred"]]]);

  expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(last()).toContainEqual(["range", [50, 99]]));
  expect(await screen.findByText("Page 2 of 3")).toBeInTheDocument();
});

test("export downloads the API CSV", async () => {
  h.state.csv = "first_name,masked_phone,code\nRiya,+91 98••• ••421,BACK20-7KQ\nAsha,+91 97••• ••118,BACK20-8MN\n";
  const blobs: Blob[] = [];
  URL.createObjectURL = vi.fn((b: Blob) => { blobs.push(b); return "blob:csv"; });
  URL.revokeObjectURL = vi.fn();
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  at(`/campaigns/${ID}`);
  const exportButton = await screen.findByRole("button", { name: "Export CSV" });
  await act(async () => fireEvent.click(exportButton));
  expect(vi.mocked(api.GET)).toHaveBeenCalledWith("/campaigns/{id}/recipients/export",
    expect.objectContaining({ params: expect.objectContaining({ path: { id: ID } }), parseAs: "text" }));
  expect(click).toHaveBeenCalled();
  expect(await blobs[0]!.text()).toBe(h.state.csv);
  expect(await screen.findByText("Exported 2 rows")).toBeInTheDocument();
  click.mockRestore();
});

test("insights best campaign with ≥ 10 delivered", async () => {
  h.state.tables.v_campaign_insights = [
    insight({ campaign_id: ID, name: "Few but keen", sent: 9, delivered: 9, came_back: 9 }),
    insight({ campaign_id: ID2, name: "Diwali", sent: 40, delivered: 12, came_back: 8 }),
  ];
  const r = at("/campaigns/insights");
  const best = await screen.findByRole("region", { name: "Best campaign" });
  expect(within(best).getByRole("link", { name: "Diwali" })).toHaveAttribute("href", `/campaigns/${ID2}`);
  r.unmount();
  h.state.tables.v_campaign_insights = [insight({ sent: 9, delivered: 9 })];
  at("/campaigns/insights");
  expect(await screen.findByText("Not enough results yet: a campaign needs 10 delivered messages")).toBeInTheDocument();
});

test("redemptions by staff", async () => {
  h.state.tables.v_redemptions = [
    redemption({ redemption_id: "1", redeemed_by: "u-2", redeemed_by_name: "Ravi", channel: "counter", discount_paise: 24800 }),
    redemption({ redemption_id: "2", redeemed_by: "u-2", redeemed_by_name: "Ravi", channel: "table_bill", discount_paise: 10000, table_label: "T7" }),
    redemption({ redemption_id: "3", redeemed_by: null, redeemed_by_name: null, channel: "counter", discount_paise: 5000 }),
  ];
  at("/campaigns/insights");
  const table = await screen.findByRole("table", { name: "Redemptions by staff" });
  const rows = within(table).getAllByRole("row").slice(1).map((tr) => within(tr).getAllByRole("cell").slice(0, 5).map((c) => c.textContent));
  expect(rows).toEqual([["Ravi", "2", "1", "1", "₹ 348"], ["Unknown", "1", "1", "0", "₹ 50"]]);
  const r = reads("v_redemptions").at(-1)!.calls;
  expect(r).toContainEqual(["eq", ["cafe_id", "cafe-1"]]);
  expect(r.some(([m, a]) => m === "gte" && a[0] === "day")).toBe(true); // 90 days by default
});

test("insights: campaigns scheduled or cancelled before sending anything → the empty state", async () => {
  h.state.tables.v_campaign_insights = [
    insight({ campaign_id: ID, status: "scheduled", sent: 0, delivered: 0, read: 0, came_back: 0, redeemed: 0 }),
    insight({ campaign_id: ID2, status: "cancelled", sent: 0, delivered: 0, read: 0, came_back: 0, redeemed: 0 }),
  ];
  at("/campaigns/insights");
  expect(await screen.findByText("No campaigns sent in this period.")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Best campaign" })).not.toBeInTheDocument();
});

test("insights: redemption panels say Loading… until v_redemptions answers, never a false empty", async () => {
  h.state.pending = ["v_redemptions"];
  at("/campaigns/insights");
  await screen.findByRole("region", { name: "Best campaign" });
  expect(screen.getAllByText("Loading…")).toHaveLength(3);
  expect(screen.queryByText("No redemptions in this period.")).not.toBeInTheDocument();
  expect(screen.queryByText("No data in this range.")).not.toBeInTheDocument();
});

test("insights pages through v_redemptions 1000 at a time and says when the cap is reached", async () => {
  h.state.tables.v_redemptions = Array.from({ length: 1000 }, (_, i) => redemption({ redemption_id: String(i) }));
  at("/campaigns/insights");
  expect(await screen.findByText(/Showing the latest 10,000 redemptions/, {}, { timeout: 5000 })).toBeInTheDocument();
  const ranges = reads("v_redemptions").map((e) => e.calls.find(([m]) => m === "range")?.[1]);
  expect(ranges.slice(0, 2)).toEqual([[0, 999], [1000, 1999]]);
  expect(ranges).toHaveLength(10);
  const table = screen.getByRole("table", { name: "Redemptions by staff" });
  expect(within(table).getAllByRole("cell")[1]).toHaveTextContent("10000");
});

test("a non-owner on /campaigns/insights → /orders", async () => {
  h.state.role = "manager";
  at("/campaigns/insights");
  expect(await screen.findByText("Orders screen")).toBeInTheDocument();
});

test("owner sees the three tabs", async () => {
  at("/campaigns");
  const tabs = await screen.findByRole("navigation", { name: "Campaigns" });
  expect(within(tabs).getByRole("link", { name: "Campaigns" })).toHaveAttribute("href", "/campaigns");
  expect(within(tabs).getByRole("link", { name: "Insights" })).toHaveAttribute("href", "/campaigns/insights");
  expect(within(tabs).getByRole("link", { name: "Redeem" })).toHaveAttribute("href", "/campaigns/redeem");
});

test("recipient name search waits 300 ms; the table, count and pager stay while the next page loads", async () => {
  h.state.counts.v_campaign_recipients = 120;
  at(`/campaigns/${ID}?tab=recipients`);
  expect(await screen.findByText("412 recipients · 120 match")).toBeInTheDocument();
  const named = () => reads("v_campaign_recipients").map((e) => e.calls.find(([m]) => m === "ilike")?.[1]).filter(Boolean);
  const search = screen.getByRole("searchbox", { name: "Search recipients by name" });
  fireEvent.change(search, { target: { value: "R" } });
  fireEvent.change(search, { target: { value: "Riya" } });
  expect(named()).toEqual([]); // nothing sent per keystroke
  await waitFor(() => expect(named()).toEqual([["name", "%Riya%"]]));
  expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();

  h.state.pending = ["v_campaign_recipients"]; // the next page never answers: the last one stays on screen
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("412 recipients · 120 match · updating…")).toBeInTheDocument();
  expect(screen.getByText("Riya Sharma")).toBeInTheDocument();
  expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
  expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
  expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
});

test("insights comparison is a card table on phones with a sort select in place of the header", async () => {
  h.state.tables.v_campaign_insights = [
    insight({ campaign_id: ID, name: "Weekend comeback", sent: 40 }),
    insight({ campaign_id: ID2, name: "Diwali", sent: 12 }),
  ];
  at("/campaigns/insights");
  const table = await screen.findByRole("table", { name: "Compare campaigns" });
  expect(table).toHaveClass("card-table");
  const names = () => within(table).getAllByRole("row").slice(1).map((tr) => within(tr).getAllByRole("cell")[0]!.textContent);
  expect(names()).toEqual(["Weekend comeback", "Diwali"]); // sent, most first
  expect(within(table).getAllByRole("cell")[1]).toHaveAttribute("data-label", "Sent");
  fireEvent.change(screen.getByRole("combobox", { name: "Sort by" }), { target: { value: "name" } });
  expect(names()).toEqual(["Diwali", "Weekend comeback"]); // A to Z
  fireEvent.click(screen.getByRole("button", { name: "Ascending" }));
  expect(names()).toEqual(["Weekend comeback", "Diwali"]);
  expect(screen.getByRole("button", { name: "Descending" })).toBeInTheDocument();
});

test("the campaigns list is a card table on phones", async () => {
  at("/campaigns");
  const table = await screen.findByRole("table", { name: "Campaigns" });
  expect(table).toHaveClass("card-table");
  expect(within(table).getAllByRole("cell").map((c) => c.getAttribute("data-label")))
    .toEqual(["Name", "Audience", "Status", "Sent", "Redeemed", "Came back"]);
});
