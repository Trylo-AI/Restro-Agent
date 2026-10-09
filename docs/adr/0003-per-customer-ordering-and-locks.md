# ADR 0003: Per-customer ordering key and advisory lock

> **Area:** Backend
> **Source:** MVP Architecture v0.1 §5.2, §5.3 (principles 2 and 7), §6.2, §6.4 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Accepted · **Date:** 2026-10-05 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/app/domain/conversation/CLAUDE.md](../../backend/app/domain/conversation/CLAUDE.md), [backend/app/db/CLAUDE.md](../../backend/app/db/CLAUDE.md), [infra/CLAUDE.md](../../infra/CLAUDE.md)

## Summary

One message at a time per customer: Pub/Sub ordering key `{phone_number_id}:{wa_id}` plus a Postgres advisory lock per conversation.

## Context

Customers often send "hi", "menu?" and "table 5" within two seconds. Without serialisation two worker instances could handle them in parallel and race to create two carts or reply out of order. Conversation and cart state live in Postgres, not worker memory, so any instance can pick up the next message.

## Decision

- Every publish to `wa-inbound` sets ordering key `{phone_number_id}:{wa_id}`.
- Before handling an inbound message, the worker takes `pg_advisory_xact_lock` on the conversation key, inside the transaction that handles the turn.
- Duplicates are dropped first: `messages` insert with `wamid` unique, on conflict do nothing, ack.

## Consequences

- **Good:** no double carts or crossed replies; correctness does not depend on Pub/Sub ordering alone.
- **Costs:** one slow turn (e.g. a 12 s AI timeout) delays that customer's next message; locks hold a database connection for the turn.
- **Follow-ups:** optional P2 burst coalescing (wait ~1.5 s and pass several messages as one turn) builds on this ordering.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Ordering key only | Redeliveries and retries can still overlap across instances |
| Lock only | Messages could be processed out of order |
| In-memory per-customer queue | Breaks with multiple worker instances; state must live in Postgres |

## Status

Accepted

## Date

2026-10-05

## Related

- [../backend/inbound-pipeline.md](../backend/inbound-pipeline.md)
- [../backend/conversation-state-machine.md](../backend/conversation-state-machine.md)
- [../infra/async-messaging.md](../infra/async-messaging.md)
- [0002-ack-fast-process-async.md](0002-ack-fast-process-async.md)
