-- LOCAL DEV ONLY (decision: dev traces). Never part of supabase/migrations/ or contracts/schema.sql.
-- Raw LLM output and guardrail actions per call, for the simulator's behind-the-scenes panel.
-- Written only when DEV_TRACES=true. Synthetic data only.

create table if not exists dev_turn_traces (
  id             uuid primary key default gen_random_uuid(),
  created_at     timestamptz not null default now(),
  cafe_id        uuid not null references cafes (id) on delete cascade,
  conversation_id uuid references conversations (id) on delete cascade,
  llm_call_id    uuid references llm_calls (id) on delete cascade,
  purpose        text not null,
  prompt_chars   integer,
  suffix         text,
  raw_output     text,
  parsed         jsonb,
  guardrails     jsonb not null default '[]'::jsonb
);
create index if not exists dev_turn_traces_conv on dev_turn_traces (conversation_id, created_at desc);

alter table dev_turn_traces enable row level security;  -- no policy: read by the dev router only
revoke all on dev_turn_traces from anon, authenticated;
