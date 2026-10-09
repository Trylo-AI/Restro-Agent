# ADR 0002: Acknowledge webhooks fast, process asynchronously

> **Area:** WhatsApp
> **Source:** MVP Architecture v0.1 §2 (stack notes), §5.2, §5.3 (principle 1), §6.2 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Accepted · **Date:** 2026-10-05 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/app/integrations/whatsapp/CLAUDE.md](../../backend/app/integrations/whatsapp/CLAUDE.md), [backend/app/api/CLAUDE.md](../../backend/app/api/CLAUDE.md), [infra/CLAUDE.md](../../infra/CLAUDE.md)

## Summary

The webhook verifies the signature, stores the raw event, publishes it to Pub/Sub and returns `200`. All real work happens in the worker.

## Context

Meta expects a fast `200` on webhooks and retries otherwise. A single inbound turn can involve several database round-trips, an LLM call of up to ~12 s and outbound sends. Doing that inside the webhook request risks timeouts, duplicate deliveries and lost messages under load.

## Decision

- `POST /webhooks/whatsapp` on `api`: HMAC-SHA256 check of the raw body against `X-Hub-Signature-256`, insert into `webhook_events`, publish to `wa-inbound` with the ordering key, return `200` (target under 1 s). Invalid signature → `401`, logged, not processed.
- Pub/Sub pushes to `/internal/pubsub/inbound` on `worker` with OIDC. Non-2xx → retry with backoff; after N attempts → `wa-inbound-dlq` and an alert.
- `api` keeps `min-instances=1` in production to avoid cold starts.

## Consequences

- **Good:** Meta always gets a fast ack; slow AI turns never block ingestion; retries are free because of `wamid` deduplication.
- **Costs:** an extra hop and queue to operate; the min-instance is a fixed cost against the GCP credit; one more failure mode (DLQ) to monitor.
- **Follow-ups:** DLQ depth > 0 alert; webhook → reply p95 latency alert at > 8 s.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Process synchronously in the webhook | Slow LLM calls risk Meta timeouts and retries |
| Background task inside the `api` process | Lost on instance shutdown; no retry or DLQ |

## Status

Accepted

## Date

2026-10-05

## Related

- [../whatsapp/webhooks.md](../whatsapp/webhooks.md)
- [../backend/inbound-pipeline.md](../backend/inbound-pipeline.md)
- [../infra/async-messaging.md](../infra/async-messaging.md)
- [0003-per-customer-ordering-and-locks.md](0003-per-customer-ordering-and-locks.md), [0007-one-codebase-two-services.md](0007-one-codebase-two-services.md)
