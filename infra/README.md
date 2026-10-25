# infra

`gcloud` scripts for one environment, run by a founder and reviewed line by line ([architecture](../docs/architecture.md#infrastructure), [rules](../docs/rules.md#infra)). Idempotent: describe, create if missing, then set. `DRY_RUN=1` prints every changing command without running it.

| Environment | Folder | Is |
|---|---|---|
| `dev` | `environments/dev/` | Staging: its own GCP, Supabase and Meta app |
| `prod` | `environments/prod/` | The pilot, from Phase 6 |

## First time, per environment

1. Create the GCP project, link billing. Fill `PROJECT_ID` and `GITHUB_REPO` in the env file, or pass them in the shell.
2. `DRY_RUN=1 infra/setup.sh dev`: read the commands. Then run `infra/setup.sh dev`. It creates:
   - APIs and the service accounts `api-sa`, `worker-sa`, `invoker-sa` and `ci-deployer-sa` with their roles;
   - Artifact Registry `restro`;
   - empty secret containers;
   - the topics `wa-inbound` and `wa-inbound-dlq`;
   - the queues `wa-ack`, `timers`, `campaign-send` and `profile-note`;
   - Workload Identity Federation for `GITHUB_REPO` on `main`;
   - the budget alert.
3. Add the secret values yourself, never from a script or a chat:
   `gcloud secrets versions add meta-app-secret --data-file=- --project $PROJECT_ID`
   Do the same for `meta-verify-token`, `supabase-service-key`, `supabase-db-url` and `supabase-db-url-session`.
4. In GitHub, environment `staging`:
   - variables `GCP_PROJECT`, `WIF_PROVIDER`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `CONSOLE_ORIGIN`;
   - secret `SUPABASE_DB_URL`, used for migrations only.

   Then set the repository variable `DEPLOY_ENABLED=true` and run the `deploy-staging` workflow.
   `WIF_PROVIDER` is `projects/<number>/locations/global/workloadIdentityPools/github/providers/github-oidc`.
5. `infra/wire.sh dev` wires everything that needs the worker URL:
   - `run.invoker` for `invoker-sa` on `worker` only;
   - the push subscription `wa-inbound-push` (10 s ack deadline, DLQ after 5 attempts);
   - the Cloud Scheduler jobs.
6. By hand in the console:
   - Vertex AI quota for the models in use;
   - alert policies from [monitoring](../docs/architecture.md#monitoring);
   - Firebase Hosting site linked to the project, with the custom domain.

## Before staging can take traffic (team-owned, critical core)

- `INTERNAL_AUTH=oidc`: `core/internal_auth.py` still refuses every call. It must verify invoker-sa's Google-signed token, with the worker URL as audience.
- `SECRETS_BACKEND=gcp`: `core/secrets.py` must resolve `gcp:` references (`cafes.token_ref`) from Secret Manager.

Test the scripts without GCP: `bash infra/test/dry_run.sh` (CI runs it).
