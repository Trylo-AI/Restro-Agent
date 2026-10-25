// Console helpers shared by the specs.
import { expect, test, type Page } from "@playwright/test";

export async function signIn(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(process.env.E2E_STAFF_PASSWORD ?? "");
  await page.getByRole("button", { name: /^Sign in/ }).click();
  await expect(page.getByRole("heading", { name: /^(Live orders|Today)$/ })).toBeVisible(); // owners land on Today
}

export function needsStack() {
  test.skip(!process.env.E2E_STAFF_PASSWORD || !process.env.E2E_SIM_TOKEN, "needs frontend/.env.local and simulator/.env.local");
}

const NAMES = ["Kabir", "Tara", "Ishaan", "Meher", "Zoya", "Vihaan", "Anaya", "Reyansh", "Nila", "Arush"];

/** A random seed-safe first name, so a run never matches a card left over from an earlier run. */
export const randomName = () => NAMES[Math.floor(Math.random() * NAMES.length)]!;

/** Opens the order card for `number` on its column tab. */
export async function orderCard(page: Page, number: number, table: string, column: "New" | "Accepted" | "Ready" = "New") {
  await page.getByRole("link", { name: /Live orders/ }).first().click();
  await page.getByRole("tab", { name: new RegExp(`^${column}`) }).click();
  const card = page.getByRole("article", { name: `Order ${number}, table ${table}` });
  await expect(card).toBeVisible({ timeout: 10_000 });
  return card;
}
