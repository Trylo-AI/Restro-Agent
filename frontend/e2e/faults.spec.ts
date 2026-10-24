// Fault injection through the simulator (/dev/sim/faults): a dead-lettered turn reaches a person; an ambiguous
// send is resolved by its status webhook and never resent. Faults are global, so these run one at a time.
import { expect, test } from "@playwright/test";

import { needsStack, randomName, signIn } from "./console";
import { arm, armedFaults, Customer } from "./sim";

test.beforeEach(needsStack);

test("dead letter: four crashes → the customer is told how to reach a person → Calls → Resolve", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page, "shift@restro.test");
  const customer = await Customer.create();
  const name = randomName();
  await customer.seat(11, name);

  await arm("turn_crash", 4); // first try + 3 retries (2 / 4 / 8 s)
  await customer.tap("menu");
  await customer.expectReply(/Sorry, something went wrong on our side/, 60_000);
  expect((await armedFaults()).turn ?? []).toHaveLength(0);
  const outs = (await customer.outItems()).map((o) => JSON.stringify(o.payload));
  expect(outs.filter((p) => p.includes("something went wrong"))).toHaveLength(1);
  expect(outs.some((p) => /"id":\s*"cat:/.test(p))).toBe(false); // the crashed turn never half-ran

  await page.getByRole("link", { name: /Calls/ }).first().click();
  const call = page.getByRole("article", { name: "Needs help, table T11" }).filter({ hasText: name }).last();
  await expect(call).toBeVisible({ timeout: 10_000 });
  await call.getByRole("button", { name: "Acknowledge" }).click();
  await call.getByRole("button", { name: "Resolve" }).click();
  await expect(call).toHaveCount(0);
});

test("ambiguous send: the reply goes once, its status webhook confirms it, nothing is resent", async () => {
  test.setTimeout(90_000);
  const customer = await Customer.create();
  await customer.seat(12, randomName());
  // Rows are written before the send, so wait for the last reply to leave before arming the fault.
  await expect.poll(async () => (await customer.outItems()).every((o) => !["pending", "ambiguous"].includes(o.status)), { timeout: 15_000 }).toBe(true);

  await arm("send_timeout");
  await customer.tap("menu");
  await customer.expectReply(/"id":\s*"cat:/);
  await expect.poll(async () => (await armedFaults()).send ?? [], { timeout: 15_000 }).toHaveLength(0);

  const categories = async () => (await customer.outItems()).filter((o) => /"id":\s*"cat:/.test(JSON.stringify(o.payload)));
  await expect.poll(async () => (await categories()).map((o) => o.status), { timeout: 20_000 })
    .toEqual([expect.stringMatching(/^(sent|delivered|read)$/)]);
  // A resend would add a second row; give it a few seconds to show up.
  await new Promise((r) => setTimeout(r, 5_000));
  const rows = await categories();
  expect(rows).toHaveLength(1);
  expect(rows[0]!.error_code).toBe("timeout");
});
