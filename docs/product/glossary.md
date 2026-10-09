# Glossary

> **Area:** Product
> **Source:** MVP Architecture v0.1, all sections ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md)

## Summary

The terms used across these docs, in alphabetical order. Each entry is one or two lines; follow the link for the full design.

## Terms

| Term | Meaning | Explained in |
|---|---|---|
| **24-hour window** | Meta's customer service window: a free-form (non-template) message can be sent only within 24 h of the customer's last inbound message (`customers.last_inbound_at`). Outside it, only a template can be sent. | [outbound-sender.md](../whatsapp/outbound-sender.md) |
| **131049** | A Meta send error code for the per-user marketing cap; the recipient is marked `FAILED`. | [campaigns.md](../retention/campaigns.md) |
| **Advisory lock** | A Postgres transaction-level lock (`pg_advisory_xact_lock`) on a conversation key, taken by the worker so two messages from the same customer are never processed at once. | [inbound-pipeline.md](../backend/inbound-pipeline.md), [ADR 0003](../adr/0003-per-customer-ordering-and-locks.md) |
| **ai_usage** | Table with tokens, latency, model, cost and `fallback_used` per AI turn; the basis of AI cost tracking. | [cost-tracking.md](../observability/cost-tracking.md) |
| **api / worker** | The two Cloud Run services built from one FastAPI codebase. `api` is public (webhooks + REST); `worker` is internal (conversation engine + jobs). | [container-view.md](../architecture/container-view.md), [ADR 0007](../adr/0007-one-codebase-two-services.md) |
| **App Secret** | The Meta app secret used to compute and verify `X-Hub-Signature-256` on webhooks. Kept in Secret Manager. | [webhooks.md](../whatsapp/webhooks.md) |
| **Audit log** | `audit_log (actor, cafe_id, action, entity, before, after, at)`: who changed what, for status changes, sold-out flips, cancellations, campaigns, redemptions and every platform-admin action. | [security-controls.md](../security/security-controls.md) |
| **Burst coalescing** | Optional P2: wait ~1.5 s for more messages from the same customer and pass them to the AI as one turn. | [inbound-pipeline.md](../backend/inbound-pipeline.md) |
| **Cart version** | Integer on `carts` incremented each time a priced summary is shown. The Confirm button carries it; a stale version is rejected, and `UNIQUE (cart_id, cart_version)` on `orders` prevents double orders. | [cart-and-confirmation.md](../backend/cart-and-confirmation.md) |
| **Cloud Tasks** | GCP queue for delayed and throttled HTTP jobs: `campaign-send` (rate-limited) and `delayed-jobs` (e.g. feedback prompt). | [async-messaging.md](../infra/async-messaging.md) |
| **Cloud Scheduler** | GCP cron that calls `/internal/jobs/{job_name}` on the worker for periodic sweeps. | [background-jobs.md](../backend/background-jobs.md) |
| **Consent** | Append-only records per customer and type (`Unknown`, `OptedIn`, `Declined`, `OptedOut`), each with `wording_version` and `source`. The latest row is the current state. | [consent.md](../retention/consent.md) |
| **DLQ** | Dead-letter queue. `wa-inbound-dlq` receives inbound events that failed after max retries; depth > 0 raises an alert. | [async-messaging.md](../infra/async-messaging.md) |
| **DPDP** | India's Digital Personal Data Protection Act. Drives consent records, STOP handling, data-deletion requests and retention limits. | [data-protection.md](../security/data-protection.md), [data-deletion.md](../retention/data-deletion.md) |
| **Embedded Signup** | Meta's flow for a business to connect its own WhatsApp number. Phase 2 of the integration plan; out of scope for the MVP. | [purpose-and-scope.md](purpose-and-scope.md) |
| **Guardrails** | Code checks on each AI answer: suggested ids exist and are available, no currency amounts in free text, length limit. One retry, then fixed fallback. | [guardrails.md](../ai-agent/guardrails.md) |
| **Human mode** | `conversation.mode = human`: the bot stays silent, inbound messages are shown in the staff chat view, staff reply via the API. Returns to `bot` on resolve or idle timeout. | [human-handoff.md](../backend/human-handoff.md) |
| **Interactive message** | A WhatsApp message with reply buttons or a list. The tap comes back with an id the router handles deterministically. | [interactive-messages.md](../whatsapp/interactive-messages.md) |
| **Interactive id** | The `action:arg1:arg2` id on a button or list row, e.g. `confirm:{cart_id}:{cart_version}`, `item:{menu_item_id}`, `fb:{order_id}:{n}`. Handlers act on the id, never the visible title. | [conversation-router.md](../backend/conversation-router.md) |
| **JWKS** | Supabase's published signing keys, used by the API to verify staff JWTs. | [auth-and-tenant-isolation.md](../security/auth-and-tenant-isolation.md) |
| **Messaging tier** | Meta's daily limit on business-initiated conversations for a number. Campaign recipients over the limit are `DEFERRED` to the next day. | [number-health.md](../whatsapp/number-health.md) |
| **Mode** | `bot` or `human` on a conversation; an overlay that can switch at any stage. | [conversation-state-machine.md](../backend/conversation-state-machine.md) |
| **OIDC** | Signed identity tokens that Pub/Sub, Cloud Tasks and Cloud Scheduler attach (as `invoker-sa`) when calling the worker's internal endpoints. | [iam-and-secrets.md](../infra/iam-and-secrets.md), [internal.md](../api/internal.md) |
| **Ordering key** | Pub/Sub ordering key `{phone_number_id}:{wa_id}` so one customer's messages are delivered in order. | [async-messaging.md](../infra/async-messaging.md), [ADR 0003](../adr/0003-per-customer-ordering-and-locks.md) |
| **Overdue** | A flag on an order, not a state: now > `expected_ready_at` and not READY. Set by `overdue_sweep`, shown in red. | [order-lifecycle.md](../backend/order-lifecycle.md) |
| **phone_number_id** | Meta's id for a WhatsApp business number. Unique per café; the café (tenant) is resolved from it on every webhook. | [webhooks.md](../whatsapp/webhooks.md), [ADR 0006](../adr/0006-tenant-from-infrastructure.md) |
| **Priced snapshot** | `carts.priced_snapshot`: lines, current prices and totals stored when the summary is shown; order items copy name and unit price snapshots. | [cart-and-confirmation.md](../backend/cart-and-confirmation.md) |
| **Quality rating** | Meta's rating of a number based on recipient feedback (blocks, reports). Campaigns are the main risk to it. Read daily by `quality_and_limits`. | [number-health.md](../whatsapp/number-health.md) |
| **Redemption code** | A unique code per recipient per campaign (`campaign_recipients.redemption_code`). Staff enter it at the café via `POST /staff/redeem`, linking the visit to the campaign. | [redemption.md](../retention/redemption.md) |
| **RLS** | Postgres Row Level Security. Enabled on every table; `SELECT` policies only on what the SPA subscribes to; no write policies for `authenticated`. | [row-level-security.md](../database/row-level-security.md) |
| **Segment** | A campaign audience. MVP has three: all opted-in, lapsed (no order in N days), regulars (≥ K orders in 60 days). | [campaigns.md](../retention/campaigns.md) |
| **Service-role key** | Supabase key that bypasses RLS. Never leaves Secret Manager and the backend. The SPA uses only the anon key. | [iam-and-secrets.md](../infra/iam-and-secrets.md) |
| **Stage** | `conversation.stage`: where the customer is in the ordering loop (Greeting, AwaitingName, Browsing, CartOpen, AwaitingConfirmation, OrderActive, AwaitingFeedback, AwaitingFeedbackComment, Closed). | [conversation-state-machine.md](../backend/conversation-state-machine.md) |
| **STOP** | Text "STOP" or the "Stop offers" button. Sets consent to `OptedOut` before anything else in the router. | [consent.md](../retention/consent.md) |
| **Supavisor** | Supabase's connection pooler. The backend connects through it with asyncpg. | [module-structure.md](../backend/module-structure.md) |
| **Table session** | Opened or refreshed when the first message matches "Hi, I'm at Table N" (pre-filled by the table QR); sets the table on the conversation. | [conversation-router.md](../backend/conversation-router.md) |
| **Template** | A Meta-approved message that can be sent outside the 24-hour window. **Utility** (e.g. `order_update`, `new_order_alert`) or **marketing** (e.g. `weekly_offer`, `winback`; needs `OptedIn`). | [templates.md](../whatsapp/templates.md) |
| **Token health** | Daily debug-token check per café (`token_health` job); alerts and flags "Reconnect" if a café token stops working. | [number-health.md](../whatsapp/number-health.md) |
| **WABA** | WhatsApp Business Account. Holds the café's number(s) and templates; stored as `cafes.waba_id`. | [whatsapp/README.md](../whatsapp/README.md) |
| **Waiter request** | `waiter_requests` row created by "Call waiter", AI fallback, `call_human` or "Request bill" (`reason = BILL`). PENDING → ACKNOWLEDGED → RESOLVED. | [human-handoff.md](../backend/human-handoff.md) |
| **wa_id** | The customer's WhatsApp id (their number). The customer's only identity; unique per café in `customers`. | [customers.md](../backend/customers.md) |
| **wamid** | Meta's unique message id. Primary key of `messages`; deduplicates Meta retries. | [inbound-pipeline.md](../backend/inbound-pipeline.md) |
| **webhook_events** | Raw log of every webhook payload with `signature_valid`; pruned after 30 days. | [webhooks.md](../whatsapp/webhooks.md) |
| **X-Hub-Signature-256** | Header with the HMAC-SHA256 of the raw webhook body; verified with the App Secret before JSON parsing. | [webhooks.md](../whatsapp/webhooks.md) |

## Related

- [feature-map.md](feature-map.md)
- [../architecture/README.md](../architecture/README.md)
- [../README.md](../README.md)
