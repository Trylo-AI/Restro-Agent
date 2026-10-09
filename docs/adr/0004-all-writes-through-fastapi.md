# ADR 0004: All writes go through FastAPI

> **Area:** Frontend
> **Source:** MVP Architecture v0.1 §5.3 (principle 5), §6.8, §6.14, §7 (RLS), §10 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Accepted · **Date:** 2026-10-05 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [frontend/CLAUDE.md](../../frontend/CLAUDE.md), [supabase/CLAUDE.md](../../supabase/CLAUDE.md), [backend/app/api/CLAUDE.md](../../backend/app/api/CLAUDE.md)

## Summary

The SPA uses Supabase directly only for login and for realtime reads. Every write goes through the FastAPI `api` service.

## Context

Most staff actions have side effects beyond a row update. A status change must check the allowed transition, write `order_status_events`, write `audit_log` and send a WhatsApp message to the customer. A sold-out flip must be audited. If the SPA wrote to Supabase directly, these side effects could be skipped and tenant checks would rely on RLS alone.

## Decision

- The SPA uses Supabase Auth to log in and Supabase Realtime (Postgres Changes) to subscribe to `orders`, `order_items`, `waiter_requests` and `messages` for the human-mode chat view.
- All writes are REST calls to `api` with the Supabase JWT.
- RLS is enabled on every table, with `SELECT` policies only on the subscribed tables and no `INSERT/UPDATE/DELETE` policies for `authenticated`.
- The SPA holds only the anon key; the service-role key never leaves Secret Manager and the backend.

## Consequences

- **Good:** one place for validation, audit and outbound messages; the frontend cannot write even with a valid JWT.
- **Costs:** every new write needs an API endpoint; realtime updates arrive after the API commit, so the panel relies on the API response for the acting device.
- **Follow-ups:** integration test that tries a direct write and cross-café access.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| SPA writes via Supabase with RLS write policies | Side effects (WhatsApp send, audit, transition check) would need triggers or be skipped |
| Database triggers send messages | Hides business logic in SQL and makes outbound calls from the database |

## Status

Accepted

## Date

2026-10-05

## Related

- [../database/row-level-security.md](../database/row-level-security.md)
- [../frontend/realtime-and-alerts.md](../frontend/realtime-and-alerts.md)
- [../api/staff.md](../api/staff.md)
- [../security/auth-and-tenant-isolation.md](../security/auth-and-tenant-isolation.md)
