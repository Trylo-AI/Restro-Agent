-- Performance dashboard tables (docs/OPS_PLAN.md, PDS v1.0 §5). Written only by backend/app/ops/tracing, read only
-- by /api/ops. RLS on with no policies and no client grants: the console never reads these through Supabase.
-- Rollback: supabase/rollback/20261024000006_ops_tracing_down.sql

create table ops_runs (
  run_id              uuid primary key,
  parent_run_id       uuid,
  upstream_trace      varchar(64),
  upstream_service    varchar(100),
  service             varchar(100) not null,
  kind                varchar(20)  not null check (kind in ('http', 'job', 'cron', 'message', 'cli', 'ws')),
  name                varchar(150) not null,
  batch_type          varchar(50),
  batch_id            varchar(100),
  subject_type        varchar(50),
  subject_id          varchar(100),
  tenant_id           varchar(100),
  attempt             int not null default 1,
  instance            varchar(100),
  queued_at           timestamptz,
  started_at          timestamptz not null,
  finished_at         timestamptz,
  duration_ms         double precision,
  queue_wait_ms       double precision,
  current_stage       varchar(80),
  outcome             varchar(24) not null default 'RUNNING' check (outcome in
                        ('RUNNING', 'SUCCEEDED', 'SUCCEEDED_DEGRADED', 'REJECTED', 'RETRYING', 'FAILED', 'CANCELLED',
                         'SUPERSEDED', 'ABANDONED')),
  http_status         smallint,
  error_stage         varchar(80),
  error_code          varchar(80),
  error_type          varchar(200),
  failure_reason      text check (char_length(failure_reason) <= 500),
  failure_fingerprint varchar(16),
  degradations        jsonb not null default '[]',
  git_sha             varchar(40),
  app_version         varchar(50),
  config_hash         varchar(16),
  settings            jsonb not null default '{}',
  labels              jsonb not null default '{}',
  input_size          double precision,
  input_size_unit     varchar(20),
  counts              jsonb not null default '{}',
  stage_ms            jsonb not null default '{}',
  dep_ms              jsonb not null default '{}',
  dep_calls           jsonb not null default '{}',
  cost_usd            double precision,          -- null: no price tracking yet (OPS_PLAN decisions)
  bytes_in            bigint,
  bytes_out           bigint,
  cpu_ms              double precision,
  peak_mem_mb         int,
  gpu_peak_mb         int,
  sampled             boolean not null default true,
  span_count          int not null default 0
);
create index ix_ops_runs_started  on ops_runs (started_at desc);
create index ix_ops_runs_name     on ops_runs (kind, name, started_at desc);
create index ix_ops_runs_batch    on ops_runs (batch_id, started_at);
create index ix_ops_runs_subject  on ops_runs (subject_type, subject_id, started_at desc);
create index ix_ops_runs_failures on ops_runs (failure_fingerprint, started_at desc)
  where outcome in ('FAILED', 'RETRYING', 'ABANDONED');
create index ix_ops_runs_running  on ops_runs (started_at) where outcome = 'RUNNING';
create index ix_ops_runs_upstream on ops_runs (upstream_trace) where upstream_trace is not null;

create table ops_spans (
  run_id       uuid not null references ops_runs (run_id) on delete cascade,
  span_id      int  not null,
  parent_id    int,
  name         varchar(100) not null,
  level        smallint not null check (level between 1 and 3),
  kind         varchar(20) not null,
  start_ms     double precision not null,
  duration_ms  double precision not null,
  outcome      varchar(10) not null check (outcome in ('ok', 'failed', 'degraded')),
  target       varchar(150),
  attrs        jsonb not null default '{}',
  error        text,
  primary key (run_id, span_id)
);
create index ix_ops_spans_name   on ops_spans (name, level);
create index ix_ops_spans_target on ops_spans (kind, target);

create table ops_run_events (
  run_id     uuid not null references ops_runs (run_id) on delete cascade,
  seq        int  not null,
  at_ms      double precision not null,
  level      varchar(10) not null check (level in ('WARNING', 'ERROR', 'DEGRADED')),
  span_name  varchar(100),
  logger     varchar(150),
  message    text not null,
  detail     jsonb,
  primary key (run_id, seq)
);

-- Deploy markers: a row at startup when the git sha differs from the last one for the service.
create table ops_deploys (
  at          timestamptz not null,
  service     varchar(100) not null,
  git_sha     varchar(40),
  app_version varchar(50),
  primary key (service, at)
);

-- One row per running process, heartbeat every 15 s. The sweeper marks RUNNING runs of a silent instance ABANDONED.
create table ops_instances (
  instance    varchar(100) primary key,
  service     varchar(100) not null,
  git_sha     varchar(40),
  started_at  timestamptz not null,
  last_seen   timestamptz not null
);

-- Insights the team dismissed (the only write the ops API makes).
create table ops_insight_state (
  finding_key varchar(200) primary key,
  dismissed_at timestamptz not null default now(),
  note        text check (char_length(note) <= 500)
);

do $$
declare t text;
begin
  foreach t in array array['ops_runs', 'ops_spans', 'ops_run_events', 'ops_deploys', 'ops_instances', 'ops_insight_state'] loop
    execute format('alter table %I enable row level security', t);
    execute format('revoke all on %I from anon, authenticated', t);
  end loop;
end $$;
