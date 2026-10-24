// Calls, Chats and Menu on the real stack (goal item 4 and the Menu "Done when"), taps and keywords only (no LLM).
import { expect, test, type Page } from "@playwright/test";

import { Customer } from "./sim";

async function signIn(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(process.env.E2E_STAFF_PASSWORD ?? "");
  await page.getByRole("button", { name: /^Sign in/ }).click();
  await expect(page.getByRole("heading", { name: "Live orders" })).toBeVisible();
}

const NAMES = ["Kabir", "Tara", "Ishaan", "Meher", "Zoya", "Vihaan", "Anaya", "Reyansh", "Nila", "Arush"];

test.beforeEach(() => {
  test.skip(!process.env.E2E_STAFF_PASSWORD || !process.env.E2E_SIM_TOKEN, "needs frontend/.env.local and simulator/.env.local");
});

test("call waiter → Calls → Acknowledge → Chats reply reaches the customer → hand back → bot answers again", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  await customer.say("Hi, I'm at Table 6");
  await customer.expectReply(/What should we call you\?/);
  const name = NAMES[Math.floor(Math.random() * NAMES.length)]!;
  await customer.say(name);
  await customer.expectReply(/"id":\s*"menu"/);
  await customer.say("call waiter");
  await customer.expectReply(/Someone from our team will be with you shortly/);

  await page.getByRole("link", { name: /Calls/ }).first().click();
  const call = page.getByRole("article", { name: "Needs help, table T6" }).filter({ hasText: `Needs help · ${name}` }).last();
  await expect(call).toBeVisible({ timeout: 10_000 });
  await expect(call.getByText("“call waiter”")).toBeVisible();
  await call.getByRole("button", { name: "Acknowledge" }).click();
  await expect(call.getByText(/Acknowledged by You/)).toBeVisible();

  await call.getByRole("button", { name: "Message" }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Waiting for a person. The bot is paused.")).toBeVisible();
  await expect(page.getByText("call waiter").first()).toBeVisible();
  await page.getByRole("button", { name: "Coming to your table now" }).click(); // fills, never sends
  await page.getByRole("button", { name: "Send" }).click();
  await customer.expectReply(/Coming to your table now/);
  await expect(page.getByText("You're replying. The bot is paused.")).toBeVisible();

  await page.getByRole("button", { name: "Hand back to bot" }).click();
  await expect(page.getByText("The bot is handling this chat.")).toBeVisible();
  await customer.tap("menu");
  await customer.expectReply(/"id":\s*"cat:/);

  // Resolve the call so runs don't pile up.
  await page.getByRole("link", { name: /Calls/ }).first().click();
  await call.getByRole("button", { name: "Resolve" }).click();
  await expect(call).toHaveCount(0);
});

test("menu: two taps mark sold out, back in one tap; shift_manager sees no prices", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  await page.getByRole("link", { name: /Menu/ }).first().click();
  await expect(page.getByRole("switch").first()).toBeVisible();
  await expect(page.locator(".menu-list").getByText(/₹/)).toHaveCount(0);

  const sw = page.getByRole("switch", { name: / available$/ }).last();
  const name = (await sw.getAttribute("aria-label"))!.replace(/ available$/, "");
  await sw.click();
  await expect(page.getByRole("switch", { name: `Tap again to mark ${name} sold out` })).toBeVisible();
  await page.getByRole("switch", { name: `Tap again to mark ${name} sold out` }).click();
  await expect(page.getByRole("switch", { name: `${name} sold out — tap to bring back` })).toBeVisible();
  await page.getByRole("switch", { name: `${name} sold out — tap to bring back` }).click();
  await expect(page.getByRole("switch", { name: `${name} available` })).toBeVisible();
});
