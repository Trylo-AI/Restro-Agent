// Pure helpers behind the campaign insights (docs/console.md#campaigns): best campaign, by staff, by day, totals.
import { bestCampaign, byDay, byStaff, totals, type CampaignInsight } from "./campaignInsights";
import { staffLabel, type RedemptionRow } from "./offers";

function campaign(over: Partial<CampaignInsight>): CampaignInsight {
  return {
    campaign_id: "c", cafe_id: "cafe-1", name: "C", status: "completed", segment: "all_opted_in", template_name: "weekly_offer",
    offer_text: null, discount_percent: 20, code_prefix: "BACK20", valid_until: null, scheduled_at: "2026-10-01T10:00:00Z",
    created_at: "2026-10-01T09:00:00Z", recipients: 0, queued: 0, deferred: 0, sent: 0, held: 0, delivered: 0, read: 0, failed: 0,
    replied: 0, ordered: 0, redeemed: 0, redeemed_counter: 0, redeemed_table_bill: 0, came_back: 0, opted_out: 0, ordered_paise: 0,
    counter_bill_paise: 0, came_back_paise: 0, discount_paise: 0, whatsapp_cost: 0, ...over,
  };
}

function redemption(over: Partial<RedemptionRow>): RedemptionRow {
  return {
    redemption_id: "r", cafe_id: "cafe-1", campaign_id: "c", campaign_name: "C", campaign_recipient_id: "rc", code: "BACK20-7KQ",
    channel: "counter", redeemed_at: "2026-10-12T07:11:00Z", day: "2026-10-12", customer_id: "cu", first_name: "Riya",
    masked_phone: "+91 98••• ••421", bill_paise: 124000, discount_paise: 24800, redeemed_by: "u-1", redeemed_by_name: "Ravi",
    order_number: null, table_label: null, table_session_id: null, ...over,
  };
}

describe("bestCampaign", () => {
  test("highest came_back / sent among campaigns with at least 10 delivered", () => {
    const a = campaign({ campaign_id: "a", sent: 20, delivered: 12, came_back: 4 });
    const b = campaign({ campaign_id: "b", sent: 10, delivered: 10, came_back: 3 });
    const tiny = campaign({ campaign_id: "tiny", sent: 5, delivered: 5, came_back: 5 }); // 100 % but too few delivered
    expect(bestCampaign([a, b, tiny])?.campaign_id).toBe("b");
  });

  test("ties go to more redemptions", () => {
    const a = campaign({ campaign_id: "a", sent: 20, delivered: 20, came_back: 5, redeemed: 2 });
    const b = campaign({ campaign_id: "b", sent: 40, delivered: 40, came_back: 10, redeemed: 6 });
    expect(bestCampaign([a, b])?.campaign_id).toBe("b");
  });

  test("none with enough delivered → null; the threshold can be changed", () => {
    const a = campaign({ sent: 9, delivered: 9, came_back: 3 });
    expect(bestCampaign([a])).toBeNull();
    expect(bestCampaign([a], 5)?.campaign_id).toBe("c");
    expect(bestCampaign([])).toBeNull();
  });
});

describe("byStaff", () => {
  test("totals per person, counter vs bills, discount, last; Unknown for a null redeemed_by", () => {
    const rows = byStaff([
      redemption({ redemption_id: "1", redeemed_by: "u-1", redeemed_by_name: "Ravi", channel: "counter", discount_paise: 1000, redeemed_at: "2026-10-12T07:00:00Z" }),
      redemption({ redemption_id: "2", redeemed_by: "u-1", redeemed_by_name: "Ravi", channel: "table_bill", discount_paise: 2500, redeemed_at: "2026-10-13T07:00:00Z" }),
      redemption({ redemption_id: "3", redeemed_by: null, redeemed_by_name: null, channel: "table_bill", discount_paise: 500 }),
      redemption({ redemption_id: "4", redeemed_by: "u-2", redeemed_by_name: null, channel: "counter", discount_paise: 100 }),
    ]);
    expect(rows[0]).toMatchObject({ name: "Ravi", total: 2, counter: 1, tableBill: 1, discount_paise: 3500, last: "2026-10-13T07:00:00Z" });
    expect(rows.find((r) => r.key === "unknown")).toMatchObject({ name: "Unknown", total: 1, tableBill: 1 });
    expect(rows.find((r) => r.key === "u-2")).toMatchObject({ name: "Former staff", total: 1, counter: 1 });
  });
});

describe("byDay", () => {
  test("counts per café-local day, counter vs table bill, oldest first, gaps filled", () => {
    const rows = byDay([
      redemption({ day: "2026-10-14", channel: "counter" }),
      redemption({ day: "2026-10-12", channel: "table_bill" }),
      redemption({ day: "2026-10-12", channel: "counter" }),
      redemption({ day: "2026-10-12", channel: "counter" }),
    ]);
    expect(rows.map((r) => [r.day, r.counter, r.table_bill])).toEqual([
      ["2026-10-12", 2, 1], ["2026-10-13", 0, 0], ["2026-10-14", 1, 0],
    ]);
    expect(rows[1]!.t - rows[0]!.t).toBe(86_400_000);
  });

  test("empty and single day", () => {
    expect(byDay([])).toEqual([]);
    expect(byDay([redemption({})])).toHaveLength(1);
  });
});

test("totals sum every campaign (a visit can count for more than one)", () => {
  const t = totals([campaign({ sent: 10, came_back: 2, came_back_paise: 50000 }), campaign({ sent: 5, came_back: 1, came_back_paise: 20000 })]);
  expect(t).toMatchObject({ sent: 15, came_back: 3, came_back_paise: 70000 });
});

test("staffLabel: the name, Former staff when the membership is gone, Unknown with no staff (the same on every screen)", () => {
  expect(staffLabel({ redeemed_by: "u-1", redeemed_by_name: "Ravi" })).toBe("Ravi");
  expect(staffLabel({ redeemed_by: "u-2", redeemed_by_name: null })).toBe("Former staff");
  expect(staffLabel({ redeemed_by: null, redeemed_by_name: null })).toBe("Unknown");
});
