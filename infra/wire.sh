#!/usr/bin/env bash
# After the first deploy: everything that points at the worker URL (docs/architecture.md#queues-timers-and-jobs).
# invoker-sa may invoke worker only; the push subscription and Cloud Scheduler jobs call it with invoker-sa's OIDC.
#
#   PROJECT_ID=... GITHUB_REPO=owner/repo infra/wire.sh dev            # DRY_RUN=1 to print the commands only
set -euo pipefail
source "$(dirname "$0")/lib.sh"
load_env "${1:?usage: wire.sh dev|prod}"

WORKER_URL="$(gcloud run services describe worker --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)' 2>/dev/null || true)"
if [[ -z "$WORKER_URL" ]]; then
  [[ "${DRY_RUN:-0}" == 1 ]] || { echo "worker isn't deployed yet: deploy first" >&2; exit 1; }
  WORKER_URL="https://worker-DRYRUN.a.run.app"
fi
export WORKER_URL

log "invoker-sa invokes worker only"
run gcloud run services add-iam-policy-binding worker --region "$REGION" --project "$PROJECT_ID" \
  --member "serviceAccount:$(sa invoker-sa)" --role roles/run.invoker --quiet >/dev/null

log "Pub/Sub push subscription → /internal/pubsub/inbound (10 s ack, DLQ after 5 attempts)"
agent="serviceAccount:service-$(project_number)@gcp-sa-pubsub.iam.gserviceaccount.com"
sub_flags=(--push-endpoint "$WORKER_URL/internal/pubsub/inbound" --push-auth-service-account "$(sa invoker-sa)"
  --push-auth-token-audience "$WORKER_URL" --ack-deadline 10 --dead-letter-topic wa-inbound-dlq
  --max-delivery-attempts 5 --min-retry-delay 1s --max-retry-delay 60s)
if exists gcloud pubsub subscriptions describe wa-inbound-push --project "$PROJECT_ID"; then
  run gcloud pubsub subscriptions update wa-inbound-push --project "$PROJECT_ID" "${sub_flags[@]}"
else
  run gcloud pubsub subscriptions create wa-inbound-push --topic wa-inbound --project "$PROJECT_ID" "${sub_flags[@]}"
fi
# The Pub/Sub service agent forwards to the DLQ and mints the push OIDC token [verify on a new project].
run gcloud pubsub topics add-iam-policy-binding wa-inbound-dlq --member "$agent" --role roles/pubsub.publisher \
  --project "$PROJECT_ID" --quiet >/dev/null
run gcloud pubsub subscriptions add-iam-policy-binding wa-inbound-push --member "$agent" \
  --role roles/pubsub.subscriber --project "$PROJECT_ID" --quiet >/dev/null
sa_role_on_sa "$agent" roles/iam.serviceAccountTokenCreator invoker-sa
# A subscription that keeps the DLQ readable for the alert and for a person to look at.
exists gcloud pubsub subscriptions describe wa-inbound-dlq-pull --project "$PROJECT_ID" ||
  run gcloud pubsub subscriptions create wa-inbound-dlq-pull --topic wa-inbound-dlq \
    --message-retention-duration 7d --project "$PROJECT_ID"

log "Cloud Scheduler → /internal/jobs/{job} (Asia/Kolkata); only jobs that exist in backend/app/domain/jobs.py"
ensure_scheduler_job stuck-inbound-check "* * * * *" "$WORKER_URL/internal/jobs/stuck_inbound_check"
ensure_scheduler_job campaign-start "*/5 * * * *" "$WORKER_URL/internal/jobs/campaign_start"
ensure_scheduler_job llm-spend-check "*/15 * * * *" "$WORKER_URL/internal/jobs/llm_spend_check"
ensure_scheduler_job campaign-resume "0 10 * * *" "$WORKER_URL/internal/jobs/campaign_resume"
ensure_scheduler_job ops-sweep "* * * * *" "$WORKER_URL/internal/jobs/ops_sweep"
ensure_scheduler_job ops-retention "30 3 * * *" "$WORKER_URL/internal/jobs/ops_retention"
# Not built yet (add here when they are): template_sync, token_health, quality_and_limits, prune_webhook_events,
# daily_summary, refresh_views, weekly_report.

log "Done: worker is wired at $WORKER_URL"
