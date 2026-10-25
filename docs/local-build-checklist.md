# Local build checklist

Tracks [local-build.md](local-build.md). A step is Done when its tests pass and its "Done when" (console.md / backend.md) holds on the local stack.

| Step | Status | Commit |
|---|---|---|
| L1 Contracts | Done | L1–L2 commit |
| L2 Menu editing | Done | L1–L2 commit |
| L3 Feedback and opt-in | Done | L3 commit |
| L4 Staff names and notifications | Done | L4 commit |
| L5 Bill read and redemption | Done | L5 commit |
| L6 Setup | Done | L6 commit |
| L7 Service report and Analytics | Done | L7 commit |
| L8 Customers | Done | L8 commit |
| L9 Campaigns | Done | L9 commit |
| L10 Today and weekly report | Done | L10 commit |
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

## L4 · Staff names and notifications

- [x] Names from `v_staff_names` everywhere the console showed "a teammate": "Acknowledged by Ravi", comment authors, "Meera is replying" (closes the S13 gap)
- [x] Bell in the header for every role: this device's sound (turn on), browser notification permission (allow), WhatsApp backup state (café alert number set or not), sound tests per severity
- [x] `manager`+: in-app notifications (Realtime), unread count on the bell, "#1048 · T7 cancelled by Ravi · Other" with the staff's own text; opening the panel calls `ack-all`
- [x] e2e (two consoles): shift manager acknowledges a call → the manager sees "Acknowledged by Ravi"; shift manager cancels with Other → the manager's bell shows it unread, the panel shows the text, then it's read; a shift manager's bell has no notification list
- [ ] Deviation: tapping a notification doesn't "open the order": a cancelled order has no screen yet (it's off Live orders)

## L5 · Bill read and redemption

- [x] `GET /bills/{table_session_id}`: orders with lines, subtotal, code, discount, total, all computed in one backend function (also used by Bill delivered, which now returns the discounted total)
- [x] `POST /bills/{id}/apply-code`: this café's sent / delivered / read / held codes only, not expired, campaign not cancelled; discount = `discount_percent` of the subtotal, rounded down to the paisa; one code per bill, one redemption per code; same code again on the same bill = no-op; links the session's latest order (`source = campaign`); audit `offer.redeem`. Errors: "This isn't a valid code, or it has expired." · "This code was already used today on #1041."
- [x] Console bill sheet reads the API (the client-side `billTotal` sum is gone) and has the code field with the discount line
- [x] Tests: 2 integration (lines, discount math, no-op, delivery total, attribution, audit; used / expired / unsent / another café's code / second code), console (2)

## L6 · Setup

- [x] `PUT /cafes/{id}/settings` (owner): opening hours (mon–sun, ≤ 3 windows, past-midnight allowed), last-order cutoff, tone, feedback delay, café alert number; one save; audit lists changed fields only (never the number)
- [x] `POST /tables`, `PUT /tables/{id}` (owner): label unique, `qr_text` "Hi, I'm at Table <n>" from the label (a renamed table's QR changes and the console says to reprint)
- [x] `POST /team/invite` (Supabase Auth invite email; an existing user is reused), `PATCH /team/{id}` (role, active); owners can't be deactivated, the last active owner can't be demoted; audit without emails
- [x] Console `/setup/:tab` (owner only; others go back to Live orders): Tables & QR with a QR per table and the counter opt-in QR, a printable sheet (`/setup/qr-sheet`, two per page) with both fallback lines; Team; Settings; Alerts (number + sound tests)
- [x] Dependency: `qrcode` 1.5.4 (QR images drawn in the browser; nothing leaves it)
- [x] Tests: 3 integration (settings + validation + audit, tables + QR text joins the table, invite / role / deactivate / owner rules), 3 e2e (QR per table, edit and restore, settings round trip, team list; QR sheet with 13 cards; non-owners redirected)
- [ ] Not tested end to end: a real invite email (Supabase's built-in mail is rate-limited; the Auth call is replaced in tests)

## L7 · Service report and Analytics

- [x] `/service-report` (`manager`+, fixed last 7 days): median prep (placed → ready), % overdue, peak overdue hour, slowest 3 dishes (`v_service_report`), ratings average and every 1–3★ with text (`v_feedback_summary`)
- [x] `/analytics` (owner, 7 / 30 / 90 days): revenue, orders, average order, average rating, returning % with deltas vs the previous period; revenue bars (weekly at 90 days); top 5 dishes; the same service report for the period; 1–3★ ratings. "WhatsApp orders only; walk-in cash and UPI aren't included."
- [x] Nav: Service report for `manager`+, Analytics for owner; others are sent back to Live orders
- [x] Returning = a customer of the period first seen before it started (documented in `lib/reports.ts`)
- [x] Tests: summaries on fixed rows (2), e2e (owner analytics + period switch, manager service report, shift manager redirected), RLS per view (L1)

## L8 · Customers

- [x] `/customers` (owner): search by name, segment chips All · Regular · New · Lapsed, whole-café counts, 50 per page server-side, masked numbers; profile (visits, spend without cancelled orders, first / last visit, favourites, offers consent, average rating, campaigns received / redeemed)
- [x] `POST /customers/{id}/delete` (owner): one transaction per data-protection rules; a `data_deletion_requests` row; audit `customer.delete`; the same number later is a new customer with no consent
- [x] Delete dialog says exactly what goes and what stays, "can't be undone", Keep data has the focus
- [x] Consent register (read-only latest row per customer) + `GET /consents/export` CSV of the full history, masked, audited `consent.export`
- [x] Tests: 2 integration (every removed and kept item, idempotent, new customer after; export masked + audited), 2 e2e (list, chips, profile, dialog cancelled, CSV download has no full number; non-owners redirected)
- [ ] Deviation: staff comments are erased in place ("(deleted)", no applied changes) instead of deleted, because `llm_calls` (append-only) references them

## L9 · Campaigns

- [x] API (owner): create / edit draft, audience preview (in segment, excluded, will receive, daily limit from the messaging tier — 250 until verified — days needed, cost "not tracked", quality, template status), schedule (approved marketing template, a recipient, a future time, number connected, a code prefix), cancel (`scheduled` and `sending`; queued recipients not sent); audit `campaign.schedule` / `campaign.cancel`
- [x] Sender: `campaign_start` (every 60 s locally, 5 min in the cloud) snapshots recipients with unique codes (prefix + 3 unambiguous characters); `campaign-send` task per recipient, spaced, within the day's limit (counted from today's campaign sends), the rest `deferred`; `campaign_resume` re-queues them; `completed` when nothing is queued or deferred
- [x] Each send re-checks campaign, recipient, consent and template; the send layer refuses marketing without the latest consent `opted_in` (new purpose `marketing`, template only: invariant 10)
- [x] Status webhooks move recipients forward only; error 131049 → `held`
- [x] Console: list (notice, status tags, sent, came back), read-only templates, report (funnel as % of sent with the Held tooltip, they spent, discount given, WhatsApp cost "not tracked"), Cancel / Stop sending; five-step wizard with a live WhatsApp preview ("Order now", "Reply STOP to opt out"), draft saved after Message, Review with every preview line and the blockers
- [x] Seed: `feedback_request`, `weekly_offer`, `winback` (synthetic approvals, dev only) and opt-ins for half the seeded customers
- [x] Tests: 3 integration (draft / preview / schedule rules; start → limit → deferred → resume → completed + forward-only statuses; opt-out after snapshot, cancel, the sender's own consent check), **e2e Phase 5 exit gate locally**: counter-QR opt-in → wizard → the code arrives on WhatsApp → redeemed on the bill → report shows Came back 1 → STOP
- [ ] Not built: per-send quick-reply payloads on templates ("Stop offers" tap); STOP by text works. The preview wording approximates the approved templates (the real text lives with Meta)

## L10 · Today and weekly report

- [x] `/today` is the owner's home (others keep Live orders): customers today with the delta vs the same weekday last week, came back %, new opt-ins today, offer redemptions this week; refetched every 5 min
- [x] Needs attention, critical first: WhatsApp disconnected · low ratings not handled (→ Calls) · template rejected · ordering paused (Resume writes `ordering.resume`) · number quality Medium / Low
- [x] Service right now (New / being made / ready / overdue → Live orders), Next campaign, Last week (revenue, orders, returning %, top dish)
- [x] Tests: e2e (owner lands on Today, pause → card → Resume clears it; managers redirected), owner specs re-run green with the new landing page
- [ ] Deviation: the weekly report is computed live on Today (last full Mon–Sun week) instead of a stored Monday 09:00 job: there's no table or channel for it yet (no WhatsApp template for owners)
