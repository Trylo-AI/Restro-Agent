# Local build checklist

Tracks [local-build.md](local-build.md). A step is Done when its tests pass and its "Done when" (console.md / backend.md) holds on the local stack.

| Step | Status | Commit |
|---|---|---|
| L1 Contracts | Done | L1–L2 commit |
| L2 Menu editing | Done | L1–L2 commit |
| L3 Feedback and opt-in | Done | L3 commit |
| L4 Staff names and notifications | To do | — |
| L5 Bill read and redemption | To do | — |
| L6 Setup | To do | — |
| L7 Service report and Analytics | To do | — |
| L8 Customers | To do | — |
| L9 Campaigns | To do | — |
| L10 Today and weekly report | To do | — |
| L11 Eval set | To do | — |
| L12 Load-test harness | To do | — |
| L13 Public pages | To do | — |
| L14 Cloud adapters and infra scripts | To do | — |

## L1 · Contracts

- [x] `openapi.yaml`: bill read, apply-code, menu create / edit / import (`text/csv` body, no new dependency), café settings, tables, team invite / update, customer delete, consent export, campaigns (create, edit, audience preview, schedule, cancel); console types regenerated (38 paths)
- [x] Migration `20261025000001_local_build`: `campaigns.discount_percent`, `v_staff_names`, `v_daily_revenue`, `v_top_items`, `v_customer_segments`, `v_customer_profile`, `v_campaign_funnel`, `v_prep_times`, `v_service_report`, `v_feedback_summary`; mirrored in `schema.sql`; applied to the dev project
- [x] Docs: data-and-api (RLS row for `v_staff_names`, `GET /bills`, `discount_percent`, import body)
- [x] Tests: RLS for every new view by role (46 RLS tests pass): staff names visible to teammates of the same café only; owner / manager views empty below their role; masked numbers
- [ ] Later (needs platform login): `v_daily_costs`, `v_unit_costs`, `v_conversation_metrics`, platform endpoints

## L2 · Menu editing

- [x] `POST /menu`, `PUT /menu/{id}` (version → 409 "Someone else changed this item"), one transaction with allergen states, new menu version, audit `menu.update` (changed fields only), `item_sold_out` on open orders when an edit makes a dish unavailable; duplicate name → 400
- [x] `POST /menu/import` (owner): all or nothing, every row / column error, matches by name, unlisted allergens `unknown`, `;` separator, 1 MB / 500 rows, audit `menu.import`
- [x] Console: New item (manager+), Edit per row (manager+), Import CSV (owner) with template download and the error table; edit is a side sheet on desktop and a bottom sheet at 375 px; price typed in ₹ saved as paise; every allergen Contains / Free from / Not sure; 409 → Reload
- [x] Tests: CSV parser (6), API (3: roles, version, allergens, name clash, edit-to-unavailable flags orders, import update + insert + all-or-nothing), console (5), e2e (manager edits at 375 px on the real stack)
- [ ] Not built: dish photo (needs Supabase Storage upload; later)

## L3 · Feedback and opt-in

- [x] `feedback` timer at SERVED (staff Mark served and auto-served) after the café's delay (15 / 30 / 60, default 30); re-checks SERVED, not rated, not in human mode
- [x] Ask: 1–5 list inside 24 h, else `feedback_request` template (new send purpose `feedback`); reply ids carry the order, so taps need no conversation state
- [x] Typed rating while an ask is open (leading digit 1–5, the rest kept as text), found by a DB check, not state
- [x] Replies per rating; 1–2★ → `low_rating` request (bot stays on); after 1–3★ the next text is saved as `feedback.text` unless it's a keyword or names a dish (then a normal turn)
- [x] Opt-in Yes / No (`wording_version` 1) after the rating (after the text for 1–3★); never after STOP / button / 131050; after "No" not within 30 days; counter QR text "Hi, send me offers" asks with source `counter_qr`
- [x] Calls: a low-rating card shows the stars and the customer's words
- [x] Tests: 6 integration (5★ + Yes, typed 2★ with text + low_rating, 3★ text + No + re-ask after 30 days, an order instead of text, STOP blocks the ask but the counter QR still asks), typed-rating parser
- [ ] Not in a browser test: the 15-minute minimum delay; covered by the pipeline tests
