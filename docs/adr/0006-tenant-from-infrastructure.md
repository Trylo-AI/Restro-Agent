# ADR 0006: Tenant from the infrastructure, never from input

> **Area:** Security
> **Source:** MVP Architecture v0.1 §3.1, §5.3 (principle 6), §6.14, §7, §10 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Accepted · **Date:** 2026-10-05 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/app/core/CLAUDE.md](../../backend/app/core/CLAUDE.md), [backend/app/db/CLAUDE.md](../../backend/app/db/CLAUDE.md), [supabase/CLAUDE.md](../../supabase/CLAUDE.md)

## Summary

The café (`cafe_id`) is always resolved from infrastructure facts: `phone_number_id` on inbound messages, the staff membership behind the JWT on panel requests. Never from message text or request bodies.

## Context

Every business table carries `cafe_id`, and each café owns its own customer list. A café owner seeing another café's data would end the business. Message text is customer-controlled and request bodies are client-controlled, so neither can be trusted to name the tenant.

## Decision

- **Inbound WhatsApp:** resolve `cafe_id` from `phone_number_id` in the webhook payload (`cafes.phone_number_id` is unique).
- **Panel requests:** verify the Supabase JWT, then look up `staff_memberships` for `user_id = sub` and the requested café (`X-Cafe-Id`). No membership → `403`. Queries use the `cafe_id` from the membership.
- **Repositories** take `cafe_id` as a required argument.
- **Realtime reads** are limited by RLS to cafés the user is a member of.

## Consequences

- **Good:** cross-café access needs a bug in several layers at once; the same person at two cafés is two separate customer rows.
- **Costs:** every repository signature carries `cafe_id`; a membership lookup on every panel request.
- **Follow-ups:** integration test that tries cross-café access ([backend/tests/CLAUDE.md](../../backend/tests/CLAUDE.md)).

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| `cafe_id` in the request body or URL, trusted as-is | Any staff user could read another café by changing one value |
| `cafe_id` as a JWT custom claim only | Membership changes (deactivation) would lag until token refresh |
| RLS as the only control | Backend uses a server-side role through Supavisor, which RLS does not restrict |

## Status

Accepted

## Date

2026-10-05

## Related

- [../security/auth-and-tenant-isolation.md](../security/auth-and-tenant-isolation.md)
- [../database/row-level-security.md](../database/row-level-security.md)
- [../product/roles-and-permissions.md](../product/roles-and-permissions.md)
