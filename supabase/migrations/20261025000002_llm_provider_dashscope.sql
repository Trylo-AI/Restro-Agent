-- llm_calls.provider gains 'dashscope' (Alibaba Model Studio, Singapore): local and staging testing with
-- synthetic data only, never prod (docs/decisions.md, "Alibaba models for testing").
-- Rollback: supabase/rollback/20261025000002_llm_provider_dashscope_down.sql
alter table llm_calls drop constraint llm_calls_provider_check;
alter table llm_calls add constraint llm_calls_provider_check
  check (provider in ('vertex', 'developer_api', 'stub', 'dashscope'));
