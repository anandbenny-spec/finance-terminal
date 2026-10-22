#!/bin/bash
# Tests for 0010 (AI layer). Run after run_tests.sh on the same fresh database (rebuild_and_test.sh does both).
# Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
login()   { psql $H -U "$1" -c "$2" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
Q="(select array_fill(0.01::real, array[512])::extensions.halfvec(512))"

# An unpublished document with matching text and an embedding: retrieval must never return it.
as_pg "insert into core.chunk (document_id, chunk_no, char_start, char_end, body)
       select id, 0, 0, 40, 'Inflation remains elevated in the draft.' from core.document where doc_type='fomc_minutes';
       insert into core.chunk_embedding (document_id, chunk_no, model, embedding)
       select id, 0, 'voyage-4-lite@512', $Q from core.document where doc_type='fomc_minutes';" >/dev/null

check "A01 retrieval returns published chunks only (keyword + vector)" \
  "$(login sql_reader "select string_agg(doc_type||':'||chunk_no, ',' order by score desc, chunk_no) from semantic.search_chunks($Q, 'inflation elevated')")" '^fomc_statement:1,fomc_statement:0$'
check "A02 the bank filter applies" \
  "$(login sql_reader "select count(*) from semantic.search_chunks($Q, 'inflation', '{rbi}')")" '^0$'
check "A03 keyword-only match ranks the matching chunk first" \
  "$(login sql_reader "select chunk_no from semantic.search_chunks($Q, 'Committee lower target range') limit 1")" '^0$'
check "A04 sql_reader still can't read embeddings or chunks directly" \
  "$(login sql_reader "select count(*) from core.chunk_embedding")" 'permission denied for schema core'
check "A05 visitors can't call the retrieval function" \
  "$(as_role anon "select count(*) from semantic.search_chunks($Q, 'x')")" 'permission denied for schema semantic'
check "A06 suppressing the source empties retrieval too" \
  "$(as_pg "update core.source set is_suppressed=true where key='fed'" >/dev/null; login sql_reader "select count(*) from semantic.search_chunks($Q, 'inflation')"; as_pg "update core.source set is_suppressed=false where key='fed'" >/dev/null)" '^0$'
check "A07 rate-limit counter increments atomically for the live route" \
  "$(as_role service_role "insert into ops.cmdbar_usage values ('global', date_trunc('day', now()), 1) on conflict (bucket, window_start) do update set count = ops.cmdbar_usage.count + 1 returning count; insert into ops.cmdbar_usage values ('global', date_trunc('day', now()), 1) on conflict (bucket, window_start) do update set count = ops.cmdbar_usage.count + 1 returning count")" '1.*2'
check "A08 jobs can't write the command-bar log (only clean it up)" \
  "$(as_role job_writer "insert into ops.cmdbar_log (visitor_hash, question, answered) values ('x','y',false)")" 'permission denied for table cmdbar_log'
check "A09 visitors can read the evaluation summary but not the runs" \
  "$(as_role anon "select count(*) from api.eval_summary"; as_role anon "select count(*) from ops.eval_run")" '^0.*permission denied for schema ops'
check "A10 the admin route can mark an example answer checked, nothing else on it" \
  "$(as_role job_writer "insert into ops.cmdbar_example (question, module, display_order) values ('What did RBI decide in October?','policy',1)" >/dev/null; as_role service_role "update ops.cmdbar_example set verified=true returning verified"; as_role service_role "update ops.cmdbar_example set answer='{}'")" '^t.*permission denied for table cmdbar_example'

check "A11 retrieval_owner owns no table and can't bypass RLS" \
  "$(as_pg "select rolbypassrls::text || ',' || (select count(*) from pg_class where relowner = r.oid and relkind='r') from pg_roles r where rolname='retrieval_owner'")" '^false,0$'
check "A12 retrieval_owner sees only published documents (the gate the function relies on)" \
  "$(as_role retrieval_owner "select count(*) filter (where not is_published) from core.document")" '^0$'

echo; echo "passed $pass, failed $fail"
