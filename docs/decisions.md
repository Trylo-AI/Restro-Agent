# Decisions

Settled trade-offs. Don't reverse one in code; add a new entry that supersedes it. Add an entry when choosing between workable designs, changing an invariant, or closing an [open decision](plan.md#open-decisions).

Format: decision · why · rejected alternatives · cost.

## 1. AI talks, code decides

The LLM interprets the customer and drafts text. Prices, totals, availability, orders, status and consent are code reading the DB; the order is created only by the Confirm tap.

- **Why:** LLMs invent dishes and prices and can be prompt-injected; one wrong total breaks a café's trust.
- **Rejected:** AI quotes prices or creates orders through a tool; AI renders the dish list.
- **Cost:** more code paths (router, cart, confirmation); the AI can't "just handle" price questions.

## 2. Acknowledge webhooks fast, process asynchronously

The webhook verifies, stores, enqueues and returns 200 in < 300 ms. All real work runs in `worker`.

- **Why:** Meta retries slow webhooks; a turn has DB work, an LLM call and sends.
- **Rejected:** processing inline (timeouts, retries); FastAPI background task after the response (Cloud Run throttles CPU; no retry).
- **Cost:** one more hop to operate and monitor (`stuck_inbound_check`).

## 3. All writes go through FastAPI

The console uses Supabase only to log in and read. Every write is a REST call to `api`. RLS has read policies only.

- **Why:** writes have side effects — transition checks, `order_events`, `audit_log`, WhatsApp messages.
- **Rejected:** SPA writes with RLS write policies; database triggers sending messages.
- **Cost:** every write needs an endpoint.

## 4. Full available menu in the prompt

Every call gets the café's available menu as compact lines `id|name|price|veg|spice|tags`, cached per `(cafe_id, menu_version)`.

- **Why:** café menus are small enough; search can miss dishes and needs an index in sync with sold-out toggles.
- **Rejected:** pgvector menu search tool; long-lived cache without a version check.
- **Cost:** more input tokens; doesn't scale to very large menus (open: size limit).

## 5. Tenant from infrastructure, never input

Webhooks: `cafe_id` from `phone_number_id`. Console: from `memberships` behind the JWT. Every repository method requires `cafe_id`; RLS is the second layer.

- **Why:** one café seeing another's customers would end the business.
- **Rejected:** `cafe_id` from body or URL; `cafe_id` as a JWT claim (deactivation lags); RLS as the only control (backend role bypasses it).
- **Cost:** `cafe_id` in every signature; a membership lookup per request.

## 6. One codebase, two Cloud Run services

One FastAPI image deployed as `api` (public) and `worker` (internal, OIDC from `invoker-sa` only).

- **Why:** webhooks need a fast public service; turns, timers and campaigns are bursty and must not be internet-reachable; both share domain code.
- **Rejected:** one service (internal routes public, bursts hurt webhook latency); separate codebases (duplicated logic).
- **Cost:** both deploy together; router mounting must stay strictly separate.

## 7. Cloud Tasks for inbound and timers, no Pub/Sub

**Superseded by [12](#12-postgres-work-queue-and-conversation-lease-pubsub-as-a-wake-up-signal) for inbound processing.** Cloud Tasks still carry `wa-ack`, timers and campaign sends.

Cloud Tasks for ack, debounced processing, timers and campaign sends; Cloud Scheduler for periodic jobs. One turn at a time per conversation via a debounced `wa-process` task + advisory lock. Supersedes the earlier Pub/Sub ordering-key design.

- **Why:** retries, delayed delivery and rate limits in one service; calls `worker` as a normal request.
- **Rejected:** Pub/Sub ordering key + lock (no delays, still needs Cloud Tasks); Scheduler sweeps for timers (coarse, busy when idle).
- **Cost:** ~1.2 s added to free-text turns.

## 8. One structured LLM call per turn

Free text gets exactly one call with a fixed JSON schema; buttons and commands get none, except the Recommend, mood and "Other ideas" taps, which make one `recommend` call ([decision 20](#20-ai-recommendations-from-code-filtered-candidates)). Still at most one LLM call per turn. No tool loop.

- **Why:** tokens are the largest variable cost and the slowest step; structured output keeps validation deterministic.
- **Rejected:** tool-calling loop (more round trips); LLM for every message.
- **Cost:** the model can't look things up mid-turn; everything it needs is in the prompt.

## 9. Three café roles

`shift_manager` ⊂ `manager` ⊂ `owner`; `platform_admin` separate. No `staff` role.

- **Why:** one on-shift person runs the console; waiters and kitchen don't log in; escalation needs a clear chain.
- **Rejected:** a `staff` role for waiters; the mock's `staff` ⊂ `manager` ⊂ `owner`.
- **Cost:** no per-waiter attribution in the audit log.

## 10. Separate service screens

Live orders, Calls and bills, and Chats are three screens with their own badges.

- **Why:** matches the mock; each fits a phone; badges show where attention is needed.
- **Rejected:** one combined Live screen.
- **Cost:** the shift manager switches screens; heads-up cards and badges must be reliable.

## 11. GCP trial at Phase 3

Build Phases 0–2 locally for free; activate the $200 / 90-day trial at the start of Phase 3.

- **Why:** the 90-day clock starts on activation; spending it on development leaves nothing for the pilot.
- **Rejected:** activating now.
- **Cost:** cloud-only issues surface in Phase 3; no real data near the LLM until then.

## 12. Postgres work queue and conversation lease; Pub/Sub as a wake-up signal

The webhook durably records the inbound message and a `pending_work` row, publishes a Pub/Sub signal and returns 200. The worker acks the push, claims a lease on the conversation (`lease_owner`, `lease_expires_at`), waits out the 1 s debounce, runs the turn, sends and releases. Gemini calls and WhatsApp sends run outside short DB transactions; commits are fenced by the lease. App retries (3, exponential backoff) and dead letters live in `pending_work`; Pub/Sub retry and its DLQ cover transport only. Supersedes 7 for inbound; narrows 2 (the worker keeps working after acking a push, so it needs CPU after the response; see the open decision on billing).

- **Why:** Postgres decides processing order and holds the work, so a lost signal, crashed worker or full DLQ loses nothing, and dead letters are visible rows we can retry. An advisory transaction lock had to hold a transaction and a pooled connection across the LLM call and sends.
- **Rejected:** Cloud Tasks `wa-process` + `pg_try_advisory_xact_lock` (long transactions through the transaction pooler); Pub/Sub ordering keys (with push delivery, one failing message blocks every later message for that customer); Pub/Sub as the work queue.
- **Cost:** a table, a lease and a sweep to operate; Pub/Sub joins the stack; the worker needs instance-based billing or a Cloud Task hand-off.

## 13. Gemini on Vertex AI, global endpoint

Staging and prod call Gemini on Vertex AI through the global endpoint as `worker-sa`. Local Phases 1–2 keep the free developer API with synthetic data only. Closes the "Gemini access" open decision; routing (Flash vs Flash-Lite) stays open.

- **Why:** GCP terms for real customer data; draws on the GCP credit; no API key to hold in the cloud.
- **Rejected:** developer API in staging or prod (data terms, keys); a regional endpoint.
- **Cost:** no data-residency guarantee: prompts may be processed outside India. Accepted for the MVP and stated in the privacy policy.

## 14. Outbound step records; ambiguous sends are checked

Each outbound step is a `wa_messages` row written before the request, with a unique idempotency key and status `pending` / `sent` / `ambiguous` / `failed`; `wamid` is unique and nullable. A timeout after the request left is `ambiguous`, checked by `send_check`, never blindly retried. `wa_messages` keeps its uuid primary key, so no separate steps table is needed.

- **Why:** a blind retry after a timeout sends the customer the same message twice.
- **Rejected:** retry with backoff on every error; a separate outbound-steps table (duplicates `wa_messages`).
- **Cost:** an ambiguous send nobody can confirm becomes `failed` and may need a manual resend.

## 15. `gcloud` scripts for infra; Workload Identity Federation for CI

Infra is idempotent `gcloud` scripts per environment in `infra/`, applied by hand by a founder. GitHub Actions deploys through Workload Identity Federation; no service-account keys.

- **Why:** a handful of resources in two projects; scripts are easy to read line by line. WIF removes long-lived keys.
- **Rejected:** Terraform (state to host and lock); service-account JSON keys in GitHub secrets.
- **Cost:** no drift detection or plan step; scripts must stay idempotent by discipline.

## 16. Staff comments update the customer profile automatically

Staff (`shift_manager`+) add free-text comments to a customer's profile. One structured LLM call turns each comment into profile ops; code validates them against a fixed list of kinds and applies them at once, allergies included, with Undo and an audit row. Profile allergies are shown to staff (order card, print ticket) and never sent to the LLM. Decided by Aman, 24 Oct 2026.

- **Why:** the shift manager hears preferences at the table and has no time to fill a form; one sentence keeps the profile current.
- **Rejected:** AI proposes and staff confirm each change (one more tap per comment); auto-apply except allergies; manual profile forms.
- **Cost:** an AI misread can add or remove an allergy without anyone confirming it. Mitigated by showing every change under its comment with Undo, chip × removal, the audit log, and keeping profile allergies a staff-only warning that never answers an allergen question.

## 17. Alibaba models for testing

`LLM_BACKEND=dashscope` runs the app's AI on Alibaba Cloud Model Studio (Singapore) using the team's free-tier quota, for evals, app evaluation and local or staging testing with synthetic data only. The adapter refuses `APP_ENV=prod`. `llm_calls.provider` gains `dashscope`, and those calls are priced at zero (free quota, Stop-on-Exhaust on). Production stays on Gemini on Vertex AI (decision 13). Decided by Aman, 25 Oct 2026.

- **Why:** Phase 2 testing was blocked on model quota, and the free quota (1M tokens on each of 7 models) runs the full system with a real model at no cost.
- **Rejected:** keeping the app Gemini-only and testing the model alone, which skips the router, guardrails and cart, the parts under test.
- **Cost:** a second adapter in `integrations/llm`. Results describe our prompt and system on these models, not on Gemini, so the production model choice still needs a Vertex AI run. Never real customer data: the data terms and region differ.

## 18. Counter redemption and one eligibility rule

Offer codes can be redeemed at the counter as well as on a table bill. Counter redemptions live in `redemptions` with `channel = counter`, the amount entered (`bill_paise`) and the till's `idempotency_key`; table-bill rows get `channel = table_bill`. `domain/campaigns/redemption.py` owns the one verdict (eligible, redeemed, expired, cancelled, not sent, not delivered) and `bills.apply_code` delegates to it, so both channels give the same answer. Staff find offers through `POST /offers/lookup`, an API read, because eligibility is code and `campaign_recipients` is owner-only. Decided by Aman, 1 Nov 2026 ([plan](retention-audit-recommend.md#feature-1-campaigns)).

- **Why:** customers pay at the counter without a WhatsApp bill; one row per code across both channels keeps "one redemption per code" a database guarantee; one verdict means staff and the table bill never disagree.
- **Rejected:** a separate counter-redemptions table (two places to check for "already used"); eligibility computed in the console from Supabase reads (would need `campaign_recipients` and full numbers for digit search); a GET lookup (names and digits in URLs and logs).
- **Cost:** an exception to "reads go through Supabase"; the counter has no bill identity, so "one offer per visit" is a 60-minute rule per customer; redemptions stay append-only, so a mistaken one can't be undone (open decision).

## 19. Audit severity stored at write time; owner reads the audit log through the API

`audit_log` gains `severity` (`critical` / `warning` / `info`), computed by `domain/audit/catalog.classify()` inside `audit.write`, plus the subjects `order_id`, `customer_id`, `conversation_id` copied from `change` (no FK) and an `actor_name` snapshot. The owner reads it through `/audit/*` and `/orders/{id}/history`; `audit_log` RLS stays platform-only. Existing rows get the derived columns by a one-time backfill (`audit_log_backfill_derived()`) with the append-only trigger off for that statement; facts are never touched. `severity` is nullable until `audit.write` sets it everywhere, then becomes required (migration `20261101000002`). Owner only for now (open decision). Decided by Aman, 1 Nov 2026 ([plan](retention-audit-recommend.md#feature-2-audit-log)).

- **Why:** "critical only" needs an indexable column; one rules module gives the label, sentence and reason; a record keeps the classification it had when written. Labels and sentences come from code, and raw `change` isn't for direct client queries.
- **Rejected:** classifying at read time only (no index, a rule change rewrites history); a generic `entity_type` / `entity_id` (can't index each filter Aman asked for); FKs from `audit_log` (append-only rows would block or cascade deletes); exposing `audit_log` to the owner through RLS.
- **Cost:** a documented append-only exception for the backfill; one extra subquery and five index updates per audited write (budget ≤ 2 ms p95); every new action needs a catalogue entry (CI test).

## 20. AI recommendations from code-filtered candidates

The WhatsApp Recommend button, the five mood rows and "Other ideas" suggest up to 3 dishes. Code builds the candidates (available only; profile and visit allergies, diet, dislikes and spice filtered in code; cart and already-shown removed), then one `recommend` LLM call (single attempt, 4 s) ranks them by preferences, past orders and mood and writes a short `why`. Code renders every name and price and re-checks availability before sending. Typed moods ride the existing turn call (intent `recommend`), so a turn still makes at most one call. Any failure, the kill switch or the per-visit cap gives code-ranked picks (favourites, popular, one per category), never a handoff. The prompt carries no prices, no allergies and no customer free text; every model string reaching the customer passes `clean_why` (no digit, amount, claim, URL or "free"). Model: `llm_recommend_model`, default the café model or the primary (Flash). Decided by Aman, 1 Nov 2026 ([plan](retention-audit-recommend.md#feature-3-ai-recommendations)). Amends 8.

- **Why:** Aman asked for personal picks instead of the plain menu; filtering in code keeps allergy safety out of the model (consistent with 16); the model can't state a price it never sees.
- **Rejected:** showing the menu as before; letting the model pick from the whole menu; an `awaiting` mood step (a typed order after the picks must still order); a second call on typed moods.
- **Cost:** one metered call per tap (cap 6 per visit, about $0.0006 each on Flash **[verify pricing]**); a slow model means a 4 s wait before code picks; we never claim vegan, Jain or allergen-free, so the copy carries a caveat.

## Smaller decisions

| Topic | Decision |
|---|---|
| GST | No GST setting for now: menu prices include GST, orders store only `total_paise` (sum of lines), customers see "Total (incl. GST)". Replaces the earlier inclusive / exclusive setting (Aman, 24 Oct 2026) |
| Outbound message body | `wa_messages.payload` holds the rendered outbound message for Chats and the simulator; purged by customer data deletion |
| WhatsApp simulator | Local `simulator/` app + `api` dev router post signed Meta-format webhooks to the real route; the `simulator` transport replaces only the last hop of a send. Never mounted outside `APP_ENV=local` |
| Console mock | Development runs against the real local backend with seed data; MSW only for demo builds |
| Local shims | In-memory signal and task shims locally; a restart drops pending timers, inbound work stays in `pending_work` |
| Dev traces | `dev_turn_traces` (raw LLM output, guardrail actions) only in `supabase/dev/`, written only when `DEV_TRACES=true` |
| Profile visibility | Every member (`shift_manager`+) sees the full customer profile, including feedback and complaints |
| Gemini models (starting point) | Gemini 2.5 is closed to new API keys (Oct 2026). Primary `gemini-3.5-flash`, fallback `gemini-3.5-flash-lite`, minimal thinking; measured on this prompt: Flash p50 2.5 s, Flash-Lite 6.9 s. Both are config values; the Phase 2 evals decide routing |
| Order edits | Customer edits or cancels only while `NEW` |
| Staff cancel | `shift_manager` cancels in `NEW`; `manager`+ from `ACCEPTED` or `READY`. Reason from a fixed list + custom text. P0 |
| Cancellation message | AI-written (intent + café tone) under guardrails with a fixed fallback; one extra LLM call per staff cancel. Managers and owner get an in-app `notifications` row, no WhatsApp |
| Cost metering | `llm_calls` and `wa_messages` carry `cost`, `currency` and `pricing_version` (`wa_messages` also `cost_basis` estimated / actual); `v_daily_costs` and `v_unit_costs` aggregate in Postgres |
| Realtime recovery | Console refetches active orders and open requests on reconnect and every 30 s |
| Realtime refetch coalescing | One channel per live query, shared by every hook; the first event refetches at once, later events within 1 s become one trailing refetch. Keeps a new order ≤ 2 s and cut the Chats screen from ~500 to ~130–200 requests a minute. Rejected: patching rows from event payloads (gaps, RLS-shaped payloads, joins) and a pure debounce (delays the first order) ([perf-test](perf-test.md#console-in-the-browser), 1 Nov 2026) |
| Production access | The two founders and people they explicitly authorise |
| Feedback delay | Café setting 15 / 30 / 60 min after `SERVED` |
| Realtime on phones | WhatsApp backup alert + escalation chain |
| Multi-café | Café switcher yes; multi-location features no |
| Onboarding | Hosted only; no Embedded Signup |
| Redeem in the console | A Service nav item "Redeem offer" (`/redeem`) for `shift_manager`+ and a Redeem tab inside Campaigns (`/campaigns/redeem`, owner), one component. Campaigns is owner-only and shift managers staff the counter (1 Nov 2026) |
| Campaign "accepted" | A ladder: Read → Replied (any inbound in the window, an opt-out tap included) → Came back (a non-cancelled WhatsApp order in the window or a redemption) → Redeemed. The headline is Redeemed, the only hard proof. A visit can count for more than one campaign, and cross-campaign totals say so |
| Offer window | Send → `valid_until`, or send + 30 days when that is empty, capped at now. Replies, orders and opt-outs count only inside it |
| Opt-out after an offer | Doesn't void the code; staff see a warning. Consent governs messages, not an offer already made |
| Counter discount | `discount_percent` of the amount entered (before the discount), floored to the paisa; 0 without a percentage. Amount ₹ 1 to ₹ 1,00,000 |
| One offer per visit | At the counter, one redemption per customer per 60 minutes across both channels (409 `already_redeemed_this_visit`). The table bill keeps "one code per bill", skips the 60-minute rule but takes the same customer row lock. Lookup warns |
| Campaign views | New `v_campaign_insights` and `v_campaign_recipients` (`security_invoker`, owner through base-table RLS, no `has_role`, so the backend CSV reuses them) and `v_redemptions` (definer view filtered by `is_member`, like `v_staff_names`). `v_campaign_funnel` unchanged |
| Deleted customer's code | Still redeemable by code (a bearer code); not findable by name or digits |
| Code expiry | A code is valid to the end of the café-local day of `valid_until` (`cafes.timezone`), on both channels; "valid till" in the offer message uses the same date. Changes the table-bill rule, which compared timestamps (Aman, 1 Nov 2026) |
| Audit expand then contract | `audit_log.severity` is added nullable; a second one-statement migration (`20261101000002`, with the new `audit.write`) re-runs the backfill and sets `not null`. The only departure from one consolidated migration for this work |
| Names in audit text | Customers by first name only in sentences and the CSV |
| Audit date range | Default 7 days; an order, customer or chat filter searches all time; at most 366 days; the export is capped at 10 000 rows |
| Audited reads | Only exports: `audit.export`, `campaign.export`, `consent.export` |
| Audit highlight | Critical and warning rows get a left border, icon, text label and tint; no pulse (pulse means "act now"); 60 s refetch, no Realtime |
| Cancel note in the audit log | `orders.cancel_reason_text` is joined at read time, never copied into `change` |
| "Who accepted" filter | Accept, ready and serve stay stored as `order.transition`; the catalogue adds filter-only `order.accept` / `order.ready` / `order.serve` (= `order.transition` with `change.to`), counted separately and listed first under Orders |
| `offer.redeem` payload | One shape for both channels: `table_session_id`, `order_id`, `order_number`, `campaign_id`, `recipient_id`, `customer_id`, `code`, `channel` (`table_bill` / `counter`), `discount_percent`, `bill_paise`, `discount_paise`; keys may be null |
| Recommend cap | 6 `recommend` calls per visit (`state.rec.llm_calls`), then code picks |
| Suggest for me | A "✨ Suggest for me" row at the top of the category list when there are ≤ 9 categories: the returning greeting with a last order has no room for a Recommend button |
| Recommend language | On a tap: English unless the customer's last inbound text has Devanagari |
| Typed "recommend + order" | Apply the cart changes (summary), then append the picks: one turn, one call |
| Recommendation storage | Picks live in `conversations.state.rec` (ids only); no new table |
| Retention, audit and recommend migrations | One consolidated expand migration `20261101000001_retention_audit_recommend.sql`, plus the audit contract migration above |
