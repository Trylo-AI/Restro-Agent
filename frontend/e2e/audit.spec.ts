// Audit log on the real stack (docs/console.md#audit-log "Done when"): a shift manager cancels an order, the owner finds
// who did it, narrows by person and order, opens the order history and exports the CSV. Dry-run item: "Owner finds who
// cancelled order #N in under 30 s".
import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { needsStack, orderCard, randomName, signIn } from "./console";
import { Customer } from "./sim";

test.beforeEach(() => {
  needsStack();
  test.setTimeout(240_000);
});

test("shift manager cancels → owner sees the critical row, filters, opens the history and exports", async ({ page, browser }) => {
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  const number = await customer.placeOrder(4, randomName());
  const card = await orderCard(page, number, "T4");
  await card.getByRole("button", { name: /Cancel/ }).click();
  await page.getByLabel("Out of stock").check();
  await page.getByRole("button", { name: "Cancel order" }).click();
  await expect(card).toHaveCount(0, { timeout: 10_000 });

  const owner = await (await browser.newContext()).newPage();
  await signIn(owner, "owner@restro.test");
  const started = Date.now();
  await owner.getByRole("link", { name: "Audit log" }).click();
  await expect(owner).toHaveURL(/\/audit$/);
  const row = owner.locator(".audit-row.sev-critical").filter({ hasText: `cancelled order #${number}` }).first();
  await expect(row).toBeVisible({ timeout: 20_000 });
  await expect(row.getByText("Critical")).toBeVisible();
  await expect(row.getByText(/Out of stock/)).toBeVisible();
  expect(Date.now() - started).toBeLessThan(30_000); // dry-run: who cancelled #N, in under 30 s

  // Only this person, then only this order (all time).
  await row.getByRole("button", { name: "Show details" }).click();
  await row.getByRole("button", { name: "Only this person" }).click();
  await expect(owner).toHaveURL(/actor=[0-9a-f-]{36}/);
  await expect(owner.getByLabel("Active filters").getByText("Ravi")).toBeVisible();
  const narrowed = owner.locator(".audit-row").filter({ hasText: `cancelled order #${number}` }).first();
  await narrowed.getByRole("button", { name: "Show details" }).click();
  await narrowed.getByRole("button", { name: "Only this order" }).click();
  await expect(owner).toHaveURL(/order_id=[0-9a-f-]{36}/);
  await expect(owner.locator(".audit-row").first()).toBeVisible();

  // Order history: the cancel is highlighted.
  await owner.locator(".audit-row").filter({ hasText: `cancelled order #${number}` }).first().getByRole("link", { name: `#${number}` }).click();
  await expect(owner).toHaveURL(/\/audit\/orders\/[0-9a-f-]{36}$/);
  await expect(owner.getByRole("heading", { name: new RegExp(`^Order #${number} · T4 .*Cancelled$`) })).toBeVisible();
  await expect(owner.getByRole("note")).toContainText("Cancelled by Ravi — Out of stock");
  await expect(owner.locator(".audit-event.sev-critical")).toHaveCount(1);

  // CSV of the current view.
  await owner.getByRole("link", { name: /All actions on this order/ }).click();
  const download = owner.waitForEvent("download");
  await owner.getByRole("button", { name: /Export CSV/ }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^audit-log-\d{4}-\d{2}-\d{2}\.csv$/);
  const csv = readFileSync(await file.path(), "utf-8");
  expect(csv.split(/\r?\n/)[0]).toBe(
    "id,at_local,at_utc,severity,severity_reason,category,action,label,actor,actor_role,support,order_number,customer_first_name,sentence,details");
  expect(csv).not.toMatch(/\b91\d{10}\b/); // never a phone number
  await expect(owner.getByText(/Audit log exported \(\d+ rows?\)/)).toBeVisible();
  await owner.context().close();
});

test("phone: the Filters sheet works and nothing scrolls sideways", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signIn(page, "owner@restro.test");
  await page.goto("/audit");
  await expect(page.getByRole("switch", { name: /Critical only/ })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: /^Filters/ }).click();
  const sheet = page.getByRole("dialog", { name: "Filters" });
  await sheet.getByRole("button", { name: "30 days" }).click();
  await sheet.getByText("Action", { exact: true }).click();
  await sheet.getByRole("checkbox", { name: /Order cancelled/ }).check();
  await sheet.getByRole("button", { name: "Show results" }).click();
  await expect(page).toHaveURL(/range=30d/);
  await expect(page).toHaveURL(/action=order\.cancel/);
  await expect(page.getByRole("button", { name: "Filters (2)" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("a manager is sent back from the audit log", async ({ page }) => {
  await signIn(page, "manager@restro.test");
  await expect(page.getByRole("link", { name: "Audit log" })).toHaveCount(0);
  await page.goto("/audit");
  await expect(page).toHaveURL(/\/orders$/);
});
