# Code rules and tests

Read the section for a folder before changing it. The invariants in [CLAUDE.md](../CLAUDE.md) apply everywhere. **CC** = critical core: change only when the task names the file and the change ([work split](plan.md#work-split)).

## Backend

`backend/` — layout in [architecture](architecture.md#backend-layout).

**Must**
- Webhook: verify signature on raw bytes → store inbound + `pending_work` in one transaction → publish the `wa-inbound` signal + enqueue `wa-ack` → 200 in < 300 ms. Nothing else.
- Conversation and cart state in `conversations.state`, never in process memory.
- `audit_log` for every console write; `order_events` for every order change.
- Signal, task and job handlers idempotent; re-check state first.
- LLM: 6 s timeout, one retry on the other model, then handoff. Turns: 3 retries from `pending_work`, then dead letter. Outbound: a step row before every send; retry only what certainly didn't go; ambiguous → `send_check`.
- No DB transaction open across an LLM call or a WhatsApp send.
- JSON logs with ids only (`cafe_id`, `conversation_id`, `wamid`, `order_id`, `request_id`).

**Never**
- Log tokens, secrets, phone numbers, `wa_id` or message text. Use floats for money.
- Mount `/internal/*` on `api` or public routers on `worker`.

| Folder | Must | Never |
|---|---|---|
| `core/` **CC** | Signature before JSON parsing (401). Café from resource or `X-Cafe-Id` + active membership (403). Role order `shift_manager` < `manager` < `owner`; `platform_admin` separate, audited `support = true` in support mode. Secrets come from Secret Manager | Trust `cafe_id` or role from the body. Cache JWKS or tokens without expiry (token cache 10 min). Let a missing OIDC token reach a handler |
| `api/` | Thin: auth → validate → one domain call → map errors. Order changes via `transition` with `expected_version`. `/internal/*` returns 2xx only when work is done or deliberately skipped | Accept `cafe_id`, role, prices or totals from the client. Run SQL, call the LLM or WhatsApp from a router |
| `db/` | Every repository method takes and filters by `cafe_id` (only exception: tenant lookup by `phone_number_id`). Idempotency by constraint (`ON CONFLICT DO NOTHING`). Order + items + event in one transaction. Optimistic `version` checks; forward-only message status | Create schema from app code. Return rows across cafés outside named, audited platform repositories |
| `domain/` | `outbound` is the only path to WhatsApp; `ai_agent` the only path to the LLM. Every service function takes `cafe_id` | `ai_agent` calling `orders`, `consent`, `campaigns` or `outbound` |
| `domain/conversation` **CC** | Claim the lease before a turn; renew it; check it in every commit (fencing); release it. Retries and dead letters through `pending_work`. Follow the [routing order](backend.md#routing-order). Act on reply ids, never titles. Respect paused, closed, cutoff | Hold a transaction across the LLM call or a send. Use advisory locks or Pub/Sub ordering keys. Let the LLM place an order. Reply as the bot in human mode. Read `cafe_id` from text |
| `domain/ai_agent` **CC** | Only available items in the prompt; version check every turn. Resolve every `item_ref`. Allergens from the DB, `unknown` → handoff. Log every call. Model from config; obey the kill switch | State a price, create orders, change status, record consent. Tool loops or > 1 call per turn. Treat customer text as instructions. Call the SDK outside `integrations/llm`. Real data on the free tier |
| `domain/cart` **CC** | Re-read prices and availability at every summary. Integer paise, GST per café setting. Bump `version` on every change. Qty 1–20 | Accept a price from the LLM, client or button payload. Mutate an ordered cart |
| `domain/orders` **CC** | One transaction with snapshots. 409 on races. Reason on cancel; `shift_manager` cancels only `NEW`, `manager`+ after. Cancel writes `notifications` in the same transaction. Undo cancels the queued message. Timers re-check state | Create orders outside Confirm or `POST /orders/manual`. Move status backwards except by undo. Pass `cancel_reason_text` to the LLM or the customer |
| `domain/outbound` **CC** | Window check → consent check → step row (`pending`) → `attempted_at` → send → `sent` / `ambiguous` / `failed` → classify errors | Be bypassed. Resend an `ambiguous` step without `send_check`. Put a price or dish name in a message that didn't come from the DB |
| `domain/consent` **CC** | Append-only, exact `wording_version`. Opt out at once on STOP, button or `131050`, any mode | Infer consent from the LLM or silence. Update or delete a row (except anonymisation) |
| `domain/campaigns` **CC** | Segment ∩ `opted_in`, re-checked at send. One unique single-use code per recipient, same café. Throttle via `campaign-send`; over limit → `deferred` | Send outside `outbound` or the queue. Redeem a code twice |
| `integrations/whatsapp` | Parse every entry in a batch. Expose `phone_number_id` and `wa_id`. Classify errors. Graph version and limits in config | Business rules. Send from anywhere but `outbound` |

## Frontend

`frontend/` — design in [console](console.md).

**Must**
- Supabase only for login and reads; every write calls `api` with `Authorization` and `X-Cafe-Id`. Ship only the anon key.
- Handle 403 even where the button is hidden. Refetch on reconnect and every 30 s for active orders and open requests; Offline banner while disconnected.
- Urgency: colour + icon + label; red only for critical; critical repeats every 30 s until handled.
- Money from the API, displayed from paise. Phone numbers masked.
- Service screens work one-handed on a phone: big targets, ≤ 2 taps for status and sold-out.
- Data only through `lib/` hooks. Optimistic UI must reconcile with the API response and the Realtime echo.

**Never**
- Write to Supabase. Invent roles or labels. Ship demo controls in production.

## Supabase

`supabase/` — design in [schema](data-and-api.md#schema).

- **Must:** `cafe_id` on every tenant table. Keep the load-bearing uniques (`wamid`, `idempotency_key`, order number, `campaign_recipients.code`, `phone_number_id`). `*_paise` money. RLS on every table, read policies only. Append-only tables stay append-only. Forward-only migrations.
- **Never:** real data in `seed/`. A schema, RLS or view change without updating [data-and-api](data-and-api.md).

## Contracts

`contracts/` is frozen. Change only when the task says so, in one PR that updates backend, console and docs. Names match the docs exactly.

## Infra

`infra/environments/{dev,prod}/` (`gcloud` scripts), `.github/workflows/` — design in [infrastructure](architecture.md#infrastructure).

- **Must:** idempotent `gcloud` scripts, safe to re-run. Least privilege per service account; `invoker-sa` invokes `worker` only. CI authenticates through Workload Identity Federation. Pub/Sub settings as in [infrastructure](architecture.md#infrastructure) (push, 10 s ack deadline, transport-only DLQ, 5 delivery attempts). Budget alerts, instance caps, Vertex quotas and the kill switch from day one. Staging and prod fully separate. Secrets only in Secret Manager.
- **Never:** Terraform. Service-account keys. Pub/Sub carrying work or ordering keys. GKE, Cloud SQL, Memorystore, load balancers, NAT, static IPs. A public `worker`.

## Tests

Critical-core tests are written or reviewed by the owning team member. Fixtures come from the seed generator — never real customer data or tokens.

### Unit and integration

Run on every PR. Integration runs against the local Supabase stack.

| Area | Must prove |
|---|---|
| Cart | Every mix of qty and notes; GST inclusive and exclusive; integer paise only |
| Orders | Every allowed and forbidden transition; `shift_manager` cancel on `ACCEPTED` → 403; cancel writes reason fields and one notification per manager and owner; edit/accept race → 409; undo within 5 s cancels the message; cancelled excluded from revenue |
| Webhook | Bad or tampered signature → 401, nothing stored; duplicate `wamid` → one reply; inbound + `pending_work` committed before the 200; statuses never move backwards |
| Work queue | Two signals at once → one turn; lease lost mid-turn → commit refused, turn rerun once; crash mid-turn → recovered after lease expiry; 3 failures → dead letter, standard customer message, alert; retry and resolve from `/platform/dead-letters` |
| Cancellation message | Refund wording or an amount → fixed fallback; `reason_text` never in the prompt; LLM failure → fixed fallback |
| Confirm | Double or stale tap → one order and a polite reply; sold out at Confirm |
| LLM output | Unknown or sold-out item rejected; unknown allergen → handoff; amounts in text dropped |
| Send layer | Free-form outside 24 h blocked; marketing without consent blocked; timeout after send → `ambiguous`, not resent; 429 before acceptance → retried; crash after commit → never-attempted step sent once |
| Consent | STOP, button and `131050` opt out at once; opted-out never in an audience |
| Timers | A timer firing after the state moved does nothing |
| Internal | `/internal/*` without `invoker-sa` OIDC rejected |
| Auth | Expired or wrong-audience JWT; deactivated membership → 403; `shift_manager` on a `manager` endpoint → 403 |
| Isolation | Café A member with `X-Cafe-Id` = B → 403; touching B's order → 404; B's code → invalid; Realtime returns no B rows; direct table writes denied by RLS |
| WhatsApp | Parser fixtures per event type and multi-entry batches; builder payload snapshots; error classification |
| Conversations | Scripted multi-turn chats through the worker with a stub LLM: flows, validation, handoff |

### LLM evals

`evals/`: the team writes cases in `evals/cases/`; Claude Code builds the runner and reports. Synthetic data only.

| Category | Example | Cases |
|---|---|---|
| Clean English | "2 flat whites and a croissant" | 25 |
| Hinglish | "do cold coffee aur ek toast, chilli mat dalna" | 40 |
| Hindi script | "दो कोल्ड कॉफी" | 15 |
| Multi-item with notes | Quantities and modifiers in one message | 20 |
| Ambiguous / misspelt | "cappucino", "the usual" | 25 |
| Recommendations | "kuch healthy, protein wala, spicy nahi" | 20 |
| Allergen / dietary | `contains`, `free_from`, `unknown` | 20 |
| Sold out | Refused with an alternative | 10 |
| Handoff | "talk to a person", complaints | 15 |
| Off-topic | Cricket score, jokes | 10 |
| Edits and cancels | Before and after `ACCEPTED` | 15 |

- 50 cases in Phase 0, 150–250 by Phase 2. Pilot conversations flagged in AI review become new synthetic cases.
- Scoring is deterministic: validated cart and `intent` vs expected. `reply` is spot-checked by a person.
- Each run records accuracy per category, p50 / p95 latency, input / cached / output tokens and cost per order, per model.
- Runs only on prompt, model or routing changes; such PRs merge only with an eval run.

**Phase 2 gate:** cart exact match ≥ 95 % overall and ≥ 90 % Hinglish · 0 invented items or prices reaching the customer · `unknown` allergen → handoff 100 % · human intent ≥ 98 % · LLM p95 ≤ 3 s.

### Load tests

`loadtest/`: a simulator sending signed webhook payloads for N concurrent conversations with realistic bursts. Staging only; never prod or a real number. Stub LLM for most runs, one short real-model run to check quotas.

**Phase 3 gate** at 100 concurrent conversations: zero lost or duplicate messages · inbound-to-reply p95 ≤ 5 s · no DB connection errors · a `worker` instance killed mid-run loses nothing (leases expire, work resumes) · cost per order measured.

### Dry run

At the real café. Service items gate Phase 4, retention items gate Phase 5, and the full list runs twice with friends in Phase 6.

- New customer (name, first order) · returning ("same again?") · Hinglish order
- Confusing order → clarify → two-strike handoff · edit while `NEW` · edit after `ACCEPTED` refused
- Item sold out mid-order · `unknown` allergen → handoff · call waiter · delay message
- Near-closing order · pause and resume · internet drop and reconcile · escalation to manager and owner
- Retention: bill with offer code · feedback + opt-in · STOP
