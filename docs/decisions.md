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

Cloud Tasks for ack, debounced processing, timers and campaign sends; Cloud Scheduler for periodic jobs. One turn at a time per conversation via a debounced `wa-process` task + advisory lock. Supersedes the earlier Pub/Sub ordering-key design.

- **Why:** retries, delayed delivery and rate limits in one service; calls `worker` as a normal request.
- **Rejected:** Pub/Sub ordering key + lock (no delays, still needs Cloud Tasks); Scheduler sweeps for timers (coarse, busy when idle).
- **Cost:** ~1.2 s added to free-text turns.

## 8. One structured LLM call per turn

Free text gets exactly one call with a fixed JSON schema; buttons and commands get none. No tool loop.

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

## Smaller decisions

| Topic | Decision |
|---|---|
| GST | Inclusive or exclusive is a café setting; computed only in the cart |
| Order edits | Customer edits or cancels only while `NEW` |
| Feedback delay | Café setting 15 / 30 / 60 min after `SERVED` |
| Realtime on phones | WhatsApp backup alert + escalation chain |
| Multi-café | Café switcher yes; multi-location features no |
| Onboarding | Hosted only; no Embedded Signup |
