# Plan

Restro is WhatsApp ordering and retention for cafés. A customer scans a table QR, orders with an AI agent on WhatsApp, gets status updates and can reach a person at any time. Staff run service in the **Restro Console**. The owner keeps every customer as an opted-in contact and sends offers measured by who came back and what they spent.

Ordering is the hook; retention is what the owner pays for. Reliability beats features: one lost or wrong order costs more trust than ten missing features.

## Product

| Job | Customer path | Café gets |
|---|---|---|
| Ordering | Scan QR → chat → Confirm → status updates → call a person any time | Faster table service; no app, no extra staff |
| Retention | Opt in after the meal → offer on a slow day → come back and redeem the code | Contacts the café owns; "came back" and "they spent" per campaign |

The order creates the customer, the meal earns the opt-in, a redeemed code links the next visit to the campaign.

**Operating model.** One on-shift `shift_manager` runs the console on a phone or tablet and relays to the cook and waiter by voice; waiters and kitchen don't log in. Unhandled items escalate in-app → `manager` at 60 s → `owner` at +2 min. A person with several cafés switches between them; nothing is shared between cafés.

**Targets**

- Done: 30+ orders in one evening at a real café, zero lost or wrong, every unhandled conversation reaches a human within 1 minute.
- Load: 1–2 pilot cafés; 10–20 concurrent conversations normal, 60+ peak, 100+ headroom.
- Speed: reply p50 < 3 s, p95 < 5 s; button turns < 1 s.
- Cost: 2–5 LLM calls per completed order.

**Out of scope** (needs a decision first): payment gateway and refunds · bookings and pre-orders · voice notes (non-text gets "please type") · loyalty tiers · POS or printer integration · multi-location features · Embedded Signup · full BI.

### Roles

| Role | Who | Can |
|---|---|---|
| `shift_manager` | The one on-shift person (base role; replaces v0.1's waiter / kitchen `staff`) | Live orders and status changes, calls and bills, redeem codes, chats and human mode, sold-out toggle, manual order, customer profile (name, visits, allergies, preferences, feedback, complaints, past orders) and comments that update it; **cancel an order in `NEW`** with a reason |
| `manager` | Runs shifts, owns the menu | + cancel from `ACCEPTED` or `READY`, menu edits, pause ordering, low ratings, service report, cancellation notifications. Escalation level 1 |
| `owner` | Café owner | + Today, customers, campaigns, analytics, setup (team, tables and QR), CSV import, consent register, data deletion, cancellation notifications. Escalation level 2 |
| `platform_admin` | Restro team (`platform_admins` table) | All cafés: onboarding, templates, number health, costs, AI review, audit log. Café actions only in support mode, every action audited |
| Customer | Anyone ordering | WhatsApp only; identity is `wa_id`; never logs in |

Café roles nest `shift_manager` ⊂ `manager` ⊂ `owner` (`memberships.role`). Role per endpoint: [API](data-and-api.md#endpoints).

### Features

**P0** pilot can't run without it · **P1** needed for a busy evening or the retention pitch · **P2** useful in the pilot · **P3** after the pilot starts (until then the team uses Supabase Studio, GCP console and WhatsApp Manager). "Phase" is the build phase below.

| Feature | Priority | Phase |
|---|---|---|
| Webhook verify, dedupe, tenant from `phone_number_id`; Postgres work queue with leases, retries and dead letters; send layer with 24-hour window, consent checks and outbound step records; cost metering | P0 | 0 → 1 |
| Greeting (new vs returning, name capture), menu lists, cart summary with Confirm / Edit / Cancel, order on Confirm, status messages | P0 | 1 |
| Free-text ordering (one structured LLM call per turn, validated in code) | P0 | 1 → 2 |
| Questions, recommendations, allergen answers (`unknown` → handoff); Recommend gives up to 3 personal picks (code-filtered, AI-ranked from preferences, past orders and mood) | P0 | 2 |
| Console login; Live orders: Accept / Mark ready / Mark served | P0 | 1 |
| Shift manager cancels a `NEW` order with a reason (fixed list + custom text; `manager`+ after); AI-written cancellation message | P0 | 4 |
| Undo, delay; urgency alerts and escalation; Chats (take over, reply, hand back); Menu (sold-out toggle, allergens); Realtime recovery (refetch on reconnect + every 30 s) | P0 | 4 |
| Text-only handling (voice, image, sticker → "please type") | P0 | 4 |
| Cancellation notification (in-app) to managers and owner | P1 | 4 |
| Customer edit or cancel while `NEW`; shared table sessions; Calls and bills | P1 | 4 |
| Customer profile from the order card (at-a-glance box, feedback, complaints, orders); staff comments the AI applies to the profile | P1 | 4 |
| Opening hours, last-order cutoff, pause ordering; print ticket, offline banner, manual order, menu CSV import; Setup; service report | P1 | 4 |
| Feedback 1–5, opt-in after `SERVED`, counter QR, STOP; "same again?"; template sync | P1 | 5 |
| Campaign wizard, sender, funnel report; redemption at the bill and at the counter (Redeem offer); campaign detail and insights; Customers (profiles, segments, consent register, delete data) | P2 | 5 |
| Audit log: owner sees who did what, with severity highlighting, filters, order history and CSV | P2 | 5 |
| Weekly owner report · Today screen | P2 | 6 · 7 |
| Analytics; platform screens | P3 | 7 |

## Roadmap

Indicative, for 2–3 developers plus Claude Code. A phase ends only when its exit gate passes; no phase uses the next phase's paid resources early; anything not in a phase goes to the backlog.

| Phase | Dates (indicative) | Exit gate |
|---|---|---|
| 0 Foundations and contracts | 20–26 Oct | Contracts merged; local stack runs; a message to the Meta test number lands in `webhook_events` through the tunnel |
| 1 Thin vertical slice, local | 27 Oct–9 Nov | On a team phone: scan QR → "2 cold coffee aur ek avocado toast, toast mein chilli nahi" → Confirm → order on Live orders within 2 s → Accept → "Your order is being made". 20 runs, no lost or duplicate orders |
| 2 AI accuracy and model choice | 10–19 Nov | [Eval gate](rules.md#llm-evals) met; LLM routing decided |
| 3 Cloud staging | 20–26 Nov | GCP trial on. 100 concurrent simulated conversations, zero lost or duplicate messages, p95 ≤ 5 s, cost per order measured |
| 4 Service completeness | 27 Nov–10 Dec | Every service item of the [dry run](rules.md#dry-run) passes on staging |
| 5 Retention loop (overlaps 4) | 27 Nov–14 Dec | Test campaign to team phones incl. an opt-out, a redemption at the bill and a correct report |
| 6 Pilot readiness | 11–17 Dec | Two full dry runs at the café; Supabase paid plan; all alerts on |
| 7 Pilot | from ~18 Dec, 3 months | Month 1 stability · month 2 first campaigns, Today · month 3 Analytics, platform screens, post-trial budget |

**Parallel from 20 Oct:** Console built from the mock against the local backend with seed data (Claude Code, ~5 weeks) · Meta business verification, display name and template drafts (non-dev track).

### Phase work

| Phase | Work |
|---|---|
| 0 | The five [contracts](#contracts); local env (Docker, Supabase CLI, tunnel, local queue and signal shims); Postgres work queue (`pending_work`) + conversation leases, retries and dead letters; Meta developer account, business verification, test number; monorepo and CI; CI/CD identity: GitHub Actions → GCP through Workload Identity Federation, no keys (deploy workflows run from Phase 3); console scaffold, design tokens, role nav (against the local backend; MSW only for demo builds); first 50 eval cases; draft all five templates; pilot menu with allergens |
| 1 | Webhook ingress; worker on the work queue with the 1 s debounce and routing; deterministic flows; LLM v1 (one model, structured output, menu validation); `transition()` and status messages; send layer with outbound step records; metering from the first call; Live orders on local Realtime |
| 2 | Eval set to 150–250 cases; prompt (compact menu, cached prefix, Hinglish few-shots); all guardrails; run Flash-Lite and Flash; choose routing |
| 3 | GCP trial with every [guardrail](#credit-guardrails) on day one; `gcloud` infra scripts; deploy `api`, `worker`, Pub/Sub, queues, secrets, Vertex AI, Supabase cloud, console hosting; tune concurrency, pool, queue rates; pilot WABA and number; submit templates; load test |
| 4 | Customer edit and cancel, edit/accept race; handoff reasons; escalation chain; non-text handling; table sessions and bill sheet; human mode; sold-out flags; hours, cutoff, pause; delay, staff cancel with AI message and notifications, undo; offline and manual order; print ticket; urgency on every screen; Realtime recovery |
| 5 | Feedback and low ratings; opt-in, counter QR, consent; STOP; template sync; campaign sender, wizard, report; redemption at the bill |
| 6 | Go-live checklist 10/10; menu checked with owner; shift manager trained in < 10 min; QRs printed; two dry runs; monitoring on; Supabase paid plan |
| 7 | Daily review of failed sends, handoffs, fallbacks, cost per order; fixes only in month 1; tune routing and prompts from real conversations |

Templates are per WABA: drafted in Phase 0, submitted on the test WABA as soon as drafted and on the pilot WABA in Phase 3. `order_update` and `new_order_alert` are needed in Phase 4, `feedback_request` in Phase 5, marketing templates for the Phase 5 test and live in pilot month 2.

## Resources

**[verify]** marks vendor facts (prices, free tiers) that change; check before relying on them.

| Resource | Cost | Turn on | Why then |
|---|---|---|---|
| Meta business verification | Free, slow | Now | Gates display names, higher tiers, go-live |
| Meta developer app + test number | Free; ≤ 5 recipients **[verify]** | Phase 0 | Whole loop is built against it |
| Supabase local | Free | Phase 0 | Real Postgres, Auth, Realtime |
| Gemini free developer tier | Free, rate-limited, different data terms **[verify]** | Phases 1–2, synthetic data only | Prompt and eval work at zero cost |
| GCP trial ($200 / 90 days) **[verify]** | Credits expire 90 days after activation | Start of Phase 3 | Covers staging, dry runs, start of pilot |
| Cloud Run, Pub/Sub, Tasks, Scheduler, Secret Manager, Vertex AI (Gemini, global endpoint), Firebase Hosting | Pay per use, free tiers **[verify]** | Phase 3 | Only needed in the cloud |
| Supabase cloud free tier | Free; pauses, no backups **[verify]** | Phase 3 | Staging |
| Supabase paid plan | Monthly **[verify]** | Phase 6 | Backups; pilot orders must not be lost |
| Domain + HTTPS | Small yearly | Phase 3 | Privacy, terms and data-deletion pages for Meta review |

Trial activated ~20 Nov 2026 → credits end ~18 Feb 2027: weeks 0–4 staging and Phases 4–6, weeks 4–8 pilot month 1, weeks 8–13 first half of pilot growth, then paid. The post-trial cost must be known before day 90; the [cost model](architecture.md#cost) is filled from pilot data by pilot week 2.

### Credit guardrails

Set the day the trial is activated, before any deploy.

- Budget $200 with alerts at 25 / 50 / 75 / 90 % to two people.
- Cloud Run `max-instances` 3–5 per service; Vertex AI quotas just above peak; LLM kill switch.
- Everything in `asia-south1` except Gemini on the Vertex AI global endpoint. No GKE, Cloud SQL, Memorystore, load balancers, NAT or static IPs.
- Artifact Registry keeps the last ~10 images; Cloud Logging excludes verbose request logs.
- 10-minute weekly review: GCP billing + `llm_calls` and `wa_messages` cost.
- Never send real customer data to the free Gemini tier.

## Work split

**Critical core** is any code where a bug can lose or duplicate an order, charge a wrong price, message the wrong customer or café, send marketing without consent, leak a token, or change cost or latency materially. A team member designs it, writes or reviews every line and owns its tests. Everything else goes to Claude Code against frozen contracts, reviewed through PRs.

| Critical core | Code |
|---|---|
| Webhook ingress (signature, dedupe, tenant) | `api/`, `integrations/whatsapp/` |
| Conversation pipeline (work queue, lease, debounce, batch, retries, dead letters) | `domain/conversation` |
| Order state machine and transitions | `domain/orders` |
| Cart, pricing, tax | `domain/cart` |
| Menu availability | `domain/menu` |
| LLM prompt, schema, validation, routing, guardrails | `domain/ai_agent`, `integrations/llm` |
| Allergen and handoff rules | `domain/ai_agent`, `domain/handoff` |
| Send layer | `domain/outbound` |
| Timers and escalation | `domain/timers`, `domain/handoff` |
| Consent and opt-out | `domain/consent` |
| Campaign sender and redemption | `domain/campaigns` |
| RLS, memberships, secrets | `supabase/migrations/`, `core/`, `integrations/gcp` |
| Cost metering | `domain/ai_agent`, `domain/outbound` |

**Claude Code builds:** console shell and every screen; urgency UI; MSW mock and seed generator; menu CSV import UI; print ticket; QR sheet generator; analytics view SQL and weekly report (team reviews queries); eval runner and report (team writes cases); load-test scripts; `gcloud` infra scripts and GitHub Actions workflows (team reviews line by line); landing, privacy, terms and data-deletion pages.

**Workflow:** task with links to the contract sections → Claude Code branch and PR → team review (core-adjacent PRs need two reviewers) → CI runs lint, types, tests, and evals for prompt or routing changes.

### Contracts

Frozen in Phase 0 in `contracts/`. A change is one PR that updates backend, console and docs together.

| File | Defines |
|---|---|
| `schema.sql` | Tables, keys, constraints ([schema](data-and-api.md#schema)) |
| `state-machines.md` | Order states; conversation steps + `mode`; `pending_work` and outbound send statuses ([orders](backend.md#orders), [conversation](backend.md#conversation), [sending](backend.md#sending)) |
| `openapi.yaml` | Console and platform REST API ([API](data-and-api.md#api)) |
| `events.md` | Realtime subscriptions ([realtime](data-and-api.md#realtime)) |
| `llm_output.schema.json` | One structured LLM output per turn ([AI agent](backend.md#ai-agent)) |

## Open decisions

Build against the default. When decided, move it to [decisions](decisions.md) and update every doc it touches. A new unknown goes here first.

| Topic | Question | Default | Decide by |
|---|---|---|---|
| GCP trial | Activated yet? | Not activated; activate at start of Phase 3 | Phase 0 |
| Team | Size; who owns critical core and supervises Claude Code? | 2–3 developers + Claude Code | Phase 0 |
| Pilot café | Which café, go-live date? | None; Phase 6 ends ~17 Dec 2026 | Phase 0 (menu); Phase 3 (WABA) |
| Meta business | Verification status, legal entity, domain with email | Start verification now | Phase 0 |
| LLM routing | Flash only, Flash-Lite only, or deterministic router? | One model behind one per-café config value | Phase 2 exit |
| Menu size | When does the full-menu prompt stop working? | Full menu; measure tokens in Phase 2 | Phase 2 |
| Post-trial budget | Monthly spend after day 90 | Supabase paid plan before go-live; budget from cost model | Pilot week 2 |
| Billing unit | One bill per table session or per person? | Whole table session | Phase 4 |
| Payment | Counter takes payment, or café POS? | Café POS; bill sheet totals, redeems, marks delivered | Phase 4 |
| Pause permission | Can `shift_manager` pause ordering? | No (`manager`+) | Phase 4 |
| Manual order status | Create as `NEW` (API spec) or `ACCEPTED` with no escalation (orders spec)? | `ACCEPTED`, no customer messages, no escalation | Phase 4 |
| Prep time range | 1–120 min (backend) or 1–90 (console form)? | 1–120 | Phase 4 |
| CSV allergen separator | `;` (backend) or `\|` (console template)? | `;` | Phase 4 |
| Campaign cancel | Cancel only `scheduled`, or also `sending`? | `scheduled` and `sending`; queued recipients not sent | Phase 5 |
| Discount amount | How is the discount defined? (`campaigns` stores only `offer_text`) | Percentage of subtotal, as in the mock | Phase 5 |
| WhatsApp marketing cost | Who pays for WhatsApp marketing messages: the café or Restro? | TBD. Doesn't block the build; metered per message in `wa_messages.cost` either way | Before pilot month 2 campaigns |
| Dead-letter handling **(pending)** | When a turn is dead-lettered, does it also alert the shift manager (`help` request, `system_error`) and switch the chat to human mode (a third Bot → Human trigger)? | Yes to both, per invariant 11. The customer message and the `platform_admin` escalation happen regardless | Ordering slice S7 |
| Cancellation message guardrails **(pending)** | Beyond the standard guardrails: forbid refund or compensation promises? Keep the custom `reason_text` away from the LLM and the customer? | Yes to both: refund / compensation wording dropped like amounts (fixed fallback sent); only `reason_code` reaches the LLM | Phase 4 |
| Worker CPU after the ack | Cloud Run throttles CPU after the response: run `worker` with instance-based billing, or have the push handler hand the turn to a Cloud Task? | Instance-based billing (CPU always allocated) on `worker` | Phase 3 |
| Opt-in wording | Final consent text (`wording_version`) | Draft as version 1 | Phase 5 |
| Consent re-ask | When to ask again after "No"? | Not within 30 days | Phase 5 |
| Opt-out `source` | Which `source` do STOP / button / 131050 rows get? | Add `keyword`, `button`, `meta_block` | Phase 5 |
| Chat retention | How long is chat text kept? | Kept for the pilot; removed on deletion | Phase 6 |
| Pilot number | New dedicated number or the café's existing one? | Dedicated | Phase 3 |
| Codes from a cancelled campaign | Should codes already sent before a cancel still work? | No (unchanged) | Phase 5 |
| Redemption void | Can a manager void a mistaken redemption? | No; audit only | Phase 5 |
| Counter visit | Does a counter redemption count as a visit (`last_visit_at`, segments)? | No | Phase 5 |
| Discount cap | A per-campaign maximum discount in ₹? | None | Phase 5 |
| Audit access | Do managers see the audit log? | No, owner only | Phase 7 |
| Large-discount flag | Threshold for a `warning` on `offer.redeem` | ₹ 500 | Phase 7 |
| Sign-in audit | Show sign-ins (`auth.audit_log_entries`) in the audit log? | Not built | Phase 7 |
| Recommend model | Flash or Flash-Lite for `recommend` (part of "LLM routing") | Café model / primary | Phase 2 exit |

## Risks

Never weaken a mitigation without a decision record. The Phase 6 dry runs exercise the first six rows.

| Risk | Mitigation |
|---|---|
| Order lost between Confirm and the console | Order + items + event in one transaction; unique `idempotency_key`; Realtime alert + 30 s refetch; escalation timers; `stuck_inbound_check` |
| Message stuck or lost in the pipeline | Inbound + `pending_work` durable before the 200; lease expiry; `stuck_inbound_check`; retries, then dead letter → customer told how to reach a person + `platform_admin` |
| Duplicate WhatsApp messages | One `wa_messages` row per outbound step, written first; ambiguous sends checked, never blindly resent |
| Prompts processed outside India (Vertex global endpoint) | Accepted for the MVP; stated in the privacy policy; no phone numbers or other customers in prompts |
| AI offers sold-out or invented dishes, or states a price | Prompt has only available items; code resolves every `item_ref`; code renders names and totals |
| Hinglish accuracy below target | Phase 2 gate; confirmation always shown; handoff after two failed clarifications |
| Edit/accept race | Optimistic locking in `transition()`; 409 `version_conflict` |
| Verbal relay loses allergy notes | Print ticket with allergy boxed; `allergy_note`; unknown allergen → handoff |
| Shift manager misses alerts | Escalation chain; sound; `new_order_alert` WhatsApp backup; alerts gate before service |
| Campaigns hurt number quality | Opted-in only; small first batches; throttled sends; "Stop offers" on every marketing template; daily quality check |
| Meta verification or templates slow or rejected | Started now; drafted in Phase 0; utility wording free of promotion |
| Access token invalid | Daily `token_health`; Disconnected banner |
| Free-tier LLM data terms | Synthetic data only until Phase 3 |
| Supabase free tier pauses, no backups | Paid plan before go-live |
| Trial credits wasted or run out | Activate at Phase 3; cost model by pilot week 2 |
| LLM spend spike | Kill switch; `max-instances`; Vertex quotas; `v_daily_costs` |
| Cross-café data leak | Tenant from infrastructure; RLS; isolation tests |
| Scope creep from the mock | Platform screens are P3; phase gates; backlog |
