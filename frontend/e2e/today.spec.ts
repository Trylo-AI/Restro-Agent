// L10 Today on the real stack (docs/console.md#today "Done when").
import { expect, test } from "@playwright/test";

import { needsStack, signIn } from "./console";

test.beforeEach(needsStack);

test("owner lands on Today; pausing shows an attention card whose Resume clears it", async ({ page }) => {
  await signIn(page, "owner@restro.test");
  await expect(page).toHaveURL(/\/today$/);
  await expect(page.getByText("Customers today")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Service right now")).toBeVisible();
  await expect(page.getByRole("link", { name: /\d+ new/ })).toBeVisible();

  await page.getByRole("link", { name: "Live orders" }).first().click();
  await page.getByRole("button", { name: /Pause WhatsApp ordering/ }).click();
  await page.getByRole("link", { name: "Today" }).click();
  const card = page.locator(".attention li", { hasText: "WhatsApp ordering is paused." });
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByText("Ordering resumed")).toBeVisible();
  await expect(card).toHaveCount(0, { timeout: 15_000 });
});

test("managers still land on Live orders and can't open Today", async ({ page }) => {
  await signIn(page, "manager@restro.test");
  await expect(page).toHaveURL(/\/orders$/);
  await page.goto("/today");
  await expect(page).toHaveURL(/\/orders$/);
});
