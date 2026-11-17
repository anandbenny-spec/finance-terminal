#!/bin/bash
# Tests for 0055 (cmdbar_examples_admin: the admin page's two api.cmdbar_example* functions; issue #588, K-09 part 2). Run after
# the other scripts on the same fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

FNS="'api.cmdbar_examples_admin()'::regprocedure, 'api.cmdbar_example_check(integer,boolean)'::regprocedure"
id_of() { as_pg "select id from ops.cmdbar_example where question = '$1'"; }

as_pg "delete from ops.cmdbar_example where question like 'r55 %'" >/dev/null
as_pg "insert into ops.cmdbar_example (question, module, display_order, answer, data_version, verified, updated_at) values
         ('r55 answered', 'policy', 1, '{\"state\": \"answered\", \"route\": \"data\", \"rows\": [[1]]}', 'v1', false, now() - interval '1 day'),
         ('r55 cant', 'equity', 2, '{\"state\": \"cant_answer\", \"route\": \"data\"}', null, false, now()),
         ('r55 empty', 'equity', 3, null, null, false, now()),
         ('r55 checked', 'policy', 0, '{\"state\": \"answered\", \"route\": \"document\"}', 'v2', true, now())" >/dev/null
A=$(id_of 'r55 answered'); C=$(id_of 'r55 cant'); E=$(id_of 'r55 empty'); K=$(id_of 'r55 checked')

# ---------------------------------------------------------------- who may run them
check "D01 both functions are security definer, owned by postgres, with search_path pg_catalog, ops" \
  "$(as_pg "select count(*) filter (where p.prosecdef and pg_get_userbyid(p.proowner) = 'postgres'
                                    and p.proconfig = array['search_path=pg_catalog, ops']), count(*)
              from pg_proc p where p.oid in ($FNS)")" '^2\|2$'
check "D02 only service_role can execute them: not anon, authenticated, job_writer, sql_reader or semantic_owner" \
  "$(as_pg "select string_agg(distinct r, ',' order by r) from (select p.oid, r from pg_proc p,
              unnest(array['anon','authenticated','service_role','job_writer','sql_reader','semantic_owner','retrieval_owner']) r
              where p.oid in ($FNS) and has_function_privilege(r, p.oid, 'execute')) x")" '^service_role$'
check "D03 PUBLIC holds no EXECUTE grant on them, and each has a comment" \
  "$(as_pg "select (select count(*) from pg_proc p, aclexplode(p.proacl) a where p.oid in ($FNS) and a.grantee = 0),
                   (select count(*) from pg_proc p where p.oid in ($FNS) and obj_description(p.oid, 'pg_proc') like 'Command bar (Spec 07%service_role only.')")" \
  '^0\|2$'
check "D04 anon and authenticated are refused (a visitor's browser key, a signed-in session)" \
  "$(as_role anon "select api.cmdbar_examples_admin()"; as_role authenticated "select api.cmdbar_example_check($A, true)")" \
  'permission denied for function cmdbar_examples_admin.*permission denied for function cmdbar_example_check'

# ---------------------------------------------------------------- the list
check "D05 the admin list holds every example, checked or not (as many as the table)" \
  "$(as_role service_role "select jsonb_array_length(api.cmdbar_examples_admin())"; as_pg "select count(*) from ops.cmdbar_example")" \
  '^([0-9]+).\1$'
check "D06 in display order, with each one's id, answer, data version, verified flag and updated_at" \
  "$(as_role service_role "select string_agg(e->>'question' || ':' || (e->>'id' is not null) || ':' || coalesce(e->'answer'->>'state', '-') || ':'
                                             || coalesce(e->>'data_version', '-') || ':' || (e->>'verified') || ':' || (e->>'updated_at' is not null), ','
                                             order by o)
                             from jsonb_array_elements(api.cmdbar_examples_admin()) with ordinality x(e, o) where e->>'question' like 'r55 %'")" \
  '^r55 checked:true:answered:v2:true:true,r55 answered:true:answered:v1:false:true,r55 cant:true:cant_answer:-:false:true,r55 empty:true:-:-:false:true$'
check "D07 0054's visitor list still holds the checked examples only" \
  "$(as_role service_role "select string_agg(e->>'question', ',') from jsonb_array_elements(api.cmdbar_examples()) e where e->>'question' like 'r55 %'")" \
  '^r55 checked$'

# ---------------------------------------------------------------- checking one
before=$(as_pg "select updated_at from ops.cmdbar_example where id = $A")
check "D08 checking an answered example sets verified and returns its id, flag, version and updated_at" \
  "$(as_role service_role "select c->>'id' = '$A', c->>'verified', c->>'data_version', (c->>'updated_at') is not null from api.cmdbar_example_check($A, true) c")" \
  '^t\|true\|v1\|t$'
check "D09 it changes verified and nothing else (updated_at, the answer and the version stay the task's)" \
  "$(as_pg "select verified, updated_at = '$before'::timestamptz, answer->>'state', data_version from ops.cmdbar_example where id = $A")" \
  '^t\|t\|answered\|v1$'
check "D10 the checked example now reaches the visitor list" \
  "$(as_role service_role "select count(*) from jsonb_array_elements(api.cmdbar_examples()) e where e->>'question' = 'r55 answered'")" '^1$'
check "D11 unchecking works and takes it off the visitor list" \
  "$(as_role service_role "select api.cmdbar_example_check($A, false)->>'verified'";
     as_role service_role "select count(*) from jsonb_array_elements(api.cmdbar_examples()) e where e->>'question' = 'r55 answered'")" '^false.0$'
check "D12 a \"can't answer\" example can't be marked checked, and stays unchecked" \
  "$(as_role service_role "select api.cmdbar_example_check($C, true)"; as_pg "select verified from ops.cmdbar_example where id = $C")" \
  'only an answered example can be marked checked.*f$'
check "D13 an example with no stored answer can't be marked checked either" \
  "$(as_role service_role "select api.cmdbar_example_check($E, true)")" 'only an answered example can be marked checked'
check "D14 any example can be unchecked" \
  "$(as_role service_role "select api.cmdbar_example_check($K, false)->>'verified', api.cmdbar_example_check($C, false)->>'verified'")" '^false\|false$'
check "D15 an unknown id is null, and changes nothing" \
  "$(as_role service_role "select coalesce(api.cmdbar_example_check(-1, true)::text, 'null')")" '^null$'
check "D16 a null id or flag is refused" \
  "$(as_role service_role "select api.cmdbar_example_check(null, true)"; as_role service_role "select api.cmdbar_example_check($A, null)")" \
  'an id and true or false are required.*an id and true or false are required'
check "D17 nothing else in ops.cmdbar_example is writable through them (the function list is the two, with these arguments)" \
  "$(as_pg "select string_agg(p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', ',' order by p.proname)
              from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'api' and p.proname like 'cmdbar_example%'")" \
  '^cmdbar_example_check\(p_id integer, p_verified boolean\),cmdbar_examples\(\),cmdbar_examples_admin\(\)$'

as_pg "delete from ops.cmdbar_example where question like 'r55 %'" >/dev/null
echo
echo "passed $pass, failed $fail"
