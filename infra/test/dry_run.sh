#!/usr/bin/env bash
# Runs setup.sh and wire.sh with DRY_RUN=1 against a fake gcloud: once as a new project (every describe fails →
# create paths) and once as an existing one (every describe succeeds → update paths). Checks the commands printed.
#
#   bash infra/test/dry_run.sh
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cat >"$tmp/gcloud" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  "projects describe"*) echo 123456789 ;;
  "run services describe"*) [[ "$FAKE_STATE" == existing ]] && echo "https://worker-xyz.a.run.app" || exit 1 ;;
  "billing budgets list"*) exit 0 ;;
  *" describe "*) [[ "$FAKE_STATE" == existing ]] || exit 1 ;;
  *) echo "fake gcloud: unexpected real call: $*" >&2; exit 3 ;;  # DRY_RUN must not change anything
esac
FAKE
chmod +x "$tmp/gcloud"
export PATH="$tmp:$PATH" DRY_RUN=1 PROJECT_ID=restro-test GITHUB_REPO=owner/restro BILLING_ACCOUNT=000000-AAAAAA-000000

fail() { echo "FAIL: $*" >&2; exit 1; }
expect() { grep -qF -- "$2" <<<"$1" || fail "missing: $2"; }
reject() { ! grep -qF -- "$2" <<<"$1" || fail "unexpected: $2"; }

for env in dev prod; do
  new="$(FAKE_STATE=new bash "$here/setup.sh" "$env" 2>&1)"
  expect "$new" "+ gcloud iam service-accounts create api-sa"
  expect "$new" "+ gcloud iam service-accounts create invoker-sa"
  expect "$new" "+ gcloud tasks queues create timers --location asia-south1 --project restro-test --max-dispatches-per-second 20"
  expect "$new" "+ gcloud secrets create meta-app-secret --replication-policy user-managed --locations asia-south1"
  expect "$new" "roles/aiplatform.user"
  expect "$new" "providers create-oidc github-oidc"
  expect "$new" "assertion.repository == 'owner/restro' && assertion.ref == 'refs/heads/main'"
  expect "$new" "principalSet://iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/github/attribute.repository/owner/restro"
  expect "$new" "+ gcloud billing budgets create"
  ! grep -q "ci-deployer-sa.*secretAccessor\|secretAccessor.*ci-deployer-sa" <<<"$new" || fail "CI must not read secrets"

  again="$(FAKE_STATE=existing bash "$here/setup.sh" "$env" 2>&1)"
  reject "$again" "service-accounts create"
  reject "$again" "secrets create"
  expect "$again" "+ gcloud tasks queues update wa-ack"
  expect "$again" "providers update-oidc github-oidc"

  wire="$(FAKE_STATE=existing bash "$here/wire.sh" "$env" 2>&1)"
  expect "$wire" "--push-endpoint https://worker-xyz.a.run.app/internal/pubsub/inbound"
  expect "$wire" "--ack-deadline 10 --dead-letter-topic wa-inbound-dlq --max-delivery-attempts 5"
  expect "$wire" "+ gcloud pubsub subscriptions update wa-inbound-push"
  expect "$wire" "+ gcloud scheduler jobs update http stuck-inbound-check"
  expect "$wire" "--oidc-service-account-email invoker-sa@restro-test.iam.gserviceaccount.com --oidc-token-audience https://worker-xyz.a.run.app"
  expect "$wire" "--member serviceAccount:invoker-sa@restro-test.iam.gserviceaccount.com --role roles/run.invoker"

  first_wire="$(FAKE_STATE=new bash "$here/wire.sh" "$env" 2>&1)"
  expect "$first_wire" "+ gcloud pubsub subscriptions create wa-inbound-push --topic wa-inbound"
  expect "$first_wire" "+ gcloud scheduler jobs create http campaign-start"
done

if PROJECT_ID="" FAKE_STATE=new bash "$here/setup.sh" dev >/dev/null 2>&1; then fail "ran without PROJECT_ID"; fi
if FAKE_STATE=new bash "$here/setup.sh" staging >/dev/null 2>&1; then fail "accepted an unknown environment"; fi
echo "infra dry run: OK"
