// Public pages (frontend/public/*.html, docs/architecture.md#data-protection): Meta's app review reads them, so they
// stay static, link each other and say what the data-protection rules require.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PAGES = ["about", "privacy", "terms", "data-deletion"] as const;
const read = (name: string) => readFileSync(join(process.cwd(), "public", `${name}.html`), "utf-8"); // vitest runs in frontend/

describe("public pages", () => {
  it.each(PAGES)("%s is static, titled and links every page", (name) => {
    const html = read(name);
    expect(html).not.toMatch(/<script/i);
    expect(html).toMatch(/<title>Restro · [^<]+<\/title>/);
    expect(html).toContain('name="viewport"');
    // Relative links: the same files are served at "/" (console) and under a sub-path (GitHub Pages site).
    for (const other of PAGES) expect(html).toContain(`href="${other}.html"`);
    expect(html).not.toMatch(/href="\//);
  });

  it("the privacy policy states the processor role, the AI, processing outside India and STOP", () => {
    const html = read("privacy");
    expect(html).toMatch(/data fiduciary/);
    expect(html).toMatch(/Vertex AI/);
    expect(html).toMatch(/may be processed outside India/);
    expect(html).toMatch(/never your phone number/);
    expect(html).toMatch(/STOP/);
    expect(html).toMatch(/30 days/);
  });

  it("the deletion page says what is erased, what stays anonymous, and how to only stop offers", () => {
    const html = read("data-deletion");
    expect(html).toMatch(/can't be undone/);
    expect(html).toMatch(/chat messages/);
    expect(html).toMatch(/What stays, without your name or number/);
    expect(html).toMatch(/STOP/);
  });

  it("every open fact is a visible [TO FILL] marker, never an invented one", () => {
    const markers = PAGES.flatMap((p) => [...read(p).matchAll(/\[TO FILL: ([^\]]+)\]/g)].map((m) => `${p}: ${m[1]}`));
    expect(markers.length).toBeGreaterThan(0); // remove this check once legal fills them in
    expect(PAGES.every((p) => !/lorem|example\.com|TODO/i.test(read(p)))).toBe(true);
  });
});
