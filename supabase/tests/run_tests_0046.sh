#!/bin/bash
# Tests for 0046 (api.call_figures filters by call before it computes; issue #489). Run after the other scripts on the same fresh
# database. One transaction builds three companies with four calls, keeps 0029's api.call_figures body as a throwaway view
# (public.cf46_old, security_invoker like the real one), compares it with the new view in full as the owner and as anon (field hidden
# and shown, a source suppressed, a call unpublished, a transcript unpublished), reads the plans of a one-call read, and rolls back,
# so no row is left. run_tests_0029.sh runs unchanged and checks the view's meaning (F01 to F04, X01 to X03); this script checks that
# the rewrite returns the same rows and that a call filter reaches the base tables.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg() { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

# one comparison line: rows only in the new view, rows only in the old one, rows of each (for the fixture's companies)
CMP() { echo "select '$1|' || (select count(*) from (select * from api.call_figures except select * from public.cf46_old) x) || ':'
            || (select count(*) from (select * from public.cf46_old except select * from api.call_figures) x) || ':'
            || (select count(*) from api.call_figures where company_slug like 'ec46-%') || ':'
            || (select count(*) from public.cf46_old where company_slug like 'ec46-%');"; }

OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<SQL
begin;
-- 0029's body, verbatim, as a throwaway view (the reference for the row tests and the negative control for the plan test)
create view public.cf46_old with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok,
         max(label) filter (where status in ('shown','labelled')) as lbl
  from core.field_policy where field_key = 'ai.guidance_extraction'
), f as (
  select 'target'::text as kind, x.call_event_id, x.ai_output_id, x.citation_no, x.metric, x.metric_label,
         x.metric_text, x.kind as figure_kind, x.value, x.low, x.high, x.unit, x.direction, x.comparison,
         x.period_text, x.period_kind, x.target_start as period_start, x.target_end as period_end,
         x.basis, x.growth_basis, x.scope_text, x.speaker_role
  from equity.guidance x
  where x.duplicate_of is null
  union all
  select 'stated'::text, s.call_event_id, s.ai_output_id, s.citation_no, s.metric, s.metric_label,
         s.metric_text, null::text, s.value, null::numeric, null::numeric, s.unit, null::text, null::text,
         s.period_text, s.period_kind, s.period_start, s.period_end,
         s.basis, s.growth_basis, s.scope_text, 'management'::text
  from equity.stated_figure s
  where s.duplicate_of is null
)
select f.kind, c.company_slug, c.call_slug, c.fiscal_label,
       f.metric, f.metric_label, f.metric_text, f.figure_kind, f.value, f.low, f.high, f.unit, f.direction, f.comparison,
       f.period_text, f.period_kind, f.period_start, f.period_end, f.basis, f.growth_basis, f.scope_text, f.speaker_role,
       f.ai_output_id, f.citation_no,
       p.page_first, p.page_last, p.page_url,
       g.lbl as extraction_label
from f
cross join g
join core.ai_output o on o.id = f.ai_output_id and o.task = 'guidance_extraction' and o.status = 'published'
join core.event ev on ev.id = f.call_event_id
join api.earnings_calls c on c.call_slug = ev.slug
left join api.citation_pages p on p.ai_output_id = f.ai_output_id and p.citation_no = f.citation_no
where g.ok;;
grant select on public.cf46_old to anon;

create function pg_temp.mk_call(p_ent text, p_label text, p_period date, p_filed timestamptz, p_pages boolean)
returns void language plpgsql as \$f\$
declare txt text := repeat('Revenue grew to ₹1,200 crore this quarter. ', 15);
        eid integer; evid bigint; did bigint; tag text := lower(p_ent || '-' || replace(p_label, ' ', ''));
begin
  select id into eid from core.entity where slug = p_ent;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('earnings_call', eid, 'ec46-call-' || tag, 'ec46', p_period, 'stored',
            (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
                             char_count, text_sha256, chunked_at, is_published)
    values ('call_transcript', eid, evid, 'ec46 call ' || tag, (select id from core.source where key = 'company_web'),
            'ec46-' || tag, 'https://example.com/ec46/' || tag || '.pdf#page=3', now(),
            char_length(txt), sha256(convert_to(txt, 'UTF8')), now(), true) returning id into did;
  insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
    values (did, 0, 0, 300, 'Q&A', 'CFO', substr(txt, 1, 300)), (did, 1, 300, 645, 'Q&A', 'CFO', substr(txt, 301, 345));
  insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source, filed_at,
                                    exchange, route, transcript_document_id, words, unknown_role_share)
    values (evid, eid, p_label, p_period, lower(replace(p_label, ' ', '-')), 'results_link', p_filed, 'NSE',
            'company_link', did, 105, 0.05);
  if p_pages then
    insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
      values (did, 1, 0, 300), (did, 2, 300, 645);
  end if;
end \$f\$;

create function pg_temp.mk_out(p_ext text, p_spans int[]) returns void language plpgsql as \$f\$
declare did bigint; oid bigint; i int;
begin
  select id into did from core.document where external_key = p_ext;
  insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version, input_sha256)
    values ('guidance_extraction', did, jsonb_build_object(), 'ec46 output', 'draft', 'claude-haiku-5-5', 'guidance_extraction', 'v1',
            sha256(convert_to('ec46' || p_ext, 'UTF8'))) returning id into oid;
  for i in 1 .. array_length(p_spans, 1) / 2 loop
    insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
      values (oid, i, '/x' || i, 'document_span', did, p_spans[2 * i - 1], p_spans[2 * i], true);
  end loop;
  update core.ai_output set status = 'published' where id = oid;
end \$f\$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  values ('company', 'ec46-a', 'Ec46 A Ltd', 'ec46-a', 'IN', 'INR', true),
         ('company', 'ec46-b', 'Ec46 B Ltd', 'ec46-b', 'IN', 'INR', true),
         ('company', 'ec46-c', 'Ec46 C Ltd', 'ec46-c', 'IN', 'INR', true);
insert into equity.company (entity_id, isin, nse_symbol, bse_code)
  select id, 'INE00000460' || (ascii(right(slug, 1)) - 96), 'EC46' || upper(right(slug, 1)), '546' || (ascii(right(slug, 1)) - 96)
  from core.entity where slug like 'ec46-%';
-- a: two calls (Q1 with a page map, Q2 without); b: one call with a page map; c: a call with no figures at all
select pg_temp.mk_call('ec46-a', 'Q1 FY27', '2026-06-30', '2026-07-25', true);
select pg_temp.mk_call('ec46-a', 'Q2 FY27', '2026-09-30', '2026-10-25', false);
select pg_temp.mk_call('ec46-b', 'Q1 FY27', '2026-06-30', '2026-07-26', true);
select pg_temp.mk_call('ec46-c', 'Q1 FY27', '2026-06-30', '2026-07-27', true);
select pg_temp.mk_out('ec46-ec46-a-q1fy27', array[0, 43, 290, 310, 300, 320]);
select pg_temp.mk_out('ec46-ec46-a-q2fy27', array[0, 43]);
select pg_temp.mk_out('ec46-ec46-b-q1fy27', array[0, 43, 300, 320]);
insert into equity.guidance (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                             metric_label, metric_text, kind, value, unit, period_text, period_kind, target_start, target_end,
                             basis, growth_basis, vintage_date, speaker_role)
select ec.event_id, ec.entity_id, o.id, v.cit, ec.transcript_document_id, v.cs, v.ce, v.metric, 'Label ' || v.metric, v.metric,
       'point', 10, 'pct', 'FY27', 'year', '2026-04-01', '2027-03-31', 'consolidated', 'yoy', '2026-10-20', v.role
from (values ('ec46-a', 'Q1 FY27', 'ec46_m',   1, 0,   43,  'management'),
             ('ec46-a', 'Q1 FY27', 'ec46_u',   2, 290, 310, 'unknown'),
             ('ec46-a', 'Q1 FY27', 'ec46_dup', 1, 0,   43,  'management'),
             ('ec46-a', 'Q2 FY27', 'ec46_u2',  1, 0,   43,  'unknown'),
             ('ec46-b', 'Q1 FY27', 'ec46_b',   1, 0,   43,  'management')) v(slug, label, metric, cit, cs, ce, role)
join core.entity e on e.slug = v.slug
join equity.earnings_call ec on ec.entity_id = e.id and ec.fiscal_label = v.label
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction';
update equity.guidance set duplicate_of = (select id from equity.guidance where metric = 'ec46_m') where metric = 'ec46_dup';
insert into equity.stated_figure (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end, metric,
                                  metric_label, metric_text, value, unit, period_text, period_kind, period_start, period_end,
                                  basis, growth_basis)
select ec.event_id, ec.entity_id, o.id, 3, ec.transcript_document_id, 300, 320, 'ec46_s', 'Label ec46_s', 'ec46_s', 12, 'pct',
       'Q1 FY27', 'quarter', '2026-04-01', '2026-06-30', 'consolidated', 'yoy'
from equity.earnings_call ec
join core.ai_output o on o.document_id = ec.transcript_document_id and o.task = 'guidance_extraction'
where ec.fiscal_label = 'Q1 FY27' and ec.entity_id = (select id from core.entity where slug = 'ec46-a');

select 'SETUP|' || (select count(*) from equity.guidance where metric like 'ec46%') || ':' || (select count(*) from equity.stated_figure where metric like 'ec46%');

------------------------------------------------------------------ same rows, in full, both ways
update core.field_policy set status = 'hidden' where field_key = 'ai.guidance_extraction';
$(CMP H1)
set role anon;
$(CMP H2)
reset role;
update core.field_policy set status = 'labelled' where field_key = 'ai.guidance_extraction';
$(CMP O1)
set role anon;
$(CMP A1)
reset role;
-- a suppressed event source (nse), a suppressed transcript source (company_web)
update core.source set is_suppressed = true where key = 'nse';
set role anon;
$(CMP N1)
reset role;
update core.source set is_suppressed = false where key = 'nse';
update core.source set is_suppressed = true where key = 'company_web';
set role anon;
$(CMP W1)
reset role;
update core.source set is_suppressed = false where key = 'company_web';
-- an unpublished call (the Hindalco hold), and an unpublished transcript of a published call
update core.event set is_published = false where slug = 'ec46-call-ec46-a-q1fy27';
set role anon;
$(CMP U1)
reset role;
update core.event set is_published = true where slug = 'ec46-call-ec46-a-q1fy27';
update core.document set is_published = false where external_key = 'ec46-ec46-b-q1fy27';
set role anon;
$(CMP U2)
reset role;
update core.document set is_published = true where external_key = 'ec46-ec46-b-q1fy27';
-- spot values, so the fixture is known to exercise what the equality is about
set role anon;
select 'S1|' || string_agg(kind || ':' || metric || ':' || speaker_role || ':' || coalesce(page_first::text, '-') || '-' || coalesce(page_last::text, '-'), ',' order by company_slug, call_slug, kind, metric)
  from api.call_figures where company_slug like 'ec46-%';
select 'S2|' || count(*) from api.call_figures where company_slug = 'ec46-c';
select 'S3|' || count(*) from api.call_figures where company_slug = 'ec46-a' and call_slug = 'ec46-call-ec46-a-q1fy27';
select 'S4|' || count(*) from api.call_figures where company_slug = 'ec46-a' and call_slug = 'ec46-call-ec46-b-q1fy27';

------------------------------------------------------------------ the plans of a one-call read
set enable_seqscan = off;
select 'PLAN-NEW-START';
explain select * from api.call_figures where company_slug = 'ec46-a' and call_slug = 'ec46-call-ec46-a-q1fy27';
select 'PLAN-NEW-END';
select 'PLAN-OLD-START';
explain select * from public.cf46_old where company_slug = 'ec46-a' and call_slug = 'ec46-call-ec46-a-q1fy27';
select 'PLAN-OLD-END';
reset role;
select 'COLS|' || ((select string_agg(column_name || ' ' || data_type, ',' order by ordinal_position) from information_schema.columns where table_schema = 'api' and table_name = 'call_figures') = (select string_agg(column_name || ' ' || data_type, ',' order by ordinal_position) from information_schema.columns where table_schema = 'public' and table_name = 'cf46_old'));
rollback;
SQL
)"
get() { grep "^$1|" <<<"$OUT" | head -1 | cut -d'|' -f2-; }
plan() { sed -n "/^PLAN-$1-START/,/^PLAN-$1-END/p" <<<"$OUT"; }

check "S00 the fixture loaded (five targets with the duplicate, one stated figure)" "$(get SETUP)" '^5:1$'
check "H1 owner, field hidden: no row differs either way, and the view is empty like the old one" "$(get H1)" '^0:0:0:0$'
check "H2 anon, field hidden: no row differs either way, and the view is empty like the old one" "$(get H2)" '^0:0:0:0$'
check "O1 owner, field labelled: no row differs either way, and rows exist" "$(get O1)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "A1 anon, field labelled: no row differs either way, and rows exist" "$(get A1)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "N1 anon, nse suppressed: no row differs either way, and the view is empty (every call's event is from nse)" "$(get N1)" '^0:0:0:0$'
check "W1 anon, company_web suppressed: no row differs either way, and the view is empty (every transcript is from company_web)" "$(get W1)" '^0:0:0:0$'
check "U1 anon, a call unpublished: no row differs either way, and the other calls still show" "$(get U1)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "U2 anon, a transcript unpublished: no row differs either way, and the other calls still show" "$(get U2)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "S1 targets and the stated figure with their roles and pages; the duplicate is left out; a call with no page map has no page" \
  "$(get S1)" '^stated:ec46_s:management:2-2,target:ec46_m:management:1-1,target:ec46_u:unknown:1-2,target:ec46_u2:unknown:---,target:ec46_b:management:1-1$'
check "S2 a call that has no figures shows no row" "$(get S2)" '^0$'
check "S3 a one-call read returns that call's rows (three: target, unknown target, stated figure)" "$(get S3)" '^3$'
check "S4 a company slug with another company's call slug returns nothing" "$(get S4)" '^0$'

PN="$(plan NEW)"; PO="$(plan OLD)"
check "P1 the one-call read reaches equity.guidance by the call's event (an index condition on call_event_id)" \
  "$(grep -cE 'Index Cond: \(call_event_id = ' <<<"$PN" | sed 's/^[1-9].*/ok/')" '^ok$'
check "P2 and equity.stated_figure by the call's event too (two reads in all)" \
  "$(grep -cE 'Index Cond: \(call_event_id = ' <<<"$PN")" '^[2-9]$'
check "P3 control: 0029's body reads every call's rows (no call_event_id index condition), so the check can fail" \
  "$(grep -cE 'Index Cond: \(call_event_id = ' <<<"$PO")" '^0$'

check "C01 the columns and their order are 0029's (compared with the old body inside the transaction)" "$(get COLS)" '^true$'
check "C02 the view is still security_invoker" \
  "$(as_pg "select count(*) from pg_class where oid = 'api.call_figures'::regclass and coalesce(reloptions::text, '') like '%security_invoker=true%'")" '^1$'
check "C03 grants are unchanged: anon, authenticated and service_role can select, PUBLIC cannot" \
  "$(as_pg "select has_table_privilege('anon', 'api.call_figures', 'select') and has_table_privilege('authenticated', 'api.call_figures', 'select') and has_table_privilege('service_role', 'api.call_figures', 'select') and not exists (select 1 from pg_class c, aclexplode(c.relacl) a where c.oid = 'api.call_figures'::regclass and a.grantee = 0)")" '^t$'
check "C04 the field policy is back to its state and no fixture row is left" \
  "$(as_pg "select (select count(*) from core.entity where slug like 'ec46%') || ':' || (select count(*) from pg_class where relname = 'cf46_old')")" '^0:0$'

echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
