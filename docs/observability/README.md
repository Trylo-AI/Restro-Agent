# Observability

> **Area:** Observability
> **Source:** MVP Architecture v0.1 §9, §11, §6.16 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/app/core/CLAUDE.md](../../backend/app/core/CLAUDE.md), [infra/CLAUDE.md](../../infra/CLAUDE.md)

## Summary

A live café on a busy evening can't wait for someone to notice a problem. Every log line carries the IDs needed to trace one customer's turn from webhook to reply, a short list of metrics alerts on the failures that hurt orders or the number, and cost is measured per turn, per order and per campaign from the first pilot day rather than estimated.

## Documents in this folder

| Doc | What it covers |
|---|---|
| [logging-and-metrics.md](logging-and-metrics.md) | Structured logs and correlation IDs, the metrics/alerts table, Cloud Logging / Monitoring / Error Reporting / uptime checks |
| [cost-tracking.md](cost-tracking.md) | `ai_usage`, WhatsApp pricing on `messages`, per-campaign cost, `v_unit_costs`, the platform cost screen, monthly cost shape |

## Rules / invariants

- Every log line carries `cafe_id`, `conversation_id`, `wamid`, `order_id` and a request ID where they exist.
- No secrets or tokens in logs.
- Cost is measured from day one, not estimated.

## Related

- [../infra/deployment.md](../infra/deployment.md) · [../backend/background-jobs.md](../backend/background-jobs.md) · [../database/analytics-views.md](../database/analytics-views.md)
- Context: [backend/app/core/CLAUDE.md](../../backend/app/core/CLAUDE.md)
