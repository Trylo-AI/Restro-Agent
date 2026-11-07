-- audit_log.severity becomes required once every writer goes through catalog.classify (decision 19, D20).
-- Rows written by the old backend between 20261101000001 and this deploy are backfilled first.
-- Rollback: supabase/rollback/20261101000002_audit_severity_not_null_down.sql
select audit_log_backfill_derived();
alter table audit_log alter column severity set not null;
drop function audit_log_backfill_derived();
