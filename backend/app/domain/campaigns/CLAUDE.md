# domain/campaigns — Module Context (L2) · LEGAL + MONEY PATH

> Parent: [domain/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [campaigns](../../../../docs/retention/campaigns.md) · [redemption](../../../../docs/retention/redemption.md) · [templates](../../../../docs/whatsapp/templates.md) · [number-health](../../../../docs/whatsapp/number-health.md) · [api/admin](../../../../docs/api/admin.md)

## Responsibility

Audience selection, scheduling, throttled sending, redemption codes and the campaign report. This is the retention value the owner pays for, so **the report matters as much as the send**.

## Flow

Draft (segment + approved template + variables + time) → **preview** ("214 customers will receive this") → SCHEDULED → `campaign_start` snapshots recipients with **one unique redemption code each** → one Cloud Task per recipient on `campaign-send` (rate = the number's throughput) → each task re-checks OptedIn and the daily limit → send, or mark DEFERRED and let `campaign_resume` re-queue it the next day at 10:00 local → status webhooks update recipients → staff redeem at the café → report.

## Segments (MVP: exactly three)

All opted-in · lapsed (no order in N days) · regulars (≥ K orders in 60 days). Add more only when an owner asks.

## Must

- Build the audience as `OptedIn AND segment`, minus anyone opted out. Re-check consent **at send time**, not only at snapshot.
- Include a "Stop offers" button in every marketing template.
- Redemption (`POST /staff/redeem {code, order_id}`) succeeds only if the code is valid, unused and not expired. It sets `redeemed_at` and links the order.
- Report sent → delivered → read → redeemed → revenue, cost and ROI.
- Start small (first batches) to protect the number's quality rating.

## Must never

- Send outside `outbound` or without Cloud Tasks throttling.
- Retry a `131049` (per-user marketing cap) or a blocked recipient as if it were transient.
- Let a code be redeemed twice.
