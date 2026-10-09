# Infra — Area Context (L1)

> Loaded when working under `infra/` or `.github/workflows/`. Inherits every invariant in [/CLAUDE.md](../CLAUDE.md).
> Full design: [docs/infra/](../docs/infra/README.md) · [docs/security/](../docs/security/README.md) · [docs/observability/](../docs/observability/README.md)

## What lives here

```text
infra/
└── environments/
    ├── dev/     GCP project dev  + Supabase dev  + Meta dev app (test number)
    └── prod/    GCP project prod + Supabase prod + Meta prod app (pilot cafés)
```

The IaC tool (Terraform or gcloud scripts) is an **open decision**. See [open-decisions.md](../docs/delivery/open-decisions.md).

## Resources (per environment)

| Resource | Setting |
|---|---|
| Cloud Run `api` | Public ingress, min 1 / max 5 in prod, `api-sa` |
| Cloud Run `worker` | Internal ingress + OIDC only, min 0 / max 10, `worker-sa` |
| Pub/Sub | `wa-inbound` (message ordering on) → push to worker, `wa-inbound-dlq` |
| Cloud Tasks | `campaign-send` (rate = the number's throughput), `delayed-jobs` |
| Cloud Scheduler | Sweeps and daily jobs → `/internal/jobs/{job_name}` as `invoker-sa` |
| Secret Manager | Meta App Secret, WABA tokens, 2FA PINs, Supabase service creds, LLM key |
| Firebase Hosting | SPA build |
| Artifact Registry | One backend image used by both services |
| Region | `asia-south1`, with Supabase in Mumbai |

## Infra invariants

1. **Least privilege.** `api-sa`: Pub/Sub publisher, Secret accessor, Tasks enqueuer. `worker-sa`: Secret accessor, Tasks enqueuer. `invoker-sa`: Cloud Run invoker on `worker` **only**.
2. The worker is **never** publicly reachable. Every internal endpoint requires an OIDC token from `invoker-sa`.
3. dev and prod are fully separate: separate GCP project, Supabase project and Meta app. Test webhooks must never reach prod.
4. Keep dev scaled to zero to protect the $300 credit. Prod `api` stays at `min-instances=1` so webhook acks stay fast.
5. Secrets exist only in Secret Manager. Not in env files in git, not in CI logs, not in the frontend.
6. Alerts are required before the pilot, including DLQ depth > 0 and reply latency p95 > 8 s. → [logging-and-metrics](../docs/observability/logging-and-metrics.md)

## Task → doc

| Task | Doc |
|---|---|
| Topology and services | [deployment.md](../docs/infra/deployment.md) |
| dev vs prod | [environments.md](../docs/infra/environments.md) |
| Pub/Sub, Tasks, Scheduler | [async-messaging.md](../docs/infra/async-messaging.md) |
| Service accounts, secrets | [iam-and-secrets.md](../docs/infra/iam-and-secrets.md) |
| Pipelines | [ci-cd.md](../docs/infra/ci-cd.md) |
| Cost shape | [cost-tracking.md](../docs/observability/cost-tracking.md) |
