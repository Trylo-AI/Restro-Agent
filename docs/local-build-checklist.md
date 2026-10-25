# Local build checklist

Tracks [local-build.md](local-build.md). A step is Done when its tests pass and its "Done when" (console.md / backend.md) holds on the local stack.

| Step | Status | Commit |
|---|---|---|
| L1 Contracts | Done | L1–L2 commit |
| L2 Menu editing | Done | L1–L2 commit |
| L3 Feedback and opt-in | To do | — |
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
