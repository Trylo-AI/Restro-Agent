# Feature priority

> **Area:** Product
> **Source:** MVP Architecture v0.1 §3.3, §13 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Draft v0.1 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md)

## Summary

Every MVP feature has a priority. P0 is what the pilot cannot run without, P1 is the retention pitch, P2 is useful during the pilot, and ⏳ waits for discovery. The diagram below shows which build step in [build-order.md](../delivery/build-order.md) delivers each feature.

## Priority table

| Priority | Features |
|---|---|
| **P0 — pilot can't run without it** | Webhook + sender, table QR session, menu browse, AI Q&A/recommendations, cart, deterministic confirmation, staff login, live orders with sound alert, status changes → customer updates, sold-out toggle, call waiter + human mode |
| **P1 — the retention pitch** | Welcome back, consent capture + STOP, redemption code, simple campaign send, campaign report, feedback 1–5 |
| **P2 — useful in the pilot** | Edit/cancel before preparing, reorder, request bill, customer profile view, weekly report, cost tracking screen, AI conversation review |
| **⏳ After discovery** | Bookings, pre-orders, payment links, segments beyond "lapsed" and "all opted-in" |

## Priorities mapped to build steps

```mermaid
flowchart LR
    subgraph P0["P0 — pilot can't run without it"]
        A1["Webhook + sender"]
        A2["Table QR session, menu browse,<br/>AI Q&A, cart, deterministic confirm"]
        A3["Staff login, live orders + sound,<br/>status → customer updates, sold-out toggle"]
        A4["Call waiter + human mode"]
    end
    subgraph P1["P1 — the retention pitch"]
        B1["Welcome back, consent + STOP,<br/>campaign send, redemption code,<br/>campaign report"]
        B2["Feedback 1–5"]
    end
    subgraph P2["P2 — useful in the pilot"]
        C1["Request bill"]
        C2["Customer profile view, weekly report,<br/>cost tracking screen"]
        C3["Edit/cancel, reorder,<br/>AI conversation review"]
    end
    subgraph LATER["⏳ After discovery"]
        D1["Bookings, pre-orders, payment links,<br/>extra segments"]
    end

    S0["Step 0<br/>Foundations"]
    S1["Step 1<br/>Ordering core"]
    S2["Step 2<br/>Staff panel"]
    S3["Step 3<br/>Human help"]
    S4["Step 4<br/>Retention"]
    S5["Step 5<br/>Insight"]
    S6["Step 6<br/>Pilot hardening"]
    NS["Not in a step yet"]

    A1 --> S0
    A2 --> S1
    A3 --> S2
    A4 --> S3
    C1 --> S3
    B1 --> S4
    B2 --> S5
    C2 --> S5
    C3 -.-> NS
    D1 -.-> NS
    S5 --> S6
```

All P0 work lands in steps 0–3, the retention pitch in step 4, and insight features in step 5. Step 6 adds no features; it hardens what exists. Dashed arrows are features that §13 does not place in any step.

## Rules / invariants

- No P1 work starts before the P0 ordering loop works end to end; reliability beats features ([purpose-and-scope.md](purpose-and-scope.md)).
- MVP segments are only "all opted-in", "lapsed" and "regulars"; anything more is ⏳ ([campaigns.md](../retention/campaigns.md)).
- Templates are a step 4 feature but must be submitted for approval during step 1 ([build-order.md](../delivery/build-order.md)).

## Open questions

- Edit/cancel before preparing, reorder and AI conversation review are P2 but have no build step in §13. Placement is undecided.
- §3.3 lists "lapsed" and "all opted-in" as MVP segments, while §6.12 also lists "regulars". See [campaigns.md](../retention/campaigns.md).
- Bill / payment (UPI link now or later) — tracked in [open-decisions.md](../delivery/open-decisions.md).

## Related

- [feature-map.md](feature-map.md)
- [../delivery/build-order.md](../delivery/build-order.md)
- [../delivery/open-decisions.md](../delivery/open-decisions.md)
