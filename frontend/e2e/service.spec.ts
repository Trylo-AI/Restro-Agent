// Calls, Chats and Menu on the real stack (goal item 4 and the Menu "Done when"), taps and keywords only (no LLM).
import { expect, test } from "@playwright/test";

import { randomName, signIn } from "./console";
import { Customer } from "./sim";

test.beforeEach(() => {
  test.skip(!process.env.E2E_STAFF_PASSWORD || !process.env.E2E_SIM_TOKEN, "needs frontend/.env.local and simulator/.env.local");
});

test("call waiter → Calls → Acknowledge → Chats reply reaches the customer → hand back → bot answers again", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  await customer.say("Hi, I'm at Table 6");
  await customer.expectReply(/What should we call you\?/);
  const name = randomName();
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

test("menu: a manager edits a dish (bottom sheet at 375 px) and the change sticks", async ({ page }) => {
  await signIn(page, "manager@restro.test");
  await page.getByRole("link", { name: /Menu/ }).first().click();
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole("button", { name: "Not now" }).click(); // phones get the alerts gate first
  const edit = page.getByRole("button", { name: /^Edit / }).first();
  const dish = (await edit.getAttribute("aria-label"))!.replace(/^Edit /, "");
  const prep = page.getByLabel("Prep time (min)");
  await edit.click();
  const before = await prep.inputValue();
  const after = before === "9" ? "8" : "9";
  await prep.fill(after);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(`${dish} saved · the agent uses it from the next message`)).toBeVisible();
  await page.getByRole("button", { name: `Edit ${dish}` }).click();
  await expect(prep).toHaveValue(after);
  await prep.fill(before); // put it back
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(`${dish} saved · the agent uses it from the next message`)).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
