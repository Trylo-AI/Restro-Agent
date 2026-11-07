-- Rollback of 20261101000002_audit_severity_not_null.sql. The backfill function isn't recreated: the
-- 20261101000001 rollback drops it with `if exists`. Re-applying 20261101000002 alone needs 20261101000001 re-applied first.
begin;
alter table audit_log alter column severity drop not null;
commit;
