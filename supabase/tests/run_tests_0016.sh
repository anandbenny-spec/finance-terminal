#!/bin/bash
# Tests for 0016 (the admin refresh functions; issue #8, option B). Run after the other scripts on the same fresh
# database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

FNS="'api.admin_open_refresh(text)'::regprocedure, 'api.admin_finish_refresh(bigint,text,bigint)'::regprocedure, 'api.admin_refresh_status(bigint)'::regprocedure"
as_pg "delete from ops.refresh_request where task_key like 'test.r16_%';
       insert into ops.task (key, module, description, workflows, min_interval, overdue_after, uses_nse, enabled) values
         ('test.r16_on',  'test', '0016 test task', '{daily}', '7 days', '8 days', false, true),
         ('test.r16_off', 'test', '0016 test task', '{daily}', '7 days', '8 days', false, false),
         ('test.r16_nse', 'test', '0016 test task', '{daily}', '7 days', '8 days', true,  true)
       on conflict (key) do nothing" >/dev/null

check "R01 the three functions are security definer, owned by postgres, with a pinned search_path" \
  "$(as_pg "select string_agg(p.proname||':'||p.prosecdef||':'||pg_get_userbyid(p.proowner)||':'||array_to_string(p.proconfig,';'), ',' order by p.proname) from pg_proc p where p.oid in ($FNS)")" \
  '^admin_finish_refresh:true:postgres:search_path=pg_catalog, ops,admin_open_refresh:true:postgres:search_path=pg_catalog, ops,admin_refresh_status:true:postgres:search_path=pg_catalog, ops$'
check "R02 only service_role (and the owner) can execute them: not anon, authenticated, PUBLIC, job_writer or sql_reader" \
  "$(as_pg "select string_agg(distinct r, ',' order by r) from (select p.oid, r from pg_proc p, unnest(array['anon','authenticated','service_role','job_writer','sql_reader','semantic_owner']) r where p.oid in ($FNS) and has_function_privilege(r, p.oid, 'execute')) x")" '^service_role$'
check "R03 PUBLIC holds no EXECUTE grant on them" \
  "$(as_pg "select count(*) from pg_proc p, aclexplode(p.proacl) a where p.oid in ($FNS) and a.grantee = 0")" '^0$'
check "R04 anon is refused" \
  "$(as_role anon "select api.admin_open_refresh('test.r16_on')")" 'permission denied for function admin_open_refresh'
check "R05 authenticated (the logged-in admin's own session) is refused too" \
  "$(as_role authenticated "select api.admin_refresh_status(1)")" 'permission denied for function admin_refresh_status'
check "R06 service_role opens a request for an enabled task" \
  "$(as_role service_role "select api.admin_open_refresh('test.r16_on')->>'status'")" '^dispatched$'
check "R07 an unknown task records nothing" \
  "$(as_role service_role "select api.admin_open_refresh('test.r16_missing')::text"; as_pg "select count(*) from ops.refresh_request where task_key='test.r16_missing'")" \
  '^\{"status": "not_found", "request_id": null\}.0$'
check "R08 a disabled task is recorded as rejected_disabled" \
  "$(as_role service_role "select api.admin_open_refresh('test.r16_off')->>'status'")" '^rejected_disabled$'
check "R09 a second request inside 45 minutes is rejected_cooldown" \
  "$(as_role service_role "select api.admin_open_refresh('test.r16_on')->>'status'")" '^rejected_cooldown$'
check "R10 finish records the run id on the provisional request, once" \
  "$(as_role service_role "select api.admin_finish_refresh((select max(id) from ops.refresh_request where task_key='test.r16_on' and status='dispatched'), 'dispatched', 4242); select api.admin_finish_refresh((select max(id) from ops.refresh_request where task_key='test.r16_on' and status='dispatched'), 'failed', null)")" \
  '^t.f$'
check "R11 finish refuses a status other than dispatched or failed, and a failed dispatch with a run id" \
  "$(as_role service_role "select api.admin_finish_refresh(1, 'rejected_nse', null)"; as_role service_role "select api.admin_finish_refresh(1, 'failed', 7)")" \
  'must be dispatched or failed.*a failed dispatch has no run id'
as_pg "update ops.refresh_request set requested_at = now() - interval '46 minutes' where task_key='test.r16_on'" >/dev/null
check "R12 after 45 minutes a new request passes; a failed one doesn't start the cooldown" \
  "$(as_role service_role "select api.admin_finish_refresh((api.admin_open_refresh('test.r16_on')->>'request_id')::bigint, 'failed', null)"; as_role service_role "select api.admin_open_refresh('test.r16_on')->>'status'")" \
  '^t.dispatched$'
check "R13 an NSE task keeps its minimum interval past the cooldown" \
  "$(as_role service_role "select api.admin_open_refresh('test.r16_nse')->>'status'"; as_pg "update ops.refresh_request set requested_at = now() - interval '60 minutes' where task_key='test.r16_nse'" >/dev/null; as_role service_role "select api.admin_open_refresh('test.r16_nse')->>'status'")" \
  '^dispatched.rejected_nse$'
check "R14 the status shows the run id, and the job run once the run links itself" \
  "$(as_pg "insert into ops.job_run (workflow, trigger, runner, status) values ('refresh','admin','github','success') returning id" | head -1 | xargs -I{} psql $H -U postgres -c "update ops.refresh_request set job_run_id = {} where id = (select min(id) from ops.refresh_request where task_key='test.r16_on' and github_run_id = 4242)"; as_role service_role "select s->>'status', s->>'github_run_id', s->'job_run'->>'status', s->>'task' from api.admin_refresh_status((select min(id) from ops.refresh_request where task_key='test.r16_on' and github_run_id = 4242)) as t(s)")" \
  '^dispatched\|4242\|success\|test\.r16_on$'
check "R15 the status of a missing request is null" \
  "$(as_role service_role "select coalesce(api.admin_refresh_status(999999999)::text, 'null')")" '^null$'
as_pg "update ops.refresh_request set requested_at = now() - interval '11 minutes' where task_key='test.r16_on' and status='dispatched' and github_run_id is null" >/dev/null
check "R16 a provisional request older than 10 minutes can't be finished (only the route that just opened it can)" \
  "$(as_role service_role "select api.admin_finish_refresh((select max(id) from ops.refresh_request where task_key='test.r16_on' and status='dispatched' and github_run_id is null), 'dispatched', 99)")" '^f$'
check "R17 a request already linked to its job run can't be finished again" \
  "$(as_role service_role "select api.admin_finish_refresh((select min(id) from ops.refresh_request where task_key='test.r16_on' and job_run_id is not null), 'failed', null)")" '^f$'

as_pg "update ops.refresh_request set job_run_id = null where task_key like 'test.r16_%';
       delete from ops.refresh_request where task_key like 'test.r16_%';
       delete from ops.task where key like 'test.r16_%'" >/dev/null
echo
echo "passed $pass, failed $fail"
