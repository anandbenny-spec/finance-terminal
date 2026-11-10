#!/bin/bash
# Tests for 0035 (the call summary version floor; issue #371, job 2B-19c). Run after the other scripts on the same fresh database. The
# script creates its own rows (companies ec35-a, ec35-b, ec35-c; ec35-a's first call has a v11 summary on the transcript and a NEWER
# v9 summary on the event, its second call only a v9; ec35-b a v100; ec35-c a v10; guidance_extraction and doc_summary outputs at v1
# on ec35-a), checks through the roles and removes every row it made. Gate flips happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.transcript_page where document_id in (select id from core.document where external_key like 'ec35%');
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec35%');
         delete from core.ai_output where document_id in (select id from core.document where external_key like 'ec35%')
                                       or event_id in (select id from core.event where slug like 'ec35%');
         delete from core.chunk where document_id in (select id from core.document where external_key like 'ec35%');
         delete from core.document where external_key like 'ec35%';
         delete from core.event where slug like 'ec35%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'ec35%');
         delete from core.entity where slug like 'ec35%';
         update core.field_policy set status = 'hidden' where field_key in ('ai.call_summary','ai.guidance_extraction');
         update core.source set is_suppressed = false where key in ('nse','company_web');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
create function pg_temp.mk_call(p_ent text, p_label text, p_period date, p_filed timestamptz)
returns void language plpgsql as $f$
declare txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
        eid integer; evid bigint; did bigint; tag text := lower(p_ent || '-' || replace(p_label, ' ', ''));
begin
  select id into eid from core.entity where slug = p_ent;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec35-call-' || tag, 'ec35', p_period, 'stored',
            (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'ec35 call ' || tag, (select id from core.source where key = 'company_web'),
            'ec35-' || tag, 'https://example.com/ec35/' || tag || '.pdf#page=3', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
    values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at,
                                    exchange, route, transcript_document_id, words, unknown_role_share)
    values (evid, eid, p_label, p_period, lower(replace(p_label, ' ', '-')), 'results_link', p_filed, 'NSE',
            'company_link', did, 105, 0.05);
  insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
    values (did, 1, 0, 300), (did, 2, 300, 645);
end $f$;

-- One published output with one verified citation. p_target 'doc' targets the call's transcript, 'event' the call's event (the
-- unique index allows one published output per task and target, so two published summaries of a call need two targets). The body
-- names the call, task, version and target, so a test finds its rows by it.
create function pg_temp.mk_out(p_task text, p_ext text, p_target text, p_version text, p_spans int[]) returns void language plpgsql as $f$
declare did bigint; evid bigint; oid bigint; i int;
begin
  select d.id, d.event_id into did, evid from core.document d where d.external_key = p_ext;
  insert into core.ai_output (task, document_id, event_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
    values (p_task, case when p_target = 'doc' then did end, case when p_target = 'event' then evid end,
            jsonb_build_object(), p_ext || ' ' || p_task || ' ' || p_version || ' ' || p_target, 'draft', 'claude-haiku-5-5', p_task,
            p_version, sha256(convert_to('ec35' || p_ext || p_task || p_version || p_target, 'UTF8'))) returning id into oid;
  for i in 1 .. array_length(p_spans, 1) / 2 loop
    insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
      values (oid, i, '/x' || i, 'document_span', did, p_spans[2 * i - 1], p_spans[2 * i], true);
  end loop;
  update core.ai_output set status = 'published' where id = oid;
end $f$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec35-a', 'Ec35 A Ltd', 'ec35-a', 'IN', 'INR', true),
         ('company', 'ec35-b', 'Ec35 B Ltd', 'ec35-b', 'IN', 'INR', true),
         ('company', 'ec35-c', 'Ec35 C Ltd', 'ec35-c', 'IN', 'INR', true);
insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000350' || (ascii(right(slug, 1)) - 96), 'EC35' || upper(right(slug, 1)), '535' || (ascii(right(slug, 1)) - 96)
  from core.entity where slug like 'ec35-%';
select pg_temp.mk_call('ec35-a', 'Q1 FY27', '2026-06-30', '2026-07-25');
select pg_temp.mk_call('ec35-a', 'Q2 FY27', '2026-09-30', '2026-10-25');
select pg_temp.mk_call('ec35-b', 'Q2 FY27', '2026-09-30', '2026-10-26');
select pg_temp.mk_call('ec35-c', 'Q2 FY27', '2026-09-30', '2026-10-27');
-- ec35-a Q1: the v11 on the transcript is the OLDER row (lower id), a v9 on the event is the NEWER one: the floor must skip the newest.
select pg_temp.mk_out('call_summary', 'ec35-ec35-a-q1fy27', 'doc',   'v11', array[10, 50]);
select pg_temp.mk_out('call_summary', 'ec35-ec35-a-q1fy27', 'event', 'v9',  array[60, 100]);
-- ec35-a Q2: only a v9. ec35-b: v100 (above the floor, below it as text). ec35-c: v10 (below the floor).
select pg_temp.mk_out('call_summary', 'ec35-ec35-a-q2fy27', 'doc', 'v9',   array[10, 50]);
select pg_temp.mk_out('call_summary', 'ec35-ec35-b-q2fy27', 'doc', 'v100', array[10, 50]);
select pg_temp.mk_out('call_summary', 'ec35-ec35-c-q2fy27', 'doc', 'v10',  array[10, 50]);
-- Other tasks, at v1: the floor must not touch them.
select pg_temp.mk_out('guidance_extraction', 'ec35-ec35-a-q1fy27', 'doc', 'v1', array[0, 43]);
select pg_temp.mk_out('doc_summary',         'ec35-ec35-a-q2fy27', 'doc', 'v1', array[0, 43]);
commit;
SQL
)"
check "G01 the test rows load (three companies, four calls, five call summaries, two outputs of other tasks)" \
  "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|UPDATE [0-9]+|COMMIT|[[:space:]]|[|])*$'

oid() { as_pg "select id from core.ai_output where body = '$1'"; }
A1V11=$(oid 'ec35-ec35-a-q1fy27 call_summary v11 doc')
A1V9=$(oid 'ec35-ec35-a-q1fy27 call_summary v9 event')
A2V9=$(oid 'ec35-ec35-a-q2fy27 call_summary v9 doc')
BV100=$(oid 'ec35-ec35-b-q2fy27 call_summary v100 doc')
CV10=$(oid 'ec35-ec35-c-q2fy27 call_summary v10 doc')
A1G1=$(oid 'ec35-ec35-a-q1fy27 guidance_extraction v1 doc')
A2DS=$(oid 'ec35-ec35-a-q2fy27 doc_summary v1 doc')
check "G02 the v9 on the event is the newest summary of ec35-a's first call (the case the floor has to see through)" \
  "$(as_pg "select ($A1V9 > $A1V11)::text")" '^true$'

SUM=ai.call_summary
FIG=ai.guidance_extraction
SHOWN="$(POL $SUM labelled); $(POL $FIG labelled)"
HID="$(POL $SUM hidden); $(POL $FIG hidden)"

# --- the function
check "F01 the floor reads v<n> with n >= 11 as a number, and fails closed on anything else (older, text-sorted, padded, unreadable, null)" \
  "$(as_role anon "select string_agg(coalesce(v, 'NULL') || '=' || core.call_summary_version_ok(v)::text, ',' order by n)
     from (values (1,'v1'),(2,'v9'),(3,'v10'),(4,'v11'),(5,'v12'),(6,'v100'),(7,'v999999999'),(8,'v011'),(9,'v0'),(10,'v1e3'),(11,'v11a'),
                  (12,'V11'),(13,'11'),(14,'v'),(15,''),(16,' v11'),(17,'v11 '),(18,'v-11'),(19,'v11.0'),(20,'vv11'),(21,'v9999999999'),
                  (22,'draft'),(23,null::text)) t(n, v)")" \
  '^v1=false,v9=false,v10=false,v11=true,v12=true,v100=true,v999999999=true,v011=false,v0=false,v1e3=false,v11a=false,V11=false,11=false,v=false,=false, v11=false,v11 =false,v-11=false,v11.0=false,vv11=false,v9999999999=false,draft=false,NULL=false$'
check "F02 anon, authenticated and service_role can run it; PUBLIC can't" \
  "$(as_pg "select has_function_privilege('anon', 'core.call_summary_version_ok(text)', 'execute')::text || ','
                || has_function_privilege('authenticated', 'core.call_summary_version_ok(text)', 'execute')::text || ','
                || has_function_privilege('service_role', 'core.call_summary_version_ok(text)', 'execute')::text || ','
                || (select not exists (select 1 from pg_proc p, unnest(p.proacl) a where p.oid = 'core.call_summary_version_ok(text)'::regprocedure and a::text like '=%'))::text")" \
  '^true,true,true,true$'
check "F03 the function says in its comment what it is for" \
  "$(as_pg "select (obj_description('core.call_summary_version_ok(text)'::regprocedure, 'pg_proc') like 'The call summary version floor: true only when%v<n> with n >= 11%fails closed%')::text")" '^true$'

# --- api.earnings_calls and api.earnings_latest
CALLS() { echo "select string_agg(fiscal_label || ':' || summary_state || ':' || coalesce(summary_output_id::text, '-'), ',' order by period_end) from api.earnings_calls where company_slug = '$1'"; }
check "C01 summary shown: the first call shows the v11 (not the newer v9); the second call, with only a v9, is not_generated with no id" \
  "$(flip anon "$SHOWN" "$(CALLS ec35-a)")" "^Q1 FY27:available:$A1V11,Q2 FY27:not_generated:-$"
check "C02 the call the page opens on is the first one, though the second is the newest: an older summary doesn't make a call available" \
  "$(flip anon "$SHOWN" "select string_agg(fiscal_label || ':' || is_newest::text || ':' || is_default::text, ',' order by period_end) from api.earnings_calls where company_slug = 'ec35-a'")" \
  '^Q1 FY27:false:true,Q2 FY27:true:false$'
check "C03 a v100 counts (the floor is numeric, not alphabetical)" \
  "$(flip anon "$SHOWN" "$(CALLS ec35-b)")" "^Q2 FY27:available:$BV100$"
check "C04 a v10, one below the floor, is not_generated with no id" \
  "$(flip anon "$SHOWN" "$(CALLS ec35-c)")" '^Q2 FY27:not_generated:-$'
check "C05 api.earnings_latest follows: ec35-a's newest call is not_generated while its default call is the first; ec35-b available; ec35-c not_generated (opens on its only stored call)" \
  "$(flip anon "$SHOWN" "select string_agg(company_slug || ':' || summary_state || ':' || coalesce(summary_output_id::text, '-') || ':' || default_fiscal_label, ',' order by company_slug) from api.earnings_latest where company_slug like 'ec35-%'")" \
  "^ec35-a:not_generated:-:Q1 FY27,ec35-b:available:$BV100:Q2 FY27,ec35-c:not_generated:-:Q2 FY27$"
check "C06 summary hidden: nothing changes from before the floor (a stored call is held_back, no id), whatever its version" \
  "$(flip anon "$HID" "$(CALLS ec35-a)")" '^Q1 FY27:held_back:-,Q2 FY27:held_back:-$'
check "C07 summary hidden: the other calls too, and api.earnings_latest" \
  "$(flip anon "$HID" "select (select string_agg(summary_state, ',' order by company_slug) from api.earnings_calls where company_slug in ('ec35-b','ec35-c')) || '|' || (select string_agg(summary_state, ',' order by company_slug) from api.earnings_latest where company_slug like 'ec35-%')")" \
  '^held_back,held_back[|]held_back,held_back,held_back$'
check "C08 the floor removes no call row: ec35-a still lists both calls" \
  "$(flip anon "$SHOWN" "select count(*) from api.earnings_calls where company_slug = 'ec35-a'")" '^2$'

# --- api.ai_outputs
check "O01 summary shown: api.ai_outputs gives the v11 and the v100, in id order, and no older summary" \
  "$(flip anon "$SHOWN" "select string_agg(body, ',' order by id) from api.ai_outputs where task = 'call_summary' and body like 'ec35-%'")" \
  '^ec35-ec35-a-q1fy27 call_summary v11 doc,ec35-ec35-b-q2fy27 call_summary v100 doc$'
check "O02 none of the v9, v9, v10 outputs is readable through it" \
  "$(flip anon "$SHOWN" "select count(*) from api.ai_outputs where id in ($A1V9, $A2V9, $CV10)")" '^0$'
check "O03 another task's outputs are unchanged: the guidance and doc_summary outputs at v1 are still there" \
  "$(flip anon "$SHOWN" "select count(*) from api.ai_outputs where id in ($A1G1, $A2DS)")" '^2$'
check "O04 summary hidden: no call summary through it (as before); doc_summary, whose field is labelled, is still there" \
  "$(flip anon "$HID" "select count(*) filter (where task = 'call_summary') || ',' || count(*) filter (where task = 'doc_summary') from api.ai_outputs where body like 'ec35-%'")" '^0,1$'
check "O05 api.ai_outputs has the columns it had" \
  "$(as_pg "select string_agg(a.attname, ',' order by a.attnum) from pg_attribute a where a.attrelid = 'api.ai_outputs'::regclass and a.attnum > 0 and not a.attisdropped")" \
  '^id,task,document_id,event_slug,entity_slug,scope,body,score,output_label,content,display_label,model,created_at$'

# --- api.ai_citations
check "Q01 the v11's citation comes back with its quote" \
  "$(flip anon "$SHOWN" "select count(*) || ',' || bool_and(quote is not null)::text from api.ai_citations where ai_output_id = $A1V11")" '^1,true$'
check "Q02 the older summaries' citations (v9, v9, v10) are gone; the v100's is there" \
  "$(flip anon "$SHOWN" "select (select count(*) from api.ai_citations where ai_output_id in ($A1V9, $A2V9, $CV10)) || ',' || (select count(*) from api.ai_citations where ai_output_id = $BV100)")" '^0,1$'
check "Q03 another task's citations are unchanged (guidance and doc_summary at v1)" \
  "$(flip anon "$SHOWN" "select count(*) from api.ai_citations where ai_output_id in ($A1G1, $A2DS)")" '^2$'
check "Q04 summary hidden: no call summary citation (as before); doc_summary's is there" \
  "$(flip anon "$HID" "select count(*) from api.ai_citations where ai_output_id in ($A1V11, $BV100, $A1G1, $A2DS)")" '^1$'
check "Q05 api.ai_citations has the columns it had" \
  "$(as_pg "select string_agg(a.attname, ',' order by a.attnum) from pg_attribute a where a.attrelid = 'api.ai_citations'::regclass and a.attnum > 0 and not a.attisdropped")" \
  '^ai_output_id,citation_no,claim_path,kind,document_id,document_title,source_url,char_start,char_end,quote,series_key,obs_date,event_slug,text_public$'

# --- api.citation_pages
check "P01 the v11's citation has its page row" \
  "$(flip anon "$SHOWN" "select count(*) || ',' || min(page_first) from api.citation_pages where ai_output_id = $A1V11")" '^1,1$'
check "P02 the older summaries' page rows (v9, v9, v10) are gone" \
  "$(flip anon "$SHOWN" "select count(*) from api.citation_pages where ai_output_id in ($A1V9, $A2V9, $CV10)")" '^0$'
check "P03 another task's page rows are unchanged" \
  "$(flip anon "$SHOWN" "select count(*) from api.citation_pages where ai_output_id in ($A1G1, $A2DS)")" '^2$'
check "P04 api.citation_pages has the columns it had" \
  "$(as_pg "select string_agg(a.attname, ',' order by a.attnum) from pg_attribute a where a.attrelid = 'api.citation_pages'::regclass and a.attnum > 0 and not a.attisdropped")" \
  '^ai_output_id,citation_no,page_first,page_last,page_url$'

# --- the redefined views keep their settings, grants and comments
VIEWS="'api.earnings_calls','api.ai_outputs','api.ai_citations','api.citation_pages'"
check "S01 the four redefined views are security_invoker" \
  "$(as_pg "select count(*) from unnest(array[$VIEWS]) v where (select c.reloptions @> array['security_invoker=true'] from pg_class c where c.oid = v::regclass)")" '^4$'
check "S02 anon, authenticated and service_role can still select all four" \
  "$(as_pg "select count(*) from unnest(array[$VIEWS]) v, unnest(array['anon','authenticated','service_role']) r where has_table_privilege(r, v::regclass, 'select')")" '^12$'
check "S03 the comments are the ones 0029 and 0032 set (text unchanged)" \
  "$(as_pg "select (obj_description('api.earnings_calls'::regclass) like 'One row per published earnings call: quarter, call date, status, official link, summary state%Never transcript text.')::text
                || (obj_description('api.citation_pages'::regclass) like 'Page of a verified transcript citation: first and last PDF page of its span%Never transcript text.')::text")" '^truetrue$'
check "S04 api.earnings_calls has its 26 columns, ending at is_default, as before (api.earnings_latest is not redefined)" \
  "$(as_pg "select count(*) || ',' || (array_agg(a.attname order by a.attnum desc))[1] from pg_attribute a where a.attrelid = 'api.earnings_calls'::regclass and a.attnum > 0 and not a.attisdropped")" '^26,is_default$'

# --- no stored row changed: every output is still published, with the version it had
check "R01 the floor changed no stored output: the five call summaries are still published, with their versions" \
  "$(as_pg "select string_agg(prompt_version || ':' || status, ',' order by id) from core.ai_output where task = 'call_summary' and body like 'ec35-%'")" \
  '^v11:published,v9:published,v9:published,v100:published,v10:published$'

cleanup
echo; echo "passed $pass, failed $fail"
