// Public pages render without login, without scripts and without horizontal scroll at phone width.
import { expect, test } from "@playwright/test";

for (const page of ["about", "privacy", "terms", "data-deletion"]) {
  test(`${page} reads at 375 px`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 375, height: 800 }, javaScriptEnabled: false });
    const p = await ctx.newPage();
    const res = await p.goto(`/${page}.html`);
    expect(res?.status()).toBe(200);
    await expect(p.locator("h1")).toBeVisible();
    const css = await p.request.get("/site.css");
    expect(css.ok()).toBe(true);
    const fits = await p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(fits).toBe(true);
    await ctx.close();
  });
}
