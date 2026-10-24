#!/bin/bash
# Tests for 0017 (the admin health function; Prompt 01 C6, option B on issue #8). Run after the other scripts on
# the same fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
same()    { [ "$1" = "$2" ] && [ -n "$1" ] && echo "same $1" || echo "differ: $1 vs $2"; }
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

FN="'api.admin_health()'::regprocedure"
T_OK=test.h17_ok; T_FAIL=test.h17_fail
as_pg "insert into ops.task (key, module, description, workflows, min_interval, overdue_after, uses_nse, enabled) values
         ('test.h17_ok',   'test', '0017 test task', '{daily}', '20 hours', '2 days', false, true),
         ('test.h17_fail', 'test', '0017 test task', '{daily}', '20 hours', '2 days', false, true)
       on conflict (key) do nothing;
       insert into ops.job_run (workflow, trigger, runner, status, started_at) values
         ('daily', 'schedule', 'github', 'partial', now() - interval '1 hour');
       insert into ops.task_run (job_run_id, task_key, status, started_at, finished_at, rows_written) values
         ((select max(id) from ops.job_run), 'test.h17_ok', 'success', now() - interval '59 minutes', now() - interval '58 minutes', 7),
         ((select max(id) from ops.job_run), 'test.h17_fail', 'skipped_not_due', now() - interval '58 minutes', now() - interval '58 minutes', 0);
       insert into ops.task_run (job_run_id, task_key, status, started_at, finished_at, error) values
         ((select max(id) from ops.job_run), 'test.h17_fail', 'checks_failed', now() - interval '57 minutes', now() - interval '56 minutes', 'h17 parser broke');
       insert into ops.alert (task_key, severity, message, dedupe_key, raised_at) values
         ('test.h17_fail', 'error', 'h17 newer alert', 'h17:newer', now() - interval '1 hour'),
         ('test.h17_fail', 'warn',  'h17 older alert', 'h17:older', now() - interval '3 days'),
         ('test.h17_fail', 'warn',  'h17 resolved alert', 'h17:done', now() - interval '4 days');
       update ops.alert set resolved_at = now() where dedupe_key = 'h17:done';
       insert into ops.refresh_request (task_key, requested_by, status, requested_at) values
         ('test.h17_ok', 'admin', 'dispatched', now() - interval '15 minutes'),
         ('test.h17_fail', 'admin', 'rejected_cooldown', now() - interval '5 minutes')" >/dev/null

check "H01 admin_health is security definer, owned by postgres, stable, with a pinned search_path" \
  "$(as_pg "select p.prosecdef||':'||pg_get_userbyid(p.proowner)||':'||p.provolatile||':'||array_to_string(p.proconfig,';') from pg_proc p where p.oid = $FN")" \
  '^true:postgres:s:search_path=pg_catalog, ops$'
check "H02 only service_role (and the owner) can execute it: not anon, authenticated, job_writer, sql_reader" \
  "$(as_pg "select string_agg(r, ',' order by r) from unnest(array['anon','authenticated','service_role','job_writer','sql_reader','semantic_owner','retrieval_owner']) r where has_function_privilege(r, $FN, 'execute')")" '^service_role$'
check "H03 PUBLIC holds no EXECUTE grant on it" \
  "$(as_pg "select count(*) from pg_proc p, aclexplode(p.proacl) a where p.oid = $FN and a.grantee = 0")" '^0$'
check "H04 anon is refused" \
  "$(as_role anon "select api.admin_health()")" 'permission denied for function admin_health'
check "H05 authenticated (the admin's own browser session) is refused too" \
  "$(as_role authenticated "select api.admin_health()")" 'permission denied for function admin_health'
check "H06 service_role reads every task, each once" \
  "$(same "$(as_role service_role "select jsonb_array_length(api.admin_health()->'tasks')")" "$(as_pg "select count(*) from ops.task")")" '^same [0-9]+$'
check "H07 a task's row carries its last status, success time, error and open alerts" \
  "$(as_role service_role "select t->>'last_status', t->>'last_error', t->>'open_alerts', (t->>'last_success_at') is null from jsonb_array_elements(api.admin_health()->'tasks') t where t->>'key' = '$T_FAIL'")" \
  '^checks_failed\|h17 parser broke\|2\|t$'
check "H08 the cooldown: a dispatched request 15 minutes ago gives refresh_available_at 30 minutes from now; a rejected one doesn't count" \
  "$(as_role service_role "select round(extract(epoch from (t->>'refresh_available_at')::timestamptz - now()) / 60), coalesce(u->>'refresh_available_at', 'null') from jsonb_array_elements(api.admin_health()->'tasks') t, jsonb_array_elements(api.admin_health()->'tasks') u where t->>'key' = '$T_OK' and u->>'key' = '$T_FAIL'")" \
  '^30\|null$'
check "H09 open alerts only, oldest first" \
  "$(as_role service_role "select string_agg(a->>'message', ',' order by n) from jsonb_array_elements(api.admin_health()->'alerts') with ordinality x(a, n) where a->>'message' like 'h17%'")" \
  '^h17 older alert,h17 newer alert$'
check "H10 the open-alert count matches the table" \
  "$(same "$(as_role service_role "select api.admin_health()->>'open_alert_count'")" "$(as_pg "select count(*) from ops.alert where resolved_at is null")")" '^same [0-9]+$'
check "H11 recent runs newest first, with their task runs (not-due skips left out) and errors" \
  "$(as_role service_role "select r->>'status', jsonb_array_length(r->'tasks'), r->'tasks'->1->>'error' from jsonb_array_elements(api.admin_health()->'runs') r where r->>'id' = (select max(id)::text from ops.job_run)")" \
  '^partial\|2\|h17 parser broke$'
check "H12 at most 20 runs" \
  "$(as_pg "insert into ops.job_run (workflow, trigger, runner, status) select 'daily', 'schedule', 'github', 'success' from generate_series(1, 25)" >/dev/null; as_role service_role "select jsonb_array_length(api.admin_health()->'runs')")" '^20$'

as_pg "delete from ops.refresh_request where task_key like 'test.h17_%';
       delete from ops.alert where dedupe_key like 'h17:%';
       delete from ops.task_run where task_key like 'test.h17_%';
       delete from ops.task where key like 'test.h17_%'" >/dev/null
echo
echo "passed $pass, failed $fail"
