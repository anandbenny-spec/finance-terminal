-- 0017: the admin health page's read (Prompt 01 C6; Spec 01 §4.4; Design 01 §4.7; Architecture 02 §1.4).
--
-- `/admin/health` shows the tasks' health, the open alerts (oldest first) and the recent runs. `ops` is invisible
-- to the browser key, so the page reads it "through an admin API route" (Prompt 01 C6): `/api/admin/health`, which
-- verifies the admin's token (A3) and calls this function as RPC with the secret key. It follows the pattern Anand
-- chose for the admin routes on 24 Oct 2026 (option B on GitHub issue #8, whose text named the health page as one
-- of the later admin actions needing its own function): `security definer` in `api`, owned by the migration role,
-- `search_path` pinned, EXECUTE for `service_role` only (anon and authenticated revoked, as in 0016), so the Data
-- API stays `api`-only and a visitor's key gets "permission denied". Read only: it changes nothing.
--
-- Errors (`last_error`, a task run's `error`) are for the admin's eyes only (Design 01 §1.8); visitors never reach
-- this function. Sizes are capped (200 open alerts, 20 runs) so the answer stays under the Data API's row limit
-- whatever happens.

create function api.admin_health()
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
    'as_of', now(),
    -- One row per task (ops.task_health, Architecture 02 §4), plus when the admin may next refresh it: 45 minutes
    -- after its last dispatched admin request (the cooldown 0016 enforces), null when it may refresh now.
    'tasks', coalesce((
      select jsonb_agg(jsonb_build_object(
               'key', h.key, 'module', h.module, 'description', h.description, 'enabled', h.enabled,
               'uses_nse', h.uses_nse, 'workflows', to_jsonb(h.workflows), 'due_rules', h.due_rules,
               'last_status', h.last_status, 'last_run_at', h.last_run_at, 'last_error', h.last_error,
               'last_success_at', h.last_success_at, 'next_due_at', h.next_due_at, 'is_overdue', h.is_overdue,
               'open_alerts', h.open_alerts,
               'refresh_available_at', (select max(r.requested_at) + interval '45 minutes'
                                          from ops.refresh_request r
                                         where r.task_key = h.key and r.status = 'dispatched'
                                           and r.requested_at > now() - interval '45 minutes'))
             order by h.key)
        from ops.task_health h), '[]'::jsonb),
    -- Open alerts, oldest first.
    'alerts', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', a.id, 'task_key', a.task_key, 'severity', a.severity, 'message', a.message,
               'dedupe_key', a.dedupe_key, 'raised_at', a.raised_at)
             order by a.raised_at, a.id)
        from (select * from ops.alert where resolved_at is null order by raised_at, id limit 200) a), '[]'::jsonb),
    'open_alert_count', (select count(*) from ops.alert where resolved_at is null),
    -- The last 20 job runs, newest first, each with the task runs that did something (not-due skips left out).
    'runs', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', j.id, 'workflow', j.workflow, 'trigger', j.trigger, 'runner', j.runner,
               'github_run_id', j.github_run_id, 'started_at', j.started_at, 'finished_at', j.finished_at,
               'status', j.status,
               'tasks', coalesce((
                 select jsonb_agg(jsonb_build_object(
                          'task_key', tr.task_key, 'status', tr.status, 'rows_written', tr.rows_written,
                          'started_at', tr.started_at, 'finished_at', tr.finished_at, 'error', tr.error)
                        order by tr.started_at, tr.id)
                   from ops.task_run tr
                  where tr.job_run_id = j.id and tr.status <> 'skipped_not_due'), '[]'::jsonb))
             order by j.started_at desc, j.id desc)
        from (select * from ops.job_run order by started_at desc, id desc limit 20) j), '[]'::jsonb)
  );
$$;

revoke execute on function api.admin_health() from public, anon, authenticated;
grant  execute on function api.admin_health() to service_role;

comment on function api.admin_health() is
  'Admin health page (Design 01 §4.7): every task''s health with its refresh cooldown, open alerts oldest first, the last 20 runs. service_role only; read only.';
