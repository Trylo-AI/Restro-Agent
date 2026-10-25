# Local build: everything that doesn't need Meta, GCP or a café

The rest of the MVP that can be built and tested on the local stack (simulator, hosted Supabase dev project, stub or free-tier model) while Meta verification, GCP and the pilot café are pending. Progress: [local-build-checklist.md](local-build-checklist.md).

**Out of this plan (needs something external):** real WhatsApp number and template submission (Meta) · cloud deploy, real Pub/Sub / Cloud Tasks / Vertex, load at 100 conversations next to the DB (GCP) · running the full eval set (model quota) · café dry runs, training, printed QRs (pilot café) · template sync with Meta.

**Rules:** same as the ordering slice: docs first, exact names, contracts change only in step L1 (this plan is the task that names them), synthetic data only, every step ends with tests and an assumptions note, local commits only.

## Steps

| Step | What | Priority (plan.md) | Docs |
|---|---|---|---|
| **L1** Contracts | `openapi.yaml`: every endpoint L2–L10 use (menu create / edit / import, settings, tables, team, customer delete, consent export, campaigns, apply-code, bill read). `schema.sql` + migration: the analytics views, `v_staff_names`, feedback / consent columns if missing. Console types regenerated | — | data-and-api, console |
| **L2** Menu editing | `POST /menu`, `PUT /menu/{id}` (version), `POST /menu/import` (CSV, all-or-nothing, `;` allergen separator); Menu screen: New item, Edit dialog (every allergen state), Import with template download and row errors | P0 | backend#menu, console#menu |
| **L3** Feedback and opt-in | `feedback` timer after `SERVED` (café delay, default 30 min) → rating list → reply rules → `low_rating` request (Calls, `manager`+) → opt-in ask (Yes / No, `wording_version` 1, re-ask rule); counter QR opt-in; opt-out sources `keyword` / `button` / `meta_block` | P1 | backend#feedback, #consent |
| **L4** Staff names and notifications | `v_staff_names` → "Acknowledged by Ravi", comment authors, "Meera is replying"; in-app cancellation notifications (bell, list, ack, ack all) for `manager`+ | P1 (gap) | console#shell |
| **L5** Bill read and redemption | `GET /bills/{table_session_id}` (totals by code); `POST /bills/{id}/apply-code` (discount = % of subtotal, open-decision default) with the two error texts; bill sheet code field | P1 / P2 | backend#redemption |
| **L6** Setup | Tabs Tables & QR (add / edit, QR per table, printable sheet of all) · Team (invite by email through Supabase Auth, role change, deactivate; owner rules) · Settings (hours, cutoff, tone, feedback delay) · Alerts (alert number, sound tests) | P1 | console#setup |
| **L7** Service report and Analytics | Views `v_prep_times`, `v_service_report`, `v_feedback_summary`, `v_daily_revenue`, `v_top_items`, `v_customer_segments`; `/service-report` (`manager`+) and `/analytics` (`owner`, 7 / 30 / 90 days) | P1 / P3 | console#analytics |
| **L8** Customers | `v_customer_profile`; list (search, segment chips, 50 per page), profile, consent register + CSV export, **Delete customer data** (`data_deletion_requests`, audited) | P2 | console#customers, architecture#data-protection |
| **L9** Campaigns | Wizard (Audience → Template → Message → Schedule → Review), audience preview, schedule / cancel, sender (`campaign_start`, `campaign-send` task, deferral at the daily limit, `campaign_resume`), marketing sends through the simulator, `v_campaign_funnel` report, read-only templates | P2 | backend#campaigns, console#campaigns |
| **L10** Today and weekly report | Owner home: four KPIs, Needs attention, Service right now, Next campaign; `weekly_report` job (Mon 09:00) stored for the Today card | P2 | console#today |
| **L11** Eval set | Grow `evals/cases` from 32 to 150+ synthetic cases across intents, Hinglish, allergens, edits, off-topic, human asks; runner reports skipped when rate-limited (running them needs quota) | Phase 2 | rules#llm-evals |
| **L12** Load-test harness | `loadtest/`: signed-webhook generator and scenarios (steady 10–20, peak 60, burst), checks for lost / duplicate messages and p95; run what this PC allows | Phase 3 prep | rules#load-tests |
| **L13** Public pages | Landing, privacy policy, terms, data-deletion request page (static, for Meta verification and the consent wording) | Phase 0 | plan#work-split |
| **L14** Cloud adapters and infra scripts | Pub/Sub publisher and Cloud Tasks queue behind the existing interfaces (unit-tested with fakes), `infra/` gcloud scripts, deploy workflows. Written and reviewed, not run until GCP | Phase 3 prep | architecture#infrastructure |

Order: L1 → L2 → L3 → L4 → L5 → L6 → L7 → L8 → L9 → L10 → L11 → L12 → L13 → L14. Each step: backend + tests, console + tests, e2e where the flow crosses WhatsApp, checklist ticked, local commit.

## Defaults applied (open decisions: say if any should change)

| Topic | Default used | Source |
|---|---|---|
| CSV allergen separator | `;` | plan.md open decisions |
| Prep time range | 1–120 min | plan.md |
| Discount | Percentage of subtotal | plan.md |
| Campaign cancel | `scheduled` and `sending`; queued recipients not sent | plan.md |
| Opt-in wording | Draft as version 1 | plan.md |
| Consent re-ask | Not within 30 days after "No" | plan.md |
| Opt-out `source` | `keyword`, `button`, `meta_block` | plan.md |
| Staff names | Read-only `v_staff_names` (`cafe_id`, `user_id`, `display_name`) for members | proposed in S13; built here |
| Bill read | `GET /bills/{table_session_id}` | proposed in S15; built here |
| QR "PDF of all" | A printable QR sheet page (the browser prints it to PDF); no PDF library | this plan |
| Team invite | Supabase Auth invite email (the dev project's built-in mail, rate-limited) | this plan |
| Weekly report | Computed Mondays 09:00 and shown on Today; not sent on WhatsApp (no template for it) | this plan |
| Platform screens and endpoints | Not in this plan: they need the `platform_admin` login (OPS_PLAN Q-E, later) | this plan |
| Campaign discount | New column `campaigns.discount_percent` (1–90): the schema had nowhere to store "% of subtotal" | this plan |
