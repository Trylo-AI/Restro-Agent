// L4: staff names across consoles and in-app cancellation notifications (docs/console.md#urgency-and-alerts).
import { expect, test } from "@playwright/test";

import { needsStack, orderCard, randomName, signIn } from "./console";
import { Customer } from "./sim";

test.beforeEach(() => {
  needsStack();
  test.setTimeout(240_000);
});

test("a manager sees who acknowledged a call and gets the cancellation notification", async ({ page, browser }) => {
  await signIn(page, "shift@restro.test");
  const manager = await (await browser.newContext()).newPage();
  await signIn(manager, "manager@restro.test");

  const name = randomName();
  const customer = await Customer.create();
  const number = await customer.placeOrder(6, name);
  await customer.say("call waiter");
  await customer.expectReply(/Someone from our team/);

  await page.getByRole("link", { name: /Calls/ }).first().click();
  const call = page.getByRole("article", { name: "Needs help, table T6" }).filter({ hasText: name }).last();
  await call.getByRole("button", { name: "Acknowledge" }).click();
  await manager.getByRole("link", { name: /Calls/ }).first().click();
  const seen = manager.getByRole("article", { name: "Needs help, table T6" }).filter({ hasText: name }).last();
  await expect(seen.getByText(/Acknowledged by Ravi/)).toBeVisible({ timeout: 15_000 }); // a name, not "a teammate"

  const card = await orderCard(page, number, "T6");
  await card.getByRole("button", { name: /Cancel/ }).click();
  await page.getByLabel("Other").check();
  await page.getByLabel("What happened (required)").fill("Oven tripped");
  await page.getByRole("button", { name: "Cancel order" }).click();

  const bell = manager.getByRole("button", { name: /Alerts and notifications, \d+ unread/ });
  await expect(bell).toBeVisible({ timeout: 15_000 });
  await bell.click();
  const panel = manager.getByRole("dialog", { name: "Alerts and notifications" });
  await expect(panel.getByText(`#${number} · T6 cancelled by Ravi · Other`)).toBeVisible();
  await expect(panel.getByText("“Oven tripped”")).toBeVisible(); // staff see the custom text; the customer never does
  await panel.getByRole("button", { name: "Close" }).click();
  await expect(manager.getByRole("button", { name: "Alerts and notifications" })).toBeVisible({ timeout: 15_000 }); // read

  await page.getByRole("link", { name: /Calls/ }).first().click();
  await call.getByRole("button", { name: "Resolve" }).click();
  await manager.context().close();
});

test("a shift manager's bell has the device checks but no notification list", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  await page.getByRole("button", { name: "Alerts and notifications" }).click();
  const panel = page.getByRole("dialog", { name: "Alerts and notifications" });
  await expect(panel.getByText("This device")).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Notifications", exact: true })).toHaveCount(0);
});
