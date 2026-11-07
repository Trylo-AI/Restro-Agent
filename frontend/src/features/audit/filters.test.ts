// Audit log filters (docs/console.md#audit-log): the URL is the state; presets are local days; "all" needs an entity.
import {
  activeChips, bounds, EMPTY, parseFilters, patchFilters, PRESETS, toQuery, toSearch, type AuditFilterState, type ChipLabels,
} from "./filters";

const U1 = "11111111-1111-4111-8111-111111111111";
const U2 = "22222222-2222-4222-8222-222222222222";
const ORDER = "33333333-3333-4333-8333-333333333333";
const CUSTOMER = "44444444-4444-4444-8444-444444444444";
const CHAT = "55555555-5555-4555-8555-555555555555";

const labels: ChipLabels = {
  action: (a) => ({ "order.accept": "Order accepted", "order.cancel": "Order cancelled" })[a] ?? a,
  category: (c) => c.toUpperCase(), role: (r) => `role:${r}`, actor: (id) => (id === U1 ? "Priya" : "Arjun"),
  customer: () => "Riya", orderId: () => "Order #42",
};

test("URL ↔ state round trip keeps every key, drops junk and leaves defaults out", () => {
  const url = "range=custom&from=2026-10-25T09:00&to=2026-10-26T18:30&q=Riya&critical=1&severity=warning&severity=info"
    + "&category=orders&category=menu&action=order.accept&action=order.cancel&actor=" + U1 + "&actor=" + U2
    + "&role=shift_manager&order=42&order_id=" + ORDER + "&customer=" + CUSTOMER + "&conversation=" + CHAT;
  const s = parseFilters(new URLSearchParams(url));
  expect(s).toEqual({
    range: "custom", from: "2026-10-25T09:00", to: "2026-10-26T18:30", q: "Riya", critical: true, severity: ["warning", "info"],
    category: ["orders", "menu"], action: ["order.accept", "order.cancel"], actor: [U1, U2], role: ["shift_manager"],
    order: 42, order_id: ORDER, customer: CUSTOMER, conversation: CHAT,
  });
  expect(new URLSearchParams(toSearch(s)).toString()).toBe(new URLSearchParams(url).toString());
  expect(parseFilters(toSearch(s))).toEqual(s);

  // Junk never reaches the API; a plain /audit stays plain.
  const junk = parseFilters(new URLSearchParams("severity=urgent&category=x&action=DROP TABLE&actor=nope&role=king&order=-3&customer=1&range=forever"));
  expect(junk).toEqual(EMPTY);
  expect(toSearch(EMPTY).toString()).toBe("");
  // Filter changes are history entries in the URL, never a page cursor.
  expect(toSearch(patchFilters(EMPTY, { action: ["order.accept"] })).toString()).toBe("action=order.accept");
});

test("presets compute local calendar days; `to` is exclusive (the next local midnight)", () => {
  const now = new Date(2026, 10, 1, 15, 42); // 1 Nov, 15:42 local
  const at = (range: AuditFilterState["range"], extra: Partial<AuditFilterState> = {}) => bounds({ ...EMPTY, range, ...extra }, now);
  expect(at("today")).toEqual({ from: new Date(2026, 10, 1), to: new Date(2026, 10, 2) });
  expect(at("yesterday")).toEqual({ from: new Date(2026, 9, 31), to: new Date(2026, 10, 1) });
  expect(at("7d")).toEqual({ from: new Date(2026, 9, 26), to: new Date(2026, 10, 2) });
  expect(at("30d")).toEqual({ from: new Date(2026, 9, 3), to: new Date(2026, 10, 2) });
  expect(at("custom", { from: "2026-10-25T09:00", to: "2026-10-26T18:30" }))
    .toEqual({ from: new Date(2026, 9, 25, 9, 0), to: new Date(2026, 9, 26, 18, 30) });
  // The API gets UTC instants of those local bounds; the default range is 7 days.
  const q = toQuery(EMPTY, now);
  expect(q).toEqual({ from: new Date(2026, 9, 26).toISOString(), to: new Date(2026, 10, 2).toISOString() });
  // Critical only wins over the severity filter.
  expect(toQuery({ ...EMPTY, critical: true, severity: ["warning"] }, now).severity).toEqual(["critical"]);
});

test("`all` exists only with an order, customer or chat filter", () => {
  expect(PRESETS.find((p) => p.id === "all")?.entityOnly).toBe(true);
  expect(parseFilters(new URLSearchParams("range=all")).range).toBe("7d");
  const withCustomer = parseFilters(new URLSearchParams(`customer=${CUSTOMER}&range=all`));
  expect(withCustomer.range).toBe("all");
  expect(parseFilters(new URLSearchParams(`customer=${CUSTOMER}`)).range).toBe("all"); // the default with an entity
  const q = toQuery(withCustomer);
  expect(q.from).toBeUndefined();
  expect(q.to).toBeUndefined();
  expect(q.customer_id).toBe(CUSTOMER);
  // Adding an entity filter follows the default; removing the last one goes back to 7 days; a picked range stays.
  expect(patchFilters(EMPTY, { order_id: ORDER }).range).toBe("all");
  expect(patchFilters(withCustomer, { customer: null }).range).toBe("7d");
  expect(patchFilters({ ...EMPTY, range: "30d" }, { order_id: ORDER }).range).toBe("30d");
  const chip = activeChips(withCustomer, labels).find((c) => c.key === "customer")!;
  expect(chip.without.range).toBe("7d");
});

test("an order-number search (\"#42\" or \"42\") searches all time, like the Order # field", () => {
  const now = new Date(2026, 10, 1, 15, 42);
  for (const q of ["#42", "42", " 42 "]) {
    const s = patchFilters(EMPTY, { q });
    expect(s.range).toBe("all");
    expect(toSearch(s).toString()).toBe(new URLSearchParams({ q: q.trim() }).toString()); // "all" is then the default
    expect(parseFilters(toSearch(s)).range).toBe("all");
    const query = toQuery(s, now);
    expect(query.from).toBeUndefined();
    expect(query.to).toBeUndefined();
    expect(query.q).toBe(q.trim());
  }
  // A name or a longer text keeps the 7-day default; editing "#42" into a name goes back to it.
  expect(patchFilters(EMPTY, { q: "Riya" }).range).toBe("7d");
  expect(patchFilters(patchFilters(EMPTY, { q: "42" }), { q: "42 dosa" }).range).toBe("7d");
  // A range the owner picked stays; removing the search chip can't leave "all time" behind.
  expect(patchFilters({ ...EMPTY, range: "30d" }, { q: "#42" }).range).toBe("30d");
  const numbered = patchFilters(EMPTY, { q: "#42" });
  expect(activeChips(numbered, labels).find((c) => c.key === "q")!.without.range).toBe("7d");
});

test("chips list every active filter, each removing only itself", () => {
  const s: AuditFilterState = {
    range: "30d", from: null, to: null, q: "dosa", critical: true, severity: ["warning"], category: ["orders"],
    action: ["order.accept"], actor: [U1], role: ["manager"], order: 42, order_id: ORDER, customer: CUSTOMER, conversation: CHAT,
  };
  const chips = activeChips(s, labels);
  expect(chips.map((c) => c.label)).toEqual([
    "30 days", "“dosa”", "Critical only", "Warnings", "ORDERS", "Order accepted", "Priya", "role:manager", "Order #42",
    "Order #42", "Riya", "This chat",
  ]);
  expect(chips.find((c) => c.key === "action:order.accept")!.without).toEqual({ ...s, action: [] });
  expect(chips.find((c) => c.key === "critical")!.without).toEqual({ ...s, critical: false });
  expect(activeChips(EMPTY, labels)).toEqual([]);
});
