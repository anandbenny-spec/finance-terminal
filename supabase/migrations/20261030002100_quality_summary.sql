-- 0021: the accuracy page's database side (issue #133, Accuracy 1; Plan 02 §4; Brief §8 and §1).
-- Two additions, nothing else: `core.quality_summary`, a public projection of quality counts by module and
-- period (the pattern of `core.task_freshness` and `core.eval_summary`: a table only jobs write, `read_all` to
-- visitors, an `api` view), and `ops.drill_run`, a record of each drill run that visitors never read.
-- Anand OK'd both designs on #133 (28 Oct 2026, 07:35 IST; CLAUDE.md §7). No existing table or column changes.

------------------------------------------------------------------ core.quality_summary (public projection)
create table core.quality_summary (
  module   text        not null check (module ~ '^[a-z][a-z0-9_]*$'),             -- 'policy' now, then 'equity' and the rest
  period   text        not null check (period in ('30d','90d','all')),
  measure  text        not null check (measure in (
             'ai_outputs','ai_published','ai_rejected_citation','ai_rejected_numbers','ai_rejected_format',
             'ai_rejected_stopped','ai_rejected_other','ai_held_review','loads','loads_rolled_back','loads_failed',
             'freshness_on_time_share','drills_run','drills_passed','eval_gates_passed','eval_gates_pending',
             'fields_hidden','fields_labelled')),                                 -- a fixed list: no task names, run ids or error text
  value    numeric     not null check (value >= 0),
  n        integer     check (n is null or n >= 0),                               -- the count the value is out of, where one applies
  label    text        check (label is null or label = 'derived'),
  as_of    timestamptz not null,
  primary key (module, period, measure)
);
comment on table core.quality_summary is
  'Public projection of quality counts by module and period, rewritten by jobs (no task names, run ids or error text). Counts only, from the database.';

alter table core.quality_summary enable row level security;
create policy read_all on core.quality_summary for select to anon, authenticated using (true);
create policy job_writer_all on core.quality_summary for all to job_writer using (true) with check (true);
grant select on core.quality_summary to anon, authenticated, service_role;
grant select, insert, update, delete on core.quality_summary to job_writer;         -- a projection, rewritten each run

create view api.quality_summary with (security_invoker = true) as
  select module, period, measure, value, n, label, as_of from core.quality_summary;
grant select on api.quality_summary to anon, authenticated, service_role;

------------------------------------------------------------------ ops.drill_run (never visible to visitors)
create table ops.drill_run (
  id            bigint generated always as identity primary key,
  drill         text        not null check (drill in ('d3_suppression','d4_freshness')),
  target        text        not null check (target in ('dev','production')),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  passed        boolean,                                  -- null while the drill is still open
  counts        jsonb       not null default '{}'::jsonb, -- the drill's before, during and after numbers
  github_run_id bigint
);
comment on table ops.drill_run is 'One row per drill run (D3 suppression, D4 freshness). Read by the quality projection job; not exposed to visitors.';

alter table ops.drill_run enable row level security;
create policy job_writer_all on ops.drill_run for all to job_writer using (true) with check (true);
revoke all on ops.drill_run from anon, authenticated;
grant select, insert, update on ops.drill_run to job_writer, service_role;          -- the dev SQL role (#101) is a member of service_role
grant usage on sequence ops.drill_run_id_seq to job_writer, service_role;
