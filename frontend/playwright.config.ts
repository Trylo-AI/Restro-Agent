// End-to-end against the real local stack: api :8000, worker :8001, console :5173 (scripts/dev.ps1).
// Secrets come from the git-ignored env files; nothing is printed.
import { defineConfig, devices } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";

function envFile(path: string): Record<string, string> {
  if (!existsSync(path)) return {};
  return Object.fromEntries(readFileSync(path, "utf8").split(/\r?\n/)
    .map((l) => l.match(/^([A-Z0-9_]+)=(.*)$/)).filter((m): m is RegExpMatchArray => !!m).map((m) => [m[1], m[2]]));
}

const consoleEnv = envFile("./.env.local");
const simEnv = envFile("../simulator/.env.local");
process.env.E2E_STAFF_PASSWORD ??= consoleEnv.VITE_DEMO_PASSWORD;
process.env.E2E_SIM_TOKEN ??= simEnv.VITE_SIMULATOR_TOKEN;
process.env.E2E_API_URL ??= consoleEnv.VITE_API_URL ?? "http://localhost:8000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    // An already-downloaded Chromium can be used instead of the pinned one (slow links).
    launchOptions: process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {},
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
  ],
});
