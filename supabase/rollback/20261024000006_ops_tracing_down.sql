-- Rollback of 20261024000006_ops_tracing.sql. Drops tracing data only; no business table is touched.
drop table if exists ops_insight_state;
drop table if exists ops_instances;
drop table if exists ops_deploys;
drop table if exists ops_run_events;
drop table if exists ops_spans;
drop table if exists ops_runs;
