#!/bin/bash
# Tests for 0054 (cmdbar_route: the command bar's api.cmdbar_* functions; issue #583, K-05 part 2). Run after the other scripts
# on the same fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

FNS="'api.cmdbar_state()'::regprocedure, 'api.cmdbar_count(text,timestamptz)'::regprocedure,
     'api.cmdbar_cache_get(text)'::regprocedure, 'api.cmdbar_cache_put(text,text,text,jsonb,text,timestamptz)'::regprocedure,
     'api.cmdbar_cache_hit(text)'::regprocedure, 'api.cmdbar_log(jsonb)'::regprocedure, 'api.cmdbar_examples()'::regprocedure"
V1="visitor:$(printf 'a%.0s' {1..64})"
V2="visitor:$(printf 'b%.0s' {1..64})"
HASH="$(printf 'c%.0s' {1..64})"
SHA="$(printf '0%.0s' {1..63})1"
OLD="$(printf '0%.0s' {1..63})2"
HOUR="date_trunc('hour', now())"

as_pg "delete from ops.cmdbar_usage where bucket in ('$V1', '$V2', 'global') and window_start >= now() - interval '3 days';
       delete from ops.cmdbar_cache where question_sha in (decode('$SHA','hex'), decode('$OLD','hex'));
       delete from ops.cmdbar_log where visitor_hash = '$HASH';
       delete from ops.cmdbar_example where question like 'r54 %';
       insert into ops.task (key, module, description, workflows, min_interval, overdue_after, uses_nse, enabled) values
         ('test.r54_spend', 'test', '0054 test task', '{daily}', '1 day', '2 days', false, true)
       on conflict (key) do nothing" >/dev/null

# ---------------------------------------------------------------- who may run them
check "C01 the seven functions are security definer, owned by postgres, with search_path pg_catalog, ops" \
  "$(as_pg "select count(*) filter (where p.prosecdef and pg_get_userbyid(p.proowner) = 'postgres'
                                    and p.proconfig = array['search_path=pg_catalog, ops']), count(*)
              from pg_proc p where p.oid in ($FNS)")" '^7\|7$'
check "C02 only service_role can execute them: not anon, authenticated, job_writer, sql_reader or semantic_owner" \
  "$(as_pg "select string_agg(distinct r, ',' order by r) from (select p.oid, r from pg_proc p,
              unnest(array['anon','authenticated','service_role','job_writer','sql_reader','semantic_owner','retrieval_owner']) r
              where p.oid in ($FNS) and has_function_privilege(r, p.oid, 'execute')) x")" '^service_role$'
check "C03 PUBLIC holds no EXECUTE grant on them, and each has a comment" \
  "$(as_pg "select (select count(*) from pg_proc p, aclexplode(p.proacl) a where p.oid in ($FNS) and a.grantee = 0),
                   (select count(*) from pg_proc p where p.oid in ($FNS) and obj_description(p.oid, 'pg_proc') like 'Command bar (Spec 07%service_role only.')")" \
  '^0\|7$'
check "C04 anon is refused (a visitor's browser key)" \
  "$(as_role anon "select api.cmdbar_state()")" 'permission denied for function cmdbar_state'
check "C05 authenticated is refused too" \
  "$(as_role authenticated "select api.cmdbar_count('global', now())")" 'permission denied for function cmdbar_count'

# ---------------------------------------------------------------- the switch and the budget (N8)
check "C06 the state is 0052's: the switch off, the cap 100, 10 an hour, the budget 15" \
  "$(as_role service_role "select s->>'ai_enabled', s->>'daily_cap', s->>'per_visitor_hourly', s->>'monthly_budget_usd',
                                  jsonb_typeof(s->'month_spend_usd') from api.cmdbar_state() s")" '^false\|100\|10\|15\|string$'
check "C07 the switch reads true only when the setting is JSON true (not the string \"true\")" \
  "$(as_pg "begin; update ops.setting set value = 'true'::jsonb where key = 'cmdbar_ai_enabled';
            select api.cmdbar_state()->>'ai_enabled';
            update ops.setting set value = '\"true\"'::jsonb where key = 'cmdbar_ai_enabled';
            select api.cmdbar_state()->>'ai_enabled'; rollback;" | grep -v -E '^(BEGIN|UPDATE 1|ROLLBACK)$' | tr '\n' ' ')" '^true false $'
check "C08 the switch is still off after C07" \
  "$(as_pg "select value from ops.setting where key = 'cmdbar_ai_enabled'")" '^false$'
before=$(as_role service_role "select (api.cmdbar_state()->>'month_spend_usd')::numeric")
as_pg "with j as (insert into ops.job_run (workflow, trigger, runner, status) values ('daily','cli','local','success') returning id)
       insert into ops.task_run (job_run_id, task_key, started_at, status, ai_cost_usd)
       select id, 'test.r54_spend', now(), 'success', 0.2500 from j union all
       select id, 'test.r54_spend', now() - interval '40 days', 'success', 9.0000 from j" >/dev/null
as_role service_role "select api.cmdbar_log('{\"visitor_hash\": \"$HASH\", \"question\": \"r54 spend\", \"answered\": true, \"cost_usd\": \"0.125000\"}')" >/dev/null
as_role service_role "select api.cmdbar_log('{\"visitor_hash\": \"$HASH\", \"question\": \"r54 old spend\", \"answered\": true, \"cost_usd\": \"7\"}')" >/dev/null
as_pg "update ops.cmdbar_log set at = now() - interval '40 days' where visitor_hash = '$HASH' and question = 'r54 old spend'" >/dev/null
check "C09 the month's spend adds this month's task runs and the bar's own answers, never an earlier month's" \
  "$(as_role service_role "select (api.cmdbar_state()->>'month_spend_usd')::numeric - $before")" '^0\.375'
check "C10 a setting that isn't a number is an error (the route reads it as paused), never a guess" \
  "$(as_pg "begin; update ops.setting set value = '\"lots\"'::jsonb where key = 'cmdbar_daily_cap'; select api.cmdbar_state(); rollback;")" \
  'invalid input syntax for type integer'

# ---------------------------------------------------------------- the counts
check "C11 a count starts at 1 and goes up by one per call, per bucket and window" \
  "$(as_role service_role "select api.cmdbar_count('$V1', $HOUR), api.cmdbar_count('$V1', $HOUR),
                                  api.cmdbar_count('$V2', $HOUR), api.cmdbar_count('$V1', $HOUR - interval '1 hour')")" '^1\|2\|1\|1$'
for i in $(seq 1 10); do as_role service_role "select api.cmdbar_count('global', $HOUR)" >/dev/null & done; wait
check "C12 ten concurrent increments count ten (atomic)" \
  "$(as_pg "select count from ops.cmdbar_usage where bucket = 'global' and window_start = $HOUR")" '^10$'
check "C13 an unknown bucket is refused" \
  "$(as_role service_role "select api.cmdbar_count('visitor:1.2.3.4', $HOUR)")" 'the bucket must be global or visitor'
check "C14 a window outside the last 25 hours is refused" \
  "$(as_role service_role "select api.cmdbar_count('global', now() - interval '26 hours')"; as_role service_role "select api.cmdbar_count('global', now() + interval '1 hour')")" \
  'within the last 25 hours.*within the last 25 hours'
as_pg "insert into ops.cmdbar_usage (bucket, window_start, count) values ('$V2', now() - interval '3 days', 4)" >/dev/null
as_role service_role "select api.cmdbar_count('$V2', $HOUR)" >/dev/null
check "C15 a count deletes usage windows older than two days (visitor hashes kept no longer than the limits need)" \
  "$(as_pg "select count(*) from ops.cmdbar_usage where window_start < now() - interval '2 days'")" '^0$'

# ---------------------------------------------------------------- the cache
check "C16 a missing entry is null" \
  "$(as_role service_role "select coalesce(api.cmdbar_cache_get('$SHA')::text, 'null')")" '^null$'
as_role service_role "select api.cmdbar_cache_put('$SHA', 'r54 q', 'data', '{\"state\": \"answered\", \"rows\": []}', 'v1', now() + interval '24 hours')" >/dev/null
check "C17 a stored entry comes back with its route, answer and data version" \
  "$(as_role service_role "select c->>'route', c->'answer'->>'state', c->>'data_version' from api.cmdbar_cache_get('$SHA') c")" '^data\|answered\|v1$'
as_role service_role "select api.cmdbar_cache_hit('$SHA'); select api.cmdbar_cache_hit('$SHA')" >/dev/null
check "C18 hits are counted" \
  "$(as_pg "select hits from ops.cmdbar_cache where question_sha = decode('$SHA','hex')")" '^2$'
as_role service_role "select api.cmdbar_cache_put('$SHA', 'r54 q', 'page', '{\"state\": \"page\"}', 'static', now() + interval '30 days')" >/dev/null
check "C19 a put replaces the entry, resets its hits and holds its expiry to at most 24 hours ahead" \
  "$(as_pg "select route, hits, data_version, expires_at <= now() + interval '24 hours' from ops.cmdbar_cache where question_sha = decode('$SHA','hex')")" \
  '^page\|0\|static\|t$'
as_role service_role "select api.cmdbar_cache_put('$OLD', 'r54 old', 'data', '{}', 'v1', now() + interval '1 hour')" >/dev/null
as_pg "update ops.cmdbar_cache set expires_at = now() - interval '1 second' where question_sha = decode('$OLD','hex')" >/dev/null
check "C20 an expired entry is never returned" \
  "$(as_role service_role "select coalesce(api.cmdbar_cache_get('$OLD')::text, 'null')")" '^null$'
check "C21 a key that isn't hex is an error, never a match" \
  "$(as_role service_role "select api.cmdbar_cache_get('not hex')")" 'invalid hexadecimal'

# ---------------------------------------------------------------- the log
check "C22 a log row takes its named columns only; an extra key (an IP address) is never stored" \
  "$(as_role service_role "select api.cmdbar_log('{\"visitor_hash\": \"$HASH\", \"question\": \"r54 q\", \"route\": \"data\",
       \"cache_hit\": false, \"generated_sql\": \"SELECT 1\", \"sql_valid\": true, \"reject_reason\": null, \"rows_returned\": 3,
       \"answered\": true, \"latency_ms\": 1234, \"models\": [\"claude-haiku-5-5\", \"claude-haiku-5-5\"], \"input_tokens\": 900,
       \"output_tokens\": 80, \"cost_usd\": \"0.001234\", \"error_class\": null, \"ip\": \"203.0.113.7\"}') > 0";
     as_pg "select route, rows_returned, latency_ms, array_length(models, 1), cost_usd, row_to_json(l)::text like '%203.0.113.7%'
              from ops.cmdbar_log l where visitor_hash = '$HASH' and question = 'r54 q'")" '^t.data\|3\|1234\|2\|0\.001234\|f$'
check "C23 ops.cmdbar_log has no column for an IP address, a user agent or a cookie" \
  "$(as_pg "select count(*) from information_schema.columns where table_schema = 'ops' and table_name = 'cmdbar_log'
              and column_name ~ '(ip|addr|agent|cookie)'")" '^0$'
check "C24 a row without a 64-hex visitor hash, or without answered, is refused" \
  "$(as_role service_role "select api.cmdbar_log('{\"visitor_hash\": \"203.0.113.7\", \"question\": \"r54 bad\", \"answered\": false}')";
     as_role service_role "select api.cmdbar_log('{\"visitor_hash\": \"$HASH\", \"question\": \"r54 bad\"}')")" \
  'a row with a 64-hex visitor_hash is required.*null value in column "answered"'

# ---------------------------------------------------------------- the examples
check "C25 no example checked yet: an empty list" \
  "$(as_role service_role "select jsonb_array_length(api.cmdbar_examples())")" '^0$'
as_pg "insert into ops.cmdbar_example (question, module, display_order, answer, verified) values
         ('r54 second', 'equity', 2, '{\"state\": \"answered\"}', true),
         ('r54 unchecked', 'policy', 0, '{\"state\": \"answered\"}', false),
         ('r54 first', 'policy', 1, '{\"state\": \"answered\"}', true)" >/dev/null
check "C26 only the examples the admin checked, in display order" \
  "$(as_role service_role "select string_agg(e->>'question', ',' order by o) from jsonb_array_elements(api.cmdbar_examples()) with ordinality x(e, o)")" \
  '^r54 first,r54 second$'

as_pg "delete from ops.cmdbar_usage where bucket in ('$V1', '$V2', 'global');
       delete from ops.cmdbar_cache where question_sha in (decode('$SHA','hex'), decode('$OLD','hex'));
       delete from ops.cmdbar_log where visitor_hash = '$HASH';
       delete from ops.cmdbar_example where question like 'r54 %';
       delete from ops.task_run where task_key = 'test.r54_spend';
       delete from ops.task where key = 'test.r54_spend'" >/dev/null
echo
echo "passed $pass, failed $fail"
