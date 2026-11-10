#!/bin/bash
# Tests for 0029 (earnings views; issue #181, job 2B-18; Spec 02B §13.6, §13.3, §13.4, §3.1; columns of Spec 02B.1 C9). Run after
# the other scripts on the same fresh database. The script creates its own rows (companies ec29-a, ec29-b, ec29-c; two calls of
# ec29-a with page rows on the first only; an on_company_website call of ec29-b; ec29-c has none), checks through the roles and
# removes every row it made. Gate flips and suppression happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
POL()     { echo "update core.field_policy set status = '$2' where field_key = '$1'"; }

cleanup() {
  as_pg "delete from equity.guidance_outcome; delete from equity.stated_figure where metric like 'ec29%';
         delete from equity.guidance where metric like 'ec29%';
         delete from equity.transcript_page where document_id in (select id from core.document where external_key like 'ec29%');
         delete from equity.earnings_call where entity_id in (select id from core.entity where slug like 'ec29%');
         delete from core.ai_output where document_id in (select id from core.document where external_key like 'ec29%');
         delete from core.chunk where document_id in (select id from core.document where external_key like 'ec29%');
         delete from core.document where external_key like 'ec29%';
         delete from core.event where slug like 'ec29%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'ec29%');
         delete from core.entity where slug like 'ec29%';
         update core.field_policy set status = 'hidden' where field_key in ('ai.call_summary','ai.guidance_extraction');
         update core.source set is_suppressed = false where key in ('nse','company_web');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
create function pg_temp.mk_call(p_ent text, p_label text, p_period date, p_filed timestamptz, p_share numeric, p_pages boolean)
returns void language plpgsql as $f$
declare txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
        eid integer; evid bigint; did bigint; tag text := lower(p_ent || '-' || replace(p_label, ' ', ''));
begin
  select id into eid from core.entity where slug = p_ent;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec29-call-' || tag, 'ec29', p_period, 'stored',
            (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'ec29 call ' || tag, (select id from core.source where key = 'company_web'),
            'ec29-' || tag, 'https://example.com/ec29/' || tag || '.pdf#page=3', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
    values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at,
                                    exchange, route, transcript_document_id, words, unknown_role_share)
    values (evid, eid, p_label, p_period, lower(replace(p_label, ' ', '-')), 'results_link', p_filed, 'NSE',
            'company_link', did, 105, p_share);
  if p_pages then
    insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
      values (did, 1, 0, 300), (did, 2, 300, 645);
  end if;
end $f$;

create function pg_temp.mk_out(p_task text, p_ext text, p_spans int[]) returns void language plpgsql as $f$
declare did bigint; oid bigint; i int;
begin
  select id into did from core.document where external_key = p_ext;
  insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
    values (p_task, did, jsonb_build_object(), 'ec29 output', 'draft', 'claude-haiku-5-5', p_task, case when p_task = 'call_summary' then 'v11' else 'v1' end,   -- 0035's floor counts a call_summary only from v11
            sha256(convert_to('ec29' || p_task || p_ext, 'UTF8'))) returning id into oid;
  for i in 1 .. array_length(p_spans, 1) / 2 loop
    insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
      values (oid, i, '/x' || i, 'document_span', did, p_spans[2 * i - 1], p_spans[2 * i], true);
  end loop;
  update core.ai_output set status = 'published' where id = oid;
end $f$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec29-a', 'Ec29 A Ltd', 'ec29-a', 'IN', 'INR', true),
         ('company', 'ec29-b', 'Ec29 B Ltd', 'ec29-b', 'IN', 'INR', true),
         ('company', 'ec29-c', 'Ec29 C Ltd', 'ec29-c', 'IN', 'INR', true);
insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000290' || (ascii(right(slug, 1)) - 96), 'EC29' || upper(right(slug, 1)), '529' || (ascii(right(slug, 1)) - 96)
  from core.entity where slug like 'ec29-%';
select pg_temp.mk_call('ec29-a', 'Q1 FY27', '2026-06-30', '2026-07-25', 0.10, true);
select pg_temp.mk_call('ec29-a', 'Q2 FY27', '2026-09-30', '2026-10-25', 0.1001, false);
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
  select 'earnings_call', id, 'ec29-call-b-q2', 'ec29', '2026-09-30', 'on_company_website',
         (select id from core.source where key = 'nse'), true from core.entity where slug = 'ec29-b';
insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at, exchange,
                                  route, company_url)
  select ev.id, ev.entity_id, 'Q2 FY27', '2026-09-30', 'q2-fy27', 'results_link', '2026-10-26', 'NSE', 'none',
         'https://example.com/ec29/b-ir'
  from core.event ev where ev.slug = 'ec29-call-b-q2';
select pg_temp.mk_out('call_summary', 'ec29-ec29-a-q1fy27', array[10, 50]);
select pg_temp.mk_out('guidance_extraction', 'ec29-ec29-a-q1fy27', array[0, 43, 290, 310, 300, 320]);
select pg_temp.mk_out('guidance_extraction', 'ec29-ec29-a-q2fy27', array[0, 43]);

insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date, speaker_role)
select ec.event_id, ec.entity_id, o.id, v.cit, ec.transcript_document_id, v.cs, v.ce, v.metric, 'Label ' || v.metric, v.metric,
       'point', 10, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', '2026-10-20', v.role
from (values ('ec29-a', 'Q1 FY27', 'ec29_m',   1, 0,   43,  'management'),
             ('ec29-a', 'Q1 FY27', 'ec29_u',   2, 290, 310, 'unknown'),
             ('ec29-a', 'Q1 FY27', 'ec29_dup', 1, 0,   43,  'management'),
             ('ec29-a', 'Q2 FY27', 'ec29_u2',  1, 0,   43,  'unknown')) v(slug, label, metric, cit, cs, ce, role)
join core.entity e on e.slug = v.slug
join equity.earnings_call ec on ec.entity_id = e.id and ec.fiscal_label = v.label
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
update equity.guidance set duplicate_of = (select id from equity.guidance where metric = 'ec29_m') where metric = 'ec29_dup';
insert into equity.stated_figure (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                                  metric_label, metric_text, value, unit, period_text, period_kind, period_start, period_end,
                                  basis, growth_basis)
select ec.event_id, ec.entity_id, o.id, 3, ec.transcript_document_id, 300, 320, 'ec29_s', 'Label ec29_s', 'ec29_s', 12, 'pct',
       'Q1 FY27', 'quarter', '2026-04-01', '2026-06-30', 'consolidated', 'yoy'
from equity.earnings_call ec
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction'
where ec.fiscal_label = 'Q1 FY27' and ec.entity_id = (select id from core.entity where slug = 'ec29-a');
commit;
SQL
)"
check "G01 the test rows load (three companies, three calls, three outputs, four targets, one stated figure)" \
  "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|UPDATE [0-9]+|COMMIT|[[:space:]]|[|])*$'

SUM=ai.call_summary
FIG=ai.guidance_extraction
SHOWN="$(POL $SUM shown); $(POL $FIG labelled)"
HID="$(POL $SUM hidden); $(POL $FIG hidden)"
SUMONLY="$(POL $SUM labelled); $(POL $FIG hidden)"
FIGONLY="$(POL $SUM hidden); $(POL $FIG labelled)"
CALLS="select string_agg(fiscal_label || ':' || summary_state || ':' || coalesce(summary_output_id::text, '-') || ':' || coalesce(target_count::text, '-'), ',' order by period_end) from api.earnings_calls where company_slug = 'ec29-a'"
OUTIDS="$(as_pg "select string_agg(id::text, ',') from core.ai_output where document_id in (select id from core.document where external_key like 'ec29%')")"

# --- gates and summary_state
check "C01 both fields hidden: a stored call without a visible summary is held_back, and no id or count shows" \
  "$(flip anon "$HID" "$CALLS")" '^Q1 FY27:held_back:-:-,Q2 FY27:held_back:-:-$'
check "C02 both shown or labelled: a published summary is available; a stored call without one is not_generated; counts show" \
  "$(flip anon "$SHOWN" "$CALLS")" '^Q1 FY27:available:[0-9]+:1,Q2 FY27:not_generated:-:0$'
check "C03 summary shown, figures hidden: the summary is available and the counts are null" \
  "$(flip anon "$SUMONLY" "$CALLS")" '^Q1 FY27:available:[0-9]+:-,Q2 FY27:not_generated:-:-$'
check "C04 figures labelled, summary hidden: no summary id, the counts show" \
  "$(flip anon "$FIGONLY" "$CALLS")" '^Q1 FY27:held_back:-:1,Q2 FY27:held_back:-:0$'
check "C05 an on_company_website call has no summary to hold back: not_generated, with the company's link" \
  "$(flip anon "$HID" "select summary_state || '|' || status || '|' || official_url from api.earnings_calls where company_slug = 'ec29-b'")" \
  '^not_generated[|]on_company_website[|]https://example.com/ec29/b-ir$'
check "C06 a stored call's official link is the transcript's own source_url, fragment and all" \
  "$(flip anon "$HID" "select official_url from api.earnings_calls where call_slug = 'ec29-call-ec29-a-q1fy27'")" \
  '^https://example.com/ec29/ec29-a-q1fy27[.]pdf#page=3$'
check "C07 counts: figure_ids and the three counts per call (management targets, stated figures, unattributed; duplicates out)" \
  "$(flip anon "$SHOWN" "select string_agg(fiscal_label || ':' || target_count || ':' || stated_figure_count || ':' || unattributed_targets, ',' order by period_end) from api.earnings_calls where company_slug = 'ec29-a'")" \
  '^Q1 FY27:1:1:1,Q2 FY27:0:0:1$'

# --- the role-held threshold
check "C08 roles_partial: 0.10 is false, 0.1001 is true, a call with no share is null (strictly greater than 0.10)" \
  "$(as_role anon "select string_agg(fiscal_label || ':' || coalesce(unknown_role_share::text, 'null') || ':' || roles_partial::text, ',' order by period_end) from api.earnings_calls where company_slug = 'ec29-a'")" \
  '^Q1 FY27:0[.]10:false,Q2 FY27:0[.]1001:true$'
check "C09 roles_partial is null while the share is null" \
  "$(as_role anon "select coalesce(roles_partial::text, 'null') from api.earnings_calls where company_slug = 'ec29-b'")" '^null$'

# --- is_default, is_newest, is_latest_stored (Spec 02B §13.3)
FLAGS="select string_agg(fiscal_label || ':' || is_newest::int::text || is_latest_stored::int::text || is_default::int::text, ',' order by period_end) from api.earnings_calls where company_slug = 'ec29-a'"
check "D01 a newer stored call without a summary is not the default: the newest call with a summary is" \
  "$(flip anon "$SHOWN" "$FLAGS")" '^Q1 FY27:001,Q2 FY27:110$'
check "D02 no summary available (hidden): the default is the newest stored call" \
  "$(flip anon "$HID" "$FLAGS")" '^Q1 FY27:000,Q2 FY27:111$'
check "D03 a company with only an on_company_website call: that call is newest and default, not latest stored" \
  "$(as_role anon "select is_newest::int::text || is_latest_stored::int::text || is_default::int::text from api.earnings_calls where company_slug = 'ec29-b'")" '^101$'

# --- earnings_latest
check "L01 one row per universe company, none_found included: the counts match api.companies" \
  "$(as_role anon "select ((select count(*) from api.earnings_latest) = (select count(*) from api.companies))::text")" '^true$'
check "L02 a company with no call is none_found, with the window covered as data" \
  "$(as_role anon "select status || '|' || coalesce(call_slug, '-') || '|' || (window_from <= date '2026-07-25')::text || '|' || (window_to >= date '2026-10-26')::text from api.earnings_latest where company_slug = 'ec29-c'")" \
  '^none_found[|]-[|]true[|]true$'
check "L03 a company with a call: newest call's columns, the default call's label, no window" \
  "$(flip anon "$SHOWN" "select status || '|' || fiscal_label || '|' || default_fiscal_label || '|' || default_call_slug || '|' || roles_partial::text || '|' || coalesce(window_from::text, '-') from api.earnings_latest where company_slug = 'ec29-a'")" \
  '^stored[|]Q2 FY27[|]Q1 FY27[|]ec29-call-ec29-a-q1fy27[|]false[|]-$'
check "L04 unattributed targets: the newest call's, and the sum over both calls (1 and 1 is 2)" \
  "$(flip anon "$SHOWN" "select unattributed_targets || ':' || unattributed_targets_all from api.earnings_latest where company_slug = 'ec29-a'")" '^1:2$'
check "L05 with figures hidden, the sums are null" \
  "$(flip anon "$HID" "select coalesce(unattributed_targets::text, 'null') || ':' || coalesce(unattributed_targets_all::text, 'null') from api.earnings_latest where company_slug = 'ec29-a'")" '^null:null$'

# --- call_figures
FIGS="select string_agg(kind || ':' || metric || ':' || speaker_role, ',' order by kind, metric) from api.call_figures where company_slug = 'ec29-a'"
check "F01 a management target, an unknown target and a stated figure: all three with their roles; the duplicate is left out" \
  "$(flip anon "$FIGONLY" "$FIGS")" '^stated:ec29_s:management,target:ec29_m:management,target:ec29_u:unknown,target:ec29_u2:unknown$'
check "F02 figures hidden: no row at all" \
  "$(flip anon "$SUMONLY" "select count(*) from api.call_figures where company_slug = 'ec29-a'")" '^0$'
check "F03 page_first and page_last: inside one page, across two pages, at a page's first code point, and no page map" \
  "$(flip anon "$FIGONLY" "select string_agg(metric || ':' || coalesce(page_first::text, '-') || '-' || coalesce(page_last::text, '-'), ',' order by metric) from api.call_figures where company_slug = 'ec29-a'")" \
  '^ec29_m:1-1,ec29_s:2-2,ec29_u:1-2,ec29_u2:---$'
check "F04 a figure's page_url drops the source's own fragment and adds #page=N; a document with no pages has no link" \
  "$(flip anon "$FIGONLY" "select coalesce(max(page_url) filter (where metric = 'ec29_m'), '-') || ' ' || coalesce(max(page_url) filter (where metric = 'ec29_u2'), '-') from api.call_figures where company_slug = 'ec29-a'")" \
  '^https://example.com/ec29/ec29-a-q1fy27[.]pdf#page=1 -$'

# --- citation_pages
PAGES="select count(*) from api.citation_pages where ai_output_id in ($OUTIDS)"
check "P01 both fields hidden: no row" "$(flip anon "$HID" "$PAGES")" '^0$'
check "P02 only the summary visible: only the summary's citation has a page (page 1)" \
  "$(flip anon "$SUMONLY" "select count(*) || ':' || min(page_first) || min(page_last) from api.citation_pages where ai_output_id in ($OUTIDS)")" '^1:11$'
check "P03 both visible: four citations have pages (one summary, three of the first call's figures; the second call has no page rows)" \
  "$(flip anon "$SHOWN" "$PAGES")" '^4$'
check "P04 no page link points at the storage host: every link is on the source's own host" \
  "$(flip anon "$SHOWN" "select count(*) filter (where page_url not like 'https://example.com/ec29/%') from api.citation_pages where ai_output_id in ($OUTIDS)")" '^0$'

# --- suppression (Spec 02B §3.1)
ALLROWS="select (select count(*) from api.earnings_calls where company_slug like 'ec29-%') || ':' || (select count(*) from api.call_figures where company_slug like 'ec29-%') || ':' || (select count(*) from api.citation_pages where ai_output_id in ($OUTIDS))"
check "X01 nothing suppressed: three calls, four figures, four pages" \
  "$(flip anon "$SHOWN" "$ALLROWS")" '^3:4:4$'
check "X02 suppressing nse hides every call, figure and page link at once" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'nse'" "$ALLROWS")" '^0:0:0$'
check "X03 suppressing company_web hides the calls whose transcript it holds, their figures and pages" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'company_web'" "$ALLROWS")" '^1:0:0$'
check "X04 with nse suppressed, every company is none_found" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'nse'" "select count(*) from api.earnings_latest where company_slug like 'ec29-%' and status = 'none_found'")" '^3$'

# --- no transcript text through any new view (no column holds chunk bodies)
check "T01 no column of the four views is text drawn from chunks (names checked)" \
  "$(as_pg "select count(*) from information_schema.columns where table_schema = 'api' and table_name in ('earnings_calls','earnings_latest','call_figures','citation_pages') and column_name in ('body','text','quote','chunk_body','content')")" '^0$'
check "T02 anon can't read chunks of a transcript, and the views grant nothing beyond select" \
  "$(as_role anon "select count(*) from core.chunk where document_id in (select id from core.document where external_key like 'ec29%')")" '^0$'
check "T03 the four views are security_invoker" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname in ('earnings_calls','earnings_latest','call_figures','citation_pages') and 'security_invoker=true' = any(c.reloptions)")" '^4$'

cleanup
echo; echo "passed $pass, failed $fail"
