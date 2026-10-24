// Flows that need the real model (free-text turns and profile comments). Skipped with E2E_NO_LLM=1, for
// when the Gemini quota is used up. Synthetic customers and comments only.
import { expect, test, type Browser } from "@playwright/test";

import { needsStack, orderCard, randomName, signIn } from "./console";
import { Customer } from "./sim";

test.beforeEach(() => {
  needsStack();
  test.skip(process.env.E2E_NO_LLM === "1", "E2E_NO_LLM=1");
  test.setTimeout(150_000);
});

test("unknown allergen → handoff → Calls → Reply reaches the customer", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  const name = randomName();
  await customer.seat(4, name);
  // Seed: Chocolate Brownie has tree_nuts = unknown, so code (not the model) must hand this to a person.
  await customer.say("Does the chocolate brownie have any nuts in it?");
  await customer.expectReply(/Let me check with the team/, 60_000);

  await page.getByRole("link", { name: /Calls/ }).first().click();
  const call = page.getByRole("article", { name: "Allergy question, table T4" }).filter({ hasText: name }).last();
  await expect(call).toBeVisible({ timeout: 10_000 });
  await call.getByRole("button", { name: "Reply" }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f-]{36}$/);
  await page.getByLabel("Reply").fill("It may contain walnuts, so we'd suggest the carrot cake instead.");
  await page.getByRole("button", { name: "Send" }).click();
  await customer.expectReply(/may contain walnuts/);

  await page.getByRole("link", { name: /Calls/ }).first().click();
  await call.getByRole("button", { name: "Acknowledge" }).click();
  await call.getByRole("button", { name: "Resolve" }).click();
  await expect(call).toHaveCount(0);
});

async function console2(browser: Browser, email: string) {
  const page = await (await browser.newContext()).newPage();
  await signIn(page, email);
  return page;
}

test("profile comment → AI updates the profile → Undo, seen on two consoles", async ({ page, browser }) => {
  await signIn(page, "shift@restro.test");
  const manager = await console2(browser, "manager@restro.test");
  const customer = await Customer.create();
  const name = randomName();
  const number = await customer.placeOrder(3, name);

  const open = async (p: typeof page) => {
    const card = await orderCard(p, number, "T3");
    await card.getByRole("button", { name: `Profile of ${name}` }).click();
    return p.getByRole("region", { name: "At a glance" });
  };
  const mine = await open(page);
  const theirs = await open(manager);
  await expect(mine.getByText("Allergy")).toHaveCount(0);

  await page.getByLabel("Add a comment").fill("She told me she is allergic to peanuts and prefers oat milk.");
  await page.getByRole("button", { name: "Save" }).click();
  const result = page.locator(".note-result").first();
  await expect(result).not.toHaveText("Updating profile…", { timeout: 60_000 });
  await expect(result).toHaveClass(/applied/);

  // Realtime: the manager's open panel picks the change up without a reload.
  await expect(mine.getByRole("button", { name: /Remove peanut/i })).toBeVisible();
  await expect(theirs.getByRole("button", { name: /Remove peanut/i })).toBeVisible({ timeout: 15_000 });
  await expect(theirs.getByText(/oat/i)).toBeVisible();

  await result.getByRole("button", { name: "Undo" }).click();
  await expect(result.getByText("Undone")).toBeVisible();
  await expect(theirs.getByRole("button", { name: /Remove peanut/i })).toHaveCount(0, { timeout: 15_000 });
  await expect(theirs.getByText(/oat/i)).toHaveCount(0);

  await page.getByRole("button", { name: "Close" }).click();
  const card = await orderCard(page, number, "T3");
  await card.getByRole("button", { name: "Accept" }).click();
  await manager.context().close();
});
