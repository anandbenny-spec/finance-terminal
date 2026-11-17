#!/bin/bash
# Tests for 0056 (the Earnings calls tab's reads touch fewer rows as a visitor; issue #620). Run after the other scripts on the same
# fresh database. One transaction builds two companies with three calls (six-page transcripts, so a range join over the page map
# reads pages it throws away), keeps 0048's api.call_guidance body and 0035's api.citation_pages body as throwaway views
# (public.cg56_old, public.cp56_old, security_invoker like the real ones), compares old and new in full as the owner and as anon in
# every gate state that matters (each field hidden or shown, a source suppressed, a call and a transcript unpublished), reads the
# plans, and rolls back, so no row is left. run_tests_0028.sh, run_tests_0035.sh and run_tests_0048.sh run unchanged and keep
# checking the views' meaning; this script checks that the rewrite returns the same rows and that a visitor's read no longer
# touches the rows the old bodies threw away.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

# one comparison line per view: rows only in the new view, rows only in the old one, rows of each (for the fixture's rows)
CG() { echo "select '$1|' || (select count(*) from (select * from api.call_guidance where company_slug like 'ec56-%' except all select * from public.cg56_old where company_slug like 'ec56-%') x) || ':'
            || (select count(*) from (select * from public.cg56_old where company_slug like 'ec56-%' except all select * from api.call_guidance where company_slug like 'ec56-%') x) || ':'
            || (select count(*) from api.call_guidance where company_slug like 'ec56-%') || ':'
            || (select count(*) from public.cg56_old where company_slug like 'ec56-%');"; }
OUTS="(select id from core.ai_output where body = 'ec56 output')"
CP() { echo "select '$1|' || (select count(*) from (select * from api.citation_pages where ai_output_id in $OUTS except all select * from public.cp56_old where ai_output_id in $OUTS) x) || ':'
            || (select count(*) from (select * from public.cp56_old where ai_output_id in $OUTS except all select * from api.citation_pages where ai_output_id in $OUTS) x) || ':'
            || (select count(*) from api.citation_pages where ai_output_id in $OUTS) || ':'
            || (select count(*) from public.cp56_old where ai_output_id in $OUTS);"; }
POL() { echo "update core.field_policy set status = '$2' where field_key = '$1';"; }

OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<SQL
begin;
-- 0048's api.call_guidance body, verbatim (the reference for the row tests and the control for the plan test)
create view public.cg56_old with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.guidance_extraction'), false) as ai_ok,
         max(label)  filter (where field_key = 'ai.guidance_extraction' and status in ('shown','labelled')) as ai_label,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false) as out_ok,
         max(label)  filter (where field_key = 'equity.guidance_outcome' and status in ('shown','labelled')) as out_label,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false)
         and coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_hit_rate'), false) as hit_ok,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer
  from core.field_policy
  where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.guidance_hit_rate','equity.peer_group')
)
select e.slug as company_slug, e.name as company_name, e.short_name, e.market, e.currency,
       c.peer_group_key,
       case when g.peer then pg.label end as peer_group_label,
       ev.slug as call_slug, ec.fiscal_label, ec.call_date,
       x.metric, x.metric_label, x.kind, x.value, x.low, x.high, x.unit, x.direction, x.comparison,
       x.period_text, x.period_kind, x.target_start, x.target_end, x.basis, x.growth_basis, x.scope_text,
       x.vintage_date, x.ai_output_id, x.citation_no, x.not_scored_reason,
       g.ai_label as extraction_label,
       case when g.out_ok then g.out_label end     as outcome_label,
       case when g.hit_ok then o.is_first end      as is_first,
       case when g.hit_ok then o.revision end      as revision,
       case when g.hit_ok then o.horizon_share end as horizon_share,
       case when g.hit_ok then o.counted end       as counted,
       case when g.out_ok then o.outcome end       as outcome,
       case when g.out_ok then o.relation end      as relation,
       case when g.out_ok then o.actual end        as actual,
       case when g.out_ok then o.base end          as base,
       case when g.out_ok then o.reason end        as reason,
       case when g.hit_ok then fs.call_slug end    as first_call_slug,
       case when g.hit_ok then fs.vintage_date end as first_stated_on
from equity.guidance x
cross join g
join core.ai_output ai on ai.id = x.ai_output_id and ai.task = 'guidance_extraction' and ai.status = 'published'
join equity.earnings_call ec on ec.event_id = x.call_event_id
join core.event ev on ev.id = ec.event_id
join core.entity e on e.id = x.entity_id
left join equity.company c on c.entity_id = x.entity_id
left join equity.peer_group pg on pg.key = c.peer_group_key
left join equity.guidance_outcome o on o.guidance_id = x.id
left join lateral (
  select ev1.slug as call_slug, g1.vintage_date
  from equity.guidance_outcome o1
  join equity.guidance g1 on g1.id = o1.guidance_id
  join core.event ev1 on ev1.id = g1.call_event_id
  where o1.target_key = o.target_key and o1.is_first
  order by g1.vintage_date, g1.id
  limit 1
) fs on true
where g.ai_ok and x.duplicate_of is null and x.speaker_role = 'management';

-- 0035's api.citation_pages body, verbatim
create view public.cp56_old with (security_invoker = true) as
select c.ai_output_id, c.citation_no,
       p1.pdf_page as page_first,
       p2.pdf_page as page_last,
       regexp_replace(d.source_url, '#.*\$', '') || '#page=' || p1.pdf_page as page_url
from core.ai_citation c
join core.ai_output o on o.id = c.ai_output_id and o.status = 'published'
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
join core.document d on d.id = c.document_id and d.source_url is not null
join core.source ds on ds.id = d.source_id and not ds.is_suppressed
left join core.event ev on ev.id = d.event_id
left join core.source es on es.id = ev.source_id
join equity.transcript_page p1 on p1.document_id = c.document_id
                              and c.char_start >= p1.char_start and c.char_start < p1.char_end
join equity.transcript_page p2 on p2.document_id = c.document_id
                              and c.char_end - 1 >= p2.char_start and c.char_end - 1 < p2.char_end
where c.verified and c.kind = 'document_span'
  and (o.task <> 'call_summary' or core.call_summary_version_ok(o.prompt_version))
  and (d.event_id is null or (ev.id is not null and es.id is not null and not es.is_suppressed));
grant select on public.cg56_old, public.cp56_old to anon;

-- A call with a 645-code-point transcript; its page map is six pages (or none).
create function pg_temp.mk_call(p_ent text, p_label text, p_period date, p_pages boolean) returns void language plpgsql as \$f\$
declare txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
        eid integer; evid bigint; did bigint; tag text := lower(p_ent || '-' || replace(p_label, ' ', ''));
begin
  select id into eid from core.entity where slug = p_ent;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec56-call-' || tag, 'ec56', p_period, 'stored',
            (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'ec56 call ' || tag, (select id from core.source where key = 'company_web'),
            'ec56-' || tag, 'https://example.com/ec56/' || tag || '.pdf#page=3', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
    values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at,
                                    exchange, route, transcript_document_id, words, unknown_role_share)
    values (evid, eid, p_label, p_period, lower(replace(p_label, ' ', '-')), 'results_link', p_period + 25, 'NSE',
            'company_link', did, 105, 0.05);
  if p_pages then
    insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
      values (did, 1, 0, 100), (did, 2, 100, 200), (did, 3, 200, 300), (did, 4, 300, 400), (did, 5, 400, 500), (did, 7, 500, 645);
  end if;
end \$f\$;

-- An output of a task on a call's transcript, with verified citations at the given spans (start, end pairs), published.
create function pg_temp.mk_out(p_ext text, p_task text, p_version text, p_spans int[], p_body text default 'ec56 output') returns bigint language plpgsql as \$f\$
declare did bigint; oid bigint; i int;
begin
  select id into did from core.document where external_key = p_ext;
  insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
    values (p_task, did, jsonb_build_object(), p_body, 'draft', 'claude-haiku-5-5', p_task, p_version,
            sha256(convert_to('ec56' || p_ext || p_task || p_version, 'UTF8'))) returning id into oid;
  for i in 1 .. array_length(p_spans, 1) / 2 loop
    insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
      values (oid, i, '/x' || i, 'document_span', did, p_spans[2 * i - 1], p_spans[2 * i], true);
  end loop;
  update core.ai_output set status = 'published' where id = oid;
  return oid;
end \$f\$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec56-a', 'Ec56 A Ltd', 'ec56-a', 'IN', 'INR', true),
         ('company', 'ec56-b', 'Ec56 B Ltd', 'ec56-b', 'IN', 'INR', true);
insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000560' || (ascii(right(slug, 1)) - 96), 'EC56' || upper(right(slug, 1)), '556' || (ascii(right(slug, 1)) - 96)
  from core.entity where slug like 'ec56-%';
-- a: Q1 with a page map, Q2 with a page map; b: Q1 without one (its citations give no page row, old or new)
select pg_temp.mk_call('ec56-a', 'Q1 FY27', '2026-06-30', true);
select pg_temp.mk_call('ec56-a', 'Q2 FY27', '2026-09-30', true);
select pg_temp.mk_call('ec56-b', 'Q1 FY27', '2026-06-30', false);
-- spans: one page (p1), across pages 3 and 4, from a page's first code point, the text's last code point (page 7, the PDF's
-- seventh physical page: page 6 was dropped whole), and a span ending on a page's last code point
select pg_temp.mk_out('ec56-ec56-a-q1fy27', 'guidance_extraction', 'v1', array[0, 43, 250, 320, 300, 320, 560, 645, 150, 200]);
select pg_temp.mk_out('ec56-ec56-a-q2fy27', 'guidance_extraction', 'v1', array[10, 43, 420, 480]);
select pg_temp.mk_out('ec56-ec56-b-q1fy27', 'guidance_extraction', 'v1', array[0, 43]);
-- b Q2: a page map that stops at code point 300 (never written so, but the views must give no page they can't back): a span on page 1,
-- one no page holds, and one whose first code point has a page and whose last has none
select pg_temp.mk_call('ec56-b', 'Q2 FY27', '2026-09-30', false);
insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
  select id, v.pg, v.cs, v.ce from core.document, (values (1, 0, 100), (2, 100, 300)) v(pg, cs, ce) where external_key = 'ec56-ec56-b-q2fy27';
select pg_temp.mk_out('ec56-ec56-b-q2fy27', 'guidance_extraction', 'v1', array[0, 43, 400, 420, 250, 450]);
-- For the plan checks only: a transcript with a real one's page count (40 pages of 16 code points, the last to 645) and 12 spans
-- across it, under its own output body so the row checks above don't count it.
select pg_temp.mk_call('ec56-b', 'Q3 FY27', '2026-12-31', false);
insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
  select d.id, n, (n - 1) * 16, case when n = 40 then 645 else n * 16 end from core.document d, generate_series(1, 40) n
  where d.external_key = 'ec56-ec56-b-q3fy27';
select pg_temp.mk_out('ec56-ec56-b-q3fy27', 'guidance_extraction', 'v1',
                      array[5, 30, 70, 90, 150, 170, 230, 260, 300, 340, 380, 400, 420, 450, 500, 520, 560, 590, 600, 640, 610, 645, 630, 645],
                      'ec56 plan output');
analyze equity.transcript_page;
select pg_temp.mk_out('ec56-ec56-a-q1fy27', 'call_summary', 'v11', array[100, 130, 290, 410]);
select pg_temp.mk_out('ec56-ec56-a-q2fy27', 'call_summary', 'v10', array[100, 130]);   -- under the version floor: never a row

insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date, speaker_role)
select ec.event_id, ec.entity_id, o.id, v.cit, ec.transcript_document_id, 0, 43, v.metric, 'Label ' || v.metric, v.metric,
       'point', 10, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', v.vintage::date, v.role
from (values ('ec56-a', 'Q1 FY27', 'ec56_m',   1, '2026-07-20', 'management'),
             ('ec56-a', 'Q1 FY27', 'ec56_u',   2, '2026-07-20', 'unknown'),
             ('ec56-a', 'Q1 FY27', 'ec56_dup', 1, '2026-07-20', 'management'),
             ('ec56-a', 'Q1 FY27', 'ec56_n',   3, '2026-07-20', 'management'),
             ('ec56-a', 'Q2 FY27', 'ec56_m',   1, '2026-10-20', 'management'),
             ('ec56-b', 'Q1 FY27', 'ec56_b',   1, '2026-07-21', 'management')) v(slug, label, metric, cit, vintage, role)
join core.entity e on e.slug = v.slug
join equity.earnings_call ec on ec.entity_id = e.id and ec.fiscal_label = v.label
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
update equity.guidance set duplicate_of = (select min(id) from equity.guidance where metric = 'ec56_m') where metric = 'ec56_dup';
-- outcomes: a's ec56_m stated twice (Q1 first, Q2 reiterated: the same target), b's once; ec56_n and the unknown row have none
insert into equity.guidance_outcome (guidance_id, target_key, is_first, revision, horizon_share, counted, outcome, relation, actual,
                                     inputs, rule_version)
select x.id, 'ec56|' || x.entity_id || '|' || x.metric, x.vintage_date = min(x.vintage_date) over (partition by x.entity_id, x.metric),
       case when x.vintage_date = min(x.vintage_date) over (partition by x.entity_id, x.metric) then 'first' else 'reiterated' end,
       0.3, true, 'delivered', 'within', 10.2, '{}'::jsonb, 'v1'
from equity.guidance x where x.metric in ('ec56_m', 'ec56_b') and x.duplicate_of is null;   -- only management rows may have one (0028)

select 'SETUP|' || (select count(*) from equity.guidance where metric like 'ec56%') || ':' || (select count(*) from equity.guidance_outcome where target_key like 'ec56|%')
       || ':' || (select count(*) from core.ai_citation c join core.ai_output o on o.id = c.ai_output_id where o.body like 'ec56 %output');

------------------------------------------------------------------ api.call_guidance: same rows, in full, both ways
$(POL ai.guidance_extraction hidden) $(POL equity.guidance_outcome hidden) $(POL equity.guidance_hit_rate hidden) $(POL equity.peer_group labelled)
$(CG G1O)
set role anon;
$(CG G1A)
reset role;
$(POL ai.guidance_extraction labelled)
$(CG G2O)
set role anon;
$(CG G2A)
reset role;
$(POL equity.guidance_outcome labelled)
$(CG G3O)
set role anon;
$(CG G3A)
select 'F3|' || count(*) || ':' || count(first_call_slug) || ':' || count(outcome) from api.call_guidance where company_slug like 'ec56-%';
reset role;
$(POL equity.guidance_hit_rate labelled)
$(CG G4O)
set role anon;
$(CG G4A)
select 'F4|' || string_agg(company_slug || ':' || metric || ':' || call_slug || ':' || coalesce(first_call_slug, '-') || ':' || coalesce(first_stated_on::text, '-'), ',' order by company_slug, metric, call_slug)
  from api.call_guidance where company_slug like 'ec56-%';
reset role;
update core.source set is_suppressed = true where key = 'nse';
set role anon;
$(CG G5A)
reset role;
update core.source set is_suppressed = false where key = 'nse';
update core.event set is_published = false where slug = 'ec56-call-ec56-a-q1fy27';
set role anon;
$(CG G6A)
reset role;
update core.event set is_published = true where slug = 'ec56-call-ec56-a-q1fy27';
$(POL equity.peer_group hidden)
set role anon;
$(CG G7A)
reset role;

------------------------------------------------------------------ api.citation_pages: same rows, in full, both ways
$(POL ai.guidance_extraction hidden) $(POL ai.call_summary hidden)
$(CP C1O)
set role anon;
$(CP C1A)
reset role;
$(POL ai.guidance_extraction labelled)
$(CP C2O)
set role anon;
$(CP C2A)
reset role;
$(POL ai.call_summary labelled)
$(CP C3O)
set role anon;
$(CP C3A)
select 'Q3|' || string_agg(o.task || ':' || o.prompt_version || ':' || c.citation_no || ':' || p.page_first || '-' || p.page_last || ':' || p.page_url, ',' order by o.document_id, o.task, c.citation_no)
  from api.citation_pages p join core.ai_citation c on c.ai_output_id = p.ai_output_id and c.citation_no = p.citation_no
  join core.ai_output o on o.id = p.ai_output_id where p.ai_output_id in $OUTS;
reset role;
update core.source set is_suppressed = true where key = 'company_web';
set role anon;
$(CP C4A)
reset role;
update core.source set is_suppressed = false where key = 'company_web';
update core.source set is_suppressed = true where key = 'nse';
set role anon;
$(CP C5A)
reset role;
update core.source set is_suppressed = false where key = 'nse';
update core.document set is_published = false where external_key = 'ec56-ec56-a-q2fy27';
set role anon;
$(CP C6A)
reset role;
update core.document set is_published = true where external_key = 'ec56-ec56-a-q2fy27';

------------------------------------------------------------------ the plans, as a visitor, with production's gates (hit rates hidden)
$(POL equity.guidance_hit_rate hidden)
set role anon;
select 'PLAN-CG-NEW-START';
explain (analyze, costs off, timing off, summary off) select * from api.call_guidance where company_slug = 'ec56-a';
select 'PLAN-CG-NEW-END';
select 'PLAN-CG-OLD-START';
explain (analyze, costs off, timing off, summary off) select * from public.cg56_old where company_slug = 'ec56-a';
select 'PLAN-CG-OLD-END';
set enable_seqscan = off;
select 'PLAN-CP-NEW-START';
explain (analyze, costs off, timing off, summary off) select * from api.citation_pages where ai_output_id in (select id from core.ai_output where body = 'ec56 plan output');
select 'PLAN-CP-NEW-END';
select 'PLAN-CP-OLD-START';
explain (analyze, costs off, timing off, summary off) select * from public.cp56_old where ai_output_id in (select id from core.ai_output where body = 'ec56 plan output');
select 'PLAN-CP-OLD-END';
reset enable_seqscan;
select 'CPP|' || (select count(*) from (select * from api.citation_pages where ai_output_id in (select id from core.ai_output where body = 'ec56 plan output')
                                         except all select * from public.cp56_old where ai_output_id in (select id from core.ai_output where body = 'ec56 plan output')) x)
       || ':' || (select count(*) from api.citation_pages where ai_output_id in (select id from core.ai_output where body = 'ec56 plan output'))
       || ':' || (select count(*) from public.cp56_old where ai_output_id in (select id from core.ai_output where body = 'ec56 plan output'));
reset role;

------------------------------------------------------------------ columns, options, grants and comments unchanged
select 'COLS|' || ((select string_agg(column_name || ' ' || data_type, ',' order by ordinal_position) from information_schema.columns where table_schema = 'api' and table_name = 'call_guidance')
                 = (select string_agg(column_name || ' ' || data_type, ',' order by ordinal_position) from information_schema.columns where table_schema = 'public' and table_name = 'cg56_old'))
       || ':' || ((select string_agg(column_name || ' ' || data_type, ',' order by ordinal_position) from information_schema.columns where table_schema = 'api' and table_name = 'citation_pages')
                 = (select string_agg(column_name || ' ' || data_type, ',' order by ordinal_position) from information_schema.columns where table_schema = 'public' and table_name = 'cp56_old'));
select 'OPTS|' || string_agg(c.relname || ':' || array_to_string(c.reloptions, ';'), ',' order by c.relname)
  from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname in ('call_guidance', 'citation_pages');
select 'GRANTS|' || string_agg(table_name || ':' || grantee, ',' order by table_name, grantee)
  from information_schema.role_table_grants where table_schema = 'api' and table_name in ('call_guidance', 'citation_pages') and privilege_type = 'SELECT'
   and grantee in ('anon', 'authenticated', 'service_role', 'job_writer', 'sql_reader');
select 'CMT|' || (obj_description('api.call_guidance'::regclass, 'pg_class') like 'One row per guidance target management stated on an earnings call%Never transcript text.%')::text
       || (obj_description('api.citation_pages'::regclass, 'pg_class') = 'Page of a verified transcript citation: first and last PDF page of its span and a link to the source''s own PDF at that page (never our copy). No row where the document has no page map. Never transcript text.')::text;
rollback;
SQL
)"
get() { grep "^$1|" <<<"$OUT" | head -1 | cut -d'|' -f2-; }
plan() { sed -n "/^PLAN-$1-START/,/^PLAN-$1-END/p" <<<"$OUT"; }
# Rows a scan of equity.transcript_page read and threw away: the "Rows Removed by Filter" lines of those scans' own nodes only (from a
# node's line to the next node's "->"), never a neighbouring node's, whatever the plan's layout.
removed_on_pages() { awk '/on transcript_page/ { f = 1; next } /->/ { f = 0 } f && /Rows Removed by Filter/ { n += $NF } END { print n + 0 }' <<<"$1"; }
# The most page rows any scan of equity.transcript_page returned per execution ("actual rows", the per-loop figure), whatever the join
# method: each of those rows carries a visitor's row-level check. A probe returns the one page it needs; the range join reads many.
page_rows_per_scan() { awk '/on transcript_page/ && match($0, /actual rows=[0-9]+/) { v = substr($0, RSTART + 12, RLENGTH - 12) + 0; if (v > m) m = v } END { print m + 0 }' <<<"$1"; }
# Page rows the page scans returned in all (actual rows x loops), for the control.
pages_read() { awk '/on transcript_page/ && match($0, /actual rows=[0-9]+ loops=[0-9]+/) { split(substr($0, RSTART, RLENGTH), a, /[= ]/); n += a[3] * a[5] } END { print n + 0 }' <<<"$1"; }

check "S00 the fixture loaded (six guidance rows with the duplicate, three outcomes, 26 citations with the plan transcript's 12)" "$(get SETUP)" '^6:3:26$'

check "G1 owner, extraction hidden: no row differs, both empty" "$(get G1O)" '^0:0:0:0$'
check "G1 anon, extraction hidden: no row differs, both empty" "$(get G1A)" '^0:0:0:0$'
check "G2 owner, extraction labelled, outcomes and hit rates hidden: no row differs, rows exist" "$(get G2O)" '^0:0:4:4$'
check "G2 anon, extraction labelled, outcomes and hit rates hidden: no row differs, rows exist" "$(get G2A)" '^0:0:4:4$'
check "G3 owner, outcomes labelled, hit rates hidden (production): no row differs" "$(get G3O)" '^0:0:4:4$'
check "G3 anon, outcomes labelled, hit rates hidden (production): no row differs" "$(get G3A)" '^0:0:4:4$'
check "F3 with hit rates hidden the first statement stays null and the outcomes show (4 rows, 0 first calls, 3 outcomes)" "$(get F3)" '^4:0:3$'
check "G4 owner, all three labelled (the lookup runs): no row differs" "$(get G4O)" '^0:0:4:4$'
check "G4 anon, all three labelled (the lookup runs): no row differs" "$(get G4A)" '^0:0:4:4$'
check "F4 with hit rates labelled each target's first statement is its first call, the later statement included; ec56_n has none" \
  "$(get F4)" '^ec56-a:ec56_m:ec56-call-ec56-a-q1fy27:ec56-call-ec56-a-q1fy27:2026-07-20,ec56-a:ec56_m:ec56-call-ec56-a-q2fy27:ec56-call-ec56-a-q1fy27:2026-07-20,ec56-a:ec56_n:ec56-call-ec56-a-q1fy27:-:-,ec56-b:ec56_b:ec56-call-ec56-b-q1fy27:ec56-call-ec56-b-q1fy27:2026-07-21$'
check "G5 anon, nse suppressed: no row differs, both empty (every call's event is from nse)" "$(get G5A)" '^0:0:0:0$'
check "G6 anon, a call unpublished: no row differs, its rows gone" "$(get G6A)" '^0:0:2:2$'
check "G7 anon, peer group hidden: no row differs" "$(get G7A)" '^0:0:4:4$'

check "C1 owner, both fields hidden: no row differs, both empty" "$(get C1O)" '^0:0:0:0$'
check "C1 anon, both fields hidden: no row differs, both empty" "$(get C1A)" '^0:0:0:0$'
check "C2 owner, extraction labelled: no row differs (a's two transcripts: 5 + 2 citations; b Q1 has no page map; b Q2's page map holds 1 of its 3 spans)" "$(get C2O)" '^0:0:8:8$'
check "C2 anon, extraction labelled: no row differs" "$(get C2A)" '^0:0:8:8$'
check "C3 owner, summary labelled too: no row differs (the v11 summary's 2 citations; the v10 one never)" "$(get C3O)" '^0:0:10:10$'
check "C3 anon, summary labelled too: no row differs" "$(get C3A)" '^0:0:10:10$'
check "Q3 the pages: one page, across pages 3 and 4, a page's first code point, the text's end on page 7, a page's last code point; links at that page; a span no page holds gives no row" \
  "$(get Q3)" '^call_summary:v11:1:2-2:https://example.com/ec56/ec56-a-q1fy27.pdf#page=2,call_summary:v11:2:3-5:https://example.com/ec56/ec56-a-q1fy27.pdf#page=3,guidance_extraction:v1:1:1-1:https://example.com/ec56/ec56-a-q1fy27.pdf#page=1,guidance_extraction:v1:2:3-4:https://example.com/ec56/ec56-a-q1fy27.pdf#page=3,guidance_extraction:v1:3:4-4:https://example.com/ec56/ec56-a-q1fy27.pdf#page=4,guidance_extraction:v1:4:7-7:https://example.com/ec56/ec56-a-q1fy27.pdf#page=7,guidance_extraction:v1:5:2-2:https://example.com/ec56/ec56-a-q1fy27.pdf#page=2,guidance_extraction:v1:1:1-1:https://example.com/ec56/ec56-a-q2fy27.pdf#page=1,guidance_extraction:v1:2:5-5:https://example.com/ec56/ec56-a-q2fy27.pdf#page=5,guidance_extraction:v1:1:1-1:https://example.com/ec56/ec56-b-q2fy27.pdf#page=1$'
check "C4 anon, company_web suppressed: no row differs, both empty" "$(get C4A)" '^0:0:0:0$'
check "C5 anon, nse suppressed (the documents' events): no row differs, both empty" "$(get C5A)" '^0:0:0:0$'
check "C6 anon, a transcript unpublished: no row differs, its rows gone" "$(get C6A)" '^0:0:8:8$'

CGN="$(plan CG-NEW)"; CGO="$(plan CG-OLD)"
check "P1 call_guidance as a visitor with hit rates hidden: the first-statement lookup never runs (a one-time filter on hit_ok)" \
  "$(grep -c 'One-Time Filter' <<<"$CGN"):$(grep -E 'guidance_outcome o1' <<<"$CGN" | grep -c 'never executed')" '^[1-9][0-9]*:[1-9][0-9]*$'
check "P2 control: 0048's body runs the lookup on every row as a visitor (no never-executed scan of o1)" \
  "$(grep -E 'guidance_outcome o1' <<<"$CGO" | grep -vc 'never executed')" '^[1-9][0-9]*$'
CPN="$(plan CP-NEW)"; CPO="$(plan CP-OLD)"
check "P2b the 40-page transcript: the same 12 rows from both bodies (the plan checks' rows)" "$(get CPP)" '^0:12:12$'
check "P3 citation_pages as a visitor: each page is one backward index probe on (document_id, char_start)" \
  "$(grep -c 'Index Scan Backward using transcript_page_document_id_char_start_key' <<<"$CPN")" '^2$'
P4="$(page_rows_per_scan "$CPN"):$(removed_on_pages "$CPN")"
check "P4 citation_pages as a visitor: each page scan returns at most the one page it needs (an average a run, so 0 or 1), and none is read and thrown away (rows a scan, rows removed: $P4)" \
  "$P4" '^[01]:0$'
# On a failure, the page scans' own lines (plan text only: node, conditions, row counts), so CI's annotations show the plan's shape.
[[ "$P4" =~ ^[01]:0$ ]] || awk '/on transcript_page/ { f = 1; print; next } /->/ { f = 0 } f' <<<"$CPN" | head -8 | sed 's/^ */FAIL  P4 plan: /'
# The old body's waste shows as removed rows on its page scans (a nested-loop probe with a filter, as on dev) or as many rows per scan
# (a merge or hash join reading every page, as on a small fixture); either counts.
check "P5 control: 0035's body reads page rows it throws away for the same citations (removed by its scans' filter, or many rows a scan)" \
  "$(( $(removed_on_pages "$CPO") > 0 || $(page_rows_per_scan "$CPO") > 1 )):$(( $(pages_read "$CPO") > $(pages_read "$CPN") || $(removed_on_pages "$CPO") > 0 ))" '^1:1$'

check "V01 the same columns, names and types in order, as 0048's and 0035's bodies" "$(get COLS)" '^true:true$'
check "V02 both views still security_invoker" "$(get OPTS)" '^call_guidance:security_invoker=true,citation_pages:security_invoker=true$'
check "V03 grants unchanged: anon, authenticated and service_role read both; job_writer and sql_reader neither" \
  "$(get GRANTS)" '^call_guidance:anon,call_guidance:authenticated,call_guidance:service_role,citation_pages:anon,citation_pages:authenticated,citation_pages:service_role$'
check "V04 the comments as before" "$(get CMT)" '^truetrue$'

if [ "$fail" -ne 0 ]; then echo "--- psql output (for the failures above)"; grep -E 'ERROR|FATAL' <<<"$OUT" | head -5; fi
echo "0056: passed $pass, failed $fail"
