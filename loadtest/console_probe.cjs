// Console in the browser (docs/perf-test.md#console-in-the-browser): signs in with a demo role, idles on Live orders,
// drives simulator chat traffic, idles on Chats, then opens every screen. Reports main-thread time, React commits,
// requests by table and Realtime events per phase. Needs the local stack (api :8000 with the simulator) and a console.
// Usage: node loadtest/console_probe.cjs <label>
//   env: BASE (console URL, default http://localhost:5173), ROLE (Owner), IDLE_S (40), TRAFFIC_S (40),
//        CORS_SHIM=1 to serve a `vite preview` build from an origin the api doesn't allow (answers CORS in the browser).
// Writes loadtest/results/console-<label>.json. Synthetic simulator customers only.
const path = require("node:path");
const fs = require("node:fs");
const FRONT = path.join(__dirname, "../frontend");
const { chromium } = require(path.join(FRONT, "node_modules/playwright"));

const env = (f) => Object.fromEntries(fs.readFileSync(f, "utf8").split(/\r?\n/).map((l) => l.match(/^([A-Z0-9_]+)=(.*)$/)).filter(Boolean).map((m) => [m[1], m[2]]));
const SIM_TOKEN = env(path.join(FRONT, "../simulator/.env.local")).VITE_SIMULATOR_TOKEN;
const API = "http://localhost:8000";
const BASE = process.env.BASE ?? "http://localhost:5173";
const ROLE = process.env.ROLE ?? "Owner";
const IDLE_S = +(process.env.IDLE_S ?? 40);
const TRAFFIC_S = +(process.env.TRAFFIC_S ?? 40);
const label = process.argv[2] ?? "run";

async function sim(p, body) {
  const r = await fetch(`${API}/dev/sim${p}`, { method: "POST", headers: { "content-type": "application/json", "x-sim-token": SIM_TOKEN }, body: JSON.stringify(body ?? {}) });
  if (!r.ok) throw new Error(`sim ${p} ${r.status}`);
  return r.json();
}

function classify(url) {
  const u = new URL(url);
  if (u.pathname.startsWith("/rest/v1/")) return "supabase:" + u.pathname.slice(9).split("?")[0];
  if (u.pathname.startsWith("/auth/v1/")) return "auth:" + u.pathname.slice(9);
  if (u.port === "8000") return "api:" + u.pathname.replace(/[0-9a-f-]{36}/g, ":id");
  return null;
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.E2E_CHROMIUM || undefined });
  const page = await browser.newPage({ viewport: { width: 1366, height: 860 } });
  await page.addInitScript(() => {
    window.__commits = 0;
    window.__long = { n: 0, ms: 0 };
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
      supportsFiber: true, renderers: new Map(), isDisabled: false,
      inject() { return 1; }, checkDCE() {}, onScheduleFiberRoot() {}, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {},
      onCommitFiberRoot() { window.__commits++; },
    };
    try {
      new PerformanceObserver((l) => { for (const e of l.getEntries()) { window.__long.n++; window.__long.ms += e.duration; } })
        .observe({ type: "longtask", buffered: true });
    } catch {}
  });
  if (process.env.CORS_SHIM) {
    // The api only allows the dev origins; answer CORS for the preview origin so both builds see the same api.
    const origin = new URL(BASE).origin;
    const cors = { "access-control-allow-origin": origin, "access-control-allow-headers": "*", "access-control-allow-methods": "*", "access-control-allow-credentials": "true" };
    await page.route("http://localhost:8000/**", async (route) => {
      if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
      const res = await route.fetch({ headers: { ...route.request().headers(), origin: "http://localhost:5173" } });
      await route.fulfill({ response: res, headers: { ...res.headers(), ...cors } });
    });
  }
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");

  let reqs = {}, joins = 0, wsEvents = 0, errors = [];
  page.on("request", (r) => { const k = classify(r.url()); if (k) reqs[k] = (reqs[k] ?? 0) + 1; });
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message.slice(0, 200)));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text().slice(0, 200)); });
  page.on("websocket", (ws) => {
    ws.on("framesent", (f) => { if (typeof f.payload === "string" && f.payload.includes('"phx_join"')) joins++; });
    ws.on("framereceived", (f) => { if (typeof f.payload === "string" && f.payload.includes('"postgres_changes"')) wsEvents++; });
  });

  const metrics = async () => Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));
  async function phase(name, seconds, during) {
    reqs = {}; joins = 0; wsEvents = 0; errors = [];
    const m0 = await metrics();
    const p0 = await page.evaluate(() => ({ c: window.__commits, l: { ...window.__long } }));
    const t0 = Date.now();
    const work = during ? during() : null;
    await page.waitForTimeout(seconds * 1000);
    if (work) await work.catch((e) => errors.push("traffic: " + e.message));
    const secs = (Date.now() - t0) / 1000;
    const m1 = await metrics();
    const p1 = await page.evaluate(() => ({ c: window.__commits, l: { ...window.__long } }));
    const total = Object.values(reqs).reduce((a, b) => a + b, 0);
    const out = {
      phase: name, seconds: +secs.toFixed(1),
      reactCommitsPerSec: +((p1.c - p0.c) / secs).toFixed(2),
      scriptMsPerSec: +(((m1.ScriptDuration - m0.ScriptDuration) * 1000) / secs).toFixed(1),
      taskMsPerSec: +(((m1.TaskDuration - m0.TaskDuration) * 1000) / secs).toFixed(1),
      longTasks: p1.l.n - p0.l.n, longTaskMs: Math.round(p1.l.ms - p0.l.ms),
      heapMB: +(m1.JSHeapUsedSize / 1e6).toFixed(1), nodes: m1.Nodes,
      requests: total, requestsPerMin: Math.round((total / secs) * 60),
      realtimeJoins: joins, realtimeEvents: wsEvents,
      top: Object.entries(reqs).sort((a, b) => b[1] - a[1]).slice(0, 12),
      errors: [...new Set(errors)].slice(0, 8),
    };
    console.log(JSON.stringify(out));
    return out;
  }

  const results = { label, base: BASE, role: ROLE, phases: [], nav: [] };
  const t = Date.now();
  await page.goto(BASE);
  await page.getByRole("button", { name: ROLE, exact: true }).click();
  await page.getByRole("heading", { name: /^(Live orders|Today)$/ }).waitFor({ timeout: 30_000 });
  results.loginMs = Date.now() - t;
  if (await page.getByRole("button", { name: "Not now" }).isVisible().catch(() => false)) await page.getByRole("button", { name: "Not now" }).click();
  await page.getByRole("link", { name: /Live orders/ }).first().click();
  await page.waitForTimeout(5000);

  results.phases.push(await phase("orders-idle", IDLE_S));
  results.phases.push(await phase("orders-traffic", TRAFFIC_S, async () => {
    const custs = [];
    for (let i = 0; i < 6; i++) custs.push((await sim("/customers")).wa_id);
    const end = Date.now() + (TRAFFIC_S - 5) * 1000;
    let i = 0;
    while (Date.now() < end) {
      await sim("/send", { wa_id: custs[i % custs.length], kind: "text", text: i % 2 ? "can I see the menu" : "hi" }).catch(() => {});
      i++;
      await new Promise((r) => setTimeout(r, 1500));
    }
  }));
  await page.getByRole("link", { name: /Chats/ }).first().click();
  await page.waitForTimeout(3000);
  results.phases.push(await phase("chats-idle", Math.round(IDLE_S / 2)));

  // Screen walk: time from click to the screen's data settling (no supabase request for 500 ms).
  const screens = ["Live orders", "Calls and bills", "Chats", "Menu", "Service report", "Today", "Customers", "Campaigns", "Analytics", "Setup", "Performance"];
  for (const s of screens) {
    const link = page.getByRole("link", { name: new RegExp(`^${s}`) }).first();
    if (!(await link.isVisible().catch(() => false))) continue;
    let last = Date.now(), n = 0;
    const on = (r) => { if (classify(r.url())) { last = Date.now(); n++; } };
    page.on("request", on);
    const t0 = Date.now();
    await link.click();
    while (Date.now() - last < 500 && Date.now() - t0 < 15_000) await page.waitForTimeout(100);
    await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
    page.off("request", on);
    const blank = await page.evaluate(() => (document.getElementById("root")?.innerText ?? "").trim().length === 0);
    results.nav.push({ screen: s, settleMs: last - t0, requests: n, blank });
  }
  console.log(JSON.stringify({ nav: results.nav, loginMs: results.loginMs }));
  const outDir = path.join(__dirname, "results");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, `console-${label}.json`), JSON.stringify(results, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
