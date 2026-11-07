// Audit log screen and order history (docs/console.md#audit-log, "Done when").
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ToastProvider } from "../../app/toasts";
import { ApiError, api } from "../../lib/api/client";
import * as audit from "../../lib/audit";
import type { AuditCatalog, AuditEntry, AuditFacets, OrderHistory } from "../../lib/audit";
import * as data from "../../lib/data";
import { AuditScreen } from "./AuditScreen";
import { OrderHistoryScreen } from "./OrderHistory";

const session = vi.hoisted(() => ({ role: "owner" as string }));
vi.mock("../../lib/session", async (orig) => ({
  ...(await orig<typeof import("../../lib/session")>()),
  useSession: () => ({ cafeId: "cafe-1", role: session.role, userId: "u-1", cafeName: "Kaapi House" }),
}));
vi.mock("../../lib/audit", async (orig) => ({
  ...(await orig<typeof import("../../lib/audit")>()),
  useAuditCatalog: vi.fn(), useAuditEntries: vi.fn(), useAuditFacets: vi.fn(), useOrderHistory: vi.fn(), useCustomerMatches: vi.fn(),
}));
vi.mock("../../lib/data", async (orig) => ({
  ...(await orig<typeof import("../../lib/data")>()),
  useTeam: vi.fn(), useCustomerProfileRow: vi.fn(),
}));

const PRIYA = "11111111-1111-4111-8111-111111111111";
const ORDER = "33333333-3333-4333-8333-333333333333";
const CUSTOMER = "44444444-4444-4444-8444-444444444444";
const CHAT = "55555555-5555-4555-8555-555555555555";

const catalog: AuditCatalog = {
  actions: [
    { action: "order.transition", label: "Order status changed", category: "orders", max_severity: "info", rule: "" },
    { action: "order.accept", label: "Order accepted", category: "orders", max_severity: "info", rule: "", filter_only: true },
    { action: "order.ready", label: "Order marked ready", category: "orders", max_severity: "info", rule: "", filter_only: true },
    { action: "order.cancel", label: "Order cancelled", category: "orders", max_severity: "critical", rule: "" },
    { action: "chat.takeover", label: "Chat taken over", category: "chats", max_severity: "warning", rule: "" },
    { action: "menu.update", label: "Menu item added or edited", category: "menu", max_severity: "critical", rule: "" },
  ],
  categories: [{ id: "orders", label: "Orders" }, { id: "chats", label: "Chats" }, { id: "menu", label: "Menu" }],
  roles: [{ id: "shift_manager", label: "Shift manager" }, { id: "manager", label: "Manager" }, { id: "owner", label: "Owner" }],
  severities: ["critical", "warning", "info"],
};

const facets: AuditFacets = {
  from: "2026-10-26T00:00:00Z", to: "2026-11-02T00:00:00Z", total: 142, by_severity: { critical: 3, warning: 7, info: 132 },
  actions: [{ action: "order.accept", label: "Order accepted", category: "orders", count: 40 },
    { action: "order.cancel", label: "Order cancelled", category: "orders", count: 3 }],
  actors: [{ user_id: PRIYA, name: "Priya", role: "shift_manager", count: 50 }],
  categories: [{ category: "orders", label: "Orders", count: 120 }],
};

function entry(over: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: "a1", at: new Date().toISOString(), action: "order.cancel", label: "Order cancelled", category: "orders", severity: "critical",
    severity_reason: "Cancelled after the kitchen accepted it", sentence: "Priya (Shift manager) cancelled order #42 (was Accepted) — Out of stock",
    actor: { user_id: PRIYA, name: "Priya", role: "shift_manager", support: false },
    order: { id: ORDER, number: 42, status: "CANCELLED" }, customer: { id: CUSTOMER, name: "Riya" }, conversation_id: CHAT,
    details: [{ label: "Reason", value: "Out of stock" }, { label: "Was", value: "Accepted" }], change: { from: "ACCEPTED" } as never, ...over,
  };
}

const ok = <T,>(d: T) => ({ data: d, isPending: false, isError: false, error: null, refetch: vi.fn() }) as never;
function page(rows: AuditEntry[], over: Record<string, unknown> = {}) {
  return {
    data: { pages: [{ entries: rows, next: null }], pageParams: [null] }, isPending: false, isError: false, error: null,
    hasNextPage: false, isFetchingNextPage: false, fetchNextPage: vi.fn(), refetch: vi.fn(), ...over,
  } as never;
}

function Loc() {
  const l = useLocation();
  return <output data-testid="loc">{l.pathname}{l.search}</output>;
}
const search = () => new URLSearchParams(screen.getByTestId("loc").textContent!.split("?")[1] ?? "");
const lastQuery = () => vi.mocked(audit.useAuditEntries).mock.calls.at(-1)![0];

function wrap(ui: ReactNode, path: string, route: string) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path={route} element={<>{ui}<Loc /></>} />
            <Route path="/orders" element={<p>Orders screen</p>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}
const show = (path = "/audit") => wrap(<AuditScreen />, path, "/audit");

beforeEach(() => {
  vi.clearAllMocks();
  session.role = "owner";
  vi.mocked(audit.useAuditCatalog).mockReturnValue(ok(catalog));
  vi.mocked(audit.useAuditFacets).mockReturnValue(ok(facets));
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([entry()]));
  vi.mocked(data.useTeam).mockReturnValue(ok([{ id: "m1", user_id: PRIYA, role: "shift_manager", active: true, display_name: "Priya" }]));
  vi.mocked(data.useCustomerProfileRow).mockReturnValue(ok(null));
  vi.mocked(audit.useCustomerMatches).mockReturnValue(ok([{ customer_id: CUSTOMER, name: "Riya Sharma", visits: 3 }]));
});

test("a critical row has the icon, the label, the reason and the critical border class", () => {
  show();
  const row = screen.getByText(/cancelled order #42/).closest("li")!;
  expect(row).toHaveClass("audit-row", "sev-critical");
  expect(within(row).getByText("Critical")).toBeInTheDocument();
  expect(row.querySelector(".audit-sev.critical svg")).not.toBeNull();
  expect(within(row).getByText("Cancelled after the kitchen accepted it")).toBeInTheDocument();
  expect(within(row).getByText("Priya", { selector: "strong" })).toBeInTheDocument(); // actor bold
  expect(row.className).not.toMatch(/pulse/);
  expect(screen.getByText("3 critical")).toBeInTheDocument();
  expect(screen.getByText(/142 actions/)).toBeInTheDocument();
});

test("a warning row is amber with its label; an info row is plain", () => {
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([
    entry({ id: "w", severity: "warning", severity_reason: "Took the chat from a teammate", action: "chat.takeover", sentence: "Priya (Shift manager) took over Riya's chat from Arjun" }),
    entry({ id: "i", severity: "info", severity_reason: null, action: "order.transition", sentence: "Priya (Shift manager) accepted order #43", order: null, customer: null, conversation_id: null }),
  ]));
  show();
  const warn = screen.getByText(/took over Riya's chat/).closest("li")!;
  expect(warn).toHaveClass("sev-warning");
  expect(within(warn).getByText("Warning")).toBeInTheDocument();
  expect(within(warn).getByText("Took the chat from a teammate")).toBeInTheDocument();
  const info = screen.getByText(/accepted order #43/).closest("li")!;
  expect(info).toHaveClass("sev-info");
  expect(within(info).queryByText(/Critical|Warning/)).not.toBeInTheDocument();
  expect(info.querySelector(".audit-sev")).toBeNull();
});

test("Critical only updates the URL and the query", () => {
  show();
  const toggle = screen.getByRole("switch", { name: /Critical only/ });
  expect(toggle).toHaveAttribute("aria-checked", "false");
  fireEvent.click(toggle);
  expect(search().get("critical")).toBe("1");
  expect(screen.getByRole("switch", { name: /Critical only/ })).toHaveAttribute("aria-checked", "true");
  expect(lastQuery().severity).toEqual(["critical"]);
});

test("chip × removes one filter; Clear all removes every one", () => {
  show("/audit?action=order.cancel&critical=1&q=dosa");
  const chips = screen.getByLabelText("Active filters");
  expect(within(chips).getByText("Order cancelled")).toBeInTheDocument();
  fireEvent.click(within(chips).getByRole("button", { name: "Remove Order cancelled" }));
  expect(search().getAll("action")).toEqual([]);
  expect(search().get("critical")).toBe("1");
  fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
  expect(search().toString()).toBe("");
  expect(screen.queryByLabelText("Active filters")).not.toBeInTheDocument();
});

test("a row expands to details, links and one-click filters", () => {
  show();
  const row = screen.getByText(/cancelled order #42/).closest("li")!;
  expect(within(row).getByRole("link", { name: "#42" })).toHaveAttribute("href", `/audit/orders/${ORDER}`);
  expect(within(row).getByRole("link", { name: "Riya" })).toHaveAttribute("href", `/customers/${CUSTOMER}`);
  expect(within(row).getByRole("link", { name: "Chat" })).toHaveAttribute("href", `/chats/${CHAT}`);
  const expand = within(row).getByRole("button", { name: "Show details" });
  fireEvent.click(expand);
  expect(within(row).getByRole("button", { name: "Hide details" })).toHaveAttribute("aria-expanded", "true");
  expect(within(row).getByText("Out of stock")).toBeInTheDocument();
  expect(within(row).getByRole("link", { name: "Order history" })).toHaveAttribute("href", `/audit/orders/${ORDER}`);
  expect(within(row).getByRole("link", { name: "Open chat" })).toHaveAttribute("href", `/chats/${CHAT}`);
  expect(within(row).getByRole("link", { name: "Open customer" })).toHaveAttribute("href", `/customers/${CUSTOMER}`);
  expect(within(row).getByText("Technical details")).toBeInTheDocument();
  fireEvent.click(within(row).getByRole("button", { name: "Only this person" }));
  expect(search().getAll("actor")).toEqual([PRIYA]);
});

test("empty, no-match and no-critical states differ", () => {
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([]));
  const a = show();
  expect(screen.getByText(/Nothing recorded between .+\. Staff actions appear here as they happen\./)).toBeInTheDocument();
  a.unmount();
  const b = show("/audit?action=order.cancel");
  expect(screen.getByText(/No actions match these filters\./)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(search().toString()).toBe("");
  b.unmount();
  show("/audit?critical=1");
  expect(screen.getByText("No critical actions in this period.")).toBeInTheDocument();
});

test("403 shows the owner-only message; other errors offer a retry", () => {
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([], { data: undefined, isError: true, error: new ApiError(403, "forbidden", "no") }));
  const a = show();
  expect(screen.getByText("Only the owner can see the audit log.")).toBeInTheDocument();
  a.unmount();
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([], { data: undefined, isError: true, error: new ApiError(0, "network", "x") }));
  show("/audit?q=riya");
  expect(screen.getByText(/Couldn't load the audit log\./)).toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "Search the audit log" })).toHaveValue("riya"); // filters kept
});

test("a failed Show older or background refresh keeps the rows and offers Try again", () => {
  const more = vi.fn();
  const refetch = vi.fn();
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([entry()], { isError: true, isFetchNextPageError: true, hasNextPage: true, fetchNextPage: more }));
  const a = show();
  expect(screen.getByText(/cancelled order #42/)).toBeInTheDocument();
  fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Try again" }));
  expect(more).toHaveBeenCalledOnce();
  expect(screen.queryByText(/Couldn't refresh/)).not.toBeInTheDocument();
  a.unmount();
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([entry()], { isError: true, isRefetchError: true, refetch }));
  show();
  expect(screen.getByText(/cancelled order #42/)).toBeInTheDocument();
  expect(screen.queryByText(/Couldn't load more/)).not.toBeInTheDocument();
  fireEvent.click(within(screen.getByText(/Couldn't refresh\. Showing the last loaded entries\./)).getByRole("button", { name: "Try again" }));
  expect(refetch).toHaveBeenCalledOnce();
});

test("searching \"#42\" looks the order up over all time, like the Order # field", async () => {
  show();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search the audit log" }), { target: { value: "#42" } });
  await waitFor(() => expect(search().get("q")).toBe("#42"));
  expect(search().get("range")).toBeNull(); // all time is the default with an order number
  expect(lastQuery()).toEqual({ q: "#42" });
  expect(screen.getByRole("button", { name: "All time" })).toHaveAttribute("aria-pressed", "true");
});

test("the Customer typeahead looks up once, 300 ms after the last key", async () => {
  show();
  const box = screen.getByRole("searchbox", { name: "Customer" });
  for (const v of ["Ri", "Riy", "Riya"]) fireEvent.change(box, { target: { value: v } });
  const pick = await screen.findByRole("button", { name: /Riya/ });
  expect(vi.mocked(audit.useCustomerMatches).mock.calls.map(([q]) => q)).toEqual(["Riya"]);
  fireEvent.click(pick);
  expect(search().get("customer")).toBe(CUSTOMER);
  expect(box).toHaveValue("");
});

test("non-owners are sent to Live orders and never fetch", () => {
  for (const role of ["manager", "shift_manager"]) {
    session.role = role;
    const r = show();
    expect(screen.getByText("Orders screen")).toBeInTheDocument();
    r.unmount();
  }
  expect(audit.useAuditEntries).not.toHaveBeenCalled();
});

test("Show older fetches the next page with `next` as `before`", async () => {
  const more = vi.fn();
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([entry()], { hasNextPage: true, fetchNextPage: more }));
  const a = show();
  fireEvent.click(screen.getByRole("button", { name: "Show older" }));
  expect(more).toHaveBeenCalledOnce();
  a.unmount();
  vi.mocked(audit.useAuditEntries).mockReturnValue(page([entry()]));
  show();
  expect(screen.getByText("That's everything for these filters.")).toBeInTheDocument();

  // The real hook: page 2 asks for `before` = page 1's `next`.
  const real = await vi.importActual<typeof import("../../lib/audit")>("../../lib/audit");
  const get = vi.spyOn(api, "GET").mockImplementation((async (_p: string, init: { params: { query: { before?: string } } }) => ({
    data: { entries: [], next: init.params.query.before ? null : "cursor-1" }, response: new Response(null, { status: 200 }),
  })) as never);
  const qc = new QueryClient();
  const { result } = renderHook(() => real.useAuditEntries({ severity: ["critical"] }), {
    wrapper: ({ children }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>,
  });
  await waitFor(() => expect(result.current.hasNextPage).toBe(true));
  await act(() => result.current.fetchNextPage());
  expect(get).toHaveBeenLastCalledWith("/audit/entries", expect.objectContaining({
    params: expect.objectContaining({ query: { severity: ["critical"], limit: 50, before: "cursor-1" } }),
  }));
  get.mockRestore();
});

test("Export calls /audit/export with the current filters and toasts the row count", async () => {
  const get = vi.spyOn(api, "GET").mockResolvedValue({
    data: "id,at_local\n1,x\n2,\"a\nb\"\n", response: new Response(null, { status: 200 }),
  } as never);
  URL.createObjectURL = vi.fn(() => "blob:audit");
  URL.revokeObjectURL = vi.fn();
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  show(`/audit?action=order.cancel&customer=${CUSTOMER}`);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: /Export CSV/ })));
  expect(get).toHaveBeenCalledWith("/audit/export", expect.objectContaining({
    parseAs: "text", params: expect.objectContaining({ query: { action: ["order.cancel"], customer_id: CUSTOMER } }),
  }));
  expect(click).toHaveBeenCalledOnce();
  expect(await screen.findByText("Audit log exported (2 rows)")).toBeInTheDocument();
  get.mockRestore();
  click.mockRestore();
});

test("the Action filter offers Order accepted first and sends action=order.accept", () => {
  show();
  const group = screen.getByRole("group", { name: "Action" });
  const options = within(group).getAllByRole("checkbox").map((c) => c.closest("label")!.textContent);
  expect(options.slice(0, 3).map((t) => t!.replace(/\d+$/, ""))).toEqual(["Order accepted", "Order cancelled", "Chat taken over"]);
  fireEvent.click(within(group).getByRole("checkbox", { name: /Order accepted/ }));
  expect(search().getAll("action")).toEqual(["order.accept"]);
  expect(lastQuery().action).toEqual(["order.accept"]);
  // Zero-count actions stay selectable but greyed.
  expect(within(group).getByRole("checkbox", { name: /Chat taken over/ }).closest("label")).toHaveClass("zero");
});

test("order history: oldest first, actor icons, the cancel highlighted with the staff note", () => {
  const history: OrderHistory = {
    order: {
      id: ORDER, number: 42, status: "CANCELLED", source: "qr", table: "T4", customer: { id: CUSTOMER, name: "Riya" },
      created_at: "2026-10-31T07:11:00Z", total_paise: 54000, cancel_reason_code: "out_of_stock",
      cancel_reason_text: "Gas ran out", cancelled_by_name: "Priya",
    },
    events: [
      { at: "2026-10-31T07:20:00Z", kind: "action", severity: "critical", severity_reason: "Cancelled after the kitchen accepted it",
        sentence: "Priya (Shift manager) cancelled order #42 (was Accepted) — Out of stock", actor: { type: "staff", name: "Priya", role: "shift_manager" }, audit_id: "a1" },
      { at: "2026-10-31T07:11:00Z", kind: "status", severity: "info", sentence: "Riya placed order #42", actor: { type: "customer", name: "Riya" } },
      { at: "2026-10-31T07:12:00Z", kind: "escalation", severity: "warning", severity_reason: "No one accepted in 60 s — sent to managers",
        sentence: "Sent to managers", actor: { type: "system", name: "Restro (automatic)" } },
    ],
  };
  vi.mocked(audit.useOrderHistory).mockReturnValue(ok(history));
  wrap(<OrderHistoryScreen />, `/audit/orders/${ORDER}`, "/audit/orders/:orderId");
  expect(screen.getByRole("heading", { name: "Order #42 · T4 · Riya · Cancelled" })).toBeInTheDocument();
  const items = within(screen.getByRole("region", { name: "Timeline" })).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual([
    expect.stringContaining("Riya placed order #42"), expect.stringContaining("Sent to managers"), expect.stringContaining("cancelled order #42"),
  ]);
  expect(items[0]!.querySelector("[aria-label='Customer']")).not.toBeNull();
  expect(items[2]).toHaveClass("sev-critical");
  const block = screen.getByRole("note");
  expect(block).toHaveTextContent("Cancelled by Priya — Out of stock");
  expect(within(block).getByText("Staff note (never sent to the customer)")).toBeInTheDocument();
  expect(within(block).getByText("Gas ran out")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /All actions on this order/ })).toHaveAttribute("href", `/audit?order_id=${ORDER}&range=all`);

  vi.mocked(audit.useOrderHistory).mockReturnValue({ data: undefined, isPending: false, isError: true, error: new ApiError(404, "not_found", "x"), refetch: vi.fn() } as never);
  wrap(<OrderHistoryScreen />, `/audit/orders/${ORDER}`, "/audit/orders/:orderId");
  expect(screen.getByText(/Order not found in this café\./)).toBeInTheDocument();
});
