// L8 Customers on the real stack (docs/console.md#customers "Done when"). Nothing is deleted: the dialog is cancelled.
import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { needsStack, signIn } from "./console";

test.beforeEach(needsStack);

test("owner: list with masked numbers, segment chips, profile, delete dialog keeps data, consent export masked", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page, "owner@restro.test");
  await page.getByRole("link", { name: "Customers" }).click();
  const table = page.locator(".customers-table");
  await expect(table).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/\d+ customers · \d+ regular · \d+ new · \d+ lapsed/)).toBeVisible();
  for (const phone of await table.locator("td.mono").allInnerTexts()) expect(phone).toMatch(/••/);

  await page.getByRole("button", { name: "Regular", exact: true }).click();
  await expect(page.getByRole("button", { name: "Regular", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "All", exact: true }).click();

  await table.locator("tbody tr").first().click();
  await expect(page).toHaveURL(/\/customers\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Total spend")).toBeVisible();
  await page.getByRole("button", { name: /Delete customer data/ }).click();
  const confirm = page.getByRole("dialog", { name: "Delete this customer's data?" });
  await expect(confirm.getByText("This can't be undone")).toBeVisible();
  await expect(confirm.getByRole("button", { name: "Keep data" })).toBeFocused(); // safe default
  await confirm.getByRole("button", { name: "Keep data" }).click();
  await expect(confirm).toHaveCount(0);

  await page.goto("/customers?tab=consent");
  await expect(page.getByText("Only the customer can change their consent")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: /Export CSV/ }).click();
  const csv = readFileSync(await (await download).path(), "utf-8");
  expect(csv.split(/\r?\n/)[0]).toBe("customer,first_name,type,status,wording_version,source,at");
  expect(csv).not.toMatch(/\b91\d{10}\b/); // never a full number
});

test("non-owners are sent back", async ({ page }) => {
  await signIn(page, "manager@restro.test");
  await page.goto("/customers");
  await expect(page).toHaveURL(/\/orders$/);
});
