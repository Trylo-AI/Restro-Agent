# app/core — Module Context (L2)

> Parent: [backend/CLAUDE.md](../../CLAUDE.md) · Root: [/CLAUDE.md](../../../CLAUDE.md)
> Docs: [auth-and-tenant-isolation](../../../docs/security/auth-and-tenant-isolation.md) · [security-controls](../../../docs/security/security-controls.md) · [webhooks](../../../docs/whatsapp/webhooks.md)

## Responsibility

Cross-cutting plumbing used by every router. No business rules.

| Planned file | Does |
|---|---|
| `config.py` | Typed settings per environment. Secrets are fetched via `integrations/gcp/secrets`, never from env literals |
| `logging.py` | JSON logs with request ID plus `cafe_id`, `conversation_id`, `wamid`, `order_id` when known |
| `auth.py` | Verify the Supabase JWT (JWKS: signature, expiry, audience) → load `staff_memberships` for `X-Cafe-Id` → RBAC |
| `signature.py` | `X-Hub-Signature-256`: HMAC-SHA256 of the **raw** body with the Meta App Secret, constant-time compare |
| `internal_auth.py` | Verify the OIDC token on `/internal/*` (Pub/Sub, Cloud Tasks, Scheduler) came from `invoker-sa` |

## Must

- Verify the signature **before** any JSON parsing, and reject with `401`.
- Return `403` when there's no active membership for `(sub, X-Cafe-Id)`. Hand the resolved `cafe_id` and `role` to the handler, and **never** trust a `cafe_id` from the request body.
- Order the roles: `staff` < `manager` < `owner`. `platform_admin` is separate and gives cross-café access, with every call audited.

## Must never

- Log the JWT, App Secret, WABA token or a full phone number.
- Cache JWKS or tokens without expiry. The WABA token cache TTL is 10 min.
- Let a missing or invalid OIDC token fall through to a handler.

## Tests that must stay green

Signature valid/invalid/tampered body · expired or wrong-audience JWT · cross-café access attempt returns 403 · `/internal/*` without OIDC is rejected.
