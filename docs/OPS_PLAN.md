# Performance dashboard plan: Restro

Commit: `d28e61d` + S16 work in progress   Date: 24 Oct 2026   Standard: PDS v1.0 (Performance Dashboard Standard)
Docs read: `CLAUDE.md`, [architecture](architecture.md) (System, Latency budget, Message pipeline, Queues, Monitoring, Cost), [backend](backend.md), [data-and-api](data-and-api.md), [rules](rules.md), [plan](plan.md#open-decisions), [ordering-slice](ordering-slice.md), [checklist](ordering-slice-checklist.md)
Code areas read: `backend/app/main_api.py`, `main_worker.py`, `api/*`, `core/logging.py`, `db/pool.py`, `db/tx.py`, `domain/conversation/pipeline.py`, `domain/ai_agent/calls.py`, `domain/outbound`, `domain/jobs.py`, `domain/timers`, `integrations/llm/*`, `integrations/whatsapp/client.py`, `integrations/tasks/local.py`

**Status: awaiting approval (Gate 0).** Nothing below is built. Answer §6 and §7, then I build phase by phase.

## 1. The app in brief

Restro takes café orders on WhatsApp. A customer scans a table QR, orders through an AI agent, and staff run service in the Restro Console. Two people wait on it: **the customer** (every message should get its reply in seconds) and **staff** (a new order must reach the console and be acted on within a minute).

What the docs say matters:

- **Done** = 30+ orders in one evening at a real café, **zero lost or wrong** (`CLAUDE.md`). Reliability beats features.
- **Latency budget** ([architecture](architecture.md#latency-budget)): webhook → 200 in < 300 ms; read + typing < 1 s; LLM p50 ~1–1.5 s, p95 ≤ 3 s; **turn p50 < 3 s, p95 < 5 s, button turns < 1 s**.
- **Alerts already designed** ([monitoring](architecture.md#monitoring)): dead letters, ambiguous sends, LLM p95 > 4 s or errors > 2 %, LLM spend, stuck inbound, lease steals, handoff rate, time to confirm, turns per order.
- **Cost**: LLM tokens are the largest variable cost; every LLM call is already metered in `llm_calls`.

Today nothing records how long a turn took, where the time went, or which attempt failed. The S16 run of 10 simultaneous customers failed with timeouts, and finding out why meant reading logs that have no timings (§7, Q-B).

## 2. Discovery

### Stack

| Concern | Choice | Where |
|---|---|---|
| Language / framework | Python 3.12, FastAPI, two apps from one image | `backend/app/main_api.py`, `main_worker.py` |
| DB | Supabase Postgres via asyncpg pool (min 1, max 5; `statement_cache_size=0` for the pooler) | `app/db/pool.py:22`, `app/db/tx.py` (`conn()`, `tx()`) |
| SQL style | Raw SQL in `db/repositories/*` | `app/db/repositories/` |
| Migrations | Supabase CLI, `supabase/migrations/000N_*.sql`; schema contract in `contracts/schema.sql` (frozen) | `supabase/migrations/` |
| Outbound HTTP | `httpx.AsyncClient` (WhatsApp client, simulator hooks, local task shim) | `app/integrations/whatsapp/client.py`, `core/devhooks.py`, `integrations/tasks/local.py` |
| LLM | `google-genai` (Developer API now, Vertex later) behind one call site | `app/integrations/llm/gemini.py`, `app/domain/ai_agent/calls.py:61` |
| Queue / timers | Pub/Sub signal + Postgres `pending_work`; Cloud Tasks for `wa-ack`, timers, profile notes; in-process shims locally | `integrations/gcp/*`, `integrations/tasks/local.py` |
| Logging | structlog JSON to stdout, scrubber drops text / phone / token keys | `app/core/logging.py:14` |
| Config | pydantic-settings from `backend/.env` | `app/core/config.py` |
| Tests | pytest (unit + integration against the hosted dev project), Vitest, Playwright | `backend/tests`, `frontend/src`, `frontend/e2e` |
| Deploy | Cloud Run `api` (public) + `worker` (internal), `asia-south1`; Firebase Hosting for the console. Not deployed yet: local only | [architecture](architecture.md#infrastructure) |

### Entry points

| Kind | Name or route template | Handler | Volume (pilot, one café, one evening) |
|---|---|---|---|
| message | `POST /webhooks/whatsapp` (inbound messages **and** status callbacks) | `app/api/webhooks.py:33` | ~300 inbound + ~900 statuses |
| http | Staff REST: `POST /orders/{order_id}/transition`, `/cancel`, `/undo`, `/delay`, `/orders/manual`, `/requests/{request_id}/ack`, `/resolve`, `/conversations/{conversation_id}/takeover`, `/reply`, `/handback`, `/conversations/open`, `/bills/{table_session_id}/deliver`, `/menu/{item_id}/availability`, `/cafes/{cafe_id}/pause-ordering`, `/customers/{customer_id}/notes`, `/customer-notes/{note_id}/undo`, `/customers/{customer_id}/preferences/remove`, `/notifications/*`, `/telemetry/console` | `app/api/staff/*.py` | ~300 |
| job | Turn: `POST /internal/pubsub/inbound` → lease → `pending_work` rows | `app/api/internal/pubsub.py:28` → `domain/conversation/pipeline.py` | ~300 turns |
| job | `POST /internal/tasks/ack` (mark read + typing) | `app/api/internal/tasks.py:22` | ~300 |
| job | `POST /internal/tasks/timer` (`status_message`, `accept_escalation_1/2`, `overdue`, `auto_served`, `help_escalation`, `idle_handback`, `work_retry`, `send_check`) | `app/api/internal/tasks.py:15` | ~400 |
| job | `POST /internal/tasks/profile-note` | `app/api/internal/tasks.py:39` | ~10 |
| cron | `POST /internal/jobs/{job}` (`stuck_inbound_check`, `llm_spend_check`, …); locally `jobs.local_loop` | `app/api/internal/jobs.py:7`, `app/domain/jobs.py:59` | ~1/min |
| — | `/dev/sim/*` (simulator, local only) | `app/api/dev/sim.py` | **not traced** |

### Units of work

- **Hierarchy:** visit (`table_session`) → **subject** = conversation turn (`pending_work` row) / order / request / customer note → **run** (one attempt) → stage → step → call.
- **Attempts:** turns retry from the `pending_work` row (`attempts`, `next_attempt_at`, 2 / 4 / 8 s), then `dead_lettered` (`pipeline.py:144`). Cloud Tasks retry timers and acks. A lost lease means the turn is dropped and re-run by the next holder (→ `SUPERSEDED`).
- **Final status lives in:** `pending_work.status` (`done`, `dead_lettered`, `resolved`), `wa_messages.status`, `orders.status`. These stay the source of truth; ops rows only observe them.
- **Batch:** none in this app (campaigns will be, later). The Batches page is dropped.

### Stages per unit of work

**Turn** (`kind=job`, name `turn`), in order, no parallel stages:

1. `turn.claim`: lease claim (short transaction)
2. `turn.debounce`: wait until `pending_work` is due (1 s for free text). Recorded as wait, not work.
3. `turn.load`: unprocessed `inbound_messages` + turn context
4. `turn.route`: routing order 1–9 → deterministic flow **or** `turn.agent` (prompt build → `llm.call` → guardrails → cart ops)
5. `turn.commit`: fenced transaction (state, `processed_at`, outbound rows, orders via `transition()`)
6. `turn.send`: one `wa.send` call per outbound step, after commit
7. `turn.release`

**Webhook** (`kind=message`): `webhook.verify` → `webhook.store` (event + inbound + `pending_work`, one transaction) → `webhook.signal` (publish + enqueue ack). Status callbacks: `webhook.verify` → `webhook.status`.

**Staff action** (`kind=http`): `auth` → domain call (`orders.transition`, …) → `after_status_change` (enqueue status message).

### Dependencies

| Kind | Target | Call sites |
|---|---|---|
| db | Supabase Postgres (transaction pooler) | every `db/repositories/*` call through `conn()` / `tx()` |
| llm | Gemini `gemini-3.5-flash`, `gemini-3.5-flash-lite` | `domain/ai_agent/calls.py:61` (single place) |
| http_out | `graph.facebook.com` (WhatsApp send, mark read) or the simulator | `integrations/whatsapp/client.py` |
| queue | Pub/Sub publish, Cloud Tasks enqueue (local: in-process shims) | `integrations/gcp/*`, `integrations/tasks/local.py` |
| http_out | Supabase Auth JWKS (JWT check) | `core/auth.py:92` |

### Concurrency and claiming

Async FastAPI, one process per service locally (Cloud Run: concurrency 40–80, max 3–5 instances). Turns are serialised per conversation by the lease (`lease.py:25`, 30 s, renewed every 10 s) and run in parallel across conversations. DB pool max 5 per process (`pool.py:22`); no transaction is held across the LLM call or a send.

### Existing telemetry and its gaps (reuse what exists)

| Need | Today | Gap |
|---|---|---|
| Per-call LLM latency, tokens, cost, outcome | `llm_calls` (every attempt, priced, `pricing_version`) | Not linked to a turn run; the dashboard reads it by `conversation_id` + time. **Reused as the `llm.call` span source; not duplicated.** |
| Send outcome | `wa_messages.status`, `error_code`, `attempted_at`, `sent_at`, cost columns | No send latency |
| Retry / dead letter | `pending_work.attempts`, `last_error`, `dead_lettered_at` | Earlier attempts' errors are overwritten |
| Logs | structlog JSON, ids only, `service` bound | No `run_id`, no durations, no file; uvicorn access lines unstructured |
| Turn timings | none (only `dev_turn_traces`, local, LLM text) | **Everything in Q4/Q5** |
| Request ids | Docs say `request_id` is passed into Pub/Sub and Cloud Tasks ([monitoring](architecture.md#monitoring)) | **Not in the code**; `run_id` + `traceparent` fills this |
| Console telemetry | `POST /telemetry/console` (Realtime disconnects) | Kept as is; shown on Overview later |

### Sensitive data, and how the app redacts today

Message text, phone numbers, `wa_id`, names, staff comments, LLM prompts and outputs are personal or sensitive. `core/logging.py` drops fields named `text`, `body`, `payload`, `prompt`, `note`, `wa_id`, `phone`, `token`, … The tracer reuses that list and adds value redaction (digits runs ≥ 10 → `[phone]`, emails, `Bearer …`, `sig=`). **No message text, prompt, completion, phone or `wa_id` is ever stored in an `ops_` table** — ids only (`conversation_id`, `order_id`, `wamid`).

### Volume estimate

Pilot (one café): ~2,500 runs/day × ~15 spans (L3 DB calls dominate: a turn makes 10–25 queries) ≈ 40k span rows/day ≈ **1.2 M/month**; events ≈ 0–2 per run. Ten cafés ≈ 12 M/month → L3 DB compaction (§5) and sampling become necessary then, not now.

## 3. What will be tracked

### Runs

| Entry point | kind | name | subject_type | Outcome mapping | Status |
|---|---|---|---|---|---|
| Inbound webhook | message | `webhook.inbound` / `webhook.status` | — | 200 → SUCCEEDED; 401 bad signature → REJECTED; duplicate `wamid` → SUCCEEDED + count `duplicate=1`; 5xx → FAILED | planned |
| Staff REST | http | route template, e.g. `POST /orders/{order_id}/transition` | order / request / conversation / customer | §4.4 HTTP mapping; 409 / 422 → REJECTED | planned |
| Turn | job | `turn` | `pending_work` (id) | done → SUCCEEDED(_DEGRADED); error with retries left → RETRYING; dead letter → FAILED; lease lost → SUPERSEDED; process died → ABANDONED (sweeper) | planned |
| wa-ack | job | `task.ack` | inbound message | as job | planned |
| Timers | job | `timer.<name>` (`timer.status_message`, …) | order / request / wa_message | ran → SUCCEEDED; state changed so it no-ops → SUCCEEDED + `counts.noop=1` | planned |
| Profile note | job | `task.profile_note` | customer note | applied / no_changes → SUCCEEDED; failed → SUCCEEDED_DEGRADED (`profile_note_failed`) | planned |
| Scheduled jobs | cron | `job.<name>` | — | as job | planned |

`queued_at`: `pending_work.created_at` for turns (exact), Cloud Tasks schedule time for timers, `received_at` for acks.

### Spans: the registry (`backend/app/ops/tracing/names.py`)

| Name | Level | Kind | Where | Attrs | Q |
|---|---|---|---|---|---|
| `webhook.verify` | 1 | internal | `api/webhooks.py:33` | `ok` | Q4 |
| `webhook.store` / `webhook.status` / `webhook.signal` | 1 | internal | `domain/conversation/ingress.py:22`, `:52` | `messages`, `duplicate` | Q4, Q5 |
| `turn.claim` / `turn.load` / `turn.route` / `turn.commit` / `turn.send` / `turn.release` | 1 | internal | `pipeline.py:_process` | `messages`, `route` (`button`, `keyword`, `agent`, …), `steps` | Q4, Q5 |
| `turn.debounce` | 1 | internal | `pipeline.py` | `waited_ms` (shown as wait) | Q4 |
| `agent.prompt` / `agent.guardrails` / `agent.apply` | 2 | internal | `domain/ai_agent/*`, `conversation/agent_turn.py` | `prompt_chars`, `ops`, `rejected` | Q5 |
| `orders.transition` | 2 | internal | `domain/orders/transition` | `from`, `to` | Q5 |
| `db.query` | 3 | db | hook on `conn()` / `tx()` (asyncpg) | `db.operation`, `db.table`, `db.statement_hash`, `db.rows` | Q5, Q9 |
| `db.tx` | 2 | db | `tx()` | `statements` | Q5 |
| `llm.call` | 3 | llm | `ai_agent/calls.py:61` | `llm.model`, `tokens_in/out/cached`, `cost_usd`, `outcome`, `llm_call_id` | Q5, Q8 |
| `wa.send` / `wa.mark_read` | 3 | http_out | `integrations/whatsapp/client.py` | `http.status`, `send_class`, `step_id`, `wamid` | Q5 |
| `queue.publish` / `queue.enqueue` | 3 | queue | `integrations/gcp/*`, `tasks/local.py` | `queue.name` | Q5 |
| `auth.jwt` | 2 | http_out | `core/auth.py` | `cache_hit` | Q5 |

### Degradation reasons (the app's silent paths)

| Reason code | Where | What happens silently | cost_only? |
|---|---|---|---|
| `llm_fallback_model` | `ai_agent/calls.py:66` loop, 2nd attempt | Primary model failed (error / timeout / 429 / invalid); answered by the other model | no |
| `llm_rate_limited` | `integrations/llm/gemini.py:50` | 429 from Gemini | **yes** |
| `llm_handoff` | `calls.py` returns `None` | Both attempts failed → `system_error` handoff to staff | no |
| `llm_kill_switch` | `calls.py:33` | `cheap` / `handoff` mode active | no |
| `guardrail_reply_dropped` | `ai_agent/guardrails.py:31` (amount scan) | The model's reply contained an amount and was replaced | no |
| `cancel_draft_fallback` | `ai_agent/cancel_draft.py:28` | AI cancellation text rejected → fixed text | no |
| `send_ambiguous` | `domain/outbound/__init__.py:129` | Send timed out after leaving; left for `send_check` | no |
| `send_retry_429` | `domain/outbound/__init__.py:126` | Meta 429 (`NOT_SENT_RETRY`), retried with backoff | **yes** |
| `send_template_fallback` | `domain/outbound/__init__.py:78` | Window closed → status template instead of free text | no |
| `sold_out_dropped` | `conversation/flows.py:106`, `confirm.py:55` | Dish sold out between summary and Confirm, removed with a notice | no (business, counted) |
| `publish_failed_sweep` | `api/internal/pubsub.py:24`, `ingress.py:40` | Signal failed; `stuck_inbound_check` will re-signal | no |
| `profile_note_failed` | profile-note task | Comment unread, profile unchanged | no |
| `ack_failed` | `api/internal/tasks.py:34` | Read receipt / typing not shown | **yes** |

### Run fields

- `input_size`: turns = inbound messages in the turn (`messages`); LLM-bearing turns also record `prompt_chars` as an attr.
- `counts`: `messages`, `outbound_steps`, `orders_created`, `llm_calls`, `db_calls`.
- `labels` (low-cardinality): `route` (`button` / `keyword` / `agent` / `flow`), `llm_model`, `provider` (`developer_api` / `vertex` / `stub`), `transport` (`simulator` / `meta`).
- `tenant_id` = `cafe_id`.
- `settings` / `config_hash`: `llm_model_primary`, `llm_model_fallback`, `llm_timeout_seconds`, debounce, `LEASE_SECONDS`, pool size, `tasks_backend`.

### Cost

| Dependency | Price unit | Source | Price |
|---|---|---|---|
| Gemini | per 1M tokens in / cached / out | **existing** `integrations/llm/pricing.py` (`pricing_version`) — reused, not duplicated in `OPS_PRICES` | **Needs Gemini 3.5 prices** (table has 2.5 only; 3.5 is priced at the Flash default) |
| WhatsApp | per billable message by category | `wa_messages.cost` (estimated → actual) | Service messages free inside the window; utility templates **[verify]** |
| Compute | — | not tracked in v1 | — |

### Resources and logs

- Resource sampling: **off** in v1 (Cloud Run already reports CPU / memory; pool usage is the one useful sample — added as `db_pool_in_use` on a 15 s sampler if you want it, P13).
- Events: WARNING and above during a run → `ops_run_events` (scrubbed).
- Logs: local dev writes `backend/logs/{api,worker}.jsonl` (rotating, git-ignored) for the Logs page; in Cloud Run logs stay in Cloud Logging and the Logs page links out by `run_id`.

### Deliberately not tracked, and why

- `/dev/sim/*` and `/healthz` (noise; local-only tooling).
- Message text, prompts, completions, names, phones (`CLAUDE.md` invariant 12).
- The console (browser) side beyond the existing `/telemetry/console`.
- GPU, cache, storage (none in this app).

## 4. What the dashboard will show

### The questions in this app's words

| # | Question | Priority | Page |
|---|---|---|---|
| Q1 | Is ordering healthy right now? Any turn stuck, dead-lettered, sends ambiguous, LLM failing? | **high** | Overview, Live |
| Q2 | How many turns, orders, staff actions and webhooks tonight; how many retried, dead-lettered, rejected? | **high** | Overview, Runs |
| Q3 | Why did a turn fail: which stage (route, commit, send), which error, since when? | **high** | Failures |
| Q4 | Turn time p50 / p95 against the 3 s / 5 s budget; button turns against 1 s; webhook against 300 ms; waiting (debounce, queue) vs working | **high** | Overview, Latency |
| Q5 | Where does a turn's time go: DB round trips, the LLM, the WhatsApp send? | **high** | Latency, Run trace, Dependencies |
| Q6 | Did a change (commit, model, prompt, pool size) make turns faster, cheaper or more error-prone? | medium | Latency compare |
| Q7 | How often do fallbacks fire: other model, handoff, ambiguous send, sold-out drop? | **high** | Overview, Failures (degradations) |
| Q8 | What does the AI cost per order and per café, and where is it wasted (retries on the other model)? | medium | Efficiency |
| Q9 | Waste: retries, N+1 queries per turn, chatty sends | medium | Insights |
| Q10 | Which staff endpoints and timers are slowest or failing most? | medium | Endpoints |
| Q11 | — (no batches) | — | dropped |
| Q12 | For one customer turn: what happened, step by step, on every attempt? | **high** | Run trace |
| Q13 | What did the logs say during that turn? | medium | Run trace events, Logs |

### Pages

| Page | In scope? | What it shows here | Fields it reads |
|---|---|---|---|
| Overview | yes | Tiles below; turns/min stacked by outcome; p50/p95 turn time with the 3 s / 5 s lines; time by stage; time by dependency; top failures; fallbacks | `ops_runs` |
| Live | yes | Running turns (stage, elapsed), due `pending_work`, held leases, ambiguous sends | `ops_runs` RUNNING, `pending_work`, `wa_messages` |
| Endpoints | yes | Staff REST + webhook + timers by name: count, error / reject rate, p50/p95/p99 | `ops_runs` |
| Batches | **no** | No batch concept | — |
| Runs explorer | yes | Filter by kind, name, outcome, café, label, min ms, conversation / order id | `ops_runs` |
| Run trace | yes | Waterfall: claim → debounce → load → route (→ LLM) → commit → send ×n; attempts; events; links to `llm_calls` and `wa_messages` rows | `ops_spans`, `ops_run_events` |
| Failures / Degradations | yes | Stage → code → fingerprint; reasons with counts | `ops_runs` |
| Latency | yes | Percentile table per span; histogram; compare by `git_sha` / `llm_model` / `route` | `ops_runs`, `ops_spans` |
| Dependencies | yes | Postgres (statement hash), Gemini (model), WhatsApp (send class) | `ops_spans` |
| Efficiency and cost | yes | AI cost per order, per café; retry waste; tokens per turn | `ops_runs.cost_usd`, `llm_calls` |
| Insights | yes, subset | D1, D2, D3, D4, D6, D7, D13, D14, D15, D17 | as each detector |
| Resources | **no** (v1) | P13 | — |
| Logs | local only | JSONL viewer; Cloud Logging link in the cloud | log file |

### Overview tiles

| Tile | Metric (PDS §7) | Why it matters here |
|---|---|---|
| Turns | count of final turn runs | volume |
| Turn success | succeeded ÷ (succeeded + failed) | "zero lost" |
| Dead letters | FAILED turns | each one is a customer told to call a waiter |
| Fallbacks | degraded rate + top reason | the AI or send path quietly struggling |
| Turn p50 / p95 | final successful turn runs | the 3 s / 5 s budget |
| Button turn p95 | `labels.route = button` | the 1 s budget |
| Webhook p95 | `webhook.inbound` | the 300 ms budget |
| AI cost / order | Σ `cost_usd` ÷ orders created | the main variable cost |

### Insights detectors

| Detector | In scope? | Data / reason |
|---|---|---|
| D1 regression after deploy | yes | `git_sha` per run + `ops_deploys` |
| D2 error spike / new failure | yes | fingerprints |
| D3 N+1 queries | yes | `db.query` statement hashes per turn — likely the first finding (a turn makes 10–25 round trips) |
| D4 chatty dependency | yes | sends per turn |
| D5 serial calls | no | turns are deliberately serial (commit before send) |
| D6 retry waste | yes | turn attempts, LLM second attempts |
| D7 slow tail | yes | per name |
| D8 size-unexplained | no | input size is tiny (1–3 messages) |
| D9 / D10 cache, cold start | no | no cache; Cloud Run cold starts are visible in Cloud Run itself |
| D11 / D12 idle, saturation | no (needs P13) | — |
| D13 timeouts near the limit | yes | LLM 6 s timeout, lease 30 s |
| D14 degradation creep | yes | reasons |
| D15 cost hot spot | yes | per model / purpose |
| D16 payload bloat | partly | LLM `tokens_in` p95 vs p50 |
| D17 slowest statements | yes | statement hash |
| D18 batch idle gaps | no | no batches |

## 5. Retention and sampling

PDS defaults: runs 365 days, spans L1–L2 90 days, L3 14 days, events 90 days. Retention runs as a new `ops_retention` entry in the existing scheduled-jobs registry (`domain/jobs.py:59`), daily, 5,000 rows per batch. Sampling: keep all (`OPS_SAMPLE_RATE=1.0`; pilot volume ~1.2 M span rows/month). Revisit at ten cafés.

## 6. Decisions

| # | Decision | Recommended default for Restro | Reason |
|---|---|---|---|
| P1 | Where ops data lives | Mode A: `ops_runs`, `ops_spans`, `ops_run_events`, `ops_deploys` in the same Supabase Postgres, **RLS on with no policies** (no client can read them); migration `0006_ops_tracing.sql` + rollback. This is a **contract change** to `contracts/schema.sql` (frozen) | No new infrastructure; one DB to back up |
| P2 | Path and access | API `/api/ops/*` on `api`, **`platform_admin` only** (JWT + `platform_admins`, already in the schema); locally also `OPS_DASHBOARD_TOKEN`. Disabled when neither is configured | Matches [roles](../CLAUDE.md#roles); never public |
| P3 | Entry points in scope | All in §3 except `/dev/sim/*` and `/healthz` | |
| P4 | Units of work | turn (`pending_work` row) / webhook / staff request / timer / task / job; no batches | |
| P5 | Success / reject / retryable | §3 mapping; 409 / 422 / 403 → REJECTED (expected races and roles) | Races are by design (optimistic locking) |
| P6 | Fallbacks that matter | All of §3; `llm_rate_limited`, `send_retry_429`, `ack_failed` cost-only | |
| P7 | Retention | PDS defaults | |
| P8 | Sampling | Keep all | Pilot volume |
| P9 | Prices | Reuse `pricing.py`. **You give the Gemini 3.5 Flash / Flash-Lite prices** (today they are priced as Flash 2.5). WhatsApp: existing estimate | Never guess prices |
| P10 | Sensitive fields | **None** | Invariant 12 |
| P11 | Dashboard frontend | **Departure:** a `platform_admin` section of the existing Restro Console (`/platform/ops/...`, React + TS, same auth, charts as hand-drawn SVG / CSS bars, no new library) **instead of** PDS's static no-build pages on `api` | The console already has Supabase login, design tokens and a typed client; a second login on `api` would be a second auth path. Alternative: PDS default (static `/ops` on `api` with a token) |
| P12 | OTLP / fleet | No | |
| P13 | Resource sampling | Off; optional `db_pool_in_use` sample | Cloud Run has the rest |
| P14 | Queue-entry time | `pending_work.created_at` (exact) | |

## 7. Open questions for you

- **Q-A (contract):** adding `ops_*` tables changes the frozen `contracts/schema.sql`. Approve as part of this work (one PR: contract, migration, backend, console)?
- **Q-B (S16 finding):** the S16 run with 10 simultaneous simulated customers failed: the api took > 20 s to answer. Cause found by measurement: each `/dev/sim/thread` poll costs ~0.9 s because every query round-trips ~150–200 ms from this PC to the hosted Supabase, and 10 pollers at 0.5 s need ~14 connection-seconds per second from a pool of 5. In Cloud Run next to the DB that round trip is a few ms. The same ratio means a turn locally makes 10–25 round trips (~2–4 s). Do you want the DB-round-trip view (D3 / D17) built first, so this is measured instead of estimated?
- **Q-C (docs vs code):** [monitoring](architecture.md#monitoring) says a `request_id` travels into Pub/Sub attributes and Cloud Tasks headers; the code has none. Proposed: the run's `run_id` (as W3C `traceparent`) replaces it, and the doc is updated.
- **Q-D (docs vs code):** `v_daily_costs`, `v_unit_costs`, `v_conversation_metrics`, `daily_summary` are designed but not built. The Efficiency page reads `llm_calls` directly until they exist. OK?
- **Q-E:** `platform_admin` has no login in this slice. For local use: seed one platform admin user (synthetic) so the console section can be tested, or token only?

## 8. Departures from PDS

1. **Dashboard in the existing console** (P11), not static pages served by `api`. Same pages, routes `/platform/ops/#…` → `/platform/ops/...`.
2. **`llm.call` cost comes from the existing `llm_calls` meter** (`pricing.py`, `pricing_version`), not a separate `OPS_PRICES` table. Spans carry `llm_call_id`.
3. **Package path** `backend/app/ops/` (read side) and `backend/app/ops/tracing/` (write side) to fit `app/` layout.
4. **Logs page is local-only**; in the cloud it links to Cloud Logging by `run_id`.
5. **Batches page and resource sampling dropped** (no batches; Cloud Run covers resources).

## Build order after approval

Each phase ends at its PDS gate, with evidence, before the next starts:

1. **Phase 1** run record: migration + rollback; tracing core (contextvars, registry, never-raise, kill switch `OPS_TRACING_ENABLED`); hooks for webhook, staff REST, turn, tasks, timers, jobs; turn stage spans; sweeper → ABANDONED; events + redaction + fingerprint. Gate 1: 20 simulated turns incl. forced failures (simulator faults) → one run per attempt, failures kept after retry, Σ stages within 5 % of the run, killed worker → ABANDONED.
2. **Phase 2** detail: asyncpg hook (`db.query`), LLM (`llm.call`), WhatsApp (`wa.send`), queue; degradation reasons; JSONL logs. Gate 2: overhead < 1 % of the p50 turn, measured on/off × 10.
3. **Phase 3** read API (`/api/ops/meta`, `overview`, `runs`, `runs/{run_id}`, `failures`, `degradations`) + console pages Overview, Runs, Run trace, Failures. Gate 3: every Gate 1 turn reachable in ≤ 3 clicks.
4. **Phase 4** Latency (+ compare), Endpoints, Dependencies, Live, Logs. Gate 4: a planted sleep in one step shows up in Compare on that span only.
5. **Phase 5** Efficiency and Insights. Gate 5: a planted N+1 is reported with a saving and a trace link.
6. **Phase 6** retention job, ops docs in [architecture](architecture.md#monitoring), this file's departures kept current.
