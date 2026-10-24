# Restro Console

The staff web app for all four roles. One on-shift person runs service from it, so urgent things must be seen and heard, and the active café must always be obvious. Visual reference: `Restro Console v2-Updates.html`; this doc wins where they differ.

## Shell

| Part | Choice |
|---|---|
| App | React + Vite + TypeScript SPA on Firebase Hosting |
| Auth | Supabase Auth, anon key only in the bundle |
| Reads | Supabase tables and views under RLS + [Realtime](data-and-api.md#realtime) |
| Writes | `api` only, typed client generated from `contracts/openapi.yaml` |
| Mock | MSW handlers from `openapi.yaml` + seed generator + fake Realtime emitting the same events |
| Icons | Lucide |

```text
frontend/src/
├── app/         router, route guard, shell (sidebar, tab bar, More sheet, header, banners, toasts), login, café context
├── components/  presentational only; never fetch
├── lib/         Supabase client, API client, realtime hooks, urgency engine, formatters (₹, timers, masked phone), mock/
└── features/    today, orders, calls, chats, menu, customers, campaigns, analytics, setup, platform
```

Only `lib/` talks to Supabase or `api`. Features use `lib/` hooks; they never create their own client.

### Login and café context

- Email + password or magic link. Customers never sign in. Demo builds add a "sign in as" role picker.
- After sign-in: `platform_admins` row → Cafés. Otherwise active memberships: none → "No access to any café"; one → that café; several → last used.
- The active café sets `X-Cafe-Id` on every call and the `cafe_id` filter on every channel. A person with several cafés gets a switcher (sidebar on desktop, More sheet on phones). Switching ends any pending undo, drops channels, refetches, re-reads the role and toasts "Switched to <café>".

### Navigation

| Role | Groups | Phone tabs | Lands on |
|---|---|---|---|
| `shift_manager` | Service: Live orders, Calls and bills, Chats, Menu | Orders, Calls, Chats, Menu, More | Live orders |
| `manager` | Service + Service report | Orders, Calls, Chats, Menu, More | Live orders |
| `owner` | Today · Service · Customers, Campaigns, Analytics, Setup | Today, Orders, Calls, Chats, More | Today |
| `platform_admin` | Cafés, Templates, Number health, Costs, AI review, Audit log | Cafés, Health, Templates, Audit, More | Cafés |

- A route the role can't use redirects to its landing screen. Hiding is UX only; `api` returns 403 regardless and the UI handles it.
- Desktop (≥ 768 px): sticky 244 px sidebar with café block, nav and user. Phone: bottom tab bar + More sheet. Dialogs are centred on desktop, bottom sheets on phones.
- The active café name is on every screen.

### Banners

Stacked under the header in this order.

| Banner | When | Action |
|---|---|---|
| Support mode (dark) | `platform_admin` opened a café: "Support mode · <café> · every action is logged" | Exit |
| Disconnected (critical) | `cafes.status = disconnected` | Details / Open café |
| Offline (critical) | `api` or Realtime unreachable > 5 s, or browser offline: "New orders may be missing until you're back." | Retry |
| Paused (warning) | `cafes.settings.paused` | Resume (`manager`+) |
| Alerts off (warning) | Sound not enabled on this device | Enable alerts |

- While offline, write buttons are disabled except Add manual order.
- Back online: refetch every live list, toast "Back online · N orders arrived while offline", replay first-seen alerts; urgency is recomputed from server timestamps.

### Undo toast

After Accept, Mark ready or Mark served: "#1048 → Accepted · Message sends in 5 s" + Undo, counting down 5 s. Undo → "#1048 back to New · Nothing was sent to the customer". One undo at a time; a new transition, route change, café switch or sign-out ends it.

### Design tokens

From the mock. All radii 0.

| Token | Value |
|---|---|
| Font | Archivo (headings 800); monospace for codes, template names, ids |
| `--color-bg` / `--color-surface` / `--color-text` | #f3f2f2 / #eae9e9 / #201e1d |
| `--color-accent` | #ec3013 (600 #dd2b0f, 700 #ae1800) — critical only |
| Amber set | warning |
| Green #2f9e5b | Veg dot, quality High |
| `--space-1…8` | 4, 8, 12, 16, 24, 32 px |

Severity → colour: `critical` accent · `warning` amber · `info` text colour · `calm` neutral grey · `none` transparent. `critPulse` ring on critical cards.

### Shell rules

- Never write to Supabase. Never compute money: format `*_paise` from the API as "₹ 1,240".
- Phone numbers always masked (`+91 98••• ••421`).
- Demo controls (role switch, Simulate order, demo code hints) are behind a build flag, off in production.

## Urgency and alerts

The engine in `lib/` recomputes severity every second for each live order, request and human-mode chat (never stored), updates badges, and rings on a severity change.

| Severity | Look | Sound |
|---|---|---|
| `critical` | Red, `circle-alert`, red left border, pulsing | Alarm + vibrate; repeats every 30 s while any critical item is open |
| `warning` | Amber, `triangle-alert`, amber border | Two-note tone once, when it first turns amber |
| `info` | Dark chip | Soft chime once, new orders only |
| `calm` | Grey, card at 72 % | Silent |
| `none` | Plain | Silent |

Always colour + icon + label; red only for `critical`.

**Orders** (first match wins; due = `accepted_at` + `expected_minutes` + `delay_minutes`)

| Status | Condition | Severity | Chip |
|---|---|---|---|
| `READY` | — | `none` | Timer "Ready · N min" |
| `NEW` | `allergy_note` set | `critical` | Allergy |
| `NEW` | < 60 s / ≥ 60 s | `info` / `warning` | New / Not started |
| `ACCEPTED` | Past due or `overdue` | `critical` | Overdue |
| `ACCEPTED` | ≥ 80 % of expected + delay | `warning` | Due soon |
| `ACCEPTED` | `delay_minutes` > 0 | `info` (silent) | Delayed +N |

Allergy rings until accepted (accepting is the kitchen check); the banner stays until `READY`. Flags `edited`, `item_sold_out`, `near_closing`, `escalated` are grey chips that don't change severity; escalated cards show the chain ("Escalated · Meera (manager) 12:41").

**Calls**

| Kind | State | Severity | Chip |
|---|---|---|---|
| `allergy` | open | `critical` (repeats) | Allergy |
| `help` | open < 2 min / ≥ 2 min | `warning` / `critical` (repeats) | Needs help / Waiting too long |
| `help`, `allergy` | acknowledged | `info`, silent | With <name> |
| `bill` | open | `warning`, silent, badge only | Bill |
| `low_rating` | open (`manager`+) | `critical`, rings once | Low rating |

**Chats:** `bot` → `none`. `human` with last message from staff or agent → `calm`; from the customer < 60 s → `warning`, ≥ 60 s → `critical`.

**Sorting and badges.** Lists sort by severity, then oldest. Nav badge = warning + critical count, red if any critical, hidden at 0. Column tabs show a red or amber dot.

**Ringing.** Only the highest new event per tick rings. First load and reconnect record existing items silently. Never rings for bills, status changes, handled items, `calm` or `none`. No alerts for `platform_admin` outside support mode.

**Alerts gate** (phones, `shift_manager` and `manager`, alerts off): full screen "Turn on alerts before service." The tap unlocks audio, asks for notification permission and plays the chime. The bell panel shows this device's sound, notification permission and WhatsApp backup state, with a test button.

**Notifications.** Ring events while the tab is hidden become browser notifications ("Overdue · T11 #1044", "Allergy · T7 #1048"); critical ones stay until tapped. Ring events for another screen show a heads-up card that opens it. The WhatsApp backup is sent by the backend, never the browser: `new_order_alert` to the café alert number when an order is `NEW` at 60 s or a call is open at 2 min.

## Screens

| Screen | Route | Roles | Priority | Phase |
|---|---|---|---|---|
| Login | `/login` | all | P0 | 0 → 1 |
| Live orders | `/orders` | `shift_manager`+ | P0 | 1 → 4 |
| Chats | `/chats/:conversationId?` | `shift_manager`+ | P0 | 4 |
| Menu | `/menu` | `shift_manager`+ (edit `manager`+, import `owner`) | P0 | 4 |
| Calls and bills | `/calls` | `shift_manager`+ (low ratings `manager`+) | P1 | 4 → 5 |
| Service report | `/service-report` | `manager`+ | P1 | 4 |
| Setup | `/setup/:tab` | `owner` | P1 | 4 → 6 |
| Customers | `/customers/:id?` | `owner` | P2 | 5 |
| Campaigns | `/campaigns`, `/campaigns/new`, `/campaigns/:id` | `owner` | P2 | 5 |
| Today | `/today` | `owner` | P2 | 7 |
| Analytics | `/analytics` | `owner` | P3 | 7 |
| Platform | `/platform/...` | `platform_admin` | P3 | 7 |

Owner screens also open for `platform_admin` in support mode, minus Print ticket, Add manual order and Service report.

**Building a screen:** read its section + Shell + Urgency → build on MSW with contract names only → check at 375 px and desktop, in every role that sees it and in support mode → switch reads to Supabase and writes to `api` with no UI change → done when "Done when" passes on MSW and the real backend. If this doc and `contracts/` disagree, stop and raise it.

**Every screen** handles: empty state, filter with no match, 403, 409 (refetch and show the new state), network error (keep input, "try again", Offline banner if it persists).

### Changes from the mock

| Area | Mock v2 | Build |
|---|---|---|
| Roles | Staff / Manager / Owner / Platform | `shift_manager` "Shift manager" / `manager` / `owner` / `platform_admin` |
| Order columns | New / Preparing / Ready; "Start preparing" | New / Accepted / Ready; **Accept**; "Your order is being made" |
| Cancel | `manager`+, not on Ready | `shift_manager`+ on all three, reason required |
| Allergy alarm | Rings until Ready | Rings until Accepted |
| Added | — | Flag chips, escalation chain, print ticket, manual order, offline banner, Paused chip |
| Menu edit | Free-text tags | Category, veg, spice, three-state allergens |
| Bill sheet | Per table | Per table session, every non-cancelled order |
| Setup | Tab "Staff" | Tab **Team**; last-order cutoff |
| Onboarding | Hosted / Embedded toggle | Hosted only |
| Café switcher | Owner only | Anyone with several memberships |
| Numbers on Today / Analytics | Hard-coded | From `v_*` views |
| Platform templates | New template button | Read + sync only |
| Audit names | `waiter.ack`, `chat.release`, `bill.delivered`, `order.status`, `staff.deactivate`, `feedback.handled` | `request.ack`, `chat.handback`, `bill.deliver`, `order.transition`, `team.update`, `request.resolve` |

### Live orders

Moves orders New → Accepted → Ready → Served. Backend: [orders](backend.md#orders).

- **Layout:** toolbar with Tabs / Board toggle (phones always Tabs), Pause WhatsApp ordering (`manager`+), Add manual order · summary strip ("1 overdue · 1 allergy · 2 new") · column tabs New / Accepted / Ready with counts and severity dots · type chips All · Overdue · Allergy · Not started · Due soon · New · Delayed + legend · card grid.
- **Card:** allergy banner (until `READY`) · table large · "#1048 · Riya · 6th visit" · severity and flag chips · timer "6 / 10 min" from `accepted_at` · progress bar · items with notes · redeemed code · escalation chain · total · Message, Print ticket, Cancel, Delay, and the primary action (Accept / Mark ready / Mark served).
- **Delay panel:** +2 · +5 (default) · +10 · custom 1–99, note ≤ 120, preview of the customer message.
- **Cancel dialog:** reason Out of stock · Kitchen issue · Customer asked; the reason goes to the customer and the audit log.
- **Manual order:** table, available items with qty and note, optional allergy note.

| Action | API | Result |
|---|---|---|
| Accept / Mark ready / Mark served | `POST /orders/{id}/transition` with `expected_version` | Card moves; undo toast; message after 5 s |
| Undo | `POST /orders/{id}/undo` | Card returns; nothing sent |
| Delay | `POST /orders/{id}/delay` | Delayed +N; due pushed; message sent |
| Cancel | `transition` to `CANCELLED` with reason | Card leaves; customer told |
| Message | `POST /conversations/open` if no chat | Opens the chat |
| Manual order | `POST /orders/manual` | New card via Realtime |
| Pause / resume | `POST /cafes/{id}/pause-ordering` | Paused banner |

409 → "Riya changed this order — check it again", card refreshes. 422 → "This order was already updated", refetch.

**Done when:** a new order appears ≤ 2 s after Confirm with chime · transitions send `expected_version`; Undo within 5 s sends nothing · amber at 60 s, Due soon at 80 %, red past due; strip, chips, dots and badge agree · allergy rings every 30 s until accepted · Delay pushes due and sends the preview · Cancel needs a reason · 409 refreshes the card · Tabs at 375 px with no horizontal scroll · Pause `manager`+ only; Print and manual order hidden in support mode.

### Calls and bills

Help calls, allergy questions, bill requests, low ratings, and the bill sheet. Backend: [human help](backend.md#human-help).

- **Layout:** summary strip · type chips All · Waiting too long · Allergy · Low rating (`manager`+) · Needs help · Bill · With staff · one list by severity · Redeemed today.
- **Card:** table + chip · who and what ("Needs help · Arjun M.", "Dev K. rated 2★") · customer's words · waiting time or "Acknowledged by Ravi" · escalation chain.
- **Buttons:** help open → Acknowledge + Message · acknowledged → Resolve + Message · allergy open → Reply (opens chat) + Acknowledge · bill → Open bill + Message · low rating → Mark handled + Open chat.
- **Bill sheet:** every non-cancelled order of the table session, subtotal, discount, total (all from the API), code input + Apply (disabled once applied), success "Linked to order #1049 and the '<campaign>' campaign", errors from the API, **Bill delivered**.

| Action | API |
|---|---|
| Acknowledge | `POST /requests/{id}/ack` |
| Resolve / Mark handled | `POST /requests/{id}/resolve` (help → chat back to bot) |
| Apply code | `POST /bills/{table_session_id}/apply-code` |
| Bill delivered | `POST /bills/{table_session_id}/deliver` |

**Done when:** help rings, turns critical at 2 min, shows the chain, repeats until acknowledged · allergy critical from the start, Reply opens the right chat · bills silent but badged · low ratings `manager`+ only, ring once · Apply shows the API discount and both errors · Bill delivered removes the card on every console · bill sheet as bottom sheet at 375 px.

### Chats

Read conversations, take over, reply, hand back. Backend: [human mode](backend.md#handoff).

- **Layout:** list split into "Waiting for you" (human mode, by severity) and "Bot is handling" (most recent) · thread · customer panel (name, masked phone, allergy, visits, past orders). Phone: list → full-width thread → back.
- **Thread:** mode banner with Take over or Hand back to bot · bubbles for customer, agent, staff with time; "Not delivered" on failed sends · quick replies (On its way · Just 2 more minutes · Checking with the kitchen · Coming to your table now) fill the composer, never send · composer warns "this pauses the bot" in bot mode.
- **Window closed** (last customer message > 24 h): composer disabled — "WhatsApp only allows a reply after the customer messages again." Status updates still go as templates.
- **Message a table:** pick an open table session → chat in human mode.

| Action | API |
|---|---|
| Take over | `POST /conversations/{id}/takeover` |
| Send | `POST /conversations/{id}/reply` (takes over from bot mode) |
| Hand back | `POST /conversations/{id}/handback` |
| Message a table | `POST /conversations/open` |

Another staff member took over → "Meera is replying" + Take over. Idle hand-back shows "Handed back to the bot".

**Done when:** a human-mode chat appears ≤ 2 s, amber, red at 60 s · banner follows Realtime mode · quick replies never send alone · closed window disables the composer · Message a table works with no prior chat · numbers masked · works at 375 px.

### Menu

Sold-out in two taps; managers edit items. Backend: [menu](backend.md#menu).

- **Layout:** "2 items sold out today" · Import CSV (`owner`) · New item (`manager`+) · category chips · rows with veg dot, name, category, allergens / prep / price (`manager`+ only), availability switch, Edit (`manager`+).
- **Switch:** first tap arms it (amber, "Tap again to mark sold out"), second tap commits; an armed switch resets after 5 s or when another is tapped.
- **Edit dialog:** photo, name, description, category, price in ₹ (stored as paise), prep time, veg, spice, and every allergen as Contains / Free from / Not sure (default). "Not sure means the agent always passes allergen questions about this dish to a person."
- **Import:** download template → upload → "12 created · 30 updated · 2 rejected" with row and reason.

| Action | API |
|---|---|
| Sold out / back in stock | `POST /menu/{id}/availability` |
| Save / new | `PUT /menu/{id}` with `version` · `POST /menu` |
| Import | `POST /menu/import` |

**Done when:** two taps mark sold out, one tap expires, every console updates · `shift_manager` sees no prices, prep, Edit, New or Import · edit saves paise, prep, veg, spice, every allergen state · 409 shows "Someone else changed this item" + Reload · edit as bottom sheet at 375 px.

### Today

Owner home. No sound on Today itself.

| KPI | Value | Source |
|---|---|---|
| Customers today | Distinct customers with a non-cancelled order; delta vs same weekday last week | `v_daily_revenue` |
| Came back | Today's customers with an earlier visit (%) | `v_customer_segments` |
| New opt-ins | `opted_in` rows today | `consents` |
| Offer redemptions | This week (café timezone) | `redemptions` |

**Needs attention**, critical first: WhatsApp disconnected · low rating not handled (→ Calls) · template rejected · ordering paused (Resume) · number quality Medium / Low. Plus **Service right now** (live order chips → Live orders), **Next campaign**, **Weekly report**.

KPIs refetch every 5 min; attention cards follow Realtime. **Done when:** tiles match the views and never count cancelled orders · each card appears and clears with its condition · Resume writes `ordering.resume` · Service right now matches Live orders · 375 px.

### Customers

List, profile and read-only consent register. Backend: [customers](backend.md#customers-and-feedback), [consent](backend.md#consent).

- **List:** search by name · chips All · Regular · New · Lapsed · count line for the whole café · table of name + masked phone, segment, visits, spend, last visit · 50 per page, server-side.
- **Profile:** visits, total spend (cancelled excluded), first / last visit, favourite dishes, consent (Opted in / Opted out / Not asked), average rating, campaigns received / redeemed, **Delete customer data**.
- **Delete dialog:** states exactly what is removed, that it can't be undone and is audited; Keep data has default focus.
- **Consent tab:** read-only latest row per customer (type, status, wording version, source, date) + Export CSV (full history). "Only the customer can change their consent — on WhatsApp, or by replying STOP."

**Done when:** search and chips filter · numbers masked on screen and in CSV · delete needs the dialog and writes `customer.delete` · export audited · non-owners redirected.

### Campaigns

List, report, templates and the five-step wizard. Backend: [campaigns](backend.md#campaigns).

- **List:** notice "Only opted-in customers can be messaged. Sending is throttled and may roll over to the next day." · table of name, audience, status tag, came back · read-only template list with status chips ("Templates are created by the Restro team").
- **Report:** funnel as % of sent: Sent → Held by WhatsApp (tooltip: normal, a cross-business limit) → Delivered → Read → Came back; they spent, discount given, WhatsApp cost. Scheduled → Cancel scheduled send; draft → Continue editing.
- **Wizard:** Audience → Template → Message → Schedule → Review, with a live WhatsApp preview (Order now button, "Reply STOP to opt out"). Draft saved on Continue past Message. Review shows every audience-preview line and the quality warning.

Schedule is disabled with no approved marketing template, no recipients, a past time, or a disconnected number. `sending` campaigns refetch every 60 s.

**Done when:** tags and Came back correct · funnel matches `v_campaign_funnel` · opted-out never counted · Schedule and Cancel audited · preview has Order now and the STOP line · 375 px.

### Analytics and service report

Read-only, from views refreshed nightly. Revenue = WhatsApp orders only, cancelled excluded ("walk-in cash and UPI aren't included").

- **Analytics** (`owner`): period 7 / 30 / 90 days · KPIs revenue, orders, average order, average rating, returning % with deltas vs the previous period · revenue bar chart (weekly bars at 90 days) · top 5 dishes · the service report for the same period.
- **Service report** (`manager`+, fixed last 7 days on its own route): median prep (placed → ready) · % orders overdue · slowest dish · peak overdue hour · slowest 3 dishes · 1–3★ ratings with text.

**Done when:** the period reloads every block · KPIs and deltas match the views · `/service-report` only for `manager` and `owner` · chart scales at 375 px.

### Setup

Owner. Tabs **Tables & QR · Team · Settings · Alerts**, each with its own URL.

- **Tables & QR:** add / edit tables (label unique, area, seats); download one QR or all as PDF. Every printed QR carries "Can't order on WhatsApp? Ask any of our team."
- **Team:** invite by email and role; change role; deactivate / reactivate (immediate). Owner rows can't be deactivated; the last active owner can't be demoted. One login per person.
- **Settings:** opening hours · last-order cutoff · agent tone (Warm / Crisp / Playful) · feedback delay 15 / 30 / 60 min (default 30) · GST inclusive / exclusive 5 %. Saved in one `PUT`.
- **Alerts:** café alert number (gets `new_order_alert`) · sound tests for each severity.

**Done when:** QR opens WhatsApp with "Hi, I'm at Table N" · invite, role change and deactivation work and deactivation blocks the next request · settings save with `settings.update` · alert number validated · 375 px.

### Platform

Our cross-café console (`platform_admin`). Until P3 the team uses Supabase Studio, GCP console and WhatsApp Manager. No sounds. Secrets are never shown — only `token_ref` / `pin_ref`.

| Screen | Content |
|---|---|
| Cafés | KPIs (live, orders today, disconnected, templates pending) · Onboard café · table: status, go-live count, quality, tier, Open as support |
| Café detail | Go-live checklist, health rows, Meta ids and secret refs |
| Templates | Per café, status and quality, Sync with Meta |
| Number health | Card per café: Healthy / Check / Reconnect |
| Costs | Cost per order, WhatsApp 7 d, AI 7 d, fallback rate; per café and per campaign |
| AI review | Fallbacks and ≤ 2★ ratings with read-only transcripts |
| Audit log | Filter by café, actor, action; export |

- Status chip: Disconnected (critical) · Paused (warning) · Needs attention (onboarding or any checklist item open) · Live (calm).
- Onboard form takes Secret Manager references only and rejects anything that looks like a token.
- Open as support → owner console of that café under the support bar; Exit → Cafés.

**Done when:** only `platform_admin` reaches `/platform/...` · onboarding accepts refs only and writes `cafe.create` · checklist, sync and support actions audited · support bar on every screen and every action `support = true`.
