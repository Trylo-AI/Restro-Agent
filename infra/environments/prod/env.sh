# prod: the pilot café's environment, from Phase 6. Fully separate from dev (staging). No secrets here, only names.
ENV_NAME=prod
PROJECT_ID="${PROJECT_ID:-}"            # the prod GCP project id
REGION=asia-south1
GITHUB_REPO="${GITHUB_REPO:-}"
DEPLOY_BRANCH=refs/heads/main
API_MIN_INSTANCES=1                     # prod api stays warm during the pilot
MAX_INSTANCES=5
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"
BUDGET_INR=15000
