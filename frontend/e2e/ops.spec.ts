// Performance dashboard on the real stack (docs/OPS_PLAN.md, PDS Gate 3): a simulated order shows up, and its
// turn is reachable from Overview in at most 3 clicks, ending at a waterfall that matches the run.
import { expect, test } from "@playwright/test";

import { needsStack, randomName, signIn } from "./console";
import { Customer } from "./sim";

test("Overview → Customer turns → a turn → its trace, in 3 clicks", async ({ page }) => {
  needsStack();
  test.skip(!process.env.E2E_OPS_TOKEN, "needs OPS_DASHBOARD_TOKEN in backend/.env");
  test.setTimeout(180_000);
  const customer = await Customer.create();
  await customer.seat(2, randomName());
  await customer.tap("menu");
  await customer.expectReply(/"id":\s*"cat:/);

  await signIn(page, "owner@restro.test");
  await page.getByRole("link", { name: "Performance" }).click();
  await page.getByLabel("Ops token").fill(process.env.E2E_OPS_TOKEN!);
  await page.getByRole("button", { name: "Open dashboard" }).click();
  await expect(page.getByText("Customer turns", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Reply time against the budget")).toBeVisible();

  await page.getByRole("link", { name: /Customer turns/ }).click(); // 1
  await expect(page.locator(".runs-table tbody tr").first()).toBeVisible({ timeout: 30_000 });
  await page.locator(".runs-table tbody tr").first().locator("a.mono").click(); // 2
  await expect(page.getByRole("heading", { name: /^turn/ })).toBeVisible({ timeout: 30_000 });
  const rows = page.getByRole("table", { name: "Spans on the run's time axis" }).getByRole("row");
  await expect(rows.filter({ hasText: "turn.route" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "turn.commit" })).toHaveCount(1);
  await page.getByRole("button", { name: "Expand all" }).click(); // 3: every statement of the run
  await expect(rows.filter({ hasText: "db.query" }).first()).toBeVisible();
});

test("without the token the dashboard API refuses", async ({ request }) => {
  const api = process.env.E2E_API_URL ?? "http://localhost:8000";
  const res = await request.get(`${api}/api/ops/meta`);
  expect([401, 404]).toContain(res.status());
});
