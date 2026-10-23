#!/bin/bash
# Tests for 0013 (hardening from the day-1 checks). Run after the other scripts on the same fresh database.
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

check "H01 every function in our schemas pins its search_path (Supabase advisor: function_search_path_mutable)" \
  "$(as_pg "select coalesce(string_agg(n.nspname||'.'||p.proname, ',' order by p.proname), 'none') from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname in ('ops','core','policy','api','semantic') and not coalesce(array_to_string(p.proconfig, ';') like '%search_path=%', false)")" '^none$'
check "H02 job_writer can use the extensions schema; PUBLIC can't (as on Supabase)" \
  "$(as_pg "select has_schema_privilege('job_writer','extensions','usage')::text || ',' || exists (select 1 from pg_namespace n, aclexplode(n.nspacl) a where n.nspname='extensions' and a.grantee=0)::text")" '^true,false$'
check "H03 job_writer writes a chunk embedding with an explicit extensions.halfvec cast" \
  "$(as_role job_writer "begin; insert into core.chunk_embedding (document_id, chunk_no, model, embedding) select document_id, chunk_no, 'h03-test', array_fill(0.02::real, array[512])::extensions.halfvec(512) from core.chunk limit 1 returning model; rollback;")" \
  'h03-test'
check "H04 the pinned trigger functions still enforce their rules (document text is immutable once chunked)" \
  "$(as_role job_writer "update core.document set text_sha256=sha256('h04'::bytea) where doc_type='fomc_statement'")" 'immutable here'

echo
echo "passed $pass, failed $fail"
