# dev = the staging environment (docs/architecture.md#infrastructure): its own GCP project, Supabase project and
# Meta app; nothing here points at prod. No secrets in this file, only names.
ENV_NAME=dev
PROJECT_ID="${PROJECT_ID:-}"            # the staging GCP project id, set when the project exists
REGION=asia-south1
GITHUB_REPO="${GITHUB_REPO:-}"          # owner/repo allowed to deploy through Workload Identity Federation
DEPLOY_BRANCH=refs/heads/main
API_MIN_INSTANCES=0
MAX_INSTANCES=3
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"  # for the budget alert; skipped when empty
BUDGET_INR=5000
