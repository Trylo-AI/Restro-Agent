// Live orders on the real stack: a WhatsApp order reaches the console, moves through the columns, Undo sends nothing.
import { expect, test, type Page } from "@playwright/test";

import { Customer } from "./sim";

async function signIn(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(process.env.E2E_STAFF_PASSWORD ?? "");
  await page.getByRole("button", { name: /^Sign in/ }).click();
  await expect(page.getByRole("heading", { name: "Live orders" })).toBeVisible();
}

test("shift manager: order arrives, Accept → Undo → Accept → Ready → Served, profile opens", async ({ page }) => {
  test.skip(!process.env.E2E_STAFF_PASSWORD || !process.env.E2E_SIM_TOKEN, "needs frontend/.env.local and simulator/.env.local");
  await signIn(page, "shift@restro.test");

  const customer = await Customer.create();
  const number = await customer.placeOrder(5, "Asha");
  const card = page.getByRole("article", { name: `Order ${number}, table T5` });

  await page.getByRole("tab", { name: /^New/ }).click();
  await expect(card).toBeVisible({ timeout: 10_000 });
  await expect(card.getByText(/Asha · First visit/)).toBeVisible();
  await expect(card.getByRole("button", { name: /Cancel/ })).toBeVisible(); // shift_manager may cancel while New

  // Accept, then Undo inside 5 s: the order goes back and the customer hears nothing.
  await card.getByRole("button", { name: "Accept" }).click();
  await expect(page.getByText(`#${number} → Accepted · Message sends in 5 s`)).toBeVisible();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByText(`#${number} back to New · Nothing was sent to the customer`)).toBeVisible();
  await expect(card).toBeVisible();

  // Accept for real: the status message goes out after 5 s; shift_manager can no longer cancel.
  await card.getByRole("button", { name: "Accept" }).click();
  await page.getByRole("tab", { name: /^Accepted/ }).click();
  await expect(card.getByText(/0 \/ \d+ min/)).toBeVisible();
  await expect(card.getByRole("button", { name: /Cancel/ })).toHaveCount(0);
  await customer.expectReply(/being made/i, 20_000);

  // Profile in one tap.
  await card.getByRole("button", { name: /Profile of Asha/ }).click();
  const glance = page.getByRole("region", { name: "At a glance" });
  await expect(glance.getByText("Asha")).toBeVisible();
  await expect(glance.getByText(/\+91 99••• ••\d{3}/)).toBeVisible(); // masked, never the full number
  await page.getByRole("button", { name: "Close" }).click();

  await card.getByRole("button", { name: "Mark ready" }).click();
  await page.getByRole("tab", { name: /^Ready/ }).click();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Mark served" }).click();
  await expect(card).toHaveCount(0, { timeout: 10_000 });
});

test("Offline banner within a few seconds of losing the api; back online toasts and clears it", async ({ page }) => {
  test.skip(!process.env.E2E_STAFF_PASSWORD, "needs frontend/.env.local");
  await signIn(page, "shift@restro.test");
  const api = process.env.E2E_API_URL ?? "http://localhost:8000";
  await page.route(`${api}/**`, (r) => r.abort("connectionrefused"));
  const dropped = Date.now();
  await expect(page.getByText(/Offline — new orders may be missing/)).toBeVisible({ timeout: 10_000 });
  expect(Date.now() - dropped).toBeLessThan(8_000);
  await expect(page.locator(".order-card .btn.primary").first()).toBeDisabled(); // writes off while offline
  await page.unroute(`${api}/**`);
  await expect(page.getByText(/Back online · \d+ orders arrived while offline/)).toBeVisible();
  await expect(page.getByText(/Offline — new orders may be missing/)).toHaveCount(0);
});

test("phone width: tabs only, no horizontal scroll", async ({ page }) => {
  test.skip(!process.env.E2E_STAFF_PASSWORD, "needs frontend/.env.local");
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await page.getByLabel("Email").fill("shift@restro.test");
  await page.getByLabel("Password").fill(process.env.E2E_STAFF_PASSWORD ?? "");
  await page.getByRole("button", { name: /^Sign in/ }).click();
  // Phones get the alerts gate first (shift_manager, sound off).
  await expect(page.getByRole("heading", { name: "Turn on alerts before service." })).toBeVisible();
  await page.getByRole("button", { name: "Not now" }).click();
  await expect(page.getByRole("tablist")).toBeVisible();
  await expect(page.getByRole("radio", { name: /Board/ })).toHaveCount(0);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
