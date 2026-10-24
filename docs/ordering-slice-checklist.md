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
| S4 Backend core | Done | S4 commit |
| S5 WhatsApp ingress, send layer, dev router | Done | S5 commit |
| S6 Simulator UI | Done | S6 commit |
| S7 Conversation pipeline and deterministic flows | Done | S7–S8 commit |
| S8 Cart, Confirm and orders | Done | S7–S8 commit |
| S9 AI agent | Done | S9–S12 commit |
| S10 Human help | Done | S9–S12 commit |
| S11 Menu availability | Done | S9–S12 commit |
| S12 Customer profile backend | Done | S9–S12 commit |
| S13 Console shell | Done | S13–S14 commit |
| S14 Live orders | Done | S13–S14 commit |
| S15 Chats, Calls and bills, Menu | Done | S15 commit |
| S16 End to end | In progress | Playwright set up; orders and service specs pass (5) |

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

- [x] Config (pydantic-settings) with every switch in [adapters](ordering-slice.md#swappable-adapters)
- [x] JSON logs with ids only (scrubber drops text, phone, wa_id, tokens)
- [x] `env` secrets backend; `token_ref` resolution (`env:NAME`), 10-min cache, `forget()` for error 190
- [x] asyncpg pool on the transaction pooler (`statement_cache_size=0`), json/jsonb codecs; `tx()` short transactions
- [x] Repositories take `cafe_id` first; named cross-café exceptions (`jobs.py`, tenant lookup)
- [x] JWT verification against JWKS (cached with expiry; ES256/RS256/EdDSA only; `exp`, `aud`, `iss`, `sub` required)
- [x] Membership and role dependency (`shift_manager` / `manager` / `owner`); `X-Cafe-Id` → 404 if missing, 403 if not a member
- [x] Error body and mapping (400 / 401 / 403 / 404 / 409 / 422 / 429 / 500); validation errors never echo submitted values
- [x] `audit` helper (one row per console write, in the caller's transaction)
- [x] Internal auth (`dev_token`; `oidc` refuses until Phase 3)
- [x] Local task and signal shims behind `integrations/tasks` (retries, Cloud Tasks-style name dedupe, Pub/Sub-shaped envelope); `integrations/gcp` interfaces
- [x] Worker job loop: `stuck_inbound_check` every 60 s (`llm_spend_check` joins in S9)
- [x] `pending_work` repository: upsert (text debounce / tap now), claim when due, done, fail with 2 / 4 / 8 s backoff, dead letter on the 4th failure, merge into a newer pending row
- [x] Conversation lease: claim, renew every 10 s, fence (row lock + owner check) in every commit, release, expiry
- [x] `timers.schedule()` and dispatch registry
- [x] Unit tests (27): expired / wrong-audience / wrong-issuer / foreign-key / HS256 JWT → 401; other café → 403; missing café header → 404; role too low → 403; deactivated → 403; `/internal/*` without token → 401; error body; delayed task delivered once; delivery retries; signal envelope; timer dispatch
- [x] Integration tests (7, hosted dev project, throwaway café per test): five concurrent claims → one holder; expired lease re-claimed and the old holder's commit refused; debounce / due / tap; 3 retries then dead letter; failure merges into newer pending row; stuck work found and expired running work reclaimed
- [x] **Exit:** all green (unit + integration); test cafés cleaned up; append-only triggers verified enabled afterwards

## S5 · WhatsApp ingress, send layer, dev router

- [x] `GET /webhooks/whatsapp` verify-token handshake
- [x] `POST /webhooks/whatsapp`: signature on raw bytes → `webhook_events` → tenant → upsert → `inbound_messages` + `pending_work` (one transaction) → signal + `wa-ack` → 200
- [x] `pending_work` due: +1 s text, now for taps
- [x] `statuses` forward-only into `wa_messages`; `biz_opaque_callback_data` resolves an ambiguous row; cost estimate per category
- [x] Parser (every entry and change; multi-entry batches; malformed entries skipped)
- [x] Builders with Meta limits (3 buttons, title ≤ 20, list ≤ 10 rows, row ≤ 24, description ≤ 72, body ≤ 1024) + `limit_violations()`
- [x] Transports `meta` (connect error → not sent; timeout / 5xx → ambiguous; 190 → token invalid) and `simulator`; error classification
- [x] `domain/outbound`: window check, step row first (`pending` / `sent` / `ambiguous` / `failed`), atomic `attempted_at` claim, retry only what certainly didn't go, `send_check`, `order_update` outside the window, `WindowClosed` for staff replies, alerts by reference (no phone in the row)
- [x] Recovery: `stuck_inbound_check` sends never-attempted steps
- [x] `wa-ack` task: mark read + typing
- [x] Dev router: send, thread, read, debug, customers (+ tables), new / reset customer, close-window, faults (arm / consume), events, alerts (`X-Sim-Token`, local only)
- [x] Unit tests (16 new): parser fixtures, builder limits, Meta error classes, tampered / missing signature → 401 with no DB access, verify handshake, dev router token
- [x] Integration tests (9, hosted dev project): stored + queued + deduped; bad signature stores nothing; unknown number not processed; window rules; send once then statuses forward-only; 4 concurrent deliveries → one send; 429 retried → one send; timeout → ambiguous, never resent, resolved by the status webhook; send_check fails an unconfirmed send
- [x] **Exit:** with the real api and worker running, a simulator message lands in `webhook_events`, `inbound_messages` and `pending_work`, and the phone shows typing

## S6 · Simulator UI

- [x] Phone: header (café, online / typing…), bubbles, times, ticks (✓ sent, ✓✓ delivered, blue read, ! failed, ? ambiguous), typing dots
- [x] Reply buttons; list bottom sheet (sections, rows, descriptions); taps send the reply id
- [x] Composer; send photo / voice note / sticker
- [x] Customer picker (seeded + New customer); table QR picker (sends `qr_text`)
- [x] Up to 3 phones side by side
- [x] "Try typing" phrases (English, Hinglish, Hindi script, changes, questions, people, off-topic); Restart; Close 24-h window
- [x] Behind the scenes: step, mode, awaiting, table, strikes, allergy, lease, cart, work queue, order events, raw state; LLM calls with parsed output, guardrails, raw output, prompt suffix
- [x] Event log
- [x] Alert phone for `new_order_alert`
- [x] Meta-limit violations shown in red
- [x] Fault switches (send timeout, send 429, LLM fail, LLM invalid, turn crash)
- [x] Worker pipeline (built here instead of S7): lease, debounce wait, turn split, fenced commit, sends after commit, retries 2 / 4 / 8 s, dead letter with the standard message; `work_retry` timer; `/internal/pubsub/inbound`
- [x] Temporary echo turn (removed in S7)
- [x] Component tests (6): ticks, buttons send ids, list sheet, violation badge
- [ ] ~~Playwright smoke test~~ moved to S16 (Playwright and its browser are installed once there)
- [x] **Exit:** with api, worker and simulator running: echo replies arrive in send order (text, buttons, list) with no limit violations; ticks reach blue read; tap and photo get their replies; a burst of three texts becomes one turn (or two when one lands after the turn started; never lost, never twice); four armed crashes dead-letter with the standard message

## S7 · Conversation pipeline and deterministic flows

Built together with S8 (Confirm needs the order code) and committed as one step.

- [x] `/internal/pubsub/inbound`: ack, lease, 1 s debounce, batch, loop while due work remains, release (built in S6)
- [x] LLM call and sends outside transactions; fenced commit; commit hooks may add replies (order number)
- [x] 3 retries with backoff (`work_retry`), then dead letter: standard customer message (+ `help` and human mode: S10 hook)
- [x] Routing order 1–9; keyword lists (English, Hinglish, Hindi), whole-message match only
- [x] `render.py`: every customer-facing string in one place; ₹ from paise with Indian grouping
- [x] Steps and back-edges; `awaiting` (name, table); new visit after 6 h or on a QR scan
- [x] Greeting new / returning ("Same again?" from SQL); name capture (≤ 3 words, not a dish; else asked after the order)
- [x] QR text → table session join or open (shared tables join the open session)
- [x] Menu and category lists (paging, prices from the DB in row descriptions); item tap; reorder
- [x] Non-text reply; call waiter (help + human mode) and bill (no human mode); status flow
- [x] Paused / closed replies (opening hours in the café's time zone, past-midnight windows, near closing)
- [x] STOP → `consents` opt-out row, also in human mode
- [x] `stuck_inbound_check` local loop (built in S4/S5)
- [x] All SQL in `db/repositories` (moved out of domain modules; the dev router is the one documented exception)
- [x] Scripted conversation tests (real pipeline, recording transport): see S8
- [x] **Exit:** Table 5 QR → name → Menu → category → items → cart summary → Confirm with taps only; three texts within 1 s give one turn (S6)

## S8 · Cart, Confirm and orders

- [x] Cart ops and rejections; version bump; lines keyed by (item, note); qty 1–20; note ≤ 120
- [x] Integer paise; total = sum of lines (no GST setting, G2); `expected_minutes`; live prices at every render
- [x] Confirmation rendering with "Total (incl. GST)"
- [x] Confirm steps 1–6 in one transaction; snapshots; `idempotency_key` = `{conversation_id}:{cart_version}`
- [x] Table ask when no session, then the order on the table tap
- [x] `transition()` with optimistic lock, `order_events`, `audit_log`
- [x] Flags: `escalated`, `overdue`, `delayed`, `edited`, `near_closing` (`item_sold_out` in S11); timer flags don't bump `version`
- [x] Undo inside 5 s (queued message never sent; other actor or after 5 s → 422)
- [x] Delay (message at once, `overdue` re-created)
- [x] Staff cancel: `shift_manager` on `NEW`, `manager`+ after; reason code + custom text; `cancel_*` fields; `notifications` rows for each manager and owner
- [x] Cancellation message: fixed fallback (AI draft in S9); `reason_text` never sent
- [x] Customer edit / cancel while `NEW`; edit / accept race → 409 / "already accepted"
- [x] Manual order (`ACCEPTED`, no customer messages, idempotent on the header)
- [x] Timers: `accept_escalation_1/2` (with `escalations` + `new_order_alert` to the café alert number and managers / owner), `overdue`, `auto_served`, `status_message`
- [x] Endpoints: transition, cancel, undo, delay, manual, pause-ordering (`manager`+)
- [x] Unit tests: cart (11), hours (5)
- [x] Integration tests (17): taps-only order; double tap → one order; stale summary; sold out at Confirm; table ask; foreign ids; non-text; call waiter / bill / human mode / STOP; paused; customer cancel before and after accept; edit + race; staff flow with undo and status messages; cancel roles + notifications; delay; manual order; escalation alerts + auto-served; REST endpoints with 409 / 422 / 403 / 400 / 404
- [x] **Exit:** taps-only order → Confirm → exactly one order; Accept sends "Your order is being made" after 5 s (status_message re-checked)

## S9 · AI agent

Built with S10 and S11 and committed together (they share the handoff wiring).

- [x] `integrations/llm`: `developer_api`, `vertex`, `stub`; JSON-schema output from the contract; timeout; never raises
- [x] Models: Gemini 2.5 is closed to new keys → `gemini-3.5-flash` primary (p50 2.5 s on this prompt), `gemini-3.5-flash-lite` fallback; minimal thinking; config values (decision recorded)
- [x] Prompt prefix cached per (café, menu version, tone); short ids (`m1`…) for dishes; version checked every turn
- [x] Suffix from SQL; last ~6 messages; never phone, `wa_id`, other customers or profile allergies
- [x] Interpret (ai_agent) vs act (conversation/agent_turn): ai_agent never calls orders, consent or outbound
- [x] `item_ref` resolution: available id → op; sold out (by name) → alternatives; not on menu → strike
- [x] Guardrails: amount scan on reply / clarify / notes; rejected ops discard the reply; off-topic one line + Menu
- [x] Allergens answered from `menu_item_allergens`; `unknown` / unknown allergen → `allergy` request + human mode
- [x] Allergies stated in chat → `allergy_note` on the order (bug found and fixed: was cleared before commit)
- [x] Cancellation draft (reason code only; refund wording or amount → fixed fallback)
- [x] Retry on the other model → `system_error` handoff
- [x] `llm_calls` for every attempt, with `provider`, `purpose`, `cost`, `currency`, `pricing_version`, `message_id`, `order_id`
- [x] Kill switch (`off` / `cheap` / `handoff`) + `llm_spend_check` job (15 min)
- [x] `dev_turn_traces` when `DEV_TRACES=true` (raw output, parsed, guardrail actions)
- [x] `evals/run.py` + 32 draft cases (team reviews and grows them); rate-limited calls counted as skipped
- [x] Unit tests (33): amounts, refund wording, allergen names and mentions, name matching, cost
- [x] Integration tests (14, scripted LLM): goal phrase + metering; sold out / not on menu; two unclear → handoff; amounts dropped; allergen contains / free from / unknown; human intent; failure → retry → handoff; invalid then fallback; kill switch; typed confirm + allergy note; preferences in, profile allergies out; cancellation guardrails
- [x] **Exit:** the goal phrase gives 2 × Cold Coffee + 1 × Avocado Toast ("no chilli") with the real model (end to end in the simulator); eval smoke report saved — 8/8 scored cases correct, 24 not scored because the free-tier quota ran out

## S10 · Human help

- [x] Requests `help`, `allergy`, `bill`; one open per conversation and kind
- [x] Handoff reasons; human mode (bot silent, STOP still works); customer messages in human mode re-arm `idle_handback`
- [x] `help_escalation` (2 min: escalation row, `new_order_alert` to café + managers, "the team is busy" to the customer), `idle_handback` (15 min, only when nothing is unanswered)
- [x] Dead-lettered turn → `help` request (`system_error`) + human mode (open-decision default)
- [x] Endpoints: request ack / resolve (resolve hands back); takeover, reply (takes over; 409 `window_closed`), handback, open (Message a table); bill deliver; notifications ack / ack-all; console telemetry
- [x] Tests (10): ack → resolve → bot answers again; staff reply takeover, silent bot, audit has no message text; window closed 409; idle hand-back rules; help escalation once; bill deliver closes the session; Message a table; dead letter; notifications + telemetry
- [x] **Exit:** call waiter → request → ack → resolve → the bot answers again; staff reply reaches the customer

## S11 · Menu availability

- [x] `POST /menu/{id}/availability`: one transaction — `available`, new menu version, audit, `item_sold_out` on open orders; no-op when unchanged
- [x] **Exit:** the very next message's prompt lacks the dish; an open cart drops it at the next summary with a notice; open orders flagged

## S12 · Customer profile backend

- [x] `v_customer_card` view (in migration 0001 from the S2 contract; read grants in 0005)
- [x] `POST /customers/{id}/notes` → note row + `profile-note` task; audit `customer.note` (length only, never the text)
- [x] `profile-note` worker task: re-reads the note (pending only) → one LLM call + retry → validate ops → one transaction → `applied` / `no_changes` / `failed`
- [x] Op validation: fixed kinds, single vs list kinds, diet and spice normalised, ≤ 40 chars, allergens mapped to ids; consent / offers / prices / orders / phone dropped
- [x] Auto-apply incl. allergies (G8); `previous` value kept per op; audit `customer.profile_update`; `llm_calls` linked to the note (`purpose = profile_note`)
- [x] `POST /customer-notes/{id}/undo` (409 when a later note touched the same value; idempotent)
- [x] `POST /customers/{id}/preferences/remove` (chip ×)
- [x] Profile allergies for the order card and print ticket: the console reads `customer_preferences` (RLS, Realtime) — done in S14
- [x] Unit tests (3): normalisation, junk and consent ops dropped, duplicates collapse
- [x] Integration tests (5): vegan + mushrooms applied, later change blocks the earlier undo (409), undo restores the previous value; allergy add and remove applied automatically; consent comment changes nothing; LLM failure → `failed`, profile unchanged, processed once; chip removal; another café's customer → 404; no phone in the prompt; comment text never in the audit log
- [x] **Exit:** a note posted through the API changes the profile and shows its applied ops (scripted model); real-model check pending free-tier quota

## S13 · Console shell

- [x] Login (email + password); demo role picker behind `VITE_DEMO`
- [x] Café context; `X-Cafe-Id` on every call
- [x] `shift_manager` nav: sidebar ≥ 768 px, tab bar on phones
- [x] Typed API client (`openapi-fetch` + types from `contracts/openapi.yaml`; all writes in `lib/mutations.ts`)
- [x] Realtime hooks: subscribe, then read; any event refetches; refetch on reconnect / visible / switch and every 30 s; disconnects to `POST /telemetry/console`
- [x] Banners: Offline, Paused, Alerts off
- [x] Urgency engine (every row of the severity tables) + sounds + alerts gate + browser notifications. Fixed in testing: first load no longer fires the repeat alarm for criticals already open
- [x] Undo toast (ends on a new transition, route change, café switch, sign-out)
- [x] 403 / 409 / network handling
- [x] Tests: `urgency.test.ts` (every table row + ringing rules), `format.test.ts`, login screen
- [x] **Exit:** shift manager lands on Live orders; Offline banner 5–6.5 s after the api drops (e2e, ping every 1.5 s + 5 s grace)
- [ ] Gap for Aman: RLS lets a `shift_manager` read only their own `memberships` row, so "Acknowledged by Ravi" and comment authors show "A teammate" unless the reader is the owner. Proposed fix: a `v_staff_names` view (`cafe_id`, `user_id`, `display_name`) for members. That needs a contract change, so it hasn't been made

## S14 · Live orders

- [x] Columns New / Accepted / Ready with counts and dots; Tabs / Board toggle (phones always Tabs; choice kept per device)
- [x] Summary strip; type chips; legend
- [x] Card: allergy banner (incl. "On profile: …"), table, number / name / visit, **Profile** button, chips, timer, progress, items, chain, "Total (incl. GST)"
- [x] Customer profile panel (shared, `features/customer-profile`): at-a-glance box, chip ×, Feedback, Complaints, Recent orders, Comments with applied changes + Undo, Add a comment ("Updating profile…" → result by Realtime)
- [x] Print ticket allergy box includes profile allergies (customer text written as text nodes, never HTML)
- [x] Pause button (`manager`+ only)
- [x] Accept / Mark ready / Mark served with `expected_version`; Undo
- [x] Delay panel with preview (same text as the backend); Cancel dialog with reason + Other text (Cancel only on New for `shift_manager`)
- [x] Message (opens chat); manual order (no client-side total: money only from the API); print ticket
- [x] 409 and 422 messages
- [x] Tests: `orders.test.tsx` (card, dialogs, `canCancel`, transition + Undo, 409/422) and `profile.test.tsx` (regular and first-visit profiles, chip ×, details, applied / pending / no-change / failed comments, Undo 409)
- [x] E2E (`npm run e2e`, real stack): simulated WhatsApp order → card ≤ 10 s → Accept → Undo → Accept (customer gets "being made") → Profile → Ready → Served; Offline banner; 375 px tabs with no horizontal scroll
- [x] **Exit:** "Done when" checked at desktop and 375 px. Not automated yet: allergy 30 s repeat in a real browser (unit-tested), and a missed Realtime event showing up within 30 s (covered by the 30 s refetch)
- [ ] Not built: redeemed code on the card (campaigns are outside this slice); support-mode hiding (no `platform_admin` in this slice)

## S15 · Chats, Calls and bills, Menu

- [x] Chats: list split, thread (taps and buttons shown as text, "Not delivered" on failed sends), mode banner (incl. "Waiting for a person" when human mode is unassigned), quick replies, window closed, Message a table, customer panel = shared profile panel (third column ≥ 1200 px, otherwise a Profile button)
- [x] Calls and bills: strip, chips, cards, buttons per kind, Profile button, escalation chain, bill sheet ("Total (incl. GST)", no code field), Bill delivered
- [x] Menu: sold-out count, category chips, rows, two-tap switch (armed state resets after 5 s or when another item is tapped; back in stock is one tap). Price and prep shown read-only to `manager`+ only; no Edit, New or Import in this slice
- [x] Tests: `features/s15.test.tsx` (menu two taps, expiry and role; calls buttons per kind, low ratings hidden, bill sheet + deliver; chats quick replies, Send, Take over, closed window, teammate replying; message rendering)
- [x] E2E: `e2e/service.spec.ts` call waiter → Calls → Acknowledge → Chats reply reaches the customer → hand back → bot answers again; menu two taps on the real stack
- [x] **Exit:** each screen's "Done when" list passes, minus the Later parts
- [ ] Assumption for Aman: the contract has no bill read endpoint, so the sheet sums the session's order totals, which code already priced (integer paise, no discount or tax maths; one helper `billTotal`). "Bill delivered" shows the API's total. A `GET /bills/{table_session_id}` would remove this; it needs a contract change

## S16 · End to end

- [x] Playwright set up (`frontend/playwright.config.ts`, `frontend/e2e/`; reads the git-ignored env files; `E2E_CHROMIUM` can point at an already-downloaded Chromium)
- [x] Playwright: goal path with simulator and console side by side (`e2e/orders.spec.ts` order → Served; `e2e/service.spec.ts` call waiter → reply → hand back)
- [ ] Playwright: edit while `NEW`, edit after `ACCEPTED`, sold out mid-order, allergy handoff, call waiter, delay, cancel with Other text
- [ ] Playwright: Profile → comment → profile updated → Undo, seen on two consoles
- [ ] Playwright: dead letter and ambiguous send via fault injection (one message, never two)
- [ ] Manual dry run of the service items that don't need a real number
- [ ] `scripts/run-20.ps1`: 20 stub runs + 3 real-model runs; no lost, duplicate or wrong orders
- [ ] **Exit:** the [goal](ordering-slice.md#goal) is met
