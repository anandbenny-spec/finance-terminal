#!/bin/bash
# Tests for 0031 (api.admin_labelling_passages; issue #175, job 2B-12; Spec 02B §3.2). Run after the other scripts on the same fresh
# database. The script creates its own rows (company ec31-a, one call document with three chunks), checks through the roles and
# removes every row it made.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from core.document where external_key like 'ec31-%';
         delete from core.event where slug like 'ec31%';
         delete from core.entity where slug like 'ec31%';" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec31-a', 'Ec31 A Ltd', 'ec31-a', 'IN', 'INR', true);
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
  select 'earnings_call', id, 'ec31-call', 'ec31', '2026-10-25', 'stored', (select id from core.source where key = 'nse'), true
  from core.entity where slug = 'ec31-a';
insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                           char_count, text_sha256, chunked_at, is_published)
  select 'call_transcript', e.id, ev.id, 'ec31 call', (select id from core.source where key = 'company_web'), 'ec31-doc',
         'https://example.com/ec31.pdf', now(), 30, sha256(convert_to(repeat('x', 30), 'UTF8')), now(), false
  from core.entity e, core.event ev where e.slug = 'ec31-a' and ev.slug = 'ec31-call';
insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
  select d.id, v.n, v.a, v.b, 'Q&A', 'CFO', v.t
  from core.document d, (values (0, 0, 10, 'first-ten-'), (1, 10, 20, 'second-ten'), (2, 20, 30, 'third-ten-')) v(n, a, b, t)
  where d.external_key = 'ec31-doc';
commit;
SQL
)"
check "A01 the test rows load" "$SETUP_OUT" '^(BEGIN|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|])*$'
DOC="$(as_pg "select id from core.document where external_key = 'ec31-doc'")"
ASK="[{\"document_id\": $DOC, \"chunk_no\": 2}, {\"document_id\": $DOC, \"chunk_no\": 0}, {\"document_id\": $DOC, \"chunk_no\": 7}]"

FN="'api.admin_labelling_passages(jsonb)'::regprocedure"
check "R01 only service_role (and the owner) can execute it: not anon, authenticated, PUBLIC, job_writer or sql_reader" \
  "$(as_pg "select string_agg(r, ',' order by r) from unnest(array['anon','authenticated','service_role','job_writer','sql_reader','semantic_owner']) r where has_function_privilege(r, $FN, 'execute')")" '^service_role$'
check "R02 anon is refused" "$(as_role anon "select api.admin_labelling_passages('[]')")" 'permission denied for function admin_labelling_passages'
check "R03 authenticated is refused" "$(as_role authenticated "select api.admin_labelling_passages('[]')")" 'permission denied for function admin_labelling_passages'
check "R04 sql_reader is refused" "$(as_role sql_reader "select api.admin_labelling_passages('[]')")" 'permission denied for function admin_labelling_passages'
check "R05 job_writer is refused" "$(as_role job_writer "select api.admin_labelling_passages('[]')")" 'permission denied for function admin_labelling_passages'

check "F01 service_role gets exactly the chunks asked for (an unknown one is left out), in chunk order, with their text" \
  "$(as_role service_role "select (api.admin_labelling_passages('$ASK'::jsonb)) #>> '{}'" | tr -d ' ')" \
  "^\[\{\"text\":\"first-ten-\",\"chunk_no\":0,\"document_id\":$DOC\},\{\"text\":\"third-ten-\",\"chunk_no\":2,\"document_id\":$DOC\}\]$"
check "F02 nothing else: a chunk not asked for does not come back" \
  "$(as_role service_role "select api.admin_labelling_passages('$ASK'::jsonb)::text like '%second-ten%'")" '^f$'
check "F03 an empty list, null and a non-array give []" \
  "$(as_role service_role "select api.admin_labelling_passages('[]')::text || api.admin_labelling_passages(null)::text || api.admin_labelling_passages('{\"a\": 1}')::text")" '^\[\]\[\]\[\]$'
check "F04 sixty-one items give [] even when they exist" \
  "$(as_role service_role "select api.admin_labelling_passages((select jsonb_agg(jsonb_build_object('document_id', $DOC, 'chunk_no', 0)) from generate_series(1, 61)))::text")" '^\[\]$'
check "F05 sixty items are served" \
  "$(as_role service_role "select jsonb_array_length(api.admin_labelling_passages((select jsonb_agg(jsonb_build_object('document_id', $DOC, 'chunk_no', 0)) from generate_series(1, 60))))")" '^60$'
check "F06 an element that is not a pair of whole numbers is an error, not a guess" \
  "$(as_role service_role "select api.admin_labelling_passages('[{\"document_id\": \"x\", \"chunk_no\": 1}]')")" 'invalid input syntax'
check "F07 the function writes nothing and reads only core.chunk (stable, security definer, fixed search_path)" \
  "$(as_pg "select provolatile || ':' || prosecdef || ':' || coalesce(array_to_string(proconfig, ','), '') from pg_proc where oid = $FN")" '^s:t:search_path=pg_catalog, core$'

cleanup
echo; echo "passed $pass, failed $fail"
