#!/bin/bash
# Tests for 0023 (earnings calls, 2B-lite; issue #168, Spec 02B §3.1–§3.5, §13.4–§13.6). Run after the other scripts on
# the same fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
# First §3.2's list in full (the private-text core addition), then the tables. The script creates its own rows (named
# ec23-*), checks through the roles and removes every row it made. C01 builds a second, throwaway database to compare
# module 1's rows before and after the migration, and drops it.
HERE="$(cd "$(dirname "$0")" && pwd)"
DB=${PGDATABASE:-ft}
CONN="-h ${PGHOST:-localhost} -p ${PGPORT:-5432}"
H="$CONN -d $DB -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
login()   { psql $H -U "$1" -c "$2" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

############################################################ C01: module 1's rows before and after the migration
# A second database gets every migration before this one, the fixture, a snapshot of what anon and authenticated read from
# api.ai_citations (0006's columns) and api.document_chunks, then this migration and the same snapshot again.
# Roles are cluster-wide and already made by run_all.sh, so the role-creating lines are left out there.
PRE="${DB}_pre0023"
MIG="$(ls "$HERE"/../migrations/*_earnings_calls.sql)"
P="psql $CONN -U postgres -X -A -t -q -v ON_ERROR_STOP=1"
snapshot() {
  for r in anon authenticated; do
    $P -d "$PRE" -c "set role $r; select ai_output_id, citation_no, claim_path, kind, document_id, document_title, source_url,
                       char_start, char_end, quote, series_key, obs_date, event_slug
                     from api.ai_citations order by ai_output_id, citation_no" 2>&1 | grep -v '^SET$'
    $P -d "$PRE" -c "set role $r; select document_id, chunk_no, char_start, char_end, section, speaker, body
                     from api.document_chunks order by document_id, chunk_no" 2>&1 | grep -v '^SET$'
  done
}
pre_build() {
  $P -d postgres -c "drop database if exists $PRE" -c "create database $PRE" >/dev/null 2>&1 || return 1
  sed -E '/^create role /d' "$HERE/mock_supabase_roles.sql" | $P -d "$PRE" -f - >/dev/null 2>&1 || return 1
  for f in "$HERE"/../migrations/*.sql; do
    [[ "$f" < "$MIG" ]] || break
    sed -E '/^create role /d' "$f" | $P -d "$PRE" -f - 2>&1 | grep -v NOTICE
    [ "${PIPESTATUS[1]}" -eq 0 ] || { echo "pre-migration failed: $f"; return 1; }
  done
  $P -d "$PRE" -f "$HERE/test_fixture.sql" >/dev/null || return 1
}
if pre_build; then
  BEFORE="$(snapshot)"
  $P -d "$PRE" -f "$MIG" 2>&1 | grep -v NOTICE
  AFTER="$(snapshot)"
  ROWS="$(echo "$BEFORE" | grep -c '|')"
  [ "$BEFORE" = "$AFTER" ] && same="identical ($ROWS rows)" || same="differ: $(diff <(echo "$BEFORE") <(echo "$AFTER") | head -5)"
else
  same="the pre-migration database could not be built"
fi
$P -d postgres -c "drop database if exists $PRE" >/dev/null 2>&1
check "C01 module 1's api.ai_citations and api.document_chunks rows are identical before and after the migration (anon, authenticated)" \
  "$same" '^identical \([1-9][0-9]* rows\)$'

############################################################ the rows the checks use
cleanup() {
  as_pg "delete from equity.guidance; delete from equity.stated_figure; delete from equity.call_turn;
         delete from equity.transcript_page; delete from equity.earnings_call;
         delete from core.ai_output where task in ('call_summary','guidance_extraction');
         delete from core.chunk_embedding where document_id in (select id from core.document where external_key like 'ec23%');
         delete from core.document where external_key like 'ec23%';
         delete from core.event where slug like 'ec23%';
         delete from core.entity where slug like 'ec23%';
         update core.field_policy set status = 'hidden' where field_key in ('ai.call_summary','ai.guidance_extraction');
         update core.source set is_suppressed = false where key in ('nse','company_web');" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind

# Before anything flips them: the catalogue rows and the two field policies as seeded.
check "C02 both field policies are seeded hidden, with their labels" \
  "$(as_pg "select string_agg(field_key||':'||status||':'||label, ',' order by field_key) from core.field_policy where field_key in ('ai.call_summary','ai.guidance_extraction')")" \
  '^ai\.call_summary:hidden:AI summary,ai\.guidance_extraction:hidden:AI-extracted$'
check "C03 doc types: transcripts and cover letters have private text and aren't diffable; every other type keeps public text" \
  "$(as_pg "select string_agg(key||':'||text_public||':'||diffable, ',' order by key) filter (where not text_public) from core.doc_type")" \
  '^call_cover_letter:false:false,call_transcript:false:false$'
check "C04 source company_web: official, no raw export, not suppressed, with its attribution line" \
  "$(as_pg "select is_official||','||allow_raw_export||','||is_suppressed||','||attribution from core.source where key='company_web'")" \
  "^true,false,false,Source: the company's website, linked from its exchange filing$"
check "C05 catalogue: event type earnings_call with its three statuses (stored final), and the two AI tasks with citations" \
  "$(as_pg "select (select string_agg(status||':'||is_final, ',' order by status) from core.event_status where event_type='earnings_call') || '|' ||
                   (select string_agg(key||':'||output_kind||':'||requires_citations, ',' order by key) from core.ai_task where key in ('call_summary','guidance_extraction'))")" \
  '^on_company_website:false,stored:true,unreadable:false\|call_summary:text:true,guidance_extraction:structured:true$'

# The rows, written as job_writer (proves its grants and policies are enough). The transcript text runs past 400 code
# points (15 × 43 = 645) and holds the rupee sign (3 bytes in UTF-8), so the cap is checked in code points, not bytes.
SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
set role job_writer;
insert into core.entity (kind, slug, name, market, currency, is_published) values
  ('company', 'ec23-co', 'Ec23 Example Ltd', 'IN', 'INR', true);
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
select 'earnings_call', e.id, v.slug, v.slug, v.d::date, 'stored', (select id from core.source where key = 'nse'), v.pub
from core.entity e, (values ('ec23-co-call-q2-fy27', '2026-10-20', true), ('ec23-co-call-q1-fy27', '2026-07-20', false)) v(slug, d, pub)
where e.slug = 'ec23-co';
-- Two transcripts: the published one (q2) and an unpublished one (q1). Text: 15 × 43 = 645 code points.
insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                           char_count, text_sha256, chunked_at, is_published)
select 'call_transcript', ev.entity_id, ev.id, 'Ec23 call ' || ev.slug, (select id from core.source where key = 'company_web'),
       'ec23-' || ev.slug, 'https://example.com/ec23/' || ev.slug || '.pdf', now(),
       char_length(t.full), sha256(convert_to(t.full, 'UTF8')), now(), ev.is_published
from core.event ev, (select repeat('Revenue grew to ₹1,200 crore this quarter. ', 15) as full) t
where ev.slug like 'ec23-co-call-%';
insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
select d.id, x.n, x.s, x.e, 'Q&A', 'CFO', substr(t.full, x.s + 1, x.e - x.s)
from core.document d, (select repeat('Revenue grew to ₹1,200 crore this quarter. ', 15) as full) t,
     (values (0, 0, 300), (1, 300, 645)) x(n, s, e)
where d.external_key like 'ec23-%';
insert into core.chunk_embedding (document_id, chunk_no, model, embedding)
select c.document_id, c.chunk_no, 'voyage-4-lite@512', array_fill(0.01::real, array[512])::extensions.halfvec(512)
from core.chunk c join core.document d on d.id = c.document_id where d.external_key like 'ec23-%';
-- Outputs: a published summary with five citations (1 the whole text, 2 unverified, 3 into module 1's public statement,
-- 4 into the unpublished transcript, 5 an event); a draft summary; a published figures output.
insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
select v.task, d.id, '{}', v.body, 'draft', 'claude-haiku-5-5', v.task, 'v1', sha256(convert_to(v.body, 'UTF8'))
from core.document d, (values ('call_summary', 'ec23 summary'), ('call_summary', 'ec23 draft'), ('guidance_extraction', 'ec23 figures')) v(task, body)
where d.external_key = 'ec23-ec23-co-call-q2-fy27';
insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, event_id, verified)
select o.id, v.n, '/bullets/' || v.n, v.kind,
       case v.doc when 'priv' then dp.id when 'pub' then (select id from core.document where doc_type = 'fomc_statement')
                  when 'unpub' then du.id end,
       v.s, v.e, case when v.kind = 'event' then dp.event_id end, v.ok
from core.ai_output o,
     (select id, event_id from core.document where external_key = 'ec23-ec23-co-call-q2-fy27') dp,
     (select id from core.document where external_key = 'ec23-ec23-co-call-q1-fy27') du,
     (values (1, 'document_span', 'priv', 0, 645, true), (2, 'document_span', 'priv', 10, 50, false),
             (3, 'document_span', 'pub', 4, 30, true), (4, 'document_span', 'unpub', 0, 40, true),
             (5, 'event', null, null, null, true)) v(n, kind, doc, s, e, ok)
where o.body = 'ec23 summary';
insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
select o.id, 1, '/x', 'document_span', d.id, 0, 43, true
from core.ai_output o, core.document d where o.body in ('ec23 draft', 'ec23 figures') and d.external_key = 'ec23-ec23-co-call-q2-fy27';
update core.ai_output set status = 'published' where body in ('ec23 summary', 'ec23 figures');
-- The equity rows.
insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange,
                                  route, transcript_document_id, words)
select ev.id, ev.entity_id, upper(replace(replace(substr(ev.slug, 14), '-', ' '), 'fy', 'FY')),
       case when ev.slug like '%q2%' then date '2026-09-30' else date '2026-06-30' end,
       substr(ev.slug, 14), 'results_link', ev.starts_on + 1, 'NSE', 'company_link', d.id, 105
from core.event ev join core.document d on d.event_id = ev.id where ev.slug like 'ec23-co-call-%';
insert into equity.call_turn (document_id, turn_no, char_start, char_end, speaker, role, section, words)
select d.id, 0, 0, 645, 'CFO', 'management', 'qa', 105 from core.document d where d.external_key like 'ec23-%';
insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
select d.id, p.n, p.s, p.e from core.document d, (values (1, 0, 300), (2, 300, 645)) p(n, s, e) where d.external_key like 'ec23-%';
insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, low, high, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date)
select c.event_id, c.entity_id, o.id, 1, d.id, 0, 43, 'revenue_growth', 'Revenue growth', 'revenue growth', 'range', 10, 12,
       'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', '2026-10-20'
from equity.earnings_call c join core.document d on d.id = c.transcript_document_id
join core.ai_output o on o.document_id = d.id and o.body = 'ec23 figures' where c.fiscal_label = 'Q2 FY27';
insert into equity.stated_figure (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                                  metric_label, metric_text, value, unit, period_text, period_kind, period_start, period_end, basis)
select c.event_id, c.entity_id, o.id, 1, d.id, 0, 43, 'revenue', 'Revenue', 'revenue', 12000000000, 'inr', 'Q2 FY27', 'quarter',
       '2026-07-01', '2026-09-30', 'consolidated'
from equity.earnings_call c join core.document d on d.id = c.transcript_document_id
join core.ai_output o on o.document_id = d.id and o.body = 'ec23 figures' where c.fiscal_label = 'Q2 FY27';
reset role;
commit;
SQL
)"
check "C06 job_writer writes calls, turns, pages, targets and stated figures, with private-text documents and their outputs" \
  "$SETUP_OUT" '^(BEGIN|SET|INSERT 0 [0-9]+|UPDATE 2|RESET|COMMIT|[[:space:]])*$'

# Ids as literals (read as postgres), so a role that can't see a row still gets its id: a subquery would hand it null.
V=$(as_pg "select id from core.document where external_key = 'ec23-ec23-co-call-q2-fy27'")   # the published transcript
U=$(as_pg "select id from core.document where external_key = 'ec23-ec23-co-call-q1-fy27'")   # the unpublished one
S=$(as_pg "select id from core.ai_output where body = 'ec23 summary'")                        # published summary
D=$(as_pg "select id from core.ai_output where body = 'ec23 draft'")                          # draft summary
F=$(as_pg "select id from core.ai_output where body = 'ec23 figures'")                        # published figures
FULL="repeat('Revenue grew to ₹1,200 crore this quarter. ', 15)"
release() { as_pg "update core.field_policy set status = '$2' where field_key = 'ai.$1'" >/dev/null; }

############################################################ §3.2: private text
check "C07 a private-text document's chunks are invisible to anon, authenticated and semantic_owner (base table and api view)" \
  "$(for r in anon authenticated semantic_owner; do as_role $r "select count(*) from core.chunk where document_id = $V"; done
     for r in anon authenticated; do as_role $r "select count(*) from api.document_chunks where document_id = $V"; done | tr '\n' ' ')" \
  '^0.0.0.0 0 $'
check "C08 ...while its document row stays visible (base table and api.documents), and module 1's chunks still show" \
  "$(as_role anon "select (select count(*) from core.document where id = $V) || ',' || (select count(*) from api.documents where id = $V) || ',' ||
                          (select count(*) from api.document_chunks where document_id = (select id from core.document where doc_type = 'fomc_statement'))")" \
  '^1,1,2$'

release call_summary labelled
check "C09 its citation's quote comes back through api.ai_citations, capped at 400 code points (the text is 645), with text_public false" \
  "$(as_role anon "select char_length(quote) || ',' || (quote = left($FULL, 400)) || ',' || octet_length(quote) || ',' || text_public
                   from api.ai_citations where ai_output_id = $S and citation_no = 1")" \
  '^400,true,(4[0-9][0-9]),false$'
check "C10 a public-text citation of the same output keeps the quote rebuilt from its chunks, with text_public true" \
  "$(as_role anon "select quote || '|' || text_public from api.ai_citations where ai_output_id = $S and citation_no = 3")" \
  '^Committee decided to lower\|true$'
check "C11 core.citation_quote gives the cited span itself (anon, a short span)" \
  "$(release guidance_extraction labelled; as_role anon "select core.citation_quote($F, 1::smallint)"; release guidance_extraction hidden)" \
  "^Revenue grew to ₹1,200 crore this quarter\. $"
check "C12 no quote for an unverified citation, a citation into an unpublished document, an event citation, or a draft output" \
  "$(as_role anon "select coalesce(core.citation_quote($S, 2::smallint), 'null') || ',' || coalesce(core.citation_quote($S, 4::smallint), 'null') || ',' ||
                          coalesce(core.citation_quote($S, 5::smallint), 'null') || ',' || coalesce(core.citation_quote($D, 1::smallint), 'null') || ',' ||
                          (select count(*) from api.ai_citations where ai_output_id = $S and citation_no = 2) || ',' ||
                          coalesce((select quote from api.ai_citations where ai_output_id = $S and citation_no = 4), 'null')")" \
  '^null,null,null,null,0,null$'
check "C13 no quote while the field policy is hidden (the function, and no citation row in the view)" \
  "$(release call_summary hidden; as_role anon "select coalesce(core.citation_quote($S, 1::smallint), 'null') || ',' || (select count(*) from api.ai_citations where ai_output_id = $S)"; release call_summary labelled)" \
  '^null,0$'
check "C14 no quote while the document's source is suppressed" \
  "$(as_pg "update core.source set is_suppressed = true where key = 'company_web'" >/dev/null
     as_role anon "select coalesce(core.citation_quote($S, 1::smallint), 'null') || ',' || (select count(*) from api.ai_citations where ai_output_id = $S)"
     as_pg "update core.source set is_suppressed = false where key = 'company_web'" >/dev/null)" \
  '^null,0$'
check "C15 retrieval_owner still sees only published documents (A12), owns no table and can't bypass RLS (A11), and reads the private chunks" \
  "$(as_role retrieval_owner "select count(*) filter (where not is_published) || ',' || count(*) filter (where id = $V) from core.document"
     as_pg "select rolbypassrls::text || ',' || (select count(*) from pg_class where relowner = r.oid and relkind = 'r') from pg_roles r where rolname = 'retrieval_owner'"
     as_role retrieval_owner "select count(*) from core.chunk where document_id = $V")" \
  '^0,1.false,0.2$'
check "C16 retrieval_owner sees no draft output, no unverified citation and no hidden field policy (its new policies)" \
  "$(as_role retrieval_owner "select (select count(*) from core.ai_output where status <> 'published') || ',' ||
                                     (select count(*) from core.ai_citation where not verified or kind <> 'document_span') || ',' ||
                                     (select count(*) from core.field_policy where status = 'hidden')")" \
  '^0,0,0$'
Q="(select array_fill(0.01::real, array[512])::extensions.halfvec(512))"
check "C17 the command bar's server-side retrieval still finds transcript chunks (semantic.search_chunks unchanged), published ones only" \
  "$(login sql_reader "select count(*) || ',' || count(*) filter (where document_id = $V) from semantic.search_chunks($Q, 'revenue crore', null, '{call_transcript}')")" \
  '^2,2$'
check "C18 core.citation_quote: security definer, owned by retrieval_owner, search_path pinned" \
  "$(as_pg "select prosecdef || ':' || pg_get_userbyid(proowner) || ':' || array_to_string(proconfig, ';') from pg_proc where oid = 'core.citation_quote(bigint,smallint)'::regprocedure")" \
  '^true:retrieval_owner:search_path=pg_catalog, core$'
check "C19 core.citation_quote is executable by anon, authenticated, semantic_owner and service_role only: not sql_reader, job_writer or PUBLIC" \
  "$(as_pg "select string_agg(r, ',' order by r) from unnest(array['anon','authenticated','semantic_owner','service_role','sql_reader','job_writer']) r
            where has_function_privilege(r, 'core.citation_quote(bigint,smallint)'::regprocedure, 'execute')
            union all select count(*)::text from pg_proc p, aclexplode(p.proacl) a where p.oid = 'core.citation_quote(bigint,smallint)'::regprocedure and a.grantee = 0" | tr '\n' ' ')" \
  '^anon,authenticated,semantic_owner,service_role 0 $'
check "C20 sql_reader and job_writer get permission denied calling it" \
  "$(login sql_reader "select core.citation_quote(1, 1::smallint)"; as_role job_writer "select core.citation_quote(1, 1::smallint)")" \
  'permission denied for schema core.*permission denied for function citation_quote'
check "C21 service_role still reads api.ai_citations (0006 grants it)" \
  "$(as_role service_role "select count(*) from api.ai_citations where ai_output_id = $S")" '^4$'
check "C22 job_writer can't make a doc type's text public (catalogues are migrations only)" \
  "$(as_role job_writer "update core.doc_type set text_public = true where key = 'call_transcript'")" 'permission denied for table doc_type'

############################################################ the tables
check "C23 RLS is on for the five new tables, each with its job_writer_all policy" \
  "$(as_pg "select count(*) filter (where c.relrowsecurity and exists (select 1 from pg_policies p where p.schemaname = 'equity' and p.tablename = c.relname
                                       and p.policyname = 'job_writer_all' and p.roles = '{job_writer}')) || '/' || count(*)
            from pg_class c where c.relnamespace = 'equity'::regnamespace and c.relname in ('earnings_call','call_turn','guidance','stated_figure','transcript_page')")" \
  '^5/5$'
check "C24 a call is visible with its event and company: the published call only, none once the exchange's source is suppressed" \
  "$(as_role anon "select string_agg(fiscal_label, ',') from equity.earnings_call"
     as_pg "update core.source set is_suppressed = true where key = 'nse'" >/dev/null
     as_role anon "select count(*) from equity.earnings_call"
     as_pg "update core.source set is_suppressed = false where key = 'nse'" >/dev/null)" \
  '^Q2 FY27.0$'
check "C25 call_turn is invisible to visitors and the semantic layer (no grant, no policy)" \
  "$(for r in anon authenticated semantic_owner; do as_role $r "select count(*) from equity.call_turn"; done)" \
  '^(ERROR:  permission denied for table call_turn[[:space:]]*){3}$'
check "C26 targets and stated figures stay hidden while ai.guidance_extraction is hidden" \
  "$(for r in anon authenticated semantic_owner; do as_role $r "select (select count(*) from equity.guidance) || ',' || (select count(*) from equity.stated_figure)"; done | tr '\n' ' ')" \
  '^0,0 0,0 0,0 $'
release guidance_extraction labelled
check "C27 ...show once it's labelled, and disappear with the call (the exchange's source suppressed)" \
  "$(as_role anon "select (select count(*) from equity.guidance) || ',' || (select count(*) from equity.stated_figure)"
     as_pg "update core.source set is_suppressed = true where key = 'nse'" >/dev/null
     as_role anon "select (select count(*) from equity.guidance) || ',' || (select count(*) from equity.stated_figure)"
     as_pg "update core.source set is_suppressed = false where key = 'nse'" >/dev/null)" \
  '^1,1.0,0$'
check "C28 page rows are visible with their document: the published transcript's 2, none once its source is suppressed" \
  "$(as_role anon "select count(*) || ',' || count(*) filter (where document_id = $V) from equity.transcript_page"
     as_pg "update core.source set is_suppressed = true where key = 'company_web'" >/dev/null
     as_role anon "select count(*) from equity.transcript_page"
     as_pg "update core.source set is_suppressed = false where key = 'company_web'" >/dev/null)" \
  '^2,2.0$'
check "C29 job_writer may delete only on the D tables (turns, targets, stated figures)" \
  "$(as_pg "begin; set role job_writer;
            with x as (delete from equity.call_turn returning 1) select count(*) from x;
            with x as (delete from equity.guidance returning 1) select count(*) from x;
            with x as (delete from equity.stated_figure returning 1) select count(*) from x; rollback;" | tr '\n' ' '
     as_role job_writer "delete from equity.earnings_call"; as_role job_writer "delete from equity.transcript_page")" \
  '^2 1 1 .*permission denied for table earnings_call.*permission denied for table transcript_page'
check "C30 job_writer can't update page rows (written once)" \
  "$(as_role job_writer "update equity.transcript_page set char_end = char_end")" 'permission denied for table transcript_page'
check "C31 page rows: pdf_page >= 1, char_start >= 0, char_end > char_start, one row per start" \
  "$(for v in "0, 900, 950" "1001, -1, 5" "1002, 907, 907" "1003, 0, 9" "1, 1000, 1001"; do
       as_role job_writer "insert into equity.transcript_page (document_id, pdf_page, char_start, char_end) values ($U, $v)"; done)" \
  'transcript_page_pdf_page_check.*transcript_page_char_start_check.*transcript_page_check.*transcript_page_document_id_char_start_key.*transcript_page_pkey'
check "C32 one call per company per fiscal quarter" \
  "$(as_role job_writer "with ev as (insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id)
                                       select 'earnings_call', id, 'ec23-co-call-q2-fy27-again', 'x', '2026-10-25', 'stored',
                                              (select id from core.source where key = 'bse')
                                       from core.entity where slug = 'ec23-co' returning id, entity_id)
                          insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange, route)
                          select id, entity_id, 'Q2 FY27', '2026-09-30', 'q2-fy27', 'text', now(), 'BSE', 'none' from ev")" \
  'duplicate key value violates unique constraint "earnings_call_entity_id_fiscal_label_key"'
check "C33 a stated figure needs a map key ('other' isn't kept), and its period" \
  "$(as_role job_writer "update equity.stated_figure set metric = 'other'"; as_role job_writer "update equity.stated_figure set period_end = null")" \
  'stated_figure_metric_check.*null value in column "period_end"'
check "C34 a target's period kind is undated exactly when it has no dates" \
  "$(as_role job_writer "update equity.guidance set period_kind = 'undated'"; as_role job_writer "update equity.guidance set target_start = null, target_end = null")" \
  'guidance_period_dates.*guidance_period_dates'

cleanup
echo; echo "passed $pass, failed $fail"
