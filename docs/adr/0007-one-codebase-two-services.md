# ADR 0007: One codebase, two Cloud Run services

> **Area:** Infra
> **Source:** MVP Architecture v0.1 §2, §5.2, §6.1, §9, §12 ([source](../_source/mvp-architecture-v0.1.md))
> **Status:** Accepted · **Date:** 2026-10-05 · **Owner:** Aman
> **Used by:** [/CLAUDE.md](../../CLAUDE.md), [backend/CLAUDE.md](../../backend/CLAUDE.md), [backend/app/api/CLAUDE.md](../../backend/app/api/CLAUDE.md), [infra/CLAUDE.md](../../infra/CLAUDE.md)

## Summary

One FastAPI codebase and one container image, deployed twice on Cloud Run: `api` (public) mounts webhooks, staff, admin and platform routers; `worker` (internal) mounts the internal routers.

## Context

Public traffic (Meta webhooks, panel REST) needs a fast, always-warm service. Background work (conversation engine, AI turns, campaign sends, scheduled jobs) is bursty and must not be reachable from the internet. Both need the same domain services, repositories and integrations.

## Decision

- `main_api.py` mounts webhooks, staff, admin and platform routers; `main_worker.py` mounts internal routers (`/internal/pubsub/inbound`, `/internal/tasks/*`, `/internal/jobs/{job_name}`).
- Domain services don't know which service they run in.
- `api`: min 1, max 5, public ingress, runs as `api-sa`.
- `worker`: min 0, max 10, internal ingress, requires OIDC from `invoker-sa`, runs as `worker-sa`.
- GitHub Actions builds one backend image into Artifact Registry for both services.

## Consequences

- **Good:** shared code with no library packaging; the internal surface is isolated by ingress and OIDC; each service scales on its own.
- **Costs:** both services ship together, so a worker-only change redeploys `api` too; router mounting must be kept strictly separate.
- **Follow-ups:** least-privilege service accounts per service ([iam-and-secrets.md](../infra/iam-and-secrets.md)).

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Single service for everything | Internal endpoints would share public ingress; bursty work competes with webhook latency |
| Separate codebases | Duplicated domain logic and models |

## Status

Accepted

## Date

2026-10-05

## Related

- [../architecture/container-view.md](../architecture/container-view.md)
- [../backend/module-structure.md](../backend/module-structure.md)
- [../infra/deployment.md](../infra/deployment.md)
- [0002-ack-fast-process-async.md](0002-ack-fast-process-async.md)
