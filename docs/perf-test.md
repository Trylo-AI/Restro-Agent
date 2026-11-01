# Performance test: backend code and database

Measured on 25 Oct 2026 with:
- the [app evaluation](app-eval.md): 215 cases and 464 customer messages through the real system, 8 customers at a time;
- a console API benchmark;
- the console in the browser (1 Nov 2026, [below](#console-in-the-browser)).

The aim is to know where a request's time goes: our code, database reads and writes, the AI call, or sending. That shows what the Phase 3 gate on staging (p95 ≤ 5 s at 100 customers) depends on.

## Read this first: where it ran

The api and worker ran on a laptop in India against the Supabase database in Mumbai, over the internet.

- **Round trip per database statement from here:** 96–300 ms.
- **In the same region (Cloud Run `asia-south1` to Supabase Mumbai):** about 1–2 ms (**[verify]** in Phase 3).

So every **wall-clock** number below is dominated by distance, not by the code or the database. To keep the figures honest:

- **Database time is measured inside Postgres** (`pg_stat_statements`), with no network.
- **Our own code time** is a request's duration minus its database, AI and HTTP time (ops tracing).
- **Cloud Run figures are a projection:** the number of statements × the in-region round trip, plus the measured execution, AI and send times.

## Summary

| Unit of work | Statements | Our code | DB execution (in Postgres) | Measured here | Projected in-region |
|---|---|---|---|---|---|
| Webhook (store, queue, 200) | 12 | 3 ms | ~2 ms | p50 2.4 s | **~30–50 ms** (budget < 300 ms) |
| Status webhook (sent / delivered / read) | 10 | ~3 ms | ~1 ms | p50 1.2 s | ~20–30 ms |
| Button or keyword turn (no AI) | 6–26 | 36 ms | ~5 ms | p50 1.9 s | ~0.1 s + send |
| AI turn (free text) | 51 (p95 56) | see note | ~15 ms | p50 27 s | ~0.1–0.15 s DB + AI + sends |
| Console write (availability, pause, ack) | 6–9 | < 20 ms | 1–3 ms (more when contended) | p50 0.4–0.8 s | ~20–40 ms |
| Console read through Supabase (live orders, menu, views) | 1 request | n/a | < 1 ms | p50 98–209 ms | ~5–20 ms + the browser's distance |

**Projected AI turn on Cloud Run:**

> 1 s debounce + ~0.15 s DB + AI call (Gemini target p50 1–1.5 s; qwen3.8-max measured 2.9 s from India) + WhatsApp sends (~0.3–0.5 s each) ≈ **p50 2.5–3.5 s**

That is at the edge of the turn budget (p50 < 3 s, p95 < 5 s). **The AI call and the sends decide it, not our code or the database.** The Phase 3 load test on staging measures it for real.

**Our code is not a bottleneck:** 3 ms per webhook and 36 ms per router turn. In AI turns the unexplained time (p50 4.1 s here) is waiting for one of the worker's 5 pooled connections while slow statements hold them; that's the [pool finding](#findings).

## AI turn breakdown (60 turns, measured here)

| Stage | p50 | What's in it |
|---|---|---|
| `turn.load` | 2.5 s | Unprocessed messages, café, conversation and customer context (round trips) |
| `turn.route` | 11.2 s | Router plus the AI call (2.9 s). The rest is round trips: prompt context, menu, guardrails |
| `turn.commit` | 2.6 s | One transaction: fence, state, outbound step rows |
| `turn.send` | 7.7 s | One send per reply (2 per turn), each with status bookkeeping |
| `turn.devhooks` | 2.0 s | Simulator hooks; local only, not in staging or prod |
| Total | 27.5 s (p95 64 s) | DB statements: p50 51, max 58 |

## Database: reads and writes, measured inside Postgres

Over the app evaluation, from `loadtest/db_stats.py diff app-eval-start app-eval-end`:

| | Statements (distinct) | Calls | Mean execution |
|---|---|---|---|
| Reads | 59 | 9,447 | **0.11 ms** |
| Writes | 30 | 12,946 | 5.7 ms (0.1–0.9 ms without lock waits, see below) |
| Pool checkouts (pooler resets) | | 27,503 | About 59 per customer message, one per statement |

**Most frequent statements per customer message:**

| Per message | Mean | Statement |
|---|---|---|
| 3.4 | 0.27 ms | `insert into webhook_events …` and `update webhook_events set processed_at …` (inbound and status webhooks) |
| 3.4 | 0.04 ms | Café lookup by `phone_number_id` (every webhook) |
| 2.5 | 0.03 ms | `update campaign_recipients … where wamid = $2` (**every status webhook, unindexed**) |
| 2.1 | **68 ms** | Lease renewal `update conversations set lease_expires_at …` (lock wait, see below) |
| 1.2 each | 0.1–4.3 ms | `wa_messages` step insert, attempt claim and status updates |
| 0.9 each | ~0.6 ms | Customer, conversation, inbound and work-queue upserts (inbound webhook) |

**Query plans** (`EXPLAIN`, and `EXPLAIN ANALYZE` rolled back):

| Query | Plan |
|---|---|
| Turn load: unprocessed inbound messages | Index `inbound_messages_unprocessed` ✅ |
| Sweep: overdue work | Index `pending_work_one_pending` ✅ |
| Sweep: never-attempted sends | Index `wa_messages_recovery` ✅ |
| Lease renewal | Primary key, **0.09 ms** execution ✅ (the 68 ms average is waiting, not work) |
| Status webhook → `campaign_recipients` by `wamid` | **Seq scan** ❌ (no index on `wamid`) |

## Console API

From `loadtest/perf_api.py` on the seeded café: 16 sequential calls and a burst of 10 concurrent calls per endpoint. Every call succeeded. "Server" comes from the api's own trace.

| Endpoint | Kind | Client p50 / p95 | Burst of 10: per call | Server p50 | Statements | DB time (here) |
|---|---|---|---|---|---|---|
| Live orders (Supabase: orders + items + table) | read | 134 / 216 ms | 91 ms | n/a (PostgREST) | 1 request | n/a |
| Menu (Supabase) | read | 98 / 160 ms | 24 ms | n/a | 1 request | n/a |
| Customer cards, 50 (Supabase view) | read | 209 / 285 ms | 121 ms | n/a | 1 request | n/a |
| Revenue by day (Supabase view) | read | 114 / 256 ms | 39 ms | n/a | 1 request | n/a |
| Audience preview (FastAPI) | read | 581 / 2,322 ms | 147 ms | 640 ms | 6 | 641 ms |
| Dish availability off/on | write | 833 / 1,294 ms | 292 ms | 975 ms | 9 | 953 ms |
| Pause / resume ordering | write | 576 / 810 ms | 203 ms | 661 ms | 7 | 656 ms |
| Acknowledge all notifications | write | 417 / 658 ms | 125 ms | 458 ms | 6 | 453 ms |
| Console telemetry | write | 175 / 774 ms | 65 ms | 213 ms | 2 | 203 ms |

- **Server time ≈ DB time ≈ statements × the 96 ms round trip** on every FastAPI endpoint, so the code adds almost nothing.
- **Supabase reads are single requests** and take < 1 ms in Postgres. Their time here is the internet.
- **Contention under the burst is serialisation by design:**
  - `next_menu_version()` averaged 77 ms because 10 concurrent menu writes queue on the café's counter row.
  - The pause toggle averaged 55 ms because 10 concurrent toggles queue on one `cafes` row.

  Both are one café's own writes queuing as designed. Real use doesn't send 10 at once.

## Console in the browser

Measured on 1 Nov 2026 with `loadtest/console_probe.cjs` (headless Chromium, owner, seeded café with 38 live orders: 31 New, 6 Accepted, 1 Ready). Phases: 40 s idle on Live orders, 40 s on Live orders while 6 simulated customers send a message every 1.5 s, 20 s idle on Chats, then a walk through every screen. Production builds (`vite preview`), before and after the fixes below.

**Reported problems:** the console felt slow, and sometimes went white until reloaded.

| Phase (production build) | Before | After |
|---|---|---|
| Live orders idle: JavaScript per second | 29.7 ms | 19.3 ms |
| Live orders under chat traffic: requests per minute | 245 | 156 |
| Chats idle (after traffic): requests per minute | 435 | 204 |
| Live orders opened: settled / requests | 1,332 ms / 17 | 430 ms / 6 |
| Chats opened: settled / requests | 1,479 ms / 10 | 297 ms / 4 |
| Initial JavaScript download | 659 kB (187 kB gzip) | 540 kB (156 kB gzip) |
| Sign-in to first screen, median of 5 | 1.78 s | 1.65 s |

On the dev server (`npm run dev`, React development build, which is what local testing uses) Live orders idled at 191 ms of JavaScript per second with 7.4 s of long tasks per 40 s; after the fixes, 72 ms and 2.3 s.

**Causes and fixes:**

1. **Live orders re-rendered every card every second, and each render was costly.** `clock()` built a new `Intl` formatter per call (`toLocaleTimeString`) for each escalation line, so 31 cards × 2 escalations each second dominated the profile. Fix: one cached formatter (`lib/format.ts`).
2. **Every Realtime event refetched its whole query, once per subscribing hook.** Each `useLive` call opened its own channel (orders was subscribed 3 times), and the Chats list refetched four queries (up to 2,000 message rows) on every message and every sent, delivered and read update. Fix: one shared channel per query; first event refetches at once, the rest of a 1 s burst becomes one trailing refetch; the Chats list watches only new messages ([realtime](data-and-api.md#realtime), [decision](decisions.md#smaller-decisions)).
3. **App-wide re-renders.** The alerts context and the outlet context were new objects every second, and the toast API was a new object on every render (four times a second during an Undo), so the shell and the open screen re-rendered with them. Fix: stable context values; the Undo countdown ticks in its own component.
4. **White screen: no error boundary.** Any render error unmounted the whole app. Fix: a screen boundary inside the shell and a root boundary with Reload ([console](console.md#errors-and-loading)).
5. **"No access to any café" after waking a laptop.** A failed memberships read (re-run on tab focus) was treated as no memberships. Fix: keep the café, retry every 5 s.
6. **White screen after a deploy (production).** `index.html` was cacheable for an hour, so an old page asked for script files the deploy had removed. Fix: `no-cache` on HTML and routes in `firebase.json`, plus a one-time reload when a script fails to load. **[verify]** the headers with `curl -I` after the next deploy.
7. **Health ping without a timeout** could pile up requests while `api` hung. Fix: 4 s timeout, one ping at a time.

**Still true:** each card renders once a second because New orders show a seconds timer; with the formatter fixed, the whole idle screen with 31 cards costs 19 ms of JavaScript a second in production. `/healthz` every 1.5 s (40 a minute per open console) is the cost of the 5 s offline banner.

## Findings

Ordered by impact on the pilot.

1. **Turn time is the AI call plus the sends, multiplied by statement count over distance.** An AI turn makes about 51 statements, each its own pool checkout. That's fine in-region (~0.1 s) and is the whole reason local numbers are slow.
   - **Action:** measure in Phase 3. Trimming statements is optional; the obvious ones are below.
2. **The status webhook scans `campaign_recipients`.**
   - Every sent, delivered and read webhook (2–3 per outbound message, campaign or not) runs `update campaign_recipients … where wamid = $2` with no index. It's harmless at 4 rows, but grows with every campaign.
   - **Fix (team; `domain/campaigns` and schema):** a partial index on `campaign_recipients (wamid) where wamid is not null`, or skip the update unless the message is a campaign send (`wa_messages.idempotency_key` starts with `campaign:`).
3. **Status webhooks are most of the webhook traffic.** 1,195 of the 1,620 webhooks here, against 425 inbound messages. In production Meta sends sent, delivered and read for each reply, about 6–9 per customer message, 10 statements each.
   - **Action:** size `api` and the pool for webhooks per message, not customers.
   - Cheap savings: cache the café lookup by `phone_number_id`, and the campaign check in finding 2.
4. **The worker pool (5) is the limit under load.** With 8 customers at once, AI turns spent p50 4.1 s waiting for a connection here. In-region each statement holds a connection for ~1 ms instead of ~300 ms, so the same pool goes far further.
   - **Action:** check pool size against worker concurrency in the Phase 3 load test.
5. **The lease renewal waits on the commit's row lock.** It averaged 68 ms against 0.09 ms of actual work. It waits while the turn's commit transaction holds the conversation row, and that transaction spans several round trips here (2.6 s).
   - It's harmless: a 30 s lease renewed every 10 s leaves plenty of margin, and in-region the commit takes milliseconds.
   - **Action:** keep commit transactions free of network calls, as they are now.
6. **A connection drop puts the app's pool in a bad state.** During the two outages asyncpg raised `InternalClientError` ("release() called on a free connection holder") in the worker. Retries and the sweep recovered everything ([app evaluation](app-eval.md#reliability-two-outages-during-the-run)).
   - **Action (team):** track it in staging, and confirm with the kill-a-worker test.
7. **Small tables are read by sequential scans:** `memberships` (4 rows, 16k scans from RLS checks), `cafes`, `consents`. That's optimal at this size; indexes exist where they matter.
   - **Action:** re-check plans with pilot-sized data, e.g. 50k orders and a full month of messages.
8. **Monitoring: trace writes lag during an outage, then catch up.** The recorder batches off the request path. During the drop `ops_runs` showed runs stuck as `RUNNING` and nothing new. Afterwards every one was completed and none was left `RUNNING` or `ABANDONED`. That's expected, but worth knowing when reading the dashboard right after an incident.

## How to run it

```text
backend/.venv/Scripts/python.exe loadtest/db_stats.py snapshot before
backend/.venv/Scripts/python.exe evals/app_run.py --concurrency 8        # or loadtest/run.py --scenario peak
backend/.venv/Scripts/python.exe loadtest/db_stats.py snapshot after
backend/.venv/Scripts/python.exe loadtest/db_stats.py diff before after --json loadtest/results/db.json
backend/.venv/Scripts/python.exe loadtest/perf_api.py --n 16 --burst 10   # console API; reversible writes only
node loadtest/console_probe.cjs dev                                         # console in the browser, dev server :5173
cd frontend && npx vite build && npx vite preview --port 4173               # then, from the repo root:
CORS_SHIM=1 BASE=http://localhost:4173 node loadtest/console_probe.cjs prod # production build
```

Reports from this run are in `loadtest/results/`:
- `db-app-eval.json` and `db-app-eval-tables.json` (database side of the app evaluation);
- `db-perf-api.json` (database side of the API benchmark);
- `20261025-102757-perf-api.json` (API benchmark).

`results/` is git-ignored, so the numbers above are the record. Snapshots read only statistics views, and the benchmark's writes are undone at the end.
