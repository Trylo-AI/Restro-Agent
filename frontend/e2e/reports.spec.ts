// L7 Service report and Analytics on the real stack (docs/console.md#analytics-and-service-report).
import { expect, test } from "@playwright/test";

import { needsStack, signIn } from "./console";

test.beforeEach(needsStack);

test("owner: analytics tiles, revenue chart, top dishes; the period reloads every block", async ({ page }) => {
  await signIn(page, "owner@restro.test");
  await page.getByRole("link", { name: "Analytics" }).click();
  await expect(page.getByText("Revenue", { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Top 5 dishes")).toBeVisible();
  const revenue30 = await page.locator(".tile", { hasText: "Revenue" }).locator(".tile-value").innerText();
  await page.getByRole("radio", { name: "7 days" }).click();
  await expect(page.getByRole("radio", { name: "7 days" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("Revenue by day")).toBeVisible();
  await page.getByRole("radio", { name: "90 days" }).click();
  await expect(page.getByText("Revenue by week")).toBeVisible({ timeout: 20_000 });
  expect(revenue30).toMatch(/₹/);
});

test("manager: service report for the last 7 days; shift manager can't open it", async ({ page, browser }) => {
  await signIn(page, "manager@restro.test");
  await page.getByRole("link", { name: "Service report" }).click();
  await expect(page.getByText("Last 7 days · WhatsApp orders only.")).toBeVisible();
  await expect(page.getByText(/Median prep|No orders reached Ready/).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("link", { name: "Analytics" })).toHaveCount(0);
  const shift = await (await browser.newContext()).newPage();
  await signIn(shift, "shift@restro.test");
  await shift.goto("/service-report");
  await expect(shift).toHaveURL(/\/orders$/);
  await shift.context().close();
});
