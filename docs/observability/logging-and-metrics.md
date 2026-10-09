# Logging and metrics

> **Area:** Observability
> **Source:** MVP Architecture v0.1 §11, §6.2, §6.8, §6.9, §9, §10 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [backend/app/core/CLAUDE.md](../../backend/app/core/CLAUDE.md), [infra/CLAUDE.md](../../infra/CLAUDE.md), [backend/CLAUDE.md](../../backend/CLAUDE.md)

## Summary

Logs are structured JSON with the same correlation IDs on every line, so one customer's turn can be traced across `api`, Pub/Sub and `worker`. Seven metrics have alert thresholds; they cover reply speed, lost events, send failures, AI fallbacks, unanswered staff requests and number health. Everything runs on GCP's Cloud Logging, Monitoring, Error Reporting and uptime checks.

## Structured logs

| Field | Set when |
|---|---|
| `cafe_id` | Resolved from `phone_number_id` (inbound) or membership (panel) |
| `conversation_id` | Conversation loaded in the worker |
| `wamid` | Inbound message id, then each outbound message id |
| `order_id` | When an order is created or changed |
| Request ID | At the first entry point (webhook or REST call) |

Logging setup lives in `app/core/logging.py`. Secrets, tokens and the App Secret are never logged.

## Tracing one customer turn

```mermaid
sequenceDiagram
    autonumber
    participant M as Meta
    participant A as api
    participant P as Pub/Sub
    participant W as worker
    participant L as LLM
    participant O as Outbound

    M->>A: webhook
    Note over A: log request_id, cafe_id, inbound wamid
    A->>P: publish with request_id
    P->>W: push
    Note over W: log request_id, cafe_id, conversation_id, wamid
    W->>L: AI turn
    Note over W: ai_usage row tied to conversation_id
    W->>O: reply
    O->>M: send
    Note over O: log outbound wamid, order_id if any
```

Look at the request ID: it travels with the event so every log line from webhook to reply can be filtered together. Carrying it as a Pub/Sub message attribute is **(proposed)**.

## Metrics and alerts

Verbatim from §11.

| Metric | Alert when |
|---|---|
| Webhook → reply latency (p95) | > 8 s for 5 min |
| Pub/Sub DLQ depth | > 0 |
| Outbound send failures | > 5% in 15 min |
| AI fallback rate | > 15% of turns in an hour |
| Waiter requests unacknowledged > 60 s | any |
| Orders in PLACED unacknowledged > 2 min | any |
| Token invalid / quality rating drops | any |

## Tooling

| Tool | Used for |
|---|---|
| Cloud Logging | JSON logs from `api` and `worker` |
| Cloud Monitoring | Metrics and alert policies above |
| Error Reporting | Exceptions from both services |
| Uptime checks | `GET /health` on `api` |

## Alert routing

```mermaid
flowchart TD
    SRC1["Cloud Run logs and metrics"] --> MON["Cloud Monitoring<br/>alert policies"]
    SRC2["Pub/Sub wa-inbound-dlq"] --> MON
    SRC3["token_health, quality_and_limits"] --> MON
    MON --> PA["Platform admin<br/>notification channel"]
    SWEEP["overdue_sweep"] --> STAFF["Staff panel sound<br/>+ new_order_alert to staff number"]
    SWEEP --> MON
```

Look at the split: platform problems go to us, café-floor problems go to staff first.

## Rules / invariants

- Correlation IDs on every line; JSON only.
- No secrets in logs.
- Every alert in the table above is wired before the pilot (build step 6).

## Open questions

- Notification channel for platform alerts (email, chat) is not specified.
- How the request ID is propagated through Pub/Sub and Cloud Tasks **(proposed: message attributes / headers)**.

## Related

- [README.md](README.md) · [cost-tracking.md](cost-tracking.md)
- [../backend/inbound-pipeline.md](../backend/inbound-pipeline.md) · [../backend/background-jobs.md](../backend/background-jobs.md)
- [../whatsapp/number-health.md](../whatsapp/number-health.md) · [../infra/deployment.md](../infra/deployment.md)
- Context: [backend/app/core/CLAUDE.md](../../backend/app/core/CLAUDE.md)
