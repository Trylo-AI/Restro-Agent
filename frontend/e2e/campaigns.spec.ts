// Phase 5 exit gate, locally (docs/plan.md roadmap): a test campaign to an opted-in simulated customer, a
// redemption at the bill, a correct report, and an opt-out. Synthetic customers only.
import { expect, test } from "@playwright/test";

import { needsStack, randomName, signIn } from "./console";
import { Customer } from "./sim";

const local = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);

test("opt in at the counter → campaign → code on WhatsApp → redeemed on the bill → came back → STOP", async ({ page, browser }) => {
  needsStack();
  test.setTimeout(420_000);
  const name = randomName();
  const prefix = `E2E${Math.floor(Math.random() * 9000 + 1000)}`;

  const customer = await Customer.create();
  await customer.placeOrder(9, name);
  await customer.say("Hi, send me offers");
  await customer.expectReply(/"id":\s*"optin:yes:counter_qr"/);
  await customer.tap("optin:yes:counter_qr");
  await customer.expectReply(/you'll get occasional offers/);

  await signIn(page, "owner@restro.test");
  await page.getByRole("link", { name: "Campaigns" }).click();
  await page.getByRole("link", { name: /New campaign/ }).click();
  await expect(page.getByText(/\d+ opted in/)).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Continue" }).click(); // audience: everyone opted in
  await expect(page.getByRole("radio", { name: "weekly_offer" })).toBeChecked();
  await page.getByRole("button", { name: "Continue" }).click(); // template
  await page.getByLabel("Campaign name (only you see it)").fill(`E2E ${prefix}`);
  await page.getByLabel("Offer text").fill("20% off your next visit");
  await page.getByLabel("Code prefix").fill(prefix);
  await expect(page.getByLabel("WhatsApp preview")).toContainText(`${prefix}-7KQ`);
  await expect(page.getByLabel("WhatsApp preview")).toContainText("Reply STOP to opt out");
  await page.getByRole("button", { name: "Continue" }).click(); // message (saves the draft)
  await page.getByLabel("Send at").fill(local(new Date(Date.now() + 61_000)));
  await page.getByRole("button", { name: "Continue" }).click(); // schedule
  await expect(page.getByText("Will receive")).toBeVisible();
  await page.getByRole("button", { name: "Schedule" }).click();
  await expect(page.getByText(/Scheduled for/)).toBeVisible();
  const report = page.url();

  // The worker's campaign_start picks it up within a minute or two; the code arrives on WhatsApp.
  const sent = await customer.expectReply(new RegExp(`${prefix}-[A-Z0-9]{3}`), 240_000);
  const code = sent.match(new RegExp(`${prefix}-[A-Z0-9]{3}`))![0];

  await customer.say("bill");
  await customer.expectReply(/Your bill is on its way/);
  const shift = await (await browser.newContext()).newPage();
  await signIn(shift, "shift@restro.test");
  await shift.getByRole("link", { name: /Calls/ }).first().click();
  const bill = shift.getByRole("article", { name: "Bill, table T9" }).filter({ hasText: name }).last();
  await bill.getByRole("button", { name: /Open bill/ }).click();
  const sheet = shift.getByRole("dialog", { name: "Bill · T9" });
  await sheet.getByLabel("Offer code").fill(code);
  await sheet.getByRole("button", { name: "Apply" }).click();
  await expect(shift.getByText(/Code applied · ₹ [\d,]+ off/)).toBeVisible();
  await expect(sheet.getByText(`Offer code ${code}`)).toBeVisible();
  await sheet.getByRole("button", { name: "Bill delivered" }).click();
  await expect(shift.getByText(/Bill delivered · T9 closed/)).toBeVisible();
  await shift.context().close();

  await page.goto(report);
  await expect(page.locator(".funnel li", { hasText: "Came back" })).toContainText(/^Came back.*1 ·/, { timeout: 30_000 });

  await customer.say("STOP");
  await customer.expectReply(/You won't get offers/);
});
