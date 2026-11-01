#!/bin/bash
# Tests for 0021 (core.quality_summary, api.quality_summary, ops.drill_run; issue #133). Run after the other scripts
# on the same fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

as_pg "delete from core.quality_summary where module = 'test21'; delete from ops.drill_run where counts ? 'test21'" >/dev/null
as_pg "insert into core.quality_summary (module, period, measure, value, n, label, as_of) values
         ('test21','30d','ai_published', 12, 14, null,      now()),
         ('test21','30d','freshness_on_time_share', 0.97, null, 'derived', now())" >/dev/null

check "Q01 RLS is on for both new tables" \
  "$(as_pg "select string_agg(c.relname || '=' || c.relrowsecurity, ',' order by c.relname) from pg_class c where c.oid in ('core.quality_summary'::regclass, 'ops.drill_run'::regclass)")" \
  '^drill_run=true,quality_summary=true$'
check "Q02 the api view is security_invoker" \
  "$(as_pg "select reloptions::text from pg_class where oid = 'api.quality_summary'::regclass")" 'security_invoker=true'
check "Q03 anon reads the projection through the api view" \
  "$(as_role anon "select count(*) from api.quality_summary where module = 'test21'")" '^2$'
check "Q04 authenticated reads it too" \
  "$(as_role authenticated "select count(*) from api.quality_summary where module = 'test21'")" '^2$'
check "Q05 anon cannot write the projection" \
  "$(as_role anon "insert into core.quality_summary values ('test21','all','loads',1,null,null,now())")" 'permission denied'
check "Q06 anon cannot read ops.drill_run" \
  "$(as_role anon "select count(*) from ops.drill_run")" 'permission denied'
check "Q07 authenticated cannot read ops.drill_run" \
  "$(as_role authenticated "select count(*) from ops.drill_run")" 'permission denied'
check "Q08 anon has no privilege of any kind on ops.drill_run, and neither does authenticated" \
  "$(as_pg "select count(*) from information_schema.role_table_grants where table_schema = 'ops' and table_name = 'drill_run' and grantee in ('anon','authenticated','PUBLIC')")" '^0$'
check "Q09 job_writer writes the projection (insert, update, delete)" \
  "$(as_role job_writer "begin; insert into core.quality_summary values ('test21','all','loads',5,5,null,now()); update core.quality_summary set value = 6 where module = 'test21' and measure = 'loads'; delete from core.quality_summary where module = 'test21' and measure = 'loads'; select 'ok'; rollback;")" '^ok$'
check "Q10 a measure outside the fixed list is refused (nothing like a task name or error text can be written)" \
  "$(as_role job_writer "insert into core.quality_summary values ('test21','all','policy.fed_documents',1,null,null,now())")" 'violates check constraint'
check "Q11 a period outside 30d, 90d and all is refused" \
  "$(as_role job_writer "insert into core.quality_summary values ('test21','7d','loads',1,null,null,now())")" 'violates check constraint'
check "Q12 a label other than null or derived is refused" \
  "$(as_role job_writer "insert into core.quality_summary values ('test21','all','loads',1,null,'proxy',now())")" 'violates check constraint'
check "Q13 a negative value is refused" \
  "$(as_role job_writer "insert into core.quality_summary values ('test21','all','loads',-1,null,null,now())")" 'violates check constraint'
check "Q14 module, period and measure are the primary key" \
  "$(as_role job_writer "insert into core.quality_summary values ('test21','30d','ai_published',1,null,null,now())")" 'duplicate key'
check "Q15 job_writer inserts and updates a drill record, with its sequence" \
  "$(as_role job_writer "begin; insert into ops.drill_run (drill, target, counts) values ('d4_freshness','dev','{\"test21\":1}') returning drill, target, passed is null; update ops.drill_run set finished_at = now(), passed = true where counts ? 'test21'; select 'ok'; rollback;")" '^d4_freshness\|dev\|t\s+ok$'
check "Q16 service_role (the dev SQL role is a member) inserts and reads a drill record" \
  "$(as_role service_role "begin; insert into ops.drill_run (drill, target, counts) values ('d3_suppression','dev','{\"test21\":1}'); select count(*) from ops.drill_run where counts ? 'test21'; rollback;")" '^1$'
check "Q17 job_writer cannot delete a drill record" \
  "$(as_role job_writer "delete from ops.drill_run")" 'permission denied'
check "Q18 a drill outside d3_suppression and d4_freshness, or a target outside dev and production, is refused" \
  "$(as_role job_writer "insert into ops.drill_run (drill, target) values ('d9_other','dev')")$(as_role job_writer "insert into ops.drill_run (drill, target) values ('d4_freshness','staging')")" 'violates check constraint.*violates check constraint'

as_pg "delete from core.quality_summary where module = 'test21'; delete from ops.drill_run where counts ? 'test21'" >/dev/null
echo
echo "passed $pass, failed $fail"
