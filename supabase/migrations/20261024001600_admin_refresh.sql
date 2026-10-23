-- 0016: how the admin routes reach `ops` (Prompt 01 A3; Architecture 02 §1.6). Decided by Anand, 24 Oct 2026,
-- 04:12 IST, option B on GitHub issue #8.
--
-- Architecture 01 §1.9 gives the Vercel routes `service_role` (the project's secret key), and 0005 grants it what
-- it needs in `ops`. But `service_role` can't log in to Postgres, and the Data API exposes the `api` schema only
-- (CLAUDE.md §4). So the routes reach `ops` through these three `security definer` functions in `api`, called as
-- RPC with the secret key. They're owned by the migration role (postgres) and only `service_role` may execute them:
-- PUBLIC lost EXECUTE on new functions in 0001, and anon and authenticated are revoked again here so no default
-- privilege on the hosted project can hand them back. A visitor's browser key gets "permission denied".
--
-- The rules are Architecture 02 §1.6's: the task exists and is enabled; no dispatched request for it in the last 45
-- minutes (a failed dispatch doesn't start the cooldown); for an NSE task, its minimum interval since the last
-- dispatched request or real run. The checks and the insert run under one per-task advisory lock, so two clicks
-- can't both pass the cooldown. Visitor refresh isn't built (Spec 01 D10): every request here is 'admin'.

-- Opens a request. Returns {"request_id": id, "status": s}: 'dispatched' (provisional, until the route has asked
-- GitHub) or 'rejected_disabled' / 'rejected_cooldown' / 'rejected_nse'; an unknown task records nothing and
-- returns {"request_id": null, "status": "not_found"}.
create function api.admin_open_refresh(p_task_key text)
returns jsonb
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  t ops.task%rowtype;
  s text;
  rid bigint;
begin
  perform pg_advisory_xact_lock(hashtextextended('ops.refresh_request:' || p_task_key, 0));
  select * into t from ops.task where key = p_task_key;
  if not found then
    return jsonb_build_object('request_id', null, 'status', 'not_found');
  end if;
  if not t.enabled then
    s := 'rejected_disabled';
  elsif exists (select 1 from ops.refresh_request r
                 where r.task_key = p_task_key and r.status = 'dispatched'
                   and r.requested_at > now() - interval '45 minutes') then
    s := 'rejected_cooldown';
  elsif t.uses_nse and (
        exists (select 1 from ops.refresh_request r
                 where r.task_key = p_task_key and r.status = 'dispatched'
                   and r.requested_at > now() - t.min_interval)
     or exists (select 1 from ops.task_run tr
                 where tr.task_key = p_task_key and tr.status not in ('skipped_not_due', 'skipped_locked')
                   and tr.started_at > now() - t.min_interval)) then
    s := 'rejected_nse';
  else
    s := 'dispatched';
  end if;
  insert into ops.refresh_request (task_key, requested_by, status) values (p_task_key, 'admin', s)
  returning id into rid;
  return jsonb_build_object('request_id', rid, 'status', s);
end;
$$;

-- Records what GitHub answered for a request this route just opened: 'dispatched' with the run id (or none, when
-- GitHub answers 204), or 'failed'. Only a provisional request (admin, 'dispatched', no run id yet) can be finished,
-- and only once; returns whether it was.
create function api.admin_finish_refresh(p_request_id bigint, p_status text, p_github_run_id bigint default null)
returns boolean
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  n integer;
begin
  if p_status is null or p_status not in ('dispatched', 'failed') then
    raise exception 'admin_finish_refresh: status must be dispatched or failed' using errcode = '22023';
  end if;
  if p_status = 'failed' and p_github_run_id is not null then
    raise exception 'admin_finish_refresh: a failed dispatch has no run id' using errcode = '22023';
  end if;
  update ops.refresh_request
     set status = p_status, github_run_id = p_github_run_id
   where id = p_request_id and requested_by = 'admin' and status = 'dispatched' and github_run_id is null
     and job_run_id is null and requested_at > now() - interval '10 minutes';
  get diagnostics n = row_count;
  return n = 1;
end;
$$;

-- One admin request's status, with the job run it started once the run has linked itself (`--request-id`), or
-- null when there's no such request.
create function api.admin_refresh_status(p_request_id bigint)
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
           'request_id', r.id, 'task', r.task_key, 'requested_at', r.requested_at, 'status', r.status,
           'github_run_id', r.github_run_id,
           'job_run', case when r.job_run_id is null then null else jsonb_build_object(
                        'id', j.id, 'status', j.status, 'started_at', j.started_at, 'finished_at', j.finished_at) end)
    from ops.refresh_request r
    left join ops.job_run j on j.id = r.job_run_id
   where r.id = p_request_id and r.requested_by = 'admin';
$$;

revoke execute on function api.admin_open_refresh(text)                  from public, anon, authenticated;
revoke execute on function api.admin_finish_refresh(bigint, text, bigint) from public, anon, authenticated;
revoke execute on function api.admin_refresh_status(bigint)              from public, anon, authenticated;
grant  execute on function api.admin_open_refresh(text)                  to service_role;
grant  execute on function api.admin_finish_refresh(bigint, text, bigint) to service_role;
grant  execute on function api.admin_refresh_status(bigint)              to service_role;

comment on function api.admin_open_refresh(text) is
  'Admin refresh (Arch 02 §1.6): checks the task (exists, enabled, 45-minute cooldown, NSE interval) and records the request. service_role only.';
comment on function api.admin_finish_refresh(bigint, text, bigint) is
  'Admin refresh: records the GitHub dispatch result (dispatched with its run id, or failed) on a request just opened. service_role only.';
comment on function api.admin_refresh_status(bigint) is
  'Admin refresh: one request''s status and its linked job run. service_role only.';
