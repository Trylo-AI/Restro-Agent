// Report summaries on fixed rows with known answers.
import { summarisePeriod, summarisePrep, type PrepRow } from "./reports";

const prep = (order_id: string, dish: string, prep_minutes: number, overdue: boolean, hour: number): PrepRow =>
  ({ order_id, dish, prep_minutes, overdue, hour, day: "2026-10-20" });

test("prep summary counts each order once and ranks dishes by median", () => {
  const s = summarisePrep([
    prep("o1", "Dosa", 20, true, 13), prep("o1", "Chai", 20, true, 13),
    prep("o2", "Chai", 6, false, 9), prep("o3", "Dosa", 30, true, 13), prep("o4", "Toast", 10, true, 19),
  ]);
  expect(s.median).toBe(15); // orders: 20, 6, 30, 10
  expect(s.pctOverdue).toBe(75);
  expect(s.peakHour).toBe(13);
  expect(s.slowest.map((d) => d.dish)).toEqual(["Dosa", "Chai", "Toast"]);
  expect(summarisePrep([])).toEqual({ median: null, pctOverdue: null, peakHour: null, slowest: [] });
});

test("period summary: revenue, average order, rating and returning share", () => {
  const p = summarisePeriod(
    [{ day: "2026-10-20", revenue_paise: 60000, orders: 2, customers: 2 }, { day: "2026-10-21", revenue_paise: 30000, orders: 1, customers: 1 }],
    [{ order_id: "a", number: 1, rating: 5, text: null, created_at: "", day: "2026-10-20" }, { order_id: "b", number: 2, rating: 2, text: "slow", created_at: "", day: "2026-10-21" }],
    new Map([["c1", "2026-09-01"], ["c2", "2026-10-20"]]), new Set(["c1", "c2"]), "2026-10-15",
  );
  expect(p).toEqual({ revenue: 90000, orders: 3, avgOrder: 30000, avgRating: 3.5, returningPct: 50, customers: 2 });
});
