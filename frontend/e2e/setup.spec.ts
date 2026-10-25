// L6 Setup on the real stack (docs/console.md#setup "Done when"). Changes are put back; no real invites are sent.
import { expect, test } from "@playwright/test";

import { needsStack, signIn } from "./console";

test.beforeEach(() => {
  needsStack();
  test.setTimeout(180_000);
});

test("owner: QR per table opens WhatsApp with the table's text; settings save; team listed", async ({ page }) => {
  await signIn(page, "owner@restro.test");
  await page.getByRole("link", { name: "Setup" }).click();
  await expect(page).toHaveURL(/\/setup\/tables$/);
  const qr = page.getByRole("img", { name: "QR for T5" });
  await expect(qr).toBeVisible();
  await expect(page.getByText("“Hi, I'm at Table 5”")).toBeVisible();
  await expect(page.getByRole("img", { name: "Counter QR for offers" })).toBeVisible();

  // Edit a table and put it back.
  await page.getByRole("button", { name: "Edit T5" }).click();
  const area = page.getByLabel("Area");
  const before = await area.inputValue();
  await area.fill("E2E patio");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("T5 saved")).toBeVisible();
  await expect(page.getByText(/E2E patio/)).toBeVisible();
  await page.getByRole("button", { name: "Edit T5" }).click();
  await area.fill(before);
  await page.getByRole("button", { name: "Save" }).click();

  // Settings: one save, then back.
  await page.getByRole("link", { name: "Settings" }).click();
  const cutoff = page.getByLabel("Last orders (minutes before closing)");
  const was = await cutoff.inputValue();
  await cutoff.fill(was === "25" ? "20" : "25");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved")).toBeVisible();
  await page.reload();
  await expect(cutoff).toHaveValue(was === "25" ? "20" : "25");
  await cutoff.fill(was);
  await page.getByRole("button", { name: "Save settings" }).click();

  await page.getByRole("link", { name: "Team" }).click();
  await expect(page.getByRole("cell", { name: /Ravi/ })).toBeVisible();
  await expect(page.getByText("Last owner")).toBeVisible();
});

test("the printable QR sheet has every table plus the counter card", async ({ page }) => {
  await signIn(page, "owner@restro.test");
  await page.goto("/setup/qr-sheet");
  await expect(page.getByRole("button", { name: "Print" })).toBeVisible();
  await expect(page.locator(".print-card")).toHaveCount(13);
  await expect(page.getByText("Can't order on WhatsApp? Ask any of our team.").first()).toBeVisible();
});

test("non-owners are sent back to Live orders", async ({ page }) => {
  await signIn(page, "manager@restro.test");
  await expect(page.getByRole("link", { name: "Setup" })).toHaveCount(0);
  await page.goto("/setup/team");
  await expect(page).toHaveURL(/\/orders$/);
});
