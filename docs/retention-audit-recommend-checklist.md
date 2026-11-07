# Retention, audit and recommendations checklist

Tracks [retention-audit-recommend.md](retention-audit-recommend.md). A track is Done when its tests pass, its docs are updated and its "Done when" holds on the local stack.

Rules:
- Track 0 merges first. Tracks A to E then run in parallel.
- Edit only the files listed for your track. A file you need that isn't listed: stop and ask.
- `docs/backend.md`, `docs/console.md` and **this checklist** are merge-time files: while a track runs, its ticks and doc text live in its PR description; at its turn in the merge order (A, C, E, B, D) it rebases on `main` and adds one last commit touching only its own sections and ticks.
- Every unticked item at the end carries a one-line deviation.

| Track | Status | Commit |
|---|---|---|
| 0 Foundation | Done (not committed) | |
| A Campaigns backend | Done (not committed) | |
| B Campaigns console | Done (not committed; e2e not run) | |
| C Audit backend | Done (not committed) | |
| D Audit console | Done (not committed; e2e not run) | |
| E Recommendations backend and evals | Done (not committed) | |
| Final verification | Not started | |

## 0 · Foundation

### Migration
- [x] Column names the views use checked against `contracts/schema.sql`: `wa_messages` (`sent_at`, `attempted_at`, `failed_at`, `error_code`, `cost`, `idempotency_key`), `campaigns` (`code_prefix`, `segment`, `template_name`, `valid_until`, `scheduled_at`, `discount_percent`), `consents` (`status`, `source`), `cafes.timezone`, `tables.label`. Any rename noted here (none at review time) (checked: every name exists, none renamed)
- [x] `supabase/migrations/20261101000001_retention_audit_recommend.sql` written with sections 1 to 5:
  - redemptions columns, constraints and indexes
  - audit columns, `audit_log_backfill_derived()` + `revoke all … from public, anon, authenticated` + call, indexes, `audit_log_cafe_at` dropped
  - `llm_calls_purpose_check` with `recommend`
  - `v_campaign_recipients`, `v_campaign_insights`, `v_redemptions`
  - grants
- [x] `supabase/rollback/20261101000001_retention_audit_recommend_down.sql` written in the repo convention (`begin`, replica role for synthetic deletes)
- [x] Applied to a fresh local stack; rollback applied; re-applied cleanly (plain local Postgres 16 with a Supabase shim, no Docker: `reset` from scratch applied every migration; rollback applied, migration re-applied)
- [ ] Applied to the dev project — **Deviation:** skipped by instruction (no hosted project in this run); apply with the next dev deploy

### Contracts
- [x] `contracts/schema.sql`:
  - `redemptions` body + 2 constraints + 2 indexes
  - `campaign_recipients_customer`, `inbound_messages_conversation`
  - `llm_calls.purpose` + `recommend`
  - `audit_log` + 5 columns (`severity not null`, final state) + 6 indexes replacing `audit_log_cafe_at`
  - 3 views after `v_campaign_funnel`
- [x] `contracts/openapi.yaml`:
  - tags `offers`, `audit`
  - paths `/offers/lookup`, `/offers/redeem`, `/campaigns/{id}/recipients/export`, `/audit/entries`, `/audit/facets`, `/audit/catalog`, `/audit/export`, `/orders/{id}/history`
  - 12 `Audit*` parameters, 8 offer schemas, 9 audit schemas
  - apply-code description; campaign draft summaries (`campaign.draft`); info description
  - `already_redeemed_this_visit` in the `Error` code enum
  - filter-only actions `order.accept` / `order.ready` / `order.serve` described; `AuditCatalog.actions[].filter_only`
- [x] `OrderStatus` reference resolves (it exists in `openapi.yaml`)
- [x] `contracts/llm_output.schema.json` **not** touched (Track E owns it)
- [x] `cd frontend && npm run gen:api`: `src/lib/api/schema.d.ts` regenerated, not hand-edited

### Backend wiring
- [x] `backend/app/api/staff/offers.py` and `audit.py` created as empty route modules
- [x] `backend/app/api/staff/__init__.py` imports `audit` and `offers`
- [x] App starts; `GET /openapi.json` lists no new routes yet (stubs are empty)
- [ ] `supabase/seed/demo_retention.py` and `demo_audit.py` created as no-op stubs; `generate.py` calls both after `seed_retention`; seed still runs — **Deviation:** stubs and hooks done, but the seed wasn't run end to end: `generate.py` needs the Supabase Auth Admin API, which this local Postgres doesn't have. Imports and both stubs were checked

### Console wiring
- [x] Placeholders: `features/redeem/RedeemScreen.tsx` (`RedeemScreen`, `RedeemPanel`, shift_manager guard); `features/audit/index.ts`, `AuditScreen.tsx`, `OrderHistory.tsx` (owner guard → `/orders`)
- [x] `App.tsx`: lazy `RedeemScreen`, `AuditScreen`, `OrderHistoryScreen`; routes `/redeem`, `/audit`, `/audit/orders/:orderId` before `*`
- [x] `Shell.tsx`:
  - `NAV` Redeem offer (`TicketPercent`, short "Redeem")
  - owner group Audit log (`ScrollText`) after Setup
  - `TITLES` `/redeem`, `/audit`
- [x] Tab bar at 375 px with 5 items: no horizontal scroll (the tab bar now sets one grid column per entry (inline style in `Shell.tsx`, `app.css` untouched); `App.test.tsx` asserts 5 links and `repeat(5, 1fr)`; the 375 px e2e check is Track B's `redeem.spec.ts`)

### Tests
- [x] `backend/tests/integration/test_migration_retention.py`:
  - counter shape accepted, bad shapes refused
  - `discount_paise ≤ bill_paise`
  - idempotency unique per café
  - old-style table-bill insert works
  - purpose `recommend` accepted and `foo` refused
  - backfill (`order.cancel` → critical, `order.transition` → info, subjects from `change`)
  - `audit_log` still append-only
  - an insert without severity still works (before 0002; Track C removes this case)
  - `audit_log_backfill_derived()` not executable by `anon` / `authenticated`
- [x] `test_rls.py`: (written; not run locally: needs Supabase Auth + PostgREST. The same grants, policies and views are exercised at DB level in `test_migration_retention.py` as `authenticated` with JWT claims)
  - `v_campaign_recipients` / `v_campaign_insights` owner-only
  - `test_redemptions_view_member_only_own_cafe`
  - `v_redemptions` client writes refused
  - `test_owner_cannot_select_audit_log_via_supabase`
- [x] `App.test.tsx`: Redeem offer in nav + tab bar for shift_manager; Audit log nav owner-only; `/audit` as manager → `/orders`

### Docs
- [x] `docs/data-and-api.md`:
  - schema rows
  - schema-rules backfill note
  - RLS rows
  - 3 analytics view rows
  - API exceptions sentence
  - errors (`code_already_used`, `already_redeemed_this_visit`, `invalid_code` details)
  - idempotency (counter key)
  - endpoint rows for all 8 paths
  - campaign draft audit
- [x] `docs/decisions.md`: entries 18, 19, 20; §8 amended; smaller-decision rows (including D14a local-day expiry and D28a accept filter)
- [x] `docs/plan.md`: 8 open-decision rows; features rows (Audit log P2, counter redemption, personal picks + mood)
- [x] `docs/architecture.md`: backend layout (`campaigns`, `audit`, `ai_agent`, `recommend`); data protection; recommend cap
- [x] `docs/rules.md`:
  - audit Must line
  - `ai_agent` recommend line
  - Tests rows Redemption, Audit, Recommendations
  - recommend eval gate
- [x] `docs/console.md`: Navigation; Screens rows; placeholder headings `### Redeem offer`, `### Audit log`
- [x] `docs/backend.md`: placeholder headings `### Recommendations`, `## Audit`
- [x] `CLAUDE.md` Stack LLM row: "One structured call per free-text turn or Recommend tap (at most one per turn)"

### Checks
- [x] `py -3.12 scripts/check_contracts.py` passes
- [x] `ruff check`, `mypy` pass; the existing `pytest` suite (unit + integration) is green
- [x] `npm run typecheck`, `npm run lint`, `npm test` pass
- [x] Assumptions note written below this section

**Assumptions and notes (Track 0)**
- Migration and rollback are verbatim from the plan. The migration prints the backfill row count (`select audit_log_backfill_derived()`), which is harmless.
- `contracts/schema.sql` holds the final state (`audit_log.severity not null`); the DB keeps it nullable until Track C's `20261101000002`.
- `openapi.yaml`: the plan's YAML blocks as given. Additions it asked for in prose: the `AuditAction` parameter is written in block form with a description naming the filter-only actions; `AuditCatalog.actions[].filter_only` is an optional boolean; `already_redeemed_this_visit` is appended to the `Error` code enum.
- `docs/data-and-api.md`: `/offers/*` rows say `shift_manager`+ without "support" (the plan doesn't mark them); the owner screens' rows (`/audit/*`, history, recipients export) say "`owner`, support" like their siblings and D15.
- Tab bar: the plan keeps `styles/app.css` untouched (D41) but its `.tabbar` grid has 4 fixed columns, so `Shell.tsx` sets `gridTemplateColumns` from `NAV.length`.
- `test_migration_retention.py` self-skips the backfill and "insert without severity" cases once `20261101000002` lands (function dropped / column required), so a shared DB never fails other tracks; Track C may still delete them.
- `docs/console.md` Navigation: Redeem is in the phone tabs for `shift_manager` and `manager`; the owner row keeps its tab list (the shell shows the same 5 Service tabs to every role today).

## A · Campaigns backend

### Domain and repositories
- [x] `db/repositories/redemptions.py`:
  - `CodeInfo` + `customer_id`, `code`, `campaign_name`, `offer_text`, `sent_at`, `consent`
  - `code(..., lock)` compares `r.code = upper($2)` (keeps `r.cafe_id = $1`), with `for share of r, c` when locking
  - `Redemption` + `channel`, `bill_paise`, `redeemed_by_name`, `table_label`
  - `insert` returns `str | None` with `channel` / `bill_paise` / `idempotency_key`; `on conflict do nothing` with **no** conflict target
  - `by_idempotency_key`, `recent_for_customer` (both channels), `search_customers`, `offers_for`, `open_bills`, each filtered by `cafe_id`
- [x] `domain/campaigns/redemption.py`:
  - `InvalidCode` / `CodeAlreadyUsed` moved; `AlreadyRedeemedThisVisit`
  - `normalise_code`, `classify_query` (dashless code match), `verdict` (precedence as in the plan; expiry by café-local day, D14a), `explain` (texts and warnings incl. "redeemed in the last 60 min", in `cafes.timezone`), `discount_paise`, `raise_for`, `check_code`
- [x] `lookup()`: 3 query kinds; `%` and `_` escaped; ≤ 20 + `truncated`; eligible first; ≤ 10 offers each; open bill; masked phones via `privacy.masked()`; no `wa_id`; no `q` in logs
- [x] `redeem_at_counter()` in one `tx()`: bounds → unlocked code read → customer row lock → key replay (same code and amount 200, else 400) → locked check (`raise_for`) → 60-minute rule → discount → insert `on conflict do nothing` → audit `offer.redeem` (D28, `channel: counter`) → `total_paise`
- [x] `domain/bills/__init__.py`: (the bill's `table_sessions` row is locked first and the bill is read after the locks; see the notes)
  - re-exports the errors
  - unlocked code read → customer row lock → `check_code(lock=True)` (same lock order as the counter), with the same-session no-op kept
  - old error texts unchanged ("today" judged in `cafes.timezone`; new "…used today at the counter." for a counter redemption)
  - insert passes `channel="table_bill"`, `bill_paise=subtotal`
  - audit D28 payload
- [x] `db/repositories/campaign_insights.py` `recipient_rows`
- [x] `domain/campaigns/export.py`: 404 for another café; header as in the plan; integer rupees; formula escape; deleted → blank name and "deleted"; `campaign.export` audit in its own `tx()`
- [x] `domain/campaigns/__init__.py` `save_draft` writes `campaign.draft` (`campaign_id`, `new`, `fields`, `discount_percent`; no offer text); `offer_message` "valid till" in the café timezone
- [x] `supabase/seed/demo_retention.py`: demo campaign with every recipient status, a table-bill and a counter redemption, an opt-out after send, one eligible code; idempotent (checked on local Postgres in a rolled-back transaction; `generate.py` end to end needs Supabase Auth)

### API
- [x] `api/staff/offers.py`: `POST /offers/lookup`, `POST /offers/redeem` (`shift_manager`, `extra="forbid"`, `Idempotency-Key` 8 to 80 required)
- [x] `api/staff/campaigns.py`: `GET /campaigns/{campaign_id}/recipients/export` (owner, `text/csv`, attachment)
- [x] Responses match the OpenAPI schemas (`OfferLookup`, `CounterRedemption`) field for field

### Tests
- [x] `tests/unit/test_redemption_rules.py`: all 17 tests in the plan (incl. local-midnight expiry and the offer-message date)
- [x] `tests/integration/test_offers.py`: all 23 tests in the plan (incl. same-key concurrent, counter vs table bill concurrent, dashless code, lookup warnings) (plus an error-trace test)
- [x] `test_bills.py`: existing tests unchanged and green; `test_table_bill_records_channel_bill_paise_and_verdict_details`; `test_offer_redeem_payload_has_d28_keys` (plus two review-fix concurrency tests)
- [x] `test_campaigns.py`: `test_campaign_draft_audited_on_create_and_edit`; offer message date in café timezone
- [x] `loadtest/perf_api.py`: cases `offers_lookup`, `campaign_insights_read`; numbers recorded here (cases added; DB-level numbers in the notes below, the HTTP run needs the dev stack)

### Docs
- [x] `docs/backend.md` `### Redemption` (merge-time commit):
  - two channels; verdict precedence and reasons
  - held / cancelled / opt-out rules
  - counter amount, rounding, bounds, key, one per visit
  - audit payload; no undo
- [x] `docs/backend.md` `### Campaigns` report definitions (ladder, window, came-back revenue)

### Checks
- [x] `ruff check`, `mypy`, `pytest` green; `check_contracts.py` green
- [x] Logs inspected for a lookup and a redeem: ids only (no name, digits, code or `q`) (traces of redemption errors hold the error code only)
- [x] Assumptions note written

**Assumptions and notes (Track A)**
- The customer row lock is `for no key update`, not the plan's literal `for update`. It serialises the two tills, the till and the bill, and same-key retries in exactly the same way, and it still lets a new order reference the customer (the FK check takes `key share`).
- The table bill also locks its `table_sessions` row (`for no key update`, `redemptions.lock_session`) before the customer row, and reads the bill only after its locks (review fix). Without this, a double tap returned a full-price bill with 200, and two codes applied at once both went onto one bill. The counter never locks a session, so the lock order stays consistent.
- Redemption errors (`StaffMessage` subclasses, including the bill's new `OneCodePerBill` 400) have `str()` = the error code, so traces (`ops_runs.failure_reason`, fingerprints) never hold a first name, a code or a staff name. The response message is unchanged.
- `POST /offers/redeem` checks the amount bounds in the domain, not in pydantic, so the 400 reads "Check the amount: between ₹ 1 and ₹ 1,00,000." A non-integer amount is still a pydantic 400. The OpenAPI min / max are unchanged.
- Lookup also refuses a blank or 1-character query (400 "Type at least 2 characters."). Before, `"   "` passed min_length and matched every customer.
- If a query that looks like a code (`CODE_RE`) finds nothing, it is retried as a name, so hyphenated names ("Jo-Ann") are still found.
- `explain` adds " by <staff>" to the table-bill "Used on …" reason when the staff name is known.
- A not-found code returns 422 with `details.verdict = not_found` on both channels (the plan shows a bare `InvalidCode()`). The message is the unchanged generic text.
- `offer_message(camp, first, code, tz)` takes the café timezone, read in `send_one`; the default is `Asia/Kolkata`.
- Demo seed: Sana's opt-out is timestamped after her seeded opt-in, so it is her latest consent. Codes `DIWALI-*` are fixed; Meera's `DIWALI-M9P` is the eligible one. Checked by calling `demo_retention.seed` on local Postgres in a rolled-back transaction; `generate.py` end to end needs Supabase Auth.
- Perf: `perf_api.py` cases were added, but the HTTP run needs Supabase Auth + PostgREST (dev stack). DB-level timings on local Postgres (300 customers + the demo campaign, 50 runs): lookup queries p50 9.0 ms / p95 26.5 ms; `v_campaign_insights` read p50 2.7 ms / p95 13.3 ms.
- Extra tests beyond the plan: `test_redemption_errors_leave_no_name_or_code_in_the_trace` (test_offers.py); `test_double_tap_same_code_every_response_carries_the_discount`, `test_two_codes_at_once_on_one_bill_one_wins` (test_bills.py).

## B · Campaigns console

### Library
- [x] `lib/offers.ts`: `useOfferLookup` (key `["offer-lookup", cafeId, q]`, disabled for < 2 characters and 1 to 3 digits), `useRedemptionsToday` (30 s; `cafe_id` + café-local date), `redeemAtCounter` with `Idempotency-Key` (`newIdempotencyKey` from `lib/api/client`), types from the schema
- [x] `lib/campaignInsights.ts`: `useCampaignInsights` (60 s), `useCampaignRecipients` (range + exact count, status / outcome / name filters), `useRedemptions`, `exportRecipients` (Blob), `bestCampaign`, `byStaff`, `byDay`; every view read has `.eq("cafe_id", cafeId)`

### Redeem
- [x] `features/redeem/RedeemScreen.tsx`: `RedeemScreen` (shift_manager guard) + `RedeemPanel`; search, results, Redeemed today
- [x] `OfferCard.tsx`: masked phone, visits, open-bill chip, offer rows, verdict chip (colour + icon + label), reason and warning from the API, Redeem / Apply to bill (eligible only)
- [x] `RedeemDialog.tsx`: `toPaise` imported from `MenuEditor`; bounds; button repeats the amount; key per dialog (new key when the amount changes); Collect from `total_paise`; inline errors; refetch on 409 / 422; disabled offline (`useOffline` from `app/App`)
- [x] `verdict.ts` mapping for 6 verdicts
- [x] All states: idle, searching, no match, no offers, truncated, error + Retry, offline

### Campaigns
- [x] `labels.ts` extracted from `CampaignsScreen.tsx` (no behaviour change)
- [x] `CampaignsScreen.tsx`: tabs Campaigns · Insights · Redeem; dispatch `new` / `insights` / `redeem` / uuid; list numbers from `v_campaign_insights`
- [x] `CampaignDetail.tsx`:
  - Overview (funnel, side counts, 4 tiles, by-day bars, glossary)
  - Recipients (50 per page, search, status and outcome chips, cards on phones, count line)
  - Redemptions; Opted out
  - Export CSV; Cancel / Stop
- [x] `CampaignInsights.tsx`: period chips; KPI tiles (revenue sub notes per-campaign attribution); best campaign (≥ 10 delivered); sortable comparison; by day (`StackedBars` from `features/ops/charts`); by staff; recent 25; empty state
- [x] `styles/campaigns.css` imported by the feature components; `app.css` untouched
- [x] Money only via `inr()` from view or API paise; masked numbers only

### Tests
- [x] `redeem.test.tsx`: 11 cases in the plan
- [x] `campaigns.test.tsx`: 8 cases in the plan
- [x] `campaignInsights.test.ts`: pure helpers
- [ ] e2e `redeem.spec.ts`: last-4 search → counter redeem → Collect → Redeemed today → second attempt "Already used", at 375 px — **Deviation:** written, not run: needs Supabase Auth, PostgREST, the simulator and Track A's `/offers/*` on a running stack (no Docker here)
- [ ] e2e `campaigns.spec.ts`: Phase 5 gate green; detail and Insights show Came back and Redeemed — **Deviation:** additions written (incl. the 375 px table check), not run: needs the full stack

### Docs
- [x] `docs/console.md` `### Campaigns` (tabs, detail, insights, Done when) and `### Redeem offer` (layout, dialog, states, Done when), merge-time commit

### Checks
- [ ] `npm run typecheck`, `lint`, `test` green; 375 px with no horizontal scroll on every new screen — **Deviation:** typecheck green; lint and vitest green on Track B's files; 375 px handled in CSS (card tables, flex-wrap) but not checked in a real browser (the e2e checks it)
- [x] Assumptions note written

**Assumptions and notes (Track B)**
- `v_campaign_insights` / `v_campaign_recipients` / `v_redemptions` bigint and numeric columns may arrive as strings, so they are normalised with `Number()` once.
- `whatsapp_cost` is shown as a plain number (not paise), matching the existing "—" copy.
- The lookup's 400 text for 5+ digits is shown as the API sends it.
- "Former staff" means `redeemed_by` is set but the view has no name for it (membership removed). A shared `staffLabel()` in `lib/offers.ts` gives the same label on every screen.
- `useCafeTimezone()` lives in `lib/offers.ts` and reads `cafes.timezone` once (staleTime Infinity, default `Asia/Kolkata`), so `lib/data.ts` stays unedited (D41).
- Redeem dialog: amounts under ₹ 1 are refused in the console with "Check the amount: at least ₹ 1" (the API's lower bound); the plan only names the over-₹ 1,00,000 text.
- Recipients outcome chips: Redeemed and Not redeemed exclude each other (picking one clears the other), since AND-ing them always matches nothing.
- Review fixes: the Recipients name search is debounced 300 ms (`DEBOUNCE_MS` from `RedeemScreen`); `useCampaignRecipients` and `useRedemptions` keep the previous data within the same café and campaign while the next query loads (`keepWithin`); the Insights comparison and the Campaigns list are card tables on phones (no `.table-scroll` wrapper), and the comparison has a phones-only "Sort by" select and Ascending / Descending toggle.
- Side effect of keeping previous data in `useRedemptions`: when the Insights period changes, the previous period's redemption panels stay on screen until the new data arrives, instead of "Loading…". The first load still shows "Loading…".
- `CampaignDetail.tsx`: the header status follows the 60 s-refetched `v_campaign_insights` row; the stacked-bar colours use `COLOR.p50` / `COLOR.p95` from `features/ops/charts`.
- vitest and eslint were run on Track B's files only while the tracks ran in parallel; the full suite runs in Final verification.

## C · Audit backend

### Write path
- [x] `domain/audit/catalog.py`:
  - `CATALOG` covers every action in the plan table (including `campaign.draft`, `campaign.export`, `offer.redeem`, `offer.void`, `audit.export`, platform actions and every action written in `api/staff/cafes.py`)
  - `classify`, `subjects`, `actions_in`, `match_labels`, `describe`, `details`, `public`, `rupees`
  - `FILTER_ALIASES` `order.accept` / `order.ready` / `order.serve` (filter-only, D28a)
- [x] `domain/audit/__init__.py`: `FORBIDDEN_KEYS` guard (recursive into nested dicts / lists); severity from `classify`; subjects passed
- [x] `db/repositories/audit_log.py`: `insert` with severity, subjects and the `actor_name` subquery; `AuditRow`; `search` (fixed fragments, bound values, flagged-index literal, alias predicates, customer + conversation, `q` escaped, keyset); `facets` (aliases counted separately); `for_order`
- [x] `supabase/migrations/20261101000002_audit_severity_not_null.sql` + rollback; applied locally after the new write path (full down / up cycle checked in a rolled-back transaction)
- [x] `tests/integration/test_migration_retention.py`: the nullable-era cases removed (insert without severity; direct backfill call)

### Call sites
- [x] `domain/orders/__init__.py`: `_transition`, `undo` + `customer_id`
- [x] `domain/orders/service.py`: `staff_delay` + `customer_id`, `has_note`; `manual_order` + `lines`, `total_paise`, `has_allergy_note` (bool; `allergy_note` is a forbidden key)
- [x] `domain/handoff/service.py`:
  - `takeover(..., via, table_session_id)` with `from_mode` / `from_assigned_to` read before `set_mode`
  - `reply` (`via="reply"`, `customer_id`), `open_for_table` (`via="table"`), `handback`
  - `ack` / `resolve` links; `deliver_bill` payload
- [x] `domain/menu/__init__.py` `save_item`: price, availability and allergen diffs (allergens read before replace, same transaction)
- [x] `domain/setup/__init__.py`: `invite` + `user_id`; `update_member` + `user_id`, `from_role`, `from_active`
- [x] `domain/customers/__init__.py` `undo_note` + `ops`
- [x] No `change` holds a phone, email, note or message text (checked by the guard + tests)

### Read path
- [x] `db/repositories/order_history.py`: `order_header`, `events`, `escalations` (each filtered by `cafe_id`)
- [x] `domain/audit/viewer.py`:
  - `AuditFilters`, `window`, cursor encode / decode, `entries` (order number resolved, categories ∩ actions, `#42` → number, batched names), `facets`
  - `export_csv` (10 000 cap, formula escape, `audit.export`)
  - `order_history` (merge without duplicates, 200 cap, 404 for another café), `catalog_json`
  - log `audit_read` without `q`
- [x] `api/staff/audit.py`: 5 owner-only GET routes; query validation → 400; CSV filename `audit-log-<local date>.csv`
- [x] Responses match the OpenAPI audit schemas field for field

### Seed
- [x] `supabase/seed/demo_audit.py` (Track 0's stub; `generate.py` untouched): ~40 demo audit rows through `audit.write` (staff accept, staff cancel, undo, takeover from a teammate, allergy removal, price change, redemption), only when the café has none (checked on a throwaway local café: 27 rows, idempotent; `generate.py` end to end needs Supabase Auth)

### Tests
- [x] `tests/unit/test_audit_catalog.py`: every test in the plan, including the AST scan and the sentence table
- [x] `tests/unit/test_audit_viewer.py`: 8 tests
- [x] `tests/unit/test_audit_api.py`: owner-only on 5 routes; validation 400
- [x] `tests/integration/test_audit_log.py`: all 24 tests in the plan (incl. backfill after 0002 and the accept filter)
- [x] Re-run unchanged and green: `test_conversations.py`, `test_help_and_menu.py`, `test_setup.py`, `test_menu_edit.py`, `test_customer_profile.py`, `test_bills.py`
- [x] Perf: staff transition and menu save p95 delta ≤ 2 ms; `/audit/entries` p95 < 300 ms at 200 k rows (numbers recorded here) (measured at the `audit.write` level from a scratch script; see the notes)

### Docs
- [x] `docs/backend.md` `## Audit` (merge-time commit): write contract, catalogue table, filter aliases, payload per action, forbidden keys, read rules, support-mode follow-up

### Checks
- [x] `ruff check`, `mypy`, `pytest` green; `check_contracts.py` green
- [x] Assumptions note written

**Assumptions and notes (Track C)**
- `classify` takes an extra keyword `actor_id`, needed for "took the chat from a teammate" (`from_assigned_to` ≠ actor). The four positional arguments are the plan's.
- The plan gave no reason texts for these, so I chose them: `menu.update` "Allergens changed" / "Price changed"; `team.update` legacy "Access changed (details not recorded)"; `campaign.cancel` "Campaign stopped"; `offer.redeem` override "Redeemed with an override"; `offer.void` "A redemption was reversed".
- `customer.profile_undo` stores the comment's own ops (the ones being undone). An undone `add` of an allergy removes it, so the row is critical, and the sentence shows the reversal ("removed allergy …").
- `takeover` locks the conversation row (`for update`) before reading `from_mode` / `from_assigned_to`. Without the lock, two staff tapping Take over at once both read bot / none; the second takes the chat from the first, but its row is `info` with no teammate named. `test_concurrent_takeovers_record_the_teammate_they_took_from` holds the row lock, queues both taps and checks that the second row names the first, as a warning. The test fails without the lock.
- `q` matches action and category labels at the start of a word only, so "served" never hits "(reserved)"; a `q` that only matches mid-word in a label (e.g. "ancel") no longer matches that action. It also matches the filter-only alias labels ("Order accepted", "Order marked ready", "Order served") as `order.transition` rows with that `to` (`catalog.match_aliases` → `Where.q_aliases`). Staff, dish, table and customer name matching is still a substring `ilike`.
- `test_backfilled_rows_have_severity_after_0002` runs the following inside one transaction that is then rolled back: the 0002 rollback, the 0001 backfill function (read from the migration file), legacy rows without severity, then the 0002 migration. It is the only way to write pre-0002 rows once 0002 is applied.
- Rollback / re-apply: after the 0002 rollback alone, 0002 can't be re-applied, because its function is gone; 0001 has to be rolled back and re-applied first, as the plan says. The full cycle (0002 down, 0001 down, 0001 up, 0002 up) was checked in a rolled-back transaction rather than on the shared DB.
- `demo_audit.py` writes through `audit.write`, which stamps every row with the transaction time. In the same seed transaction it then spreads the rows over their order and action times with one UPDATE of `at`, with the append-only trigger disabled for that statement (seed only; no other column touched). A redemption's audit row takes the redeeming user's role from the seed memberships (`ROLES`), so Riya's redemption is "Kiran (Manager)". Checked against a throwaway local café: 27 rows on the first run, nothing on a second, every stored severity equals `classify`. `generate.py` wasn't run end to end (it needs the Supabase Auth Admin API).
- Perf, from a scratch script (`loadtest/perf_api.py` is Track A's), with 200k rows in a throwaway café on local Postgres 16. `/audit/entries` p95: default 53 ms, critical only 110 ms, `order.accept` 37 ms, actor 35 ms, customer (all time) 48 ms, `q` staff 62 ms, `q` dish 81 ms, 30 days 34 ms, 365 days critical 31 ms. Facets p95: 44–66 ms. `audit.write` p95 is 0.66–1.46 ms above a minimal insert; that is the per-transition delta. Menu save adds one allergen read in the same transaction; it wasn't timed separately.
- Window boundary: a `from` exactly 366 days back with no `to` gets 400, because `to` defaults to the server's current time a moment later. The console presets (at most 30 days) aren't affected.
- Extra tests beyond the plan: `test_owner_reads_the_catalog`, `test_query_validation_400_on_facets_export_and_history`, `test_details_join_the_staff_note_and_never_copy_it`, `test_concurrent_takeovers_record_the_teammate_they_took_from`, `test_match_labels_and_aliases_match_at_word_start`.

## D · Audit console

### Library and helpers
- [x] `lib/audit.ts`: `useAuditCatalog`, `useAuditEntries` (infinite, `next`, 60 s + focus), `useAuditFacets`, `useOrderHistory`, `exportAuditCsv`
- [x] `features/audit/filters.ts`: `parseFilters`, `toSearch`, `toQuery` (local-day presets), `activeChips`, `PRESETS` (`all` only with an entity filter)

### Screens
- [x] `AuditScreen.tsx`:
  - toolbar (presets, search, Critical only + count, Export)
  - summary strip (clickable)
  - filter bar (Category, Action grouped with Order accepted / Order cancelled / Chat taken over first, Person, Role, Order #, Customer)
  - chips + Clear all; day headers; Show older / end message
  - every state in the plan
- [x] `AuditRow.tsx`: critical (border, tint, `CircleAlert`, "Critical", reason), warning (amber, `TriangleAlert`), info plain; no pulse; link chips; expand with details, actions and Technical details
- [x] `AuditFilters.tsx`: desktop bar + phone bottom sheet (Filters (n), presets, Export inside)
- [x] URL sync for every key in the plan; filter changes push history
- [x] `OrderHistory.tsx`: header, cancelled block with the staff note label, timeline with actor icons and severity styling, footer links, 404 and network states
- [x] `styles/audit.css` imported by the audit components; `app.css` untouched
- [x] `OrderCard.tsx`: owner-only History link → `/audit/orders/:id`
- [x] `CustomerProfilePanel.tsx`: owner-only "Staff activity" → `/audit?customer=<id>&range=all`
- [x] Optional `TodayScreen.tsx` card "N critical staff actions today" (done or skipped, noted here) (done: amber card → `/audit?severity=critical&range=today`, owner only, no sound)

### Tests
- [x] `filters.test.ts`: 4 cases
- [x] `audit.test.tsx`: 12 cases
- [x] `orders.test.tsx`: History only for owner; `profile.test.tsx`: Staff activity only for owner
- [ ] e2e `audit.spec.ts`: shift manager cancels → owner sees the critical row → filters by person and order → order history → CSV; 375 px filter sheet; manager redirected — **Deviation:** written, not run: needs Supabase Auth, api, worker, the simulator and the E2E env vars (no Docker here)
- [x] Dry-run item added: "Owner finds who cancelled order #N in under 30 s" (added to `docs/rules.md` § Dry run in the merge step)

### Docs
- [x] `docs/console.md` `### Audit log` (with Done when), `### Live orders` History, `### Customer profile` Staff activity, `### Platform` reuse note (merge-time commit)

### Checks
- [ ] `npm run typecheck`, `lint`, `test` green; 375 px with no horizontal scroll — **Deviation:** typecheck, lint and the Track D and neighbouring suites green (46/46); 375 px checked in CSS and jsdom only (the e2e checks it in a browser); the full vitest suite runs in Final verification
- [x] Assumptions note written

**Assumptions and notes (Track D)**
- In the Person filter, "Restro support" and "AI (from comments)" filter by `role` (`platform_admin` / `staff_via_ai`), because those rows have no user id. Former team members named in the facets are listed with "(inactive)".
- The Today critical-actions card is amber (`critical: false`), because it is history, not an emergency (D26).
- An order-number search ("#42" or "42") stays in the search box rather than moving into the Order # field (review fix). The console treats it as an order filter, so the range defaults to All time and from / to are left out; the API's `viewer.normalise()` turns it into `order_number`.
- The customer typeahead uses `useCustomerMatches` in `lib/audit.ts`: one Supabase read of `v_customer_profile` (customer_id, name, visits; limit 8), debounced 300 ms (review fix). It replaces `useCustomerList`, which also ran 4 segment-count queries. It is the only Supabase read in `lib/audit.ts`.
- The sticky day headers sit below the shell header: `AuditScreen` measures `.header` with a ResizeObserver and writes `--audit-sticky-top` (57px fallback); `app.css` is not changed (review fix).
- The dry-run item was added to `docs/rules.md` in the merge step (Track 0's file). The e2e also checks the 30 s budget.
- `e2e/audit.spec.ts` expects the seeded shift manager's display name to be "Ravi" (`supabase/seed/generate.py`).
- No horizontal scroll at 375 px and the sticky day-header offset were checked in CSS and jsdom only; the e2e phone case checks them in a browser.
- The full vitest suite was not rerun after the review fixes (Track D and neighbouring suites, 46/46); Final verification runs it.

## E · Recommendations backend and evals

### Candidates and ranking
- [x] `db/repositories/recommend.py`: `item_history`, `popular_items` (both take `cafe_id`, exclude cancelled)
- [x] `domain/recommend/__init__.py`:
  - `MOODS`, constants, dataclasses
  - `load_context` (cached allergens per menu version, popular per café 10 min, local time, language)
  - `filter_candidates` (available, allergies incl. `unknown`, diet, dislikes, spice, cart, exclude, cap 60)
  - `fallback_rank` (tiers, ≤ 2 per category, mood boost, near closing, code `why`)
  - `merge`, `sanitise_mood`

### LLM
- [x] `contracts/llm_output.schema.json` (Track E owns it; same PR as `interpret.py`): top description; `turn.intent` + `recommend`; `turn.required` + `recommend`; `turn.properties.recommend`; `$defs.recommend`
- [x] `ai_agent/calls.py`: `timeout_s`, `retry`, `models_override`, `fail_reason` keyword-only; defaults unchanged (existing tests green); `cheap` kill switch still overrides `models_override`
- [x] `ai_agent/guardrails.py`: `CLAIM`, `URL`, `clean_why` (also drops any ASCII or Devanagari digit and "free"); Devanagari amounts in `AMOUNT`
- [x] `ai_agent/recommend.py`: `SYSTEM` rules; menu lines without price; `build_system` cached per language; `suffix` (no allergy, phone or price; first name and preference text letters-only); `rank` (purpose `recommend`, 300 tokens, 4 s, no retry); `validate` (intro and `why` via `clean_why`)
- [x] `ai_agent/interpret.py`: intent `recommend`; `Interpretation.recommend` (mood sanitised, picks resolved to available items, unresolved dropped with `rec_ref_unresolved`, each `why` via `clean_why`); a missing key is treated as null
- [x] `ai_agent/prompt.py`: recommendation rule replaced; `"recommend": null` in the 3 few-shots + one recommend shot; local time in the suffix; `MenuView.rec_system`
- [x] `integrations/llm/stub.py`: `"recommend": None` in default and `menu_words` outputs; `recommend_picks(*names)` responder

### Conversation
- [x] `conversation/recommend.py`: `on_tap`, `from_turn`, `_picks`; `state.rec` updated; strikes reset; cap 6; tracing counts and degraded reasons; log with ids and counts only
- [x] `conversation/router.py`: `menu` alone → categories; `recommend` / `rec_mood` / `rec_more` → handler after the `blocked` check; `set_recommend_handler` (default = today's menu); `_tap` gets `turn`; `item:<id>:rec` counted; docstring updated
- [x] `conversation/render.py`: `MOOD_ROWS`, `recommendations` (≤ 10 rows, DB prices via `inr`, clipping, edit-mode line, allergy caveat), `no_safe_picks`, `no_more_picks`, `categories(with_recommend=True)` (≤ 9 categories)
- [x] `conversation/agent_turn.py`: `recommend` branch (ops then picks; no second call); excluded from clarify strikes
- [x] `main_worker.py` and `tests/integration/conversation_kit.py` register the handler; `core/config.py` 3 settings; `ops/tracing/names.py` 3 reasons

### Tests
- [x] `tests/unit/test_recommend.py`: 26 tests in the plan
- [x] `tests/unit/test_ai_recommend.py`: 11 tests
- [x] `tests/unit/test_render_recommend.py`: 6 tests
- [x] `test_guardrails.py`: claims and Hindi amounts; `test_whatsapp.py`: 10-row list within Meta limits
- [x] `tests/integration/test_recommend.py`: 23 tests in the plan (incl. a turn `why` with a price, typed mood while paused / closed)
- [x] `test_privacy.py`: recommend prompt has no phone, `wa_id` or allergy; `test_conversations.py`: "For you" row accounted for
- [x] Existing `test_ai_agent.py` and `test_conversations.py` green

### Evals
- [x] `draft-v1.json` `rc-*` and `draft-v2.json` `v2-rec-*` expect `recommend` + `min_picks`
- [x] `draft-v2.json` adds `v2-rec-21` to `v2-rec-24`
- [x] `evals/cases/recommend-v1.json`: ~30 `rec2-*` cases covering every category in the plan; seed dish names only
- [x] `evals/run.py`: `recommend` scoring; `--purpose recommend` mode (RecContext from the case + seed menu and allergens)
- [x] `evals/gate.py`: recommend gate (0 outside ALLOWED, 0 amounts or claims, `any_of` ≥ 90 %, p95 ≤ 3 s); the turn gate skips recommend cases
- [x] `--stub` run passes; a real-model run recorded here when quota allows (or marked not run) (`--stub` passes the recommend gate; real-model run not run: needs quota)

### Docs
- [x] `docs/backend.md` (merge-time commit):
  - Conversation (state `rec`, routing item 4, Recommend flow row)
  - AI agent (intro, Output, Prompt, Guardrails)
  - `### Recommendations` filled
  - Customers sentence
- [x] `docs/OPS_PLAN.md`: 3 degradation reasons (if reasons are listed there)

### Checks
- [x] `ruff check`, `mypy`, `pytest` green
- [ ] A simulator run by hand: Recommend tap → picks list → tap a pick → cart summary; mood row; "Other ideas"; typed "kuch thanda" — **Deviation:** not run: `scripts/dev.ps1` starts api and worker against `backend/.env` (the hosted project), which this run must not touch. The same flows run end to end through the real pipeline in `tests/integration/test_recommend.py`
- [x] Assumptions note written

**Assumptions and notes (Track E)**
- `repositories/recommend.py` has a third read, `last_text`: the last typed message, used only to pick the tap reply's language (D37). It is never sent to a model.
- `MOOD_WORDS` / `MOOD_HINTS` (domain/recommend) are the code mood boosts. Typed-mood summaries map to a code mood only for the fallback boost (`guess_mood`). The "light" mood boosts "granola" instead of every "bowl", which would have included the curry bowls.
- `clean_why` claim words: vegan, Jain, health*, nutri*, protein, allerg*, safe*, diet, guilt*, low-cal/fat/sugar/carb, discount, offer(s), deal(s), cashback, voucher, coupon, plus Hindi एलर्जी / सुरक्षित / हेल्दी / स्वस्थ / मुफ़्त / मुफ्त. "free" and "muft" are a separate check.
- Allergy values are stored as allergen ids (`tree_nuts`; profile_note stores the id). `guardrails.normalise_allergen` now treats '_' like a space, so the id, the label and every alias normalise the same way. Integration test `test_free_text_recommend_picks_refiltered_and_topped_up` and eval case `rec2-32` use the stored id form.
- Model text naming a dish: `domain/recommend.names_other_dish` does a whole-name, case-insensitive match against the available menu, after removing the picks' own names (so "Iced Latte" is not "Latte"). It runs in `ai_agent/recommend.validate`, `interpret._resolve_recommend` and a final `_scrub` in `conversation/recommend.py` against the final shown picks. A word that is not a whole dish name ("a cup of chai") is allowed. Hindi transliterations of dish names are not caught.
- A typed recommend that also carries cart ops: strikes are set to 0 before the ops, so a dish not on the menu never adds up to a handoff, and the model `reply` is not sent between the summary and the picks; the picks get a code intro.
- The turn eval cases expect intent `recommend` for every rc-* / v2-rec-* case, as the plan says, including browse-like questions ("any vegan options?", "iced drinks kaun se hain?"). A real-model run may want `browse` accepted for those two (v2-rec-10, v2-rec-18).
- `recommend-v1.json` has 32 cases. A case's `allergens` override marks extra dishes (e.g. peanut-free) on top of the seed, so that allergy cases have more than 3 candidates.
- `--stub` for `--purpose recommend` uses the code ranking as the model, with the code `why` cleaned the same way model text is cleaned, so every case is shown to be satisfiable without the LLM. On Windows, set PYTHONIOENCODING=utf-8 when piping its output (cp1252 cannot print the check mark).
- `gate.py` skips app_run.py reports (no `model` in the summary). Before this change it crashed with KeyError 'output' on them.
- Beyond the plan's lists: `domain/recommend.names_other_dish` and `conversation/recommend._scrub` (review fix); extra tests `test_candidates_capped_at_sixty_by_score`, `test_code_why_and_mood_guess`, `test_language_from_last_text_and_safe_text`, `test_allergy_stored_as_allergen_id_filters_like_its_label`, `test_names_other_dish_whole_name_any_case_picks_exempt` (test_recommend.py), `test_intro_or_why_naming_a_dish_not_picked_dropped` (test_ai_recommend.py), `test_no_safe_picks_and_no_more` (test_render_recommend.py), and three integration tests (kill switch `cheap` over the recommend model; intro or `why` naming a filtered dish, tap and typed).
- CI: the recommend `--stub` gate still needs a step in `.github/workflows/ci.yml` (outside Track E's files; see Follow-ups).
- Real-model eval run: not run (needs quota; reported, not gated). Simulator run by hand: not run (`scripts/dev.ps1` points at `backend/.env`, i.e. the hosted project); the same paths are covered by `tests/integration/test_recommend.py` through the real router, handlers and outbound sender.

## Final verification

- [ ] All tracks merged in the order A, C, E, B, D; no file was edited outside its track list (`git log --name-only` checked against the plan); `docs/backend.md`, `docs/console.md` and this checklist changed only in each track's last (merge-time) commit
- [ ] Fresh local stack: both migrations apply; both rollbacks apply and re-apply; seed runs
- [ ] `py -3.12 scripts/check_contracts.py` green
- [ ] Backend: `ruff check`, `mypy`, full `pytest` (unit + integration) green
- [ ] Frontend: `npm run gen:api` gives no diff; `typecheck`, `lint`, `test`, `e2e` green
- [ ] Evals: `--stub` turn gate and recommend gate green
- [ ] Invariants spot check:
  - grep logs from an e2e run for phone digits and message text (none)
  - audit `change` keys checked against `FORBIDDEN_KEYS` over the seeded DB
  - no float money in new code
  - every new repository method takes `cafe_id`
- [ ] Feature 1 by hand at 375 px (seeded demo campaign): search by name, last 4, code and dashless code; each verdict seen; counter redeem; double tap gives one row; table-bill apply; owner detail tabs, Insights, CSV masked
- [ ] Feature 2 by hand: accept and cancel as shift manager → owner filters "Order accepted" and sees who; the cancel is highlighted; each filter; order history; CSV opens safely in a spreadsheet
- [ ] Feature 3 by hand in the simulator: returning customer, new customer, allergy profile, mood tap, typed mood, LLM off (`LLM_BACKEND` fault) → fallback without handoff
- [ ] Docs: every row of the plan's "Docs per track" table done; CLAUDE.md Docs table and Stack row current
- [ ] Open decisions raised in plan.md are still open (none decided silently)
- [ ] Status table at the top updated with commits

## Follow-ups

Open issues from the tracks that need a file outside the track's list, or a stack this run didn't have.

- [ ] `.github/workflows/ci.yml` (Track E's gate): in the `backend` job, right after `- run: pytest -q -m "not integration"`, add `- run: python ../evals/run.py --purpose recommend --stub` (no DB, keys or `.env`; exits 1 when the recommend gate fails).
- [ ] `backend/app/core/errors.py` (team decision, raised by Track A): record `type(exc).__name__: exc.code` in `ops_runs.failure_reason` for every `AppError`, not only the redemption errors, since other messages can carry personal data.
- [ ] `frontend/src/app/App.test.tsx` (Track 0): "/audit as manager redirects to /orders" races the lazy audit chunk in the full parallel run; use `await screen.findByText("Orders screen", {}, { timeout: 5000 })`. `menu-edit.test.tsx` flaked the same way once.
- [ ] `loadtest/perf_api.py` (Track A's file, for Track C): add a staff transition and a menu save case to confirm the ≤ 2 ms audit delta end to end (measured 0.66–1.46 ms at the `audit.write` level).
- [ ] `features/audit/filters.ts` (Track D): a Custom range of a full 366 days should also send `to`; with no `to` the API's "now" makes the range just over 366 days and returns 400.
- [ ] Support mode, when it ships: every `audit.write` call site passes `support=p.support` in `audit.Actor`.
- [ ] Evals (Track E): if real models answer v2-rec-10 ("any vegan options?") and v2-rec-18 ("iced drinks kaun se hain?") as `browse`, accept `browse` for those two.
- [ ] Dev stack (not available here): apply `20261101000001`, deploy the new `audit.write`, then apply `20261101000002` (D20 order); run `supabase/seed/generate.py` end to end; run `loadtest/perf_api.py` (`offers_lookup`, `campaign_insights_read`) and record the HTTP numbers; run `e2e/redeem.spec.ts`, `campaigns.spec.ts` and `audit.spec.ts` (incl. 375 px); run `test_rls.py`; the real-model recommend and turn evals; a simulator run of the recommend flows.
- [ ] Full-suite re-check on a quiet DB: `test_offers.py::test_concurrent_counter_redeems_one_wins` (one teardown error from `drop_test_cafe` under parallel tracks), `test_audit_log.py` and the `test_bills.py` concurrency tests each failed once while tracks shared the DB and pass alone.
