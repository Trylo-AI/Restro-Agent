-- Rollback of 20261101000001_retention_audit_recommend.sql. Synthetic data only: counter redemptions and
-- 'recommend' llm_calls rows are deleted first (append-only tables; trigger bypassed for these statements).
begin;
drop view if exists v_redemptions, v_campaign_insights, v_campaign_recipients;
set local session_replication_role = replica;
delete from redemptions where channel = 'counter';
delete from llm_calls where purpose = 'recommend';
set local session_replication_role = origin;
alter table llm_calls drop constraint llm_calls_purpose_check;
alter table llm_calls add constraint llm_calls_purpose_check check (purpose in ('turn', 'cancel_draft', 'profile_note'));
drop index if exists audit_log_cafe_at_id, audit_log_cafe_order, audit_log_cafe_customer, audit_log_cafe_conversation,
  audit_log_cafe_actor, audit_log_cafe_flagged;
create index if not exists audit_log_cafe_at on audit_log (cafe_id, at desc);
drop function if exists audit_log_backfill_derived();
alter table audit_log drop column if exists severity, drop column if exists order_id, drop column if exists customer_id,
  drop column if exists conversation_id, drop column if exists actor_name;
drop index if exists inbound_messages_conversation, campaign_recipients_customer, redemptions_cafe_at, redemptions_idempotency;
alter table redemptions drop constraint if exists redemptions_discount_within_bill,
                        drop constraint if exists redemptions_channel_shape;
alter table redemptions drop column if exists idempotency_key, drop column if exists bill_paise, drop column if exists channel;
commit;
