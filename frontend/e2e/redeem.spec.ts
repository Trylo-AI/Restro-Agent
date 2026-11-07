// Counter redemption end to end (docs/console.md#redeem-offer, "Done when"): a real campaign code reaches a simulated
// customer, then a shift manager at 375 px finds them by the last 4 digits, redeems at the counter, sees Collect from
// the API and the row in Redeemed today. A second till with the stale search is refused with "Already used".
// Synthetic customers only.
import { expect, test, type Browser, type Page } from "@playwright/test";

import { needsStack, randomName, signIn } from "./console";
import { Customer } from "./sim";

const local = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);

/** The owner schedules a 20 % campaign to everyone opted in; returns once the customer has the code. */
async function codeFor(page: Page, customer: Customer, prefix: string): Promise<string> {
  await signIn(page, "owner@restro.test");
  await page.getByRole("link", { name: "Campaigns" }).click();
  await page.getByRole("link", { name: /New campaign/ }).click();
  await expect(page.getByText(/\d+ opted in/)).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Continue" }).click(); // audience: everyone opted in
  await page.getByRole("button", { name: "Continue" }).click(); // template
  await page.getByLabel("Campaign name (only you see it)").fill(`E2E ${prefix}`);
  await page.getByLabel("Offer text").fill("20% off your next visit");
  await page.getByLabel("Discount on the bill (%)").fill("20");
  await page.getByLabel("Code prefix").fill(prefix);
  await page.getByRole("button", { name: "Continue" }).click(); // message (saves the draft)
  await page.getByLabel("Send at").fill(local(new Date(Date.now() + 61_000)));
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Schedule" }).click();
  await expect(page.getByText(/Scheduled for/)).toBeVisible();
  const sent = await customer.expectReply(new RegExp(`${prefix}-[A-Z0-9]{3}`), 240_000);
  return sent.match(new RegExp(`${prefix}-[A-Z0-9]{3}`))![0];
}

/** A shift manager on a phone, on /redeem. Phones get the alerts gate first. */
async function phoneTill(browser: Browser): Promise<Page> {
  const page = await (await browser.newContext({ viewport: { width: 375, height: 812 } })).newPage();
  await page.goto("/");
  await page.getByLabel("Email").fill("shift@restro.test");
  await page.getByLabel("Password").fill(process.env.E2E_STAFF_PASSWORD ?? "");
  await page.getByRole("button", { name: /^Sign in/ }).click();
  const gate = page.getByRole("button", { name: "Not now" });
  if (await gate.isVisible({ timeout: 10_000 }).catch(() => false)) await gate.click();
  await page.locator(".tabbar").getByRole("link", { name: "Redeem" }).click();
  await expect(page.getByRole("heading", { name: "Redeem an offer" })).toBeVisible();
  return page;
}

async function noSideScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("last 4 digits → redeem at the counter → Collect → Redeemed today → a second till gets Already used", async ({ page, browser }) => {
  needsStack();
  test.setTimeout(420_000);
  const name = randomName();
  const prefix = `RD${Math.floor(Math.random() * 9000 + 1000)}`;

  const customer = await Customer.create();
  await customer.placeOrder(8, name);
  await customer.say("Hi, send me offers");
  await customer.expectReply(/"id":\s*"optin:yes:counter_qr"/);
  await customer.tap("optin:yes:counter_qr");
  await customer.expectReply(/you'll get occasional offers/);
  const code = await codeFor(page, customer, prefix);

  const till = await phoneTill(browser);
  const other = await phoneTill(browser); // a second till that searched before the first one redeemed
  const last4 = customer.waId.slice(-4);
  for (const p of [till, other]) await p.getByLabel("Search offers").fill(last4);
  const card = till.getByRole("article", { name }).filter({ hasText: code });
  const stale = other.getByRole("article", { name }).filter({ hasText: code });
  await expect(card.getByText("Eligible").first()).toBeVisible({ timeout: 20_000 });
  await expect(stale.getByText("Eligible").first()).toBeVisible({ timeout: 20_000 });
  await noSideScroll(till);

  const row = card.locator(".offer-row", { hasText: code });
  await row.getByRole("button", { name: "Redeem at counter" }).click();
  const dialog = till.getByRole("dialog", { name: `Redeem ${code}` });
  await dialog.getByLabel("Amount paid (₹, before discount)").fill("1240");
  await dialog.getByRole("button", { name: "Redeem 20 % off ₹ 1,240" }).click();
  await expect(dialog.getByText("Collect ₹ 992")).toBeVisible();
  await expect(dialog.getByText(/Bill ₹ 1,240 · Discount −₹ 248/)).toBeVisible();
  await dialog.getByRole("button", { name: "Done" }).click();
  const today = till.getByRole("region", { name: "Redeemed today" });
  await expect(today.locator(".redeemed-row", { hasText: code })).toContainText("Counter");
  await expect(today.locator(".redeemed-row", { hasText: code })).toContainText("−₹ 248");
  await expect(card.locator(".offer-row", { hasText: code }).getByText("Used", { exact: true })).toBeVisible();
  await noSideScroll(till);

  await stale.locator(".offer-row", { hasText: code }).getByRole("button", { name: "Redeem at counter" }).click();
  const second = other.getByRole("dialog", { name: `Redeem ${code}` });
  await second.getByLabel("Amount paid (₹, before discount)").fill("800");
  await second.getByRole("button", { name: /^Redeem 20 % off/ }).click();
  await expect(second.getByRole("alert")).toContainText("Already used");
  await till.context().close();
  await other.context().close();
});
