-- 0002: ops schema — job runs, health, settings, refresh requests

-- Refresh units. Defined in the repo's config; each run upserts them here so the health page can read them.
create table ops.task (
  key                 text primary key check (key ~ '^[a-z0-9_]+\.[a-z0-9_]+$'),  -- 'policy.fed_documents'
  module              text not null,
  description         text not null,
  workflow            text not null check (workflow in ('daily','weekly','monthly')),
  min_interval        interval not null,   -- not run again sooner than this (per-module frequency)
  overdue_after       interval not null,   -- health page flags the task when the last success is older
  uses_nse            boolean not null default false,  -- never visitor-refreshable (NSE terms decision)
  visitor_refreshable boolean not null default false,
  enabled             boolean not null default true,
  synced_at           timestamptz not null default now(),
  check (not (uses_nse and visitor_refreshable))
);

-- One row per workflow run (GitHub Actions run, admin button, local CLI).
create table ops.job_run (
  id            bigint generated always as identity primary key,
  workflow      text not null check (workflow in ('daily','weekly','monthly','manual','backfill')),
  trigger       text not null check (trigger in ('schedule','admin','visitor','cli')),
  runner        text not null check (runner in ('github','self_hosted','local')),
  github_run_id bigint,
  git_sha       text,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  status        text not null default 'running'
                check (status in ('running','success','partial','failed'))
);

-- One row per task inside a run. A task loads in one transaction: if its data checks fail,
-- the load rolls back (last good data stays) and only this row records the failure.
create table ops.task_run (
  id           bigint generated always as identity primary key,
  job_run_id   bigint not null references ops.job_run(id),
  task_key     text not null references ops.task(key),
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  status       text not null default 'running'
               check (status in ('running','success','skipped_not_due','checks_failed','failed')),
  rows_written integer not null default 0,
  files_fetched integer not null default 0,
  http_stats   jsonb,          -- {"200": 41, "429": 0, "403": 0}
  checks       jsonb,          -- [{"name": "...", "passed": true, "detail": "..."}]
  error        text,
  ai_cost_usd  numeric(10,4)
);
create index on ops.task_run (task_key, started_at desc);

-- Data problems a check raises without failing the run (e.g. RBI minutes missing 15 days after a meeting).
create table ops.alert (
  id          bigint generated always as identity primary key,
  task_key    text references ops.task(key),
  severity    text not null check (severity in ('info','warn','error')),
  message     text not null,
  detail      jsonb,
  raised_at   timestamptz not null default now(),
  resolved_at timestamptz
);
create index on ops.alert (resolved_at) where resolved_at is null;

-- Runtime switches that must flip without a deploy.
create table ops.setting (
  key         text primary key,
  value       jsonb not null,
  description text not null,
  updated_at  timestamptz not null default now(),
  updated_by  text not null default 'migration'
);

-- Admin and (testing-only) visitor refresh requests; the cooldown is checked against this table.
create table ops.refresh_request (
  id            bigint generated always as identity primary key,
  task_key      text not null references ops.task(key),
  requested_by  text not null check (requested_by in ('admin','visitor')),
  visitor_hash  text,          -- salted hash, never a raw IP
  requested_at  timestamptz not null default now(),
  status        text not null check (status in ('dispatched','rejected_disabled','rejected_cooldown',
                                                'rejected_nse','failed')),
  github_run_id bigint,
  job_run_id    bigint references ops.job_run(id)
);
create index on ops.refresh_request (task_key, requested_at desc);

-- Health page: one row per task.
create view ops.task_health as
select t.key, t.module, t.description, t.workflow, t.enabled,
       last.status         as last_status,
       last.started_at     as last_run_at,
       ok.finished_at      as last_success_at,
       (t.enabled and (ok.finished_at is null or now() - ok.finished_at > t.overdue_after)) as is_overdue,
       (select count(*) from ops.alert a where a.task_key = t.key and a.resolved_at is null) as open_alerts
from ops.task t
left join lateral (select status, started_at from ops.task_run r
                   where r.task_key = t.key order by started_at desc limit 1) last on true
left join lateral (select finished_at from ops.task_run r
                   where r.task_key = t.key and r.status = 'success'
                   order by finished_at desc limit 1) ok on true;

insert into ops.setting (key, value, description) values
  ('visitor_refresh_enabled', 'false', 'Testing only. Off whenever the link is shared; the toggle code is deleted before the link goes on the resume (brief §5).'),
  ('visitor_refresh_cooldown_minutes', '45', 'Minimum gap between refreshes of one task, even with the toggle on.'),
  ('cmdbar_ai_enabled', 'true', 'Kill switch: false makes the command bar fall back to plain search.'),
  ('cmdbar_daily_cap', '300', 'AI questions per day across all visitors (piece 3 sets the final value).'),
  ('cmdbar_per_visitor_hourly', '10', 'AI questions per visitor per hour (piece 3 sets the final value).');
