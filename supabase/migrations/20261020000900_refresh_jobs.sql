-- 0009: refresh jobs (Architecture 02). Changes the ops schema only; the frozen core is untouched.

-- A task can run in more than one workflow, with a different due rule in each
-- (e.g. Fed documents: every 20 hours in 'daily', only on FOMC decision days in 'watch').
drop view ops.task_health;
alter table ops.task drop column workflow;
alter table ops.task add column workflows text[] not null default '{daily}'
  check (workflows <@ array['daily','watch','weekly']::text[] and cardinality(workflows) > 0);
alter table ops.task add column due_rules jsonb not null default '{}';     -- mirrored from config, for the health page
alter table ops.task add column run_after text[] not null default '{}';    -- ordering inside a run, not a hard dependency
alter table ops.task alter column workflows drop default;

alter table ops.job_run drop constraint job_run_workflow_check;
alter table ops.job_run add constraint job_run_workflow_check
  check (workflow in ('daily','watch','weekly','refresh','backfill'));

alter table ops.task_run drop constraint task_run_status_check;
alter table ops.task_run add constraint task_run_status_check
  check (status in ('running','success','skipped_not_due','skipped_locked','checks_failed','failed'));

-- One open alert per condition; a later run resolves it when the condition clears.
alter table ops.alert add column dedupe_key text;
create unique index alert_one_open on ops.alert (dedupe_key) where resolved_at is null and dedupe_key is not null;

-- Conditional GETs: ask the source "has this changed?" before downloading it again.
create table ops.http_cache (
  url           text primary key,
  etag          text,
  last_modified text,
  sha256        bytea check (length(sha256) = 32),
  status        integer,
  fetched_at    timestamptz not null,
  task_run_id   bigint references ops.task_run(id)
);
alter table ops.http_cache enable row level security;
create policy job_writer_all on ops.http_cache for all to job_writer using (true) with check (true);
grant select, insert, update on ops.http_cache to job_writer;
grant select on ops.http_cache to service_role;

-- Health page: one row per task.
create view ops.task_health as
select t.key, t.module, t.description, t.workflows, t.due_rules, t.enabled, t.uses_nse,
       last.status       as last_status,
       last.started_at   as last_run_at,
       last.error        as last_error,
       ok.finished_at    as last_success_at,
       ok.finished_at + t.min_interval as next_due_at,
       (t.enabled and (ok.finished_at is null or now() - ok.finished_at > t.overdue_after)) as is_overdue,
       (select count(*) from ops.alert a where a.task_key = t.key and a.resolved_at is null) as open_alerts
from ops.task t
left join lateral (select status, started_at, error from ops.task_run r
                   where r.task_key = t.key and r.status <> 'skipped_not_due'
                   order by started_at desc limit 1) last on true
left join lateral (select finished_at from ops.task_run r
                   where r.task_key = t.key and r.status = 'success'
                   order by finished_at desc limit 1) ok on true;
grant select on ops.task_health to service_role;

-- Module 1 tasks: workflows, due rules and order (mirrors config/tasks.yaml).
update ops.task set workflows = '{weekly}', due_rules = '{"weekly": "every 25 days"}', min_interval = '25 days', overdue_after = '40 days'
 where key = 'policy.calendar';
update ops.task set workflows = '{daily,watch}', due_rules = '{"daily": "every 20 hours", "watch": "FOMC decision day, until the statement is stored"}',
       run_after = '{policy.calendar}' where key = 'policy.fed_documents';
update ops.task set workflows = '{daily,watch}', due_rules = '{"daily": "every 20 hours", "watch": "MPC decision day, until the resolution is stored"}',
       run_after = '{policy.calendar}' where key = 'policy.rbi_documents';
update ops.task set workflows = '{daily}', due_rules = '{"daily": "every 20 hours"}' where key = 'policy.market_series';
update ops.task set workflows = '{daily}', due_rules = '{"daily": "every 20 hours"}',
       run_after = '{policy.market_series,policy.fed_documents,policy.rbi_documents}' where key = 'policy.reactions';
update ops.task set workflows = '{daily,watch}', due_rules = '{"daily": "every 20 hours", "watch": "after a decision-day document is stored"}',
       run_after = '{policy.fed_documents,policy.rbi_documents}' where key = 'policy.ai_batch';
