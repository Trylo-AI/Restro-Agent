-- Rollback of 20261025000002_llm_provider_dashscope.sql. Test rows with provider 'dashscope' are deleted first
-- (synthetic data only; llm_calls is append-only, so the trigger is bypassed for this one statement).
begin;
set local session_replication_role = replica;
delete from llm_calls where provider = 'dashscope';
set local session_replication_role = origin;
alter table llm_calls drop constraint llm_calls_provider_check;
alter table llm_calls add constraint llm_calls_provider_check check (provider in ('vertex', 'developer_api', 'stub'));
commit;
