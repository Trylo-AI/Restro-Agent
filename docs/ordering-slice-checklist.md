# Ordering slice — checklist

Tracks [ordering-slice.md](ordering-slice.md). Ticked in the same commit as the work, one commit per step on branch `ordering-slice`. A step is done only when its **Exit** box is ticked. Something moved out of the slice gets ~~struck~~ with a note pointing to where it went.

`[ ]` to do · `[x]` done · `[-]` in progress

## Progress

| Step | Status | Commit |
|---|---|---|
| S0 Setup and plan approval | To do | — |
| S1 Scaffold | Done | S1 commit |
| S2 Contracts and doc updates | Done | S2 commit |
| S3 Database and seed | Done | S3 commit |
| S4 Backend core | To do | — |
| S5 WhatsApp ingress, send layer, dev router | To do | — |
| S6 Simulator UI | To do | — |
| S7 Conversation pipeline and deterministic flows | To do | — |
| S8 Cart, Confirm and orders | To do | — |
| S9 AI agent | To do | — |
| S10 Human help | To do | — |
| S11 Menu availability | To do | — |
| S12 Customer profile backend | To do | — |
| S13 Console shell | To do | — |
| S14 Live orders | To do | — |
| S15 Chats, Calls and bills, Menu | To do | — |
| S16 End to end | To do | — |

## S0 · Setup and plan approval (Aman)

- [x] G1–G6 answered (24 Oct 2026)
- [x] G7–G10 customer profile answered (24 Oct 2026)
- [x] Reviewer: Aman approves the plan, then tests the result
- [x] `backend/.env`, `frontend/.env.local`, `simulator/.env.local` created with dev secrets generated
- [x] `.mcp.json` for the Supabase MCP (read-only)
- [ ] Supabase project in Mumbai (ap-south-1) with the new JWT signing keys
- [ ] Email sign-ups turned off in Auth settings
- [ ] Project URL + publishable key → `frontend/.env.local`
- [ ] Project URL + secret key + transaction pooler URL + session pooler URL → `backend/.env`
- [ ] Gemini API key → `backend/.env`
- [ ] Project ref → `.mcp.json`
- [ ] Plan approved
- [ ] **Exit:** values filled in and plan approved

## S1 · Scaffold

- [x] `backend/` project: dependencies, ruff, mypy, pytest; `requirements.lock`
- [x] `main_api` and `main_worker` with `/healthz`; `/internal/*` only on worker
- [x] `frontend/` Vite + React + TS, ESLint, Vitest, design tokens
- [x] `simulator/` Vite + React + TS
- [x] `.env.example` for backend, frontend, simulator (no values)
- [x] `scripts/dev.ps1`, `scripts/test.ps1`, `scripts/new-dev-secrets.ps1`
- [x] Test: `main_api` and `main_worker` share no routes; config parsing; log scrubbing
- [x] `.github/workflows/ci.yml` (lint, types, tests for all three; integration job once `supabase/config.toml` exists)
- [x] ~~`supabase/config.toml`~~ moved to S3 (needs the Supabase CLI)
- [x] **Exit:** both services start with the real `.env` and answer `/healthz`; ruff, mypy, pytest, tsc, ESLint, Vitest and both builds pass locally. CI itself runs on the first push

## S2 · Contracts and doc updates

- [x] `contracts/schema.sql` (G1 `wa_messages.payload`; G2 only `total_paise`, no GST settings; `customer_notes`; `customer_preferences.note_id`; `cafe_counters`; `memberships.display_name`; `llm_calls.purpose` / `note_id`; append-only triggers; `v_customer_card`)
- [x] `contracts/state-machines.md` (orders with roles, conversation steps and mode, work queue, outbound steps, requests, customer notes)
- [x] `contracts/llm_output.schema.json` (turn output incl. `complaint` handoff, cancellation draft, profile-note ops)
- [x] `contracts/openapi.yaml` (slice endpoints incl. customer notes, note undo, preference remove + error body incl. `window_closed`)
- [x] `contracts/events.md` (10 Realtime tables incl. `customer_notes`, `customer_preferences`)
- [x] `contracts/allergens.json` (with Hinglish aliases), `contracts/preference_kinds.json`
- [x] Console types generated from `openapi.yaml` (`frontend/src/lib/api/schema.d.ts`)
- [x] Docs updated for G1–G6: backend (Cart, Confirmation, Bill sheet), data-and-api, console (shell, Setup), rules (cart tests), architecture, decisions, plan open decisions, CLAUDE.md + README layout
- [x] Docs updated for G7–G10: console (card Profile button, Customer profile section, Chats panel), backend (Customers, AI agent prompt and call types, Allergens, print ticket), data-and-api (`customer_notes`, `customer_preferences`, RLS for `feedback`, endpoints, Realtime, `v_customer_card`), architecture (deletion, prompt), rules (tests, never), plan (roles, features), decisions (#16 + smaller decisions)
- [x] Name-check script `scripts/check_contracts.py`: every doc table and column exists in `schema.sql`, every contract endpoint and Realtime table is in the docs (verified it fails on a planted mismatch)
- [x] **Exit:** contracts match the docs; docs updated

## S3 · Database and seed

- [x] `supabase/config.toml` (local test stack; sign-ups off; seed via Python)
- [x] Migration 0001: every table, check, unique and index in `schema.sql` (+ `v_customer_card`)
- [x] `cafe_counters` + `next_order_number()` / `next_menu_version()` (migration 0004)
- [x] Append-only tables reject UPDATE / DELETE (trigger; verified on a real row)
- [x] RLS enabled everywhere with read policies only; no client write grants (migrations 0002, 0005)
- [x] Realtime publication: `orders`, `requests`, `conversations`, `inbound_messages`, `wa_messages`, `menu_items`, `cafes`, `notifications`, `customer_notes`, `customer_preferences` (migration 0003)
- [x] RLS: `feedback`, `customer_preferences`, `customer_notes`, `v_customer_card` readable by `shift_manager`+
- [x] `supabase/dev/` migration for `dev_turn_traces` (G6), applied with `generate.py --dev-sql`
- [x] Seed: café with simulator `phone_number_id`, 31 dishes with every allergen state, 12 tables with `qr_text`
- [x] Seed: one auth user per role + memberships, returning customers with history, approved template rows
- [x] Seed: customer profiles (allergy, vegan + dislike, oat milk, complaint, empty), feedback rows, two staff notes
- [x] Seed: second café for isolation tests
- [x] Seed `--live` option (open orders in every status and flag)
- [x] `scripts/reset-dev-db.ps1` (refuses unless APP_ENV is local and only seed cafés exist)
- [x] Applied to the cloud dev project (`supabase db push` with the session URL)
- [ ] Applied to the local stack — runs in CI; locally once Docker Desktop is running
- [x] Tests (`tests/integration/test_rls.py`, 36 cases, through Supabase Auth + PostgREST): café A sees no café B rows on 16 tables; owner / platform / backend tables hidden from `shift_manager`; `low_rating` hidden below `manager`; own `notifications` only; anon sees nothing; client writes refused
- [x] **Exit:** migrations apply to an empty DB; seed runs twice with no duplicates (31 dishes, 5 customers, 27 orders, 7 profile values); RLS tests green against the cloud dev project; no table without RLS, no client write grants (SQL check; the Supabase MCP isn't connected in this session)

## S4 · Backend core

- [ ] Config (pydantic-settings) with every switch in [adapters](ordering-slice.md#swappable-adapters)
- [ ] JSON logs with ids only
- [ ] `env` secrets backend; `token_ref` resolution
- [ ] asyncpg pool on the transaction pooler (`statement_cache_size=0`)
- [ ] Repository base that requires `cafe_id`
- [ ] JWT verification against JWKS (cached with expiry; `exp`, `aud`, `iss`)
- [ ] Membership and role dependency; `X-Cafe-Id` handling
- [ ] Error body and mapping (400 / 401 / 403 / 404 / 409 / 422 / 429 / 5xx)
- [ ] `audit` helper (one row per console write)
- [ ] Internal auth (`dev_token`; `oidc` stubbed)
- [ ] Local task and signal shims behind `integrations/tasks`; `integrations/gcp` interfaces
- [ ] Worker job loop: `stuck_inbound_check` 60 s, `llm_spend_check` 15 min
- [ ] `pending_work` repository: upsert, due rows, retry with backoff, dead letter
- [ ] Conversation lease: claim, renew, fenced commit, release, expiry
- [ ] `timers.schedule()` and dispatch
- [ ] Tests: expired JWT, wrong audience, other café → 403, role too low → 403, deactivated → 403, `/internal/*` without token → 401; two concurrent claims → one holder; stale holder's commit refused
- [ ] **Exit:** auth tests green; the shim delivers a delayed task exactly once; lease tests green

## S5 · WhatsApp ingress, send layer, dev router

- [ ] `GET /webhooks/whatsapp` verify-token handshake
- [ ] `POST /webhooks/whatsapp`: signature on raw bytes → `webhook_events` → tenant → upsert → `inbound_messages` + `pending_work` (one transaction) → signal + `wa-ack` → 200
- [ ] `pending_work` due: +1 s text, now for taps
- [ ] `statuses` forward-only into `wa_messages`
- [ ] Parser (every entry and change; multi-entry batches)
- [ ] Builders with Meta limits (3 buttons, title ≤ 20, list ≤ 10 rows, row ≤ 24, body ≤ 1024)
- [ ] Client with `meta` and `simulator` transports; error classification
- [ ] Simulator transport: fake `wamid`, signed status webhooks, armed faults
- [ ] `domain/outbound`: window check, step row first (`pending` / `sent` / `ambiguous` / `failed`), `attempted_at`, retry only what certainly didn't go, `send_check`, `order_update` outside the window
- [ ] `wa-ack` task: mark read + typing
- [ ] Dev router: send, thread, read, debug, customers, close-window, faults, alerts (`X-Sim-Token`, local only)
- [ ] Tests: tampered signature → 401 and nothing stored; duplicate `wamid` → one reply; status never backwards; timeout after send → `ambiguous`, not resent; parser fixtures; builder snapshots; window block
- [ ] **Exit:** a simulator message lands in `webhook_events`, `inbound_messages` and `pending_work`; the phone shows typing

## S6 · Simulator UI

- [ ] Phone: header, bubbles, times, ticks, typing dots
- [ ] Reply buttons; list bottom sheet
- [ ] Composer; send image / voice
- [ ] Customer picker; table QR picker
- [ ] Up to 3 phones side by side
- [ ] "Try typing" phrases (English, Hinglish, Hindi script); Restart; Close 24-h window
- [ ] Behind the scenes: state, cart, strikes, last LLM call, raw trace
- [ ] Event log
- [ ] Alert phone for `new_order_alert`
- [ ] Meta-limit violations shown in red
- [ ] Fault injection panel
- [ ] Temporary echo turn (removed in S7)
- [ ] **Exit:** echo chat on 3 phones; ticks ✓ → ✓✓ → blue; an over-long title shows the red badge

## S7 · Conversation pipeline and deterministic flows

- [ ] `/internal/pubsub/inbound`: ack, lease, 1 s debounce, batch, loop while due work remains, release
- [ ] LLM call and sends outside transactions; fenced commit
- [ ] 3 retries with backoff (`work_retry`), then dead letter: standard customer message, alert, `help` + human mode (open default)
- [ ] Routing order 1–9; keyword lists (English, Hinglish, Hindi)
- [ ] `render.py`: every customer-facing string in one place
- [ ] Steps and back-edges; `awaiting`
- [ ] Greeting new / returning; name capture
- [ ] QR text → table session join or open
- [ ] Menu and category lists (pagination); item tap
- [ ] Non-text reply; keywords (whole message only)
- [ ] Paused / closed replies; status flow
- [ ] STOP → `consents` opt-out row
- [ ] `stuck_inbound_check` local loop (overdue `pending_work`, expired leases, never-attempted sends)
- [ ] Scripted conversation tests (stub LLM) for every flow and every stale / invalid case
- [ ] Tests: lease held → second signal exits; crash mid-turn → rerun, no double send; 3 failures → dead letter
- [ ] **Exit:** Table 5 QR → name → Menu → items → cart summary with taps only; three texts within 1 s give one turn

## S8 · Cart, Confirm and orders

- [ ] Cart ops and rejections; version bump
- [ ] Integer paise; total = sum of lines (no GST setting, G2); `expected_minutes`
- [ ] Confirmation rendering with "Total (incl. GST)"
- [ ] Confirm steps 1–6 in one transaction; snapshots; `idempotency_key`
- [ ] Table ask when no session
- [ ] `transition()` with optimistic lock, `order_events`, `audit_log`
- [ ] Flags: `escalated`, `overdue`, `delayed`, `edited`, `item_sold_out`, `near_closing`
- [ ] Undo inside 5 s (queued message never sent)
- [ ] Delay
- [ ] Staff cancel: `shift_manager` on `NEW`, `manager`+ after; reason code + custom text; `cancel_*` fields; `notifications` rows
- [ ] AI-drafted cancellation message (reason code only), fixed fallback
- [ ] Customer edit / cancel while `NEW`; edit / accept race → 409
- [ ] Manual order (`ACCEPTED`, no customer messages)
- [ ] Timers: `accept_escalation_1/2` (with `escalations` + `new_order_alert`), `overdue`, `auto_served`, `status_message`
- [ ] Endpoints: transition, cancel, undo, delay, manual, pause-ordering
- [ ] Tests: every allowed and forbidden transition; `shift_manager` cancel on `ACCEPTED` → 403; cancel → one notification per manager and owner; race → 409; double / stale tap → one order; sold out at Confirm; undo after 5 s → 422; timer after state moved does nothing; cart qty / note mixes; manual order idempotent
- [ ] **Exit:** taps-only order → Confirm → exactly one order; Accept sends "Your order is being made" after 5 s

## S9 · AI agent

- [ ] `integrations/llm`: `developer_api`, `vertex`, `stub`; schema output; 6 s timeout
- [ ] Prompt prefix cache per `(cafe_id, menu_version)`; version check per turn
- [ ] Suffix from SQL; last ~6 turns; nothing sensitive in the prompt
- [ ] Intent handlers
- [ ] `item_ref` resolution (sold out vs not on menu)
- [ ] Guardrails: amount scan, alternatives, strikes, off-topic
- [ ] Allergens from DB; `unknown` → handoff
- [ ] Cancellation draft (reason code only; refund wording or amount → fixed fallback)
- [ ] Retry on the other model → `system_error` handoff
- [ ] `llm_calls` for every call, with `provider`, `cost`, `currency`, `pricing_version`, `message_id`, `order_id`, `outcome`
- [ ] Kill switch (`off` / `cheap` / `handoff`)
- [ ] `dev_turn_traces` when `DEV_TRACES=true`
- [ ] `evals/` runner + ~30 draft cases (team reviews the cases)
- [ ] Tests: unknown / sold-out rejected; unknown allergen → handoff; amounts dropped; invalid JSON → retry → handoff; kill switch; prompt has no phone, `wa_id` or profile allergy; profile dislikes reach the suffix
- [ ] **Exit:** the goal phrase gives 2 × Cold Coffee + 1 × Avocado Toast ("no chilli"); eval smoke report saved

## S10 · Human help

- [ ] Requests `help`, `allergy`, `bill`; one open per conversation and kind
- [ ] Handoff reasons; human mode (bot silent, STOP still works)
- [ ] `help_escalation`, `idle_handback` timers
- [ ] Endpoints: request ack / resolve; takeover, reply, handback, open; bill deliver
- [ ] Tests: one open request per kind; ack / resolve idempotent; reply outside 24 h refused; idle hand-back (fake clock); bill deliver closes the session
- [ ] **Exit:** call waiter → ack → resolve → bot answers again; two unclear → handoff; staff reply reaches the simulator

## S11 · Menu availability

- [ ] `POST /menu/{id}/availability`: version, audit, `item_sold_out` on open orders
- [ ] **Exit:** the next message doesn't offer the item; the open cart drops it at the next summary

## S12 · Customer profile backend

- [ ] `v_customer_card` view (migration 0005)
- [ ] `POST /customers/{id}/notes` → note row + `profile_note` task; audit `customer.note`
- [ ] `profile_note` handler: one LLM call + retry → validate ops → one transaction → `applied` / `no_changes` / `failed`
- [ ] Op validation: fixed kinds, single vs list kinds, ≤ 40 chars, allergens matched; consent / offers / prices / orders dropped
- [ ] Auto-apply incl. allergies (G8); audit `customer.profile_update`; `llm_calls` linked to the note
- [ ] `POST /customer-notes/{id}/undo` (409 if changed again since)
- [ ] `POST /customers/{id}/preferences/remove` (chip ×)
- [ ] Profile allergies in the order read for the card and print ticket
- [ ] Tests: vegan + mushrooms applied; allergy removal applied and audited; consent comment dropped; bad kinds dropped; LLM failure → profile unchanged; undo; undo after later change → 409; cross-café → 404; note text never logged
- [ ] **Exit:** a note posted from the API changes the profile within a few seconds and shows its applied ops

## S13 · Console shell

- [ ] Login (email + password); demo role picker behind `VITE_DEMO`
- [ ] Café context; `X-Cafe-Id` on every call
- [ ] `shift_manager` nav: sidebar ≥ 768 px, tab bar on phones
- [ ] Typed API client
- [ ] Realtime hooks: subscribe → buffer → read → apply; refetch on reconnect / visible / switch and every 30 s; disconnects to `POST /telemetry/console`
- [ ] Banners: Offline, Paused, Alerts off
- [ ] Urgency engine (every row of the severity tables) + sounds + alerts gate + browser notifications
- [ ] Undo toast
- [ ] 403 / 409 / network handling
- [ ] **Exit:** shift manager lands on Live orders; Offline banner within 5 s of stopping `api`

## S14 · Live orders

- [ ] Columns New / Accepted / Ready with counts and dots; Tabs / Board toggle
- [ ] Summary strip; type chips; legend
- [ ] Card: allergy banner (incl. "On profile: …"), table, number / name / visit, **Profile** button, chips, timer, progress, items, chain, "Total (incl. GST)"
- [ ] Customer profile panel (shared): at-a-glance box, chip ×, Feedback, Complaints, Recent orders, Comments with applied changes + Undo, Add a comment ("Updating profile…" → result by Realtime)
- [ ] Print ticket allergy box includes profile allergies
- [ ] Pause button (`manager`+ only)
- [ ] Accept / Mark ready / Mark served with `expected_version`; Undo
- [ ] Delay panel with preview; Cancel dialog with reason + Other text (Cancel only on New for `shift_manager`)
- [ ] Message (opens chat); manual order; print ticket
- [ ] 409 and 422 messages
- [ ] Tests: profile box for each seeded customer type; comment → pending → applied → Undo
- [ ] **Exit:** the [Live orders](console.md#live-orders) "Done when" list passes at 375 px and desktop; Profile opens in one tap and the top box needs no scrolling at 375 px

## S15 · Chats, Calls and bills, Menu

- [ ] Chats: list split, thread, mode banner, quick replies, window closed, Message a table, customer panel = shared profile panel
- [ ] Calls and bills: strip, chips, cards, buttons per kind, Profile button, escalation chain, bill sheet ("Total (incl. GST)", no code field), Bill delivered
- [ ] Menu: sold-out count, category chips, rows, two-tap switch; nothing `manager`+ shown
- [ ] **Exit:** each screen's "Done when" list passes, minus the Later parts

## S16 · End to end

- [ ] Playwright: goal path with simulator and console side by side
- [ ] Playwright: edit while `NEW`, edit after `ACCEPTED`, sold out mid-order, allergy handoff, call waiter, delay, cancel with Other text
- [ ] Playwright: Profile → comment → profile updated → Undo, seen on two consoles
- [ ] Playwright: dead letter and ambiguous send via fault injection (one message, never two)
- [ ] Manual dry run of the service items that don't need a real number
- [ ] `scripts/run-20.ps1`: 20 stub runs + 3 real-model runs; no lost, duplicate or wrong orders
- [ ] **Exit:** the [goal](ordering-slice.md#goal) is met
