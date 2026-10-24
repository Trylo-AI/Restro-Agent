// Every row of the urgency tables in docs/console.md#urgency-and-alerts, plus ringing rules.
import type { Order, Request } from "./types";
import { Ringer, badge, chatUrgency, orderUrgency, requestUrgency } from "./urgency";

const NOW = Date.parse("2026-10-24T12:00:00Z");
const ago = (s: number) => new Date(NOW - s * 1000).toISOString();

function order(over: Partial<Order>): Order {
  return {
    id: "o", cafe_id: "c", customer_id: "u", table_session_id: "t", number: 1048, status: "NEW", flags: [],
    allergy_note: null, expected_minutes: 10, delay_minutes: 0, total_paise: 0, source: "qr", version: 1,
    cancel_reason_code: null, created_at: ago(10), accepted_at: null, ready_at: null, served_at: null,
    cancelled_at: null, updated_at: ago(10), ...over,
  };
}

function request(over: Partial<Request>): Request {
  return {
    id: "r", cafe_id: "c", table_session_id: "t", conversation_id: "v", order_id: null, kind: "help", status: "open",
    reason_tag: "asked", acknowledged_by: null, acknowledged_at: null, resolved_at: null, created_at: ago(10),
    updated_at: ago(10), ...over,
  };
}

describe("orders, first match wins", () => {
  test("READY → none with a ready timer", () => {
    expect(orderUrgency(order({ status: "READY", ready_at: ago(300) }), NOW)).toMatchObject({ severity: "none", chip: "Ready · 5 min" });
  });
  test("NEW with an allergy → critical, repeating", () => {
    expect(orderUrgency(order({ allergy_note: "peanuts" }), NOW)).toMatchObject({ severity: "critical", chip: "Allergy", repeats: true });
  });
  test("NEW < 60 s → info (chime); ≥ 60 s → warning", () => {
    expect(orderUrgency(order({ created_at: ago(30) }), NOW)).toMatchObject({ severity: "info", chip: "New", sound: "chime" });
    expect(orderUrgency(order({ created_at: ago(61) }), NOW)).toMatchObject({ severity: "warning", chip: "Not started" });
  });
  test("ACCEPTED past due or flagged overdue → critical", () => {
    expect(orderUrgency(order({ status: "ACCEPTED", accepted_at: ago(11 * 60) }), NOW)).toMatchObject({ severity: "critical", chip: "Overdue" });
    expect(orderUrgency(order({ status: "ACCEPTED", accepted_at: ago(60), flags: ["overdue"] }), NOW).severity).toBe("critical");
  });
  test("ACCEPTED at ≥ 80 % of expected + delay → warning", () => {
    expect(orderUrgency(order({ status: "ACCEPTED", accepted_at: ago(8 * 60) }), NOW)).toMatchObject({ severity: "warning", chip: "Due soon" });
    expect(orderUrgency(order({ status: "ACCEPTED", accepted_at: ago(7 * 60) }), NOW).severity).toBe("none");
  });
  test("ACCEPTED with a delay → info, silent; the delay pushes the due time", () => {
    const u = orderUrgency(order({ status: "ACCEPTED", accepted_at: ago(9 * 60), delay_minutes: 5 }), NOW);
    expect(u).toMatchObject({ severity: "info", chip: "Delayed +5", sound: null });
  });
});

describe("calls", () => {
  test("allergy open → critical, repeating", () => {
    expect(requestUrgency(request({ kind: "allergy" }), NOW)).toMatchObject({ severity: "critical", chip: "Allergy", repeats: true });
  });
  test("help open < 2 min → warning; ≥ 2 min → critical", () => {
    expect(requestUrgency(request({ created_at: ago(60) }), NOW)).toMatchObject({ severity: "warning", chip: "Needs help" });
    expect(requestUrgency(request({ created_at: ago(121) }), NOW)).toMatchObject({ severity: "critical", chip: "Waiting too long" });
  });
  test("help or allergy acknowledged → info, silent, with the name", () => {
    expect(requestUrgency(request({ status: "acknowledged" }), NOW, "Ravi")).toMatchObject({ severity: "info", chip: "With Ravi", sound: null });
  });
  test("bill open → warning, silent (badge only)", () => {
    expect(requestUrgency(request({ kind: "bill" }), NOW)).toMatchObject({ severity: "warning", chip: "Bill", sound: null });
  });
  test("low rating → critical, rings once", () => {
    expect(requestUrgency(request({ kind: "low_rating" }), NOW)).toMatchObject({ severity: "critical", repeats: false });
  });
});

describe("chats", () => {
  test("bot → none; human answered → calm; customer waiting < 60 s → warning, then critical", () => {
    expect(chatUrgency("bot", ago(5), null, NOW).severity).toBe("none");
    expect(chatUrgency("human", ago(100), ago(50), NOW).severity).toBe("calm");
    expect(chatUrgency("human", ago(30), ago(100), NOW).severity).toBe("warning");
    expect(chatUrgency("human", ago(61), null, NOW).severity).toBe("critical");
  });
});

describe("ringing", () => {
  const crit = { severity: "critical" as const, chip: "Overdue", sound: "alarm" as const, repeats: true };
  const info = { severity: "info" as const, chip: "New", sound: "chime" as const, repeats: false };
  const warn = { severity: "warning" as const, chip: "Not started", sound: "two_note" as const, repeats: false };

  test("first load records silently, then only the highest new event rings", () => {
    const r = new Ringer();
    expect(r.tick([{ key: "a", urgency: crit }], NOW)).toBeNull();
    expect(r.tick([{ key: "a", urgency: crit }, { key: "b", urgency: info }, { key: "c", urgency: warn }], NOW + 1000)).toBe("two_note");
  });
  test("critical repeats every 30 s while open", () => {
    const r = new Ringer();
    r.tick([], NOW);
    expect(r.tick([{ key: "a", urgency: crit }], NOW)).toBe("alarm");
    expect(r.tick([{ key: "a", urgency: crit }], NOW + 10_000)).toBeNull();
    expect(r.tick([{ key: "a", urgency: crit }], NOW + 30_000)).toBe("alarm");
  });
  test("a severity drop never rings", () => {
    const r = new Ringer();
    r.tick([{ key: "a", urgency: warn }], NOW);
    expect(r.tick([{ key: "a", urgency: info }], NOW + 1000)).toBeNull();
  });
  test("badge counts warning + critical, red if any critical", () => {
    expect(badge([crit, info, warn])).toEqual({ count: 2, critical: true });
    expect(badge([info])).toEqual({ count: 0, critical: false });
  });
});
