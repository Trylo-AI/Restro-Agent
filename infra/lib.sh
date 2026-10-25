#!/usr/bin/env bash
# Idempotent gcloud helpers: describe first, create if missing, then set (docs/rules.md#infra). Safe to re-run.
# DRY_RUN=1 prints every changing command instead of running it; describes still run (read-only).
set -euo pipefail

log() { printf '\n==> %s\n' "$*" >&2; }
run() {
  if [[ "${DRY_RUN:-0}" == 1 ]]; then printf '+ %s\n' "$*" >&2; else "$@"; fi
}
exists() { "$@" >/dev/null 2>&1; }

sa() { echo "$1@${PROJECT_ID}.iam.gserviceaccount.com"; }
project_number() { gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'; }

ensure_sa() { # name "display name"
  exists gcloud iam service-accounts describe "$(sa "$1")" --project "$PROJECT_ID" ||
    run gcloud iam service-accounts create "$1" --display-name "$2" --project "$PROJECT_ID"
}

project_role() { # member role (bindings are idempotent)
  run gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "$1" --role "$2" --condition None --quiet >/dev/null
}

sa_role_on_sa() { # member role target-sa-name
  run gcloud iam service-accounts add-iam-policy-binding "$(sa "$3")" --member "$1" --role "$2" \
    --project "$PROJECT_ID" --quiet >/dev/null
}

ensure_topic() { # name [--message-retention-duration ...]
  local name="$1"; shift
  exists gcloud pubsub topics describe "$name" --project "$PROJECT_ID" ||
    run gcloud pubsub topics create "$name" --project "$PROJECT_ID" "$@"
}

ensure_queue() { # name flags... (create, or update to the same flags)
  local name="$1"; shift
  if exists gcloud tasks queues describe "$name" --location "$REGION" --project "$PROJECT_ID"; then
    run gcloud tasks queues update "$name" --location "$REGION" --project "$PROJECT_ID" "$@"
  else
    run gcloud tasks queues create "$name" --location "$REGION" --project "$PROJECT_ID" "$@"
  fi
}

ensure_secret() { # name: an empty container in asia-south1; a person adds the value (never from a script)
  exists gcloud secrets describe "$1" --project "$PROJECT_ID" ||
    run gcloud secrets create "$1" --replication-policy user-managed --locations "$REGION" --project "$PROJECT_ID"
}

secret_reader() { # secret member
  run gcloud secrets add-iam-policy-binding "$1" --member "$2" --role roles/secretmanager.secretAccessor \
    --project "$PROJECT_ID" --quiet >/dev/null
}

ensure_scheduler_job() { # name schedule uri
  local verb=create
  exists gcloud scheduler jobs describe "$1" --location "$REGION" --project "$PROJECT_ID" && verb=update
  run gcloud scheduler jobs "$verb" http "$1" --location "$REGION" --project "$PROJECT_ID" \
    --schedule "$2" --time-zone "Asia/Kolkata" --uri "$3" --http-method POST \
    --oidc-service-account-email "$(sa invoker-sa)" --oidc-token-audience "$WORKER_URL" \
    --attempt-deadline 60s --max-retry-attempts 1
}

load_env() { # environment name → sources environments/<env>/env.sh and checks the required values
  local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [[ -f "$here/environments/$1/env.sh" ]] || { echo "unknown environment: $1 (dev or prod)" >&2; exit 2; }
  # shellcheck source=/dev/null
  source "$here/environments/$1/env.sh"
  : "${PROJECT_ID:?set PROJECT_ID in environments/$1/env.sh or the shell}"
  : "${REGION:?}" "${GITHUB_REPO:?}"
}
