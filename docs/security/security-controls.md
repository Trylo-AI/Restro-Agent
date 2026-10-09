# Security controls

> **Area:** Security
> **Source:** MVP Architecture v0.1 §10, §6.2, §6.5, §6.13, §6.14, §6.17, §4 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [`backend/app/core/CLAUDE.md`](../../backend/app/core/CLAUDE.md), [`backend/app/api/CLAUDE.md`](../../backend/app/api/CLAUDE.md), [`backend/app/domain/ai_agent/CLAUDE.md`](../../backend/app/domain/ai_agent/CLAUDE.md), [`backend/app/integrations/whatsapp/CLAUDE.md`](../../backend/app/integrations/whatsapp/CLAUDE.md), [`frontend/CLAUDE.md`](../../frontend/CLAUDE.md), [`infra/CLAUDE.md`](../../infra/CLAUDE.md)

## Summary

The complete list of security controls from §10, with the place each one is enforced so an implementer knows which module owns it. Below the table are details for the controls that need more than one line: rate limiting, staff accounts and audit, and prompt injection.

## Controls (§10) and where they are enforced

| Area | Control | Where enforced |
|---|---|---|
| Webhook authenticity | HMAC-SHA256 of the **raw** body with the App Secret, constant-time compare, before JSON parsing | `core/signature.py` on `POST /webhooks/whatsapp`; invalid → 401, logged, not processed ([../whatsapp/webhooks.md](../whatsapp/webhooks.md)) |
| Secrets | App Secret, WABA tokens, 2FA PINs, Supabase service credentials and LLM key in Secret Manager; the DB stores only references; nothing in logs or the frontend | Secret Manager + `integrations/gcp/secrets.py`; `cafes.token_secret_ref` ([../infra/iam-and-secrets.md](../infra/iam-and-secrets.md)) |
| Tenant isolation | `cafe_id` from `phone_number_id` (inbound) or membership (panel); repositories require `cafe_id`; RLS for direct frontend reads; integration test that tries cross-café access | `core/auth.py`, inbound pipeline, `app/db/repositories/*`, Supabase RLS, `tests/integration` ([auth-and-tenant-isolation.md](auth-and-tenant-isolation.md)) |
| Frontend | Supabase anon key only; no write policies; all writes through FastAPI | `frontend/src/lib` Supabase client; RLS policies ([../database/row-level-security.md](../database/row-level-security.md)) |
| Internal endpoints | Worker has internal ingress and requires OIDC from `invoker-sa` | Cloud Run ingress setting + `core/internal_auth.py` ([../infra/async-messaging.md](../infra/async-messaging.md)) |
| Staff accounts | Per-person logins, no shared accounts; owner can deactivate staff instantly; audit log on status changes, sold-out flips, cancellations, campaigns and redemptions | Supabase Auth, `/admin/staff`, `staff_memberships.active`, `domain/audit` ([below](#staff-accounts-and-audit)) |
| Personal data | Mask phone numbers in the staff panel; minimise what the AI sees (first name and past items, no number); encryption at rest (Supabase default) | API response shaping, `ai_agent` prompt builder, Supabase ([data-protection.md](data-protection.md)) |
| DPDP / Meta | Consent records with wording version; STOP handling; deletion requests and Meta deletion callback; retention limits on raw webhook logs and message payloads | `domain/consent`, conversation router, `data_deletion` job, `prune_webhook_events` ([data-protection.md](data-protection.md), [../retention/consent.md](../retention/consent.md)) |
| Prompt injection | Customer text is never treated as instructions for tools; tools are scoped to the current conversation; the AI has no tool that reads other customers or sends templates | `ai_agent` tool executor and guardrails ([below](#prompt-injection), [../ai-agent/guardrails.md](../ai-agent/guardrails.md)) |
| Rate limiting | Per-IP limits on public endpoints; per-customer limit on AI turns per minute to cap cost from spam | `api` edge or middleware; conversation engine ([below](#rate-limiting)) |
| Token health | Daily debug-token check; alert and "Reconnect" flag if a café token stops working | `token_health` job, outbound sender auth-error path ([../whatsapp/number-health.md](../whatsapp/number-health.md)) |

## Rate limiting

| Limit | Applies to | Purpose | Where |
|---|---|---|---|
| Per-IP | Public endpoints: `/webhooks/*`, `/staff/*`, `/admin/*`, `/platform/*`, `/health` | Blunt abuse and credential-stuffing against the API | App middleware on `api`, or Cloud Armor if `api` sits behind a load balancer **(proposed: decide with domain mapping)** |
| Per-customer AI turns per minute | Conversation engine, per `(cafe_id, wa_id)` | Cap LLM cost from spam | Checked before the AI turn; over the limit, reply with the fixed fallback buttons instead of calling the LLM **(proposed behaviour)** |

Notes:

- Meta delivers webhooks from a small set of addresses at high volume during a busy evening. The per-IP limit on `/webhooks/whatsapp` must be set high enough, or exempt Meta traffic that passes the signature check, so legitimate retries are never throttled **(proposed)**.
- The per-customer counter has to work across worker instances, so it lives in Postgres (for example counting recent `ai_usage` rows for the conversation) rather than in memory **(proposed)**.
- Limit values are not set in the source.

## Staff accounts and audit

- **One login per person.** No shared "kitchen" account; every action is attributable to a user.
- **Instant deactivation.** The owner deactivates a staff member via `/admin/staff`. The API checks membership on every request, so access stops on the next call even though the JWT is still valid until expiry. Realtime access should stop too, by checking `active` in the RLS policy **(proposed)**.
- **Audit log** (`audit_log (actor, cafe_id, action, entity, before, after, at)`) is written for:

| Action | Trigger |
|---|---|
| Order status change | `PATCH /staff/orders/{id}/status` (same transaction as `order_status_events`) |
| Order cancellation | `POST /staff/orders/{id}/cancel`, customer cancel |
| Sold-out flip | `PATCH /staff/menu/items/{id}/availability` |
| Menu edits and import | `PUT /admin/menu/items/{id}`, `POST /admin/menu/import` |
| Campaign actions | Create, schedule, cancel |
| Redemption | `POST /staff/redeem` |
| Platform-admin actions | Every action (§4) |

The audit log is viewed in `/platform/audit`.

## Prompt injection

The AI sees customer text, so it must be assumed that some customer text will try to steer it. The design makes that harmless rather than trying to detect it:

| Control | Effect |
|---|---|
| Customer text is never treated as instructions for tools | Tool arguments are validated by code; the AI cannot unlock extra behaviour by asking |
| Tools are scoped to the current conversation | The executor injects `cafe_id` and conversation; `update_cart_item` / `remove_from_cart` require the cart to belong to this conversation |
| No tool reads other customers or sends templates | There is nothing to exfiltrate or spam with, even if the model is fully convinced |
| AI cannot set prices, create orders, change status or record consent (§6.5) | All of these are fixed code paths; `request_confirmation` hands off to deterministic code |
| Guardrails on output | Suggested ids must exist and be available; no currency amounts in free text; length limit; dish list and prices rendered by code |
| Fallback | Two guardrail failures → fixed fallback with Menu / Call waiter |
| No phone number in the prompt | A leaked prompt contains a first name and past item names only |

## Rules / invariants

- Signature check runs on the raw bytes before any parsing; never parse first.
- No endpoint on `worker` accepts a request without a valid `invoker-sa` OIDC token.
- No shared staff logins.
- Every action in the audit table above writes `audit_log`.
- The AI never gets a tool that reads another customer or sends a template.

## Open questions

- Rate-limit values and where per-IP limiting lives (middleware vs Cloud Armor).
- Over-limit behaviour for the per-customer AI cap.
- Retention period for message payloads ([data-protection.md](data-protection.md)).

## Related

- [auth-and-tenant-isolation.md](auth-and-tenant-isolation.md) · [data-protection.md](data-protection.md)
- [../ai-agent/guardrails.md](../ai-agent/guardrails.md) · [../ai-agent/tools.md](../ai-agent/tools.md) · [../whatsapp/webhooks.md](../whatsapp/webhooks.md)
- [../adr/0001-ai-talks-code-decides.md](../adr/0001-ai-talks-code-decides.md)
- Context files: [`backend/app/core/CLAUDE.md`](../../backend/app/core/CLAUDE.md), [`backend/app/domain/ai_agent/CLAUDE.md`](../../backend/app/domain/ai_agent/CLAUDE.md)
