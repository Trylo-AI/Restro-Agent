# End-to-end flow

> **Area:** Architecture
> **Source:** MVP Architecture v0.1 §5.4, with steps from §6.2–§6.12 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/CLAUDE.md](../../backend/CLAUDE.md)

## Summary

How the customer-side ordering loop feeds the business-side retention loop, and how a redemption turns into a new visit. The second diagram walks one customer through a full happy-path evening and a later campaign, step by step, with a link to the detailed doc for each step.

## Customer-side and business-side flow

```mermaid
flowchart LR
    subgraph CS["Customer side"]
        direction LR
        Q["Scan table QR"] --> H["'Hi, I'm at Table 5'"] --> G["Greeting<br/>new or welcome back"] --> R["Browse / ask /<br/>recommend"] --> K["Cart"] --> F["Confirm"] --> T["Status updates"] --> FB["Feedback"] --> CO["Consent"]
        R -.-> HU["Call waiter"]
        K -.-> HU
    end

    subgraph BS["Business side"]
        direction LR
        D[("Orders, customers,<br/>consents, feedback")] --> SEG["Segments<br/>lapsed, regulars"] --> CAMP["Campaign<br/>on a slow day"] --> RED["Redemption<br/>at the café"] --> REP["Report<br/>sent → came back → ₹"]
    end

    F --> D
    FB --> D
    CO --> D
    RED -.->|"new visit"| Q
```

Confirm, Feedback and Consent are the three points where the customer side writes into the business side; the dashed "new visit" arrow closes the loop.

## A full happy-path evening, then a campaign

```mermaid
sequenceDiagram
    autonumber
    actor C as Customer
    participant M as Meta Cloud API
    participant A as api
    participant W as worker and engine
    participant DB as Postgres
    participant SP as Staff panel
    participant CT as Cloud Tasks
    actor OW as Owner

    Note over C,DB: Evening 1 — ordering
    C->>M: scans table QR, sends "Hi, I'm at Table 5"
    M->>A: POST /webhooks/whatsapp
    A->>A: verify signature, store webhook_events, publish to wa-inbound
    A-->>M: 200 OK
    A->>W: via Pub/Sub wa-inbound, push with ordering key
    W->>DB: dedupe wamid, resolve café, upsert customer, advisory lock
    W->>DB: open table session, Table 5
    W->>C: greeting with Menu · Recommend · Call waiter
    C->>W: "something spicy and veg?"
    W->>DB: available menu, last orders, cart
    W->>C: AI reply + dish list rendered from DB
    C->>W: adds two items, "that's all, place it"
    W->>DB: re-check prices and availability, cart.version += 1
    W->>C: summary with Confirm · Edit · Cancel
    C->>W: taps Confirm
    W->>DB: create order, order_items, order_status_events PLACED
    DB-->>SP: Realtime INSERT orders, sound + highlight
    W->>C: "Order received, about 20 minutes"
    W->>C: "Want offers on WhatsApp?" Yes / No
    C->>W: taps Yes
    W->>DB: INSERT consents OptedIn
    SP->>A: PATCH /staff/orders/{id}/status PREPARING, READY, SERVED
    A->>C: status message per transition
    A->>CT: schedule feedback_prompt +30 min
    CT->>W: POST /internal/tasks/feedback-prompt
    W->>C: rate 1 to 5 list
    C->>W: taps 5
    W->>DB: INSERT feedback

    Note over OW,DB: A slow day later — retention
    OW->>A: POST /admin/campaigns, segment lapsed, weekly_offer
    A->>DB: audience preview, opted-in only
    OW->>A: schedule
    W->>DB: campaign_start, snapshot recipients + unique codes
    W->>CT: one campaign-send task per recipient
    CT->>W: POST /internal/tasks/campaign-send
    W->>M: template with code + Stop offers button
    M->>C: offer message
    C->>SP: shows the offer at the counter
    SP->>A: POST /staff/redeem with code and order
    A->>DB: mark redeemed, link order to campaign
    OW->>A: GET /admin/campaigns/{id}/report
    A-->>OW: sent → delivered → read → redeemed → revenue
```

Read it in two halves: the first is one table's evening on the P0 ordering loop, the second is the P1 retention loop that brings the same customer back. Arrows straight to the customer are simplified; every outbound message really goes through the outbound sender and Meta.

## Where each step is designed

| Steps | What happens | Detailed doc |
|---|---|---|
| 1–4 | Webhook verify, store, publish, fast `200` | [webhooks.md](../whatsapp/webhooks.md), [ADR 0002](../adr/0002-ack-fast-process-async.md) |
| 5–6 | Pub/Sub push, dedupe, tenant resolution, customer upsert, advisory lock | [inbound-pipeline.md](../backend/inbound-pipeline.md), [ADR 0003](../adr/0003-per-customer-ordering-and-locks.md) |
| 7–8 | Table session and greeting (new or welcome back) | [conversation-router.md](../backend/conversation-router.md), [conversation-state-machine.md](../backend/conversation-state-machine.md) |
| 9–11 | AI turn with available-menu context and code-rendered dish list | [prompt-context.md](../ai-agent/prompt-context.md), [tools.md](../ai-agent/tools.md), [guardrails.md](../ai-agent/guardrails.md) |
| 12–15 | Cart, priced summary, Confirm with cart version | [cart-and-confirmation.md](../backend/cart-and-confirmation.md) |
| 16–18 | Transactional order creation, live staff alert, confirmation message | [order-lifecycle.md](../backend/order-lifecycle.md), [realtime-and-alerts.md](../frontend/realtime-and-alerts.md) |
| 19–21 | Consent ask after the first order | [consent.md](../retention/consent.md) |
| 22–23 | Staff status changes → customer messages | [order-lifecycle.md](../backend/order-lifecycle.md), [staff-panel.md](../frontend/staff-panel.md), [outbound-sender.md](../whatsapp/outbound-sender.md) |
| 24–28 | Feedback task, rating list, feedback row | [feedback.md](../backend/feedback.md), [background-jobs.md](../backend/background-jobs.md) |
| 29–36 | Campaign draft, audience preview, schedule, recipient snapshot with codes, throttled template send | [campaigns.md](../retention/campaigns.md), [templates.md](../whatsapp/templates.md) |
| 37–39 | Redemption at the café | [redemption.md](../retention/redemption.md) |
| 40–41 | Campaign report | [campaigns.md](../retention/campaigns.md), [analytics-views.md](../database/analytics-views.md) |

The step numbers match the `autonumber` labels in the sequence diagram above.

## Rules / invariants

- The ordering loop works without the retention loop; retention depends on orders, consent and feedback rows existing.
- Campaign audiences include only `OptedIn` customers.
- Every status message to the customer goes through the outbound sender and its 24-hour window guard.

## Related

- [design-principles.md](design-principles.md)
- [container-view.md](container-view.md)
- [../product/purpose-and-scope.md](../product/purpose-and-scope.md)
- [../retention/README.md](../retention/README.md)
