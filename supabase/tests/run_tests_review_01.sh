#!/bin/bash
# Tests for 0011 (Review 01 fixes). Run after run_tests.sh and run_tests_ai.sh on the same fresh database.
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
login()   { psql $H -U "$1" -c "$2" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

# F17: one task last succeeded 5 days ago (overdue after 4), one an hour ago, one failed.
as_pg "update core.task_freshness set status='ok', last_success_at=now()-interval '5 days', stale_after='4 days' where task_key='policy.market_series';
       update core.task_freshness set status='ok', last_success_at=now()-interval '1 hour', stale_after='4 days' where task_key='policy.reactions';
       update core.task_freshness set status='failed', last_success_at=now()-interval '9 days', stale_after='3 days' where task_key='policy.ai_batch';" >/dev/null
check "V01 freshness shows stale once a task's last success is older than its overdue_after (runs stopped)" \
  "$(as_role anon "select string_agg(task_key||':'||status, ',' order by task_key) from api.task_freshness where task_key in ('policy.market_series','policy.reactions','policy.ai_batch')")" \
  '^policy\.ai_batch:failed,policy\.market_series:stale,policy\.reactions:ok$'
check "V02 the seeded freshness rows carry each task's overdue_after" \
  "$(as_pg "select count(*) from core.task_freshness f join ops.task t on t.key=f.task_key where f.task_key not in ('policy.market_series','policy.reactions','policy.ai_batch') and f.stale_after is distinct from t.overdue_after")" '^0$'

# F15
check "V03 a fractional hawk/dove score is refused; a whole step is accepted" \
  "$(as_role job_writer "insert into core.ai_output (task, event_id, content, score, status, model, prompt_key, prompt_version, input_sha256) select 'hawk_dove_score', id, '{}', -0.4, 'draft', 'm','p','v9', sha256('g'::bytea) from core.event where slug='fomc-2026-09-16'"; as_role job_writer "begin; insert into core.ai_output (task, event_id, content, score, status, model, prompt_key, prompt_version, input_sha256) select 'hawk_dove_score', id, '{}', 2, 'draft', 'm','p','v9', sha256('h'::bytea) from core.event where slug='fomc-2026-09-16' returning score; rollback;")" \
  'violates check constraint "hawk_dove_score_integer".*2'

# F22
check "V04 api.policy_meetings carries both bounds of the Fed range" \
  "$(as_role anon "select fed_target_lower_pct||'-'||fed_target_upper_pct||'|'||policy_rate_pct from api.policy_meetings where slug='fomc-2026-09-16'")" '^3\.750-4\.000\|4\.000$'

# F24
check "V05 api.series_latest: last and previous point per published series; unpublished hidden" \
  "$(as_role anon "select string_agg(series_key||':'||last_date||':'||prev_date||':'||(change = last_value - prev_value), ',') from api.series_latest")" \
  '^fred\.DGS2:2026-09-18:2026-09-17:true$'

# N2: a daily step series, 7 days, one change on 17 Sep
as_pg "update core.series set is_published = true where key = 'fred.DFEDTARU';
       insert into core.observation (series_id, obs_date, value, task_run_id)
       select (select id from core.series where key='fred.DFEDTARU'), d::date, case when d < '2026-09-17' then 4.25 else 4.00 end, (select max(id) from ops.task_run)
       from generate_series('2026-09-14'::date, '2026-09-20', '1 day') d;" >/dev/null
check "V06 api.observation_steps returns only change points plus the latest day" \
  "$(as_role anon "select string_agg(obs_date||':'||value||':'||is_latest, ',' order by obs_date) from api.observation_steps where series_key='fred.DFEDTARU'")" \
  '^2026-09-14:4\.25:false,2026-09-17:4\.00:false,2026-09-20:4\.00:true$'
check "V07 ...while api.observations holds every day" \
  "$(as_role anon "select count(*) from api.observations where series_key='fred.DFEDTARU'")" '^7$'
check "V08 the series_key filter is applied below the window function (no full scan of every series)" \
  "$(as_pg "explain (costs off) select * from api.observation_steps where series_key='fred.DFEDTARU'")" 'WindowAgg.*key = .fred\.DFEDTARU'

# F14, F23
check "V09 text-to-SQL comments: stance is RBI only; dissents cover rate and stance" \
  "$(as_pg "select col_description('semantic.policy_meetings'::regclass, (select attnum from pg_attribute where attrelid='semantic.policy_meetings'::regclass and attname='stance')) || ' | ' || col_description('semantic.policy_votes'::regclass, (select attnum from pg_attribute where attrelid='semantic.policy_votes'::regclass and attname='is_dissent'))")" \
  '^RBI only: .*Null for Fed meetings.* \| True if the member dissented on the rate or \(RBI\) on the stance'
check "V10 sql_reader can't read the new api views" \
  "$(login sql_reader "select count(*) from api.series_latest")" 'permission denied for schema api'

echo; echo "passed $pass, failed $fail"
