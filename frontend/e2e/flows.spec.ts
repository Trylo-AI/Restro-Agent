// Order edits, sold out mid-order, delay and cancel on the real stack, taps only (no LLM).
import { expect, test } from "@playwright/test";

import { needsStack, orderCard, randomName, signIn } from "./console";
import { Customer, summaryNames } from "./sim";

test.beforeEach(() => {
  needsStack();
  test.setTimeout(240_000); // each simulated turn takes a few seconds against the hosted dev DB
});

test("edit while New saves the change and flags the card; edit after Accept is refused", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  const number = await customer.placeOrder(7, randomName());

  await customer.tap(`edit_order:${customer.orderId}`);
  await customer.expectReply(new RegExp(`Editing order #${number}`));
  const save = await customer.addDish(1, /"id":\s*"apply_edit:/);
  expect(summaryNames(save)).toHaveLength(2);
  await customer.tap(save.match(/"id":\s*"(apply_edit:[^"]+)"/)![1]!);
  await customer.expectReply(new RegExp(`Updated order #${number}`));

  const card = await orderCard(page, number, "T7");
  await expect(card.getByText("Edited")).toBeVisible();
  for (const name of summaryNames(save)) await expect(card.getByText(name)).toBeVisible();

  await card.getByRole("button", { name: "Accept" }).click();
  await customer.tap(`edit_order:${customer.orderId}`);
  await customer.expectReply(new RegExp(`already accepted #${number}, so I can't change it`));

  await page.getByRole("tab", { name: /^Accepted/ }).click();
  await card.getByRole("button", { name: "Mark ready" }).click();
  await page.getByRole("tab", { name: /^Ready/ }).click();
  await card.getByRole("button", { name: "Mark served" }).click();
  await expect(card).toHaveCount(0, { timeout: 10_000 });
});

test("sold out mid-order: Confirm drops the dish with a notice, the order keeps the rest", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  await customer.seat(9, randomName());
  await customer.addDish(0);
  const summary = await customer.addDish(1);
  const [gone, kept] = summaryNames(summary) as [string, string];
  expect(kept).toBeTruthy();

  await page.getByRole("link", { name: /Menu/ }).first().click();
  const back = page.getByRole("switch", { name: `${gone} sold out — tap to bring back` });
  try {
    await page.getByRole("switch", { name: `${gone} available` }).click();
    await page.getByRole("switch", { name: `Tap again to mark ${gone} sold out` }).click();
    await expect(back).toBeVisible();

    await customer.tap(summary.match(/"id":\s*"(confirm:\d+)"/)![1]!);
    await customer.expectReply(new RegExp(`${gone} just sold out, so I removed it`));
    const fresh = await customer.expectReply(/"id":\s*"confirm:\d+"/);
    expect(summaryNames(fresh)).toEqual([kept]);
    const number = await customer.confirm(fresh);

    const card = await orderCard(page, number, "T9");
    await expect(card.getByText(kept)).toBeVisible();
    await expect(card.getByText(gone)).toHaveCount(0);
    await card.getByRole("button", { name: "Accept" }).click();
  } finally {
    await page.getByRole("link", { name: /Menu/ }).first().click();
    if (await back.isVisible()) await back.click();
    await expect(page.getByRole("switch", { name: `${gone} available` })).toBeVisible();
  }
});

test("delay tells the customer; cancel with Other sends an apology but never the staff text", async ({ page }) => {
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  const number = await customer.placeOrder(8, randomName());
  const card = await orderCard(page, number, "T8");

  await card.getByRole("button", { name: "Delay" }).click();
  const delay = page.getByRole("dialog", { name: `Delay #${number} · T8` });
  await delay.getByRole("radio", { name: "+10" }).click();
  await expect(delay.getByText("Sorry for the wait — your order needs about 10 more minutes.")).toBeVisible();
  await delay.getByRole("button", { name: "Delay and tell the customer" }).click();
  await expect(page.getByText(`#${number} delayed +10 min · Customer told`)).toBeVisible();
  await customer.expectReply(/your order needs about 10 more minutes/);

  const secret = "Grinder jammed again";
  await card.getByRole("button", { name: /Cancel/ }).click();
  const cancel = page.getByRole("dialog", { name: `Cancel #${number} · T8` });
  await cancel.getByLabel("Other").check();
  const go = cancel.getByRole("button", { name: "Cancel order" });
  await expect(go).toBeDisabled(); // Other needs text
  await cancel.getByLabel("What happened (required)").fill(secret);
  await go.click();
  await expect(page.getByText(`#${number} cancelled · The customer gets an apology`)).toBeVisible();
  await expect(card).toHaveCount(0, { timeout: 10_000 });

  await customer.expectReply(new RegExp(`#${number}`));
  const outs = (await customer.outItems()).map((o) => JSON.stringify(o.payload));
  expect(outs.some((p) => p.includes("Grinder"))).toBe(false);
});
