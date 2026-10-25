#!/usr/bin/env bash
# One environment's GCP resources before the first deploy (docs/architecture.md#infrastructure).
# Run by a founder with their own Google account, reviewed line by line; idempotent, safe to re-run.
#
#   PROJECT_ID=... GITHUB_REPO=owner/repo infra/setup.sh dev           # DRY_RUN=1 to print the commands only
#
# After the first deploy (the worker URL exists), run infra/wire.sh for the push subscription, invoker and jobs.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
load_env "${1:?usage: setup.sh dev|prod}"

log "APIs"
run gcloud services enable run.googleapis.com cloudtasks.googleapis.com pubsub.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com iamcredentials.googleapis.com sts.googleapis.com logging.googleapis.com \
  monitoring.googleapis.com clouderrorreporting.googleapis.com firebasehosting.googleapis.com \
  billingbudgets.googleapis.com --project "$PROJECT_ID"

log "Service accounts (least privilege per service)"
ensure_sa api-sa "Restro api (public webhook + console API)"
ensure_sa worker-sa "Restro worker (turns, timers, jobs)"
ensure_sa invoker-sa "Invokes worker: Pub/Sub push, Cloud Tasks, Cloud Scheduler"
ensure_sa ci-deployer-sa "GitHub Actions deploys (Workload Identity Federation, no keys)"

for runtime in api-sa worker-sa; do
  m="serviceAccount:$(sa "$runtime")"
  project_role "$m" roles/cloudtasks.enqueuer
  project_role "$m" roles/logging.logWriter
  project_role "$m" roles/monitoring.metricWriter
  project_role "$m" roles/errorreporting.writer
  sa_role_on_sa "$m" roles/iam.serviceAccountUser invoker-sa  # tasks carry invoker-sa's OIDC token
done
project_role "serviceAccount:$(sa worker-sa)" roles/aiplatform.user  # Vertex AI as worker-sa; no LLM key exists

d="serviceAccount:$(sa ci-deployer-sa)"
project_role "$d" roles/run.developer
project_role "$d" roles/artifactregistry.writer
project_role "$d" roles/firebasehosting.admin
sa_role_on_sa "$d" roles/iam.serviceAccountUser api-sa
sa_role_on_sa "$d" roles/iam.serviceAccountUser worker-sa
# No secret access for CI: Cloud Run resolves secrets at runtime as api-sa / worker-sa.

log "Artifact Registry"
exists gcloud artifacts repositories describe restro --location "$REGION" --project "$PROJECT_ID" ||
  run gcloud artifacts repositories create restro --repository-format docker --location "$REGION" \
    --description "api + worker image (tag = commit SHA)" --project "$PROJECT_ID"

log "Secrets (containers only; values are added by a person with gcloud secrets versions add)"
for s in meta-app-secret meta-verify-token supabase-service-key supabase-db-url supabase-db-url-session; do
  ensure_secret "$s"
done
for s in meta-app-secret meta-verify-token; do secret_reader "$s" "serviceAccount:$(sa api-sa)"; done
for s in supabase-service-key supabase-db-url supabase-db-url-session; do
  secret_reader "$s" "serviceAccount:$(sa api-sa)"
  secret_reader "$s" "serviceAccount:$(sa worker-sa)"
done
# Per café: wa-token-<cafe> is created when the café is onboarded (cafes.token_ref), readable by api-sa and worker-sa.

log "Pub/Sub topics (the push subscription needs the worker URL: wire.sh)"
ensure_topic wa-inbound
ensure_topic wa-inbound-dlq --message-retention-duration 7d
run gcloud pubsub topics add-iam-policy-binding wa-inbound --member "serviceAccount:$(sa api-sa)" \
  --role roles/pubsub.publisher --project "$PROJECT_ID" --quiet >/dev/null
run gcloud pubsub topics add-iam-policy-binding wa-inbound --member "serviceAccount:$(sa worker-sa)" \
  --role roles/pubsub.publisher --project "$PROJECT_ID" --quiet >/dev/null  # stuck_inbound_check, work_retry

log "Cloud Tasks queues (start values, tuned in Phase 3)"
ensure_queue wa-ack --max-dispatches-per-second 50 --max-concurrent-dispatches 50 \
  --max-attempts 3 --min-backoff 0.5s --max-backoff 5s
ensure_queue timers --max-dispatches-per-second 20 --max-concurrent-dispatches 20 \
  --max-attempts 6 --min-backoff 2s --max-backoff 60s           # 6 = OPS_TASK_MAX_ATTEMPTS
ensure_queue campaign-send --max-dispatches-per-second 5 --max-concurrent-dispatches 5 \
  --max-attempts 5 --min-backoff 10s --max-backoff 300s         # below the number's throughput [verify]
ensure_queue profile-note --max-dispatches-per-second 5 --max-concurrent-dispatches 5 \
  --max-attempts 3 --min-backoff 5s --max-backoff 60s

log "Workload Identity Federation for GitHub Actions ($GITHUB_REPO, $DEPLOY_BRANCH only)"
exists gcloud iam workload-identity-pools describe github --location global --project "$PROJECT_ID" ||
  run gcloud iam workload-identity-pools create github --location global --display-name "GitHub Actions" \
    --project "$PROJECT_ID"
condition="assertion.repository == '${GITHUB_REPO}' && assertion.ref == '${DEPLOY_BRANCH}'"
if exists gcloud iam workload-identity-pools providers describe github-oidc --workload-identity-pool github \
  --location global --project "$PROJECT_ID"; then
  run gcloud iam workload-identity-pools providers update-oidc github-oidc --workload-identity-pool github \
    --location global --attribute-condition "$condition" --project "$PROJECT_ID"
else
  run gcloud iam workload-identity-pools providers create-oidc github-oidc --workload-identity-pool github \
    --location global --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "$condition" --project "$PROJECT_ID"
fi
pool="projects/$(project_number)/locations/global/workloadIdentityPools/github"
sa_role_on_sa "principalSet://iam.googleapis.com/${pool}/attribute.repository/${GITHUB_REPO}" \
  roles/iam.workloadIdentityUser ci-deployer-sa

log "Budget alert (25 / 50 / 75 / 90 %)"
if [[ -n "${BILLING_ACCOUNT:-}" ]]; then
  if ! gcloud billing budgets list --billing-account "$BILLING_ACCOUNT" --format='value(displayName)' 2>/dev/null |
    grep -qx "restro-${ENV_NAME}"; then
    run gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" --display-name "restro-${ENV_NAME}" \
      --budget-amount "${BUDGET_INR}INR" --filter-projects "projects/${PROJECT_ID}" \
      --threshold-rule percent=0.25 --threshold-rule percent=0.5 --threshold-rule percent=0.75 \
      --threshold-rule percent=0.9
  fi
else
  log "BILLING_ACCOUNT empty: budget skipped (set it and re-run)"
fi

log "Done. Next: add secret values, deploy once (deploy-staging workflow), then infra/wire.sh ${ENV_NAME}"
