# Restro Console

The staff web app for all four roles. One on-shift person runs service from it, so urgent things must be seen and heard, and the active café must always be obvious. Visual reference: `Restro Console v2-Updates.html`; this doc wins where they differ.

## Shell

| Part | Choice |
|---|---|
| App | React + Vite + TypeScript SPA on Firebase Hosting |
| Auth | Supabase Auth, anon key only in the bundle |
| Reads | Supabase tables and views under RLS + [Realtime](data-and-api.md#realtime) |
| Writes | `api` only, typed client generated from `contracts/openapi.yaml` |
| Mock | Demo builds only: MSW handlers from `openapi.yaml` + seed generator + fake Realtime emitting the same events. Development runs against the real local backend with seed data ([decisions](decisions.md#smaller-decisions)) |
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
- A memberships read that fails (network, laptop waking) is not "no access": the open café stays and the read retries every 5 s. With no café loaded yet the console shows "Can't reach Restro" + Retry now, retrying by itself.
- The active café sets `X-Cafe-Id` on every call and the `cafe_id` filter on every channel. A person with several cafés gets a switcher (sidebar on desktop, More sheet on phones). Switching ends any pending undo, drops channels, refetches, re-reads the role and toasts "Switched to <café>".

### Navigation

| Role | Groups | Phone tabs | Lands on |
|---|---|---|---|
| `shift_manager` | Service: Live orders, Calls and bills, Chats, Menu, Redeem offer | Orders, Calls, Chats, Menu, Redeem, More | Live orders |
| `manager` | Service + Service report | Orders, Calls, Chats, Menu, Redeem, More | Live orders |
| `owner` | Today · Service · Customers, Campaigns, Analytics, Setup, Audit log | Today, Orders, Calls, Chats, More | Today |
| `platform_admin` | Cafés, Templates, Number health, Costs, AI review, Audit log | Cafés, Health, Templates, Audit, More | Cafés |

- A route the role can't use redirects to its landing screen. Hiding is UX only; `api` returns 403 regardless and the UI handles it.
- **Redeem offer** (`/redeem`, lucide `TicketPercent`, tab label "Redeem") is a Service item for every role: the shift manager redeems at the counter. The tab bar sizes one column per entry, so 5 entries are ~75 px each at 375 px with no horizontal scroll. **Audit log** (`/audit`, lucide `ScrollText`) is owner-only, after Setup ([plan](retention-audit-recommend.md)).
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
- **Realtime recovery.** Even while connected, active orders and open requests are refetched every 30 s and on tab visible, so a missed Realtime event never hides an order. Disconnects and reconnects are reported to `POST /telemetry/console`.
- The offline check pings `api` `/healthz` every 1.5 s with a 4 s timeout, one ping at a time; a hung ping counts as down.

### Errors and loading

- A render error never blanks the console. A screen that fails shows "This screen hit a problem" with Try again and Reload inside the shell; nav, banners and alerts keep working, and moving to another screen resets it. An error outside a screen shows a full-page Reload card.
- Service screens (Live orders, Calls and bills, Chats, Menu) and Today load with the app. Owner and platform screens (Customers, Campaigns, Analytics, Service report, Setup, Performance) load on first visit behind "Loading…".
- After a deploy, a page that asks for a script file that no longer exists reloads once (at most once a minute), so it picks up the new version instead of staying blank. Hosting serves the HTML and routes `no-cache` and hashed `/assets/` as immutable.

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

**In-app notifications** (`manager`+). The bell panel also lists the user's `notifications`, newest first, with an unread count on the bell. `order_cancelled`: "#1048 · T7 cancelled by Ravi · Out of stock" (custom text shown to staff). Silent, `info`; opening the panel or tapping Mark all read calls `POST /notifications/ack-all`, tapping one calls `POST /notifications/{id}/ack` and opens the order.

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
| Campaigns | `/campaigns`, `/campaigns/new`, `/campaigns/:id` (tabs `?tab=overview\|recipients\|redemptions\|opted-out`) | `owner` | P2 | 5 |
| Campaign insights | `/campaigns/insights` | `owner` | P2 | 5 |
| Redeem offer | `/redeem` (Service nav); the same body as the Campaigns tab `/campaigns/redeem` (`owner`) | `shift_manager`+ | P2 | 5 |
| Audit log | `/audit` | `owner` | P2 | 5 |
| Order history | `/audit/orders/:orderId` | `owner` | P2 | 5 |
| Today | `/today` | `owner` | P2 | 7 |
| Analytics | `/analytics` | `owner` | P3 | 7 |
| Platform | `/platform/...` | `platform_admin` | P3 | 7 |

Owner screens also open for `platform_admin` in support mode, minus Print ticket, Add manual order and Service report.

**Building a screen:** read its section + Shell + Urgency → build against the local backend and seed data with contract names only → check at 375 px and desktop, in every role that sees it and in support mode → done when "Done when" passes on the real backend. If this doc and `contracts/` disagree, stop and raise it.

**Every screen** handles: empty state, filter with no match, 403, 409 (refetch and show the new state), network error (keep input, "try again", Offline banner if it persists).

### Changes from the mock

| Area | Mock v2 | Build |
|---|---|---|
| Roles | Staff / Manager / Owner / Platform | `shift_manager` "Shift manager" / `manager` / `owner` / `platform_admin` |
| Order columns | New / Preparing / Ready; "Start preparing" | New / Accepted / Ready; **Accept**; "Your order is being made" |
| Cancel | `manager`+, not on Ready | `shift_manager`+ on New; `manager`+ on Accepted and Ready; reason required (fixed list + custom text) |
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
- **Card:** allergy banner (until `READY`; this visit's `allergy_note` and "On profile: <allergen>") · table large · "#1048 · Riya · 6th visit" + **Profile** button ([customer profile](#customer-profile)) · severity and flag chips · timer "6 / 10 min" from `accepted_at` · progress bar · items with notes · redeemed code · escalation chain · total · Message, Print ticket, **History** (owner only: a link to the order's [history](#audit-log) `/audit/orders/:id`; a read, so it works offline), Cancel (New for `shift_manager`; every column for `manager`+), Delay, and the primary action (Accept / Mark ready / Mark served).
- **Delay panel:** +2 · +5 (default) · +10 · custom 1–99, note ≤ 120, preview of the customer message.
- **Cancel dialog:** reason Out of stock · Kitchen issue · Customer asked · Other (text field, required, ≤ 120). Note under the reasons: "The customer gets an apology. Your own text stays with the team." The reason goes to the audit log and to managers' and the owner's notifications.
- **Manual order:** table, available items with qty and note, optional allergy note.

| Action | API | Result |
|---|---|---|
| Accept / Mark ready / Mark served | `POST /orders/{id}/transition` with `expected_version` | Card moves; undo toast; message after 5 s |
| Undo | `POST /orders/{id}/undo` | Card returns; nothing sent |
| Delay | `POST /orders/{id}/delay` | Delayed +N; due pushed; message sent |
| Cancel | `POST /orders/{id}/cancel` with `reason_code`, `reason_text?`, `expected_version` | Card leaves; customer told; managers and owner notified in-app |
| Message | `POST /conversations/open` if no chat | Opens the chat |
| Manual order | `POST /orders/manual` | New card via Realtime |
| Pause / resume | `POST /cafes/{id}/pause-ordering` | Paused banner |

409 → "Riya changed this order — check it again", card refreshes. 422 → "This order was already updated", refetch.

**Done when:** a new order appears ≤ 2 s after Confirm with chime · transitions send `expected_version`; Undo within 5 s sends nothing · amber at 60 s, Due soon at 80 %, red past due; strip, chips, dots and badge agree · allergy rings every 30 s until accepted · Delay pushes due and sends the preview · Cancel needs a reason (text for Other); `shift_manager` sees Cancel only on New · 409 refreshes the card · a missed Realtime event shows up within 30 s · Tabs at 375 px with no horizontal scroll · Pause `manager`+ only; History only for the owner; Print and manual order hidden in support mode.

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

- **Layout:** list split into "Waiting for you" (human mode, by severity) and "Bot is handling" (most recent) · thread · customer panel = the [customer profile](#customer-profile). Phone: list → full-width thread → back.
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

### Customer profile

One shared panel (`shift_manager`+), opened from the **Profile** button on order cards and Calls and bills cards, and shown as the customer panel in Chats. Side panel on desktop, full-height bottom sheet on phones. Backend: [customer profile](backend.md#customer-profile-and-staff-comments).

- **At a glance** (top box, no scrolling at 375 px, large type, one fact per line, icon + label on every chip): first name and "6th visit" / "First visit" · masked phone and segment · last visit ("12 Oct · 12 days ago") · **Staff activity** (owner only; every staff action on this customer, all time: `/audit?customer=<id>&range=all`, see [audit log](#audit-log)) · usually orders (top 3 dishes) · **Allergy** first, in the allergy style used on order cards · Dislikes · Diet · Prefers (milk, sugar, spice, seating, likes, other). Each chip has × to remove a wrong value.
- **Details** (below): Feedback (average, each rating with text) · Complaints (1–2★ and `complaint` / `frustration` handoffs, with the customer's words) · Recent orders (last 5, cancelled struck through) · Comments (newest first: author, time, the comment as written, what the AI changed with **Undo**, or "No profile changes" / "Couldn't read this — profile unchanged").
- **Add a comment:** text ≤ 500 + Save; "Updating profile…" until the result arrives by Realtime. Hint: "Write what the customer told you — the profile updates itself. Offers and opt-outs can only be changed by the customer."

| Action | API |
|---|---|
| Add a comment | `POST /customers/{id}/notes` |
| Undo a comment's changes | `POST /customer-notes/{id}/undo` |
| Remove a value (chip ×) | `POST /customers/{id}/preferences/remove` |

Reads: `v_customer_card`, `customer_preferences`, `customer_notes`, `feedback`, `requests`, `orders` + `order_items` through Supabase.

**Done when:** opens in one tap from a card · the top box reads at a glance at 375 px with no scrolling · a comment shows its applied changes within a few seconds on every console · Undo reverses them, 409 shows "Changed again since — remove it from the box instead" · × removes one value · numbers masked · Staff activity shows for the owner only · empty profile and first visit look intentional, not broken.

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

**Needs attention**, critical first: WhatsApp disconnected · low rating not handled (→ Calls) · template rejected · ordering paused (Resume) · number quality Medium / Low · N critical staff actions today (owner only, amber, no sound; → [audit log](#audit-log) `/audit?severity=critical&range=today`). Plus **Service right now** (live order chips → Live orders), **Next campaign**, **Weekly report**.

KPIs refetch every 5 min; attention cards follow Realtime. **Done when:** tiles match the views and never count cancelled orders · each card appears and clears with its condition · Resume writes `ordering.resume` · Service right now matches Live orders · 375 px.

### Customers

List, profile and read-only consent register. Backend: [customers](backend.md#customers-and-feedback), [consent](backend.md#consent).

- **List:** search by name · chips All · Regular · New · Lapsed · count line for the whole café · table of name + masked phone, segment, visits, spend, last visit · 50 per page, server-side.
- **Profile:** visits, total spend (cancelled excluded), first / last visit, favourite dishes, consent (Opted in / Opted out / Not asked), average rating, campaigns received / redeemed, **Delete customer data**.
- **Delete dialog:** states exactly what is removed, that it can't be undone and is audited; Keep data has default focus.
- **Consent tab:** read-only latest row per customer (type, status, wording version, source, date) + Export CSV (full history). "Only the customer can change their consent — on WhatsApp, or by replying STOP."

**Done when:** search and chips filter · numbers masked on screen and in CSV · delete needs the dialog and writes `customer.delete` · export audited · non-owners redirected.

### Campaigns

Owner only (a non-owner on `/campaigns/*` goes to `/orders`). List, detail, Insights, the Redeem tab, templates and the five-step wizard. Backend: [campaigns](backend.md#campaigns), [redemption](backend.md#redemption).

- **Tabs row** on the list: **Campaigns · Insights · Redeem**. `new`, `insights` and `redeem` are reserved values of `:campaignId`; campaign ids are UUIDs. Redeem is the same body as `/redeem` ([Redeem offer](#redeem-offer)).
- **List:** notice "Only opted-in customers can be messaged. Sending is throttled and may roll over to the next day." · table of Name (+ scheduled date) · Audience · Status tag · Sent · Redeemed (% of delivered) · Came back (% of sent), all from `v_campaign_insights`; cards on phones · read-only template list with status chips ("Templates are created by the Restro team").
- **Detail** (`/campaigns/:id?tab=overview|recipients|redemptions|opted-out`; a draft still opens the Wizard). Header: ← Campaigns, name, status chip, **Export CSV** (`GET /campaigns/{id}/recipients/export`, masked numbers, audited `campaign.export`; toast "Exported 412 rows"), Cancel scheduled send / Stop sending. Meta line plus "Codes valid till 12 Oct" (café timezone) or "No expiry (30-day window for results)".
  - **Overview:** funnel as % of sent: Sent → Delivered → Read → Replied → Came back → Redeemed. Side counts: Held by WhatsApp ⓘ (tooltip: normal, a cross-business limit), Failed, Still to send (queued incl. deferred), Opted out after. Tiles: Came-back revenue ("WhatsApp orders in the offer window + counter bills with a code"), Discount given, Redeemed ("N at the counter · M on a table bill"), WhatsApp cost ("—" while 0). Redemptions by day: stacked bars, counter vs table bill. A collapsible "What these mean" glossary (Replied, Came back, Redeemed, Offer window, Opted out after).
  - **Recipients:** `v_campaign_recipients`, 50 per page server-side with an exact count, name search (300 ms debounce, like Redeem), status chips All · Not sent · Sent · Delivered · Read · Held · Failed, outcome chips (AND) Redeemed · Not redeemed · Came back · Replied · Opted out after (Redeemed and Not redeemed exclude each other). Columns: name + masked phone · code · status · sent / read · replied · orders in window + spent · redeemed (when · channel · by whom · discount) · opted out after (date · source). Cards on phones. Count line "412 recipients · 37 match". While the next filter or page loads, the last page stays on screen, dimmed, with "· updating…" on the count line and the pager in place.
  - **Redemptions:** `v_redemptions` for the campaign: time · customer · code · "Counter" / "T7 bill #1041" · bill · discount · staff ("Former staff" when the membership is gone, "Unknown" for no staff).
  - **Opted out:** recipients with `opted_out_at`: name · masked phone · when · "Replied STOP" / "Tapped Stop offers" / "Blocked on WhatsApp". "Opted out within the offer window after this campaign was sent."
- **Insights** (`/campaigns/insights`): period chips 30 d · 90 d (default) · All (campaigns by `scheduled_at`, redemptions by café-local `day`; the panels stay on screen while a new period loads). KPI tiles: Offers delivered · Redeemed (% of delivered) · Came back (% of sent) · Came-back revenue ("Summed per campaign; a visit can count for more than one campaign") · Discount given · Opted out after. Best campaign: highest came back / sent among campaigns with ≥ 10 delivered, ties to more redemptions, else "Not enough results yet: a campaign needs 10 delivered messages". Sortable comparison table (rows open the detail); cards on phones, where a "Sort by" select and an Ascending / Descending toggle replace the header. Redemptions by day (stacked), by staff (name · total · at counter · on bills · discount · last), the latest 25. Empty: "No campaigns sent in this period."
- **Wizard:** Audience → Template → Message → Schedule → Review, with a live WhatsApp preview (Order now button, "Reply STOP to opt out"). Draft saved on Continue past Message. Review shows every audience-preview line and the quality warning.

Schedule is disabled with no approved marketing template, no recipients, a past time, or a disconnected number. The list, detail and Insights refetch every 60 s (`sending` campaigns move while open). Every read of `v_campaign_insights`, `v_campaign_recipients` and `v_redemptions` filters `.eq("cafe_id", cafeId)`. Money only from view or API paise through `inr()`; masked numbers only, the CSV included. The staff label is the same everywhere (`staffLabel` in `lib/offers.ts`): the name, "Former staff", or "Unknown". Code in `features/campaigns/` (`CampaignsScreen`, `CampaignDetail`, `CampaignInsights`, `labels.ts`), `lib/campaignInsights.ts`, `styles/campaigns.css`.

**Done when:** list numbers match `v_campaign_insights` · the detail funnel matches the view row and opted-out is never counted as came back · recipient chips filter on the server and page by 50 · Export downloads the masked API CSV and is audited · Insights picks the best campaign only with ≥ 10 delivered and shows redemptions by staff · Schedule, Cancel and draft saves audited · preview has Order now and the STOP line · non-owners on `/campaigns/*` go to `/orders` · 375 px with no horizontal scroll, every table included.

### Redeem offer

`/redeem` (Service nav "Redeem offer", tab-bar label "Redeem") for `shift_manager`+, and the same body as the Campaigns Redeem tab (`/campaigns/redeem`, owner). One column, phone first, max 720 px. Eligibility, reasons, discounts and the amount to collect all come from the API ([redemption](backend.md#redemption)); the console never decides them. Code in `features/redeem/` and `lib/offers.ts`.

- **Search:** "Redeem an offer" · "Search by name, the last 4 digits of their number, or the code they show you." Large autofocused input, `enterKeyHint="search"`, 300 ms debounce, ≥ 2 characters, `POST /offers/lookup` (text in the body, never the URL). Exactly 1–3 digits shows "Type the last 4 digits of the number" and makes no call; 5+ digits shows the API's 400 text. The input is kept on error.
- **Results:** one card per customer, eligible first (server order). First name (bold), full name muted if different, masked phone, "6th visit · last 12 Oct"; "Deleted customer" when found only by code. Open-bill chip "At T7 · bill open · ₹ 640", or "T7 bill already has code BACK20-7KQ". Offer rows newest first: mono code (highlighted when it is the searched code), campaign, offer text, "valid till 12 Oct" (café timezone), and a verdict chip (colour + icon + label): Eligible · Used · Expired · Cancelled · Not sent yet · Didn't reach them. The reason and the amber warning come from the API; a used code also shows where, when, by whom and the discount. Only eligible rows have **Redeem at counter** and, when an open bill has no code, **Apply to T7 bill** (`POST /bills/{table_session_id}/apply-code`) → "Applied to T7 bill · ₹ 128 off".
- **Redeemed today:** `v_redemptions` with `.eq("cafe_id", cafeId)` and `day` = today in `cafes.timezone` (never the browser's), newest first: time · first name + masked phone · code · "Counter" / "T7 bill" · "−₹ 248" · "by Ravi" ("by Former staff" when the membership is gone, "by Unknown" for no staff, as on every campaign screen). Refetches every 30 s and after the user's own redemption or Apply.
- **Dialog** (centred on desktop, bottom sheet on phones): "Redeem BACK20-7KQ" · "Riya · Weekend comeback · 20 % off the bill" or "Offer: free cookie · no bill discount". Field "Amount paid (₹, before discount)", `inputMode="decimal"`, parsed by `toPaise()`; rejects 0, more than 2 decimals, under ₹ 1 ("Check the amount: at least ₹ 1") and over ₹ 1,00,000 ("Check the amount: over ₹ 1,00,000"). Button repeats the amount ("Redeem 20 % off ₹ 1,240"), disabled offline or busy. `Idempotency-Key` created when the dialog opens and reused on every retry; changing the amount after a failed attempt creates a new key. Success: large **"Collect ₹ 992"** (`total_paise`), "Bill ₹ 1,240 · Discount −₹ 248 · Redeemed by you at 12:41", Done; toast "BACK20-7KQ redeemed · ₹ 248 off". Errors inline with the API text: 422 `invalid_code`, 409 `code_already_used` ("Already used. …"), 409 `already_redeemed_this_visit` ("One offer per visit. …"), 400 amount, 403 "You don't have access to this.", network (amount kept, retry with the same key). On 409 or 422 the lookup is refetched.
- **States:** idle (hint + Redeemed today) · "Searching…" · "No customer matches "xyz". Try the last 4 digits of their number or the code." · "No offers for Riya." · "Showing 20. Type more of the name." · "Couldn't search. Try again." + Retry · offline (Redeem and Apply disabled).

**Done when:** a shift manager at 375 px finds a customer by name, last 4 digits and code (typed without the dash or in lower case too) with the API's verdict and reason · only eligible offers can be redeemed · a counter redemption shows Collect from the API and appears in Redeemed today · a retry reuses the key and never redeems twice · a second till on the same code shows "Already used" with who, when and where · Apply uses the bill path · Redeem disabled offline · Redeemed today uses the café-local date · no full number anywhere · no horizontal scroll at 375 px.

### Analytics and service report

Read-only, from views refreshed nightly. Revenue = WhatsApp orders only, cancelled excluded ("walk-in cash and UPI aren't included").

- **Analytics** (`owner`): period 7 / 30 / 90 days · KPIs revenue, orders, average order, average rating, returning % with deltas vs the previous period · revenue bar chart (weekly bars at 90 days) · top 5 dishes · the service report for the same period.
- **Service report** (`manager`+, fixed last 7 days on its own route): median prep (placed → ready) · % orders overdue · slowest dish · peak overdue hour · slowest 3 dishes · 1–3★ ratings with text.

**Done when:** the period reloads every block · KPIs and deltas match the views · `/service-report` only for `manager` and `owner` · chart scales at 375 px.

### Setup

Owner. Tabs **Tables & QR · Team · Settings · Alerts**, each with its own URL.

- **Tables & QR:** add / edit tables (label unique, area, seats); download one QR or all as PDF. Every printed QR carries "Can't order on WhatsApp? Ask any of our team."
- **Team:** invite by email and role; change role; deactivate / reactivate (immediate). Owner rows can't be deactivated; the last active owner can't be demoted. One login per person.
- **Settings:** opening hours · last-order cutoff · agent tone (Warm / Crisp / Playful) · feedback delay 15 / 30 / 60 min (default 30). No GST setting: menu prices include GST. Saved in one `PUT`.
- **Alerts:** café alert number (gets `new_order_alert`) · sound tests for each severity.

**Done when:** QR opens WhatsApp with "Hi, I'm at Table N" · invite, role change and deactivation work and deactivation blocks the next request · settings save with `settings.update` · alert number validated · 375 px.

### Audit log

Owner only (D15; `platform_admin` once support mode exists). Who did what in this café: every staff write, newest first, one plain sentence per action. Reads go through the API (`/audit/*`), never Supabase: sentences, labels and severity reasons come from `domain/audit/catalog.py` (decision 19). The only Supabase read is the Customer filter's typeahead over the customer list. Backend: [audit](backend.md#audit). Non-owners get no nav entry and are sent to `/orders`; the API answers 403.

- **Toolbar:** date presets Today · Yesterday · 7 days (default) · 30 days · Custom (two `datetime-local` fields), plus All time when an order, customer or chat filter is set (then the default) · search "Search name, order #, dish…" (300 ms; "#42" or "42" is an order number and, like Order #, searches all time) · **Critical only** switch with a red count badge · **Export CSV**.
- **Summary strip** from `/audit/facets` for the date range: "**3 critical** · 7 warnings · 142 actions in the last 7 days". Each count applies that severity filter.
- **Filter bar:** multi-selects Category · Action (Order accepted, Order cancelled, Chat taken over first, then grouped by category with the filter-only accept / ready / serve first under Orders; zero-count options greyed but selectable) · Person (team members with a role chip and "(inactive)", former members the log still names, "Restro support", "AI (from comments)"; the last two filter by role) · Role · Order # · Customer (typeahead over the customer list, 300 ms after the last key, up to 8 matches, first names only). Active filters show as chips with ×, plus **Clear all**.
- **List:** day headers (Today, Yesterday, "Sat, 25 Oct") that stick just below the console header. Row: time · severity marker · sentence with the actor in bold · category chip · link chips `#42` → order history, first name → customer, Chat → the chat, Campaign → the campaign · expand chevron (`aria-expanded`).
- **Severity:** colour + icon + label, never colour alone, never a pulse (D26: pulse means "act now"; history isn't urgent):

| Severity | Look |
|---|---|
| `critical` | 4 px `--color-accent` left border, accent tint, `CircleAlert`, "Critical", the reason as a muted subline |
| `warning` | Amber left border and tint, `TriangleAlert`, "Warning", the reason |
| `info` | Plain row |

- **Expanded row:** the details list (reason, staff note, was / now, discount, bill total, fields changed, …) · Order history · Open chat · Open customer · Only this person · Only this order · Only this action (an accept row filters on `order.accept`) · a collapsible **Technical details** block (action, role, support, ids, raw `change` JSON).
- **Paging:** 50 a page; **Show older** passes the page's `next` as `before`; at the end "That's everything for these filters." The list and the counts refetch every 60 s and on focus; no Realtime.
- **URL keys:** `range`, `from`, `to`, `q`, `critical=1`, `severity`*, `category`*, `action`*, `actor`*, `role`* (* = repeatable), `order`, `order_id`, `customer`, `conversation`. Every filter change is a history entry; defaults are left out, so a plain `/audit` stays plain; paging isn't in the URL; unknown values are dropped before they reach the API.
- **Phone (375 px):** search, then Critical only and **Filters (n)**, which opens a bottom sheet with the presets, every filter and Export. Rows become cards; the critical border and tint stay; no horizontal scroll.
- **States:** "Nothing recorded between 25 Oct and 1 Nov. Staff actions appear here as they happen." · "No actions match these filters." + Clear filters · `CircleCheck` "No critical actions in this period." · 403 "Only the owner can see the audit log." · network "Couldn't load the audit log. Try again." (filters kept) · with rows on screen: "Couldn't load more." + Try again (Show older failed) or "Couldn't refresh. Showing the last loaded entries." + Try again (the 60 s / focus refresh failed) · 400: the API's message inline · export: spinner, then "Audit log exported (312 rows)", or the API's message ("Too many rows — narrow the dates or filters." over 10 000 rows).

**Order history** (`/audit/orders/:orderId`, owner): header "Order #42 · T4 · Riya · Cancelled" with total, source and "placed 31 Oct, 12:41" · a red **Cancelled** block, "Cancelled by Priya — Out of stock" (or "Cancelled by the customer"), with the staff's own text under "Staff note (never sent to the customer)" · the timeline, oldest first on a vertical rail, with actor icons customer `UserRound`, staff `BadgeCheck`, support `LifeBuoy`, AI `Sparkles`, automatic `Clock` and the same severity look · footer "All actions on this order in the audit log" (`/audit?order_id=…&range=all`) and Open customer · 404 "Order not found in this café."

**Entry points (owner only):** **History** on every order card (`/audit/orders/:id`) · **Staff activity** on the customer profile (`/audit?customer=<id>&range=all`) · on Today, an amber card "N critical staff actions today" → `/audit?severity=critical&range=today`.

| Read | API |
|---|---|
| Rows | `GET /audit/entries` (filters + `before`, `limit`) |
| Counts | `GET /audit/facets?from&to` |
| Labels, categories, roles | `GET /audit/catalog` (static per deploy, cached for the session) |
| CSV | `GET /audit/export` (same filters; `audit-log-YYYY-MM-DD.csv`; audited as `audit.export`) |
| Order history | `GET /orders/{id}/history` |
| Customer typeahead | Supabase `v_customer_profile` (name, visits; 8 rows; RLS) |

Code: `features/audit/` (`AuditScreen`, `AuditFilters`, `AuditRow`, `OrderHistory`, `filters.ts`), `lib/audit.ts`, `styles/audit.css`.

**Done when:** a staff cancel shows as a critical row with icon, label and reason · filters by action (including Order accepted), category, severity, person, role, order, customer, chat and dates, all kept in the URL and shareable · searching "#42" and Order # 42 find the same order over all time · a chip × removes one filter, Clear all removes every one · Show older pages to the end · the order history merges every source, with the cancel highlighted and the staff note labelled · CSV of the current view, toast with the row count · non-owners: no nav entry, redirect, 403 · the filter sheet works at 375 px with no horizontal scroll · the owner finds who cancelled order #N in under 30 s.

### Platform

Our cross-café console (`platform_admin`). Until P3 the team uses Supabase Studio, GCP console and WhatsApp Manager. No sounds. Secrets are never shown — only `token_ref` / `pin_ref`.

| Screen | Content |
|---|---|
| Cafés | KPIs (live, orders today, disconnected, templates pending) · Onboard café · table: status, go-live count, quality, tier, Open as support |
| Café detail | Go-live checklist, health rows, Meta ids and secret refs |
| Templates | Per café, status and quality, Sync with Meta |
| Number health | Card per café: Healthy / Check / Reconnect |
| Costs | Cost per order, WhatsApp 7 d (estimated vs actual), AI 7 d, fallback rate; per café and per campaign (`v_unit_costs`, `v_daily_costs`) |
| Dead letters | Dead-lettered `pending_work` per café: conversation, attempts, `last_error`, time; Retry · Resolve (`/platform/dead-letters`) |
| AI review | Fallbacks and ≤ 2★ ratings with read-only transcripts |
| Audit log | Filter by café, actor, action; export |

- Status chip: Disconnected (critical) · Paused (warning) · Needs attention (onboarding or any checklist item open) · Live (calm).
- Onboard form takes Secret Manager references only and rejects anything that looks like a token.
- Open as support → owner console of that café under the support bar; Exit → Cafés.
- **Audit log (P3)** reuses the café audit log rather than building a second one: the backend `domain/audit/viewer` functions already take `cafe_id`, and the console reuses `features/audit` (`AuditRow`, `filters.ts`, `lib/audit.ts`) with a café filter added. Not built in this change.

**Done when:** only `platform_admin` reaches `/platform/...` · onboarding accepts refs only and writes `cafe.create` · checklist, sync and support actions audited · support bar on every screen and every action `support = true`.
