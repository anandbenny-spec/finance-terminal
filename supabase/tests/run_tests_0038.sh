#!/bin/bash
# Tests for 0038 (segment line-up changes: equity.segment_lineup, equity.segment_change, the field policy
# equity.segment_changes and api.segment_changes; issue #409, job DD-01). Run after the other scripts on the same fresh
# database. The script makes its own companies (sc38-a … sc38-e on NSE, sc38-x on BSE), checks through the roles and
# removes every row it made. Gate flips, suppression and unpublishing happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
SHOWN="update core.field_policy set status = 'shown' where field_key = 'equity.segment_changes'"
SC="company_slug like 'sc38-%'"
EV="(select id from core.event where slug like 'sc38-%')"

cleanup() {
  as_pg "delete from equity.segment_change where entity_id in (select id from core.entity where slug like 'sc38-%');
         delete from equity.segment_lineup where entity_id in (select id from core.entity where slug like 'sc38-%');
         delete from equity.filing where entity_id in (select id from core.entity where slug like 'sc38-%');
         delete from core.document where entity_id in (select id from core.entity where slug like 'sc38-%');
         delete from core.event where slug like 'sc38-%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'sc38-%');
         delete from core.entity where slug like 'sc38-%';
         update core.field_policy set status = 'hidden' where field_key = 'equity.segment_changes';
         update core.source set is_suppressed = false where key in ('nse','bse');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
-- a quarter's results announcement with its consolidated filing; returns the event id
create function pg_temp.mk_q(p_slug text, p_end date, p_status text, p_source text) returns bigint language plpgsql as $f$
declare eid integer; evid bigint; did bigint; tag text := p_slug || '-' || p_end;
begin
  select id into eid from core.entity where slug = p_slug;
  insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
    values ('results_filing', eid, tag, 'sc38', p_end, p_end + 40, p_status,
            (select id from core.source where key = p_source), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, is_published)
    values ('results_xbrl', eid, evid, 'sc38 ' || tag, (select id from core.source where key = p_source), tag,
            'https://example.com/sc38/' || tag || '.xml', false) returning id into did;
  insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end, filed_at, source_url, exchange)
    values (did, eid, evid, 'consolidated', 'ind_as', p_end, null, null, p_end + 40,
            'https://example.com/sc38/' || tag || '.xml', upper(p_source));
  return evid;
end $f$;
-- a quarter's line-up: the names, their revenue, multi or single
create function pg_temp.mk_lines(p_ev bigint, p_names text[], p_rev numeric[], p_reporting text) returns void language plpgsql as $f$
declare i int;
begin
  for i in 1 .. cardinality(p_names) loop
    insert into equity.segment_lineup (results_event_id, line_no, entity_id, document_id, period_end, reporting, description, revenue, parser_version)
      select p_ev, i, f.entity_id, f.document_id, f.period_end, p_reporting, p_names[i], p_rev[i], 1
      from equity.filing f where f.event_id = p_ev and f.basis = 'consolidated';
  end loop;
end $f$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  select 'company', 'sc38-' || s, 'Sc38 ' || upper(s) || ' Ltd', 'sc38-' || s, 'IN', 'INR', true
  from unnest(array['a','b','c','d','e','x']) as s;
insert into equity.company (entity_id, isin, nse_symbol, fy_end_month)
  select e.id, 'INE0038' || lpad(row_number() over (order by e.slug)::text, 5, '0'), 'SC38' || upper(right(e.slug, 1)), 3
  from core.entity e where e.slug like 'sc38-%';

-- a: Refining and Petrochemicals for four quarters, then one Oil to Chemicals segment (the merged flag)
select pg_temp.mk_lines(pg_temp.mk_q('sc38-a', d::date, 'filed', 'nse'), array['Refining','Petrochemicals','Retail'], array[100,50,20], 'multi')
  from unnest(array['2025-09-30','2025-12-31','2026-03-31','2026-06-30']) as d;
select pg_temp.mk_lines(pg_temp.mk_q('sc38-a', '2026-09-30', 'filed', 'nse'), array['Oil to Chemicals (O2C)','Retail'], array[140,22], 'multi');
insert into equity.segment_change (entity_id, period_end, change_no, kind, segments_before, segments_after, revenue_ratio,
                                   latest_event_id, previous_event_id, previous_period_end, rule_version)
  select (select id from core.entity where slug = 'sc38-a'), '2026-09-30', 1, 'merged', array['Refining','Petrochemicals'],
         array['Oil to Chemicals (O2C)'], 140.0 / 150, (select id from core.event where slug = 'sc38-a-2026-09-30'),
         (select id from core.event where slug = 'sc38-a-2026-06-30'), '2026-06-30', 1;
-- b: one reportable segment
select pg_temp.mk_lines(pg_temp.mk_q('sc38-b', d::date, 'filed', 'nse'), array['Cars'], array[null::numeric], 'single')
  from unnest(array['2026-06-30','2026-09-30']) as d;
-- c: the same two segments for five quarters (none flagged)
select pg_temp.mk_lines(pg_temp.mk_q('sc38-c', d::date, 'filed', 'nse'), array['Cement','Chemicals'], array[10,5], 'multi')
  from unnest(array['2025-09-30','2025-12-31','2026-03-31','2026-06-30','2026-09-30']) as d;
-- d: the Dec 2025 quarter missing (not compared: 3 of the 4)
select pg_temp.mk_lines(pg_temp.mk_q('sc38-d', d::date, 'filed', 'nse'), array['Banking','Insurance'], array[10,5], 'multi')
  from unnest(array['2025-09-30','2026-03-31','2026-06-30','2026-09-30']) as d;
-- e: six quarters, the latest announcement superseded (its line-up must not count)
select pg_temp.mk_lines(pg_temp.mk_q('sc38-e', d::date, 'filed', 'nse'), array['Power'], array[7], 'multi')
  from unnest(array['2025-06-30','2025-09-30','2025-12-31','2026-03-31','2026-06-30']) as d;
select pg_temp.mk_lines(pg_temp.mk_q('sc38-e', '2026-09-30', 'superseded', 'nse'), array['Something else'], array[9], 'multi');
-- x: filed on BSE, five quarters, none flagged
select pg_temp.mk_lines(pg_temp.mk_q('sc38-x', d::date, 'filed', 'bse'), array['Steel','Mining'], array[10,5], 'multi')
  from unnest(array['2025-09-30','2025-12-31','2026-03-31','2026-06-30','2026-09-30']) as d;
commit;
SQL
)"
check "A01 the test rows load" "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|0-9])*$'

# --- tables, privileges and checks
check "T01 row-level security is on for both tables" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'equity' and c.relname in ('segment_lineup','segment_change') and c.relrowsecurity")" '^2$'
check "T02 job_writer may write and delete both (derived rows a run replaces)" \
  "$(as_role job_writer "update equity.segment_lineup set parser_version = parser_version where false; delete from equity.segment_lineup where false; delete from equity.segment_change where false; select 'ok'")" 'ok$'
check "T03 anon can't write the base tables" \
  "$(as_role anon "delete from equity.segment_change where false")" 'permission denied'
check "T04 authenticated can't write the base tables" \
  "$(as_role authenticated "update equity.segment_lineup set revenue = 0 where false")" 'permission denied'
check "T05 sql_reader can't read the base tables" \
  "$(as_role sql_reader "select count(*) from equity.segment_change")" 'permission denied'
check "T06 a line-up row can't be standalone (never filled from standalone)" \
  "$(as_pg "insert into equity.segment_lineup (results_event_id, basis, line_no, entity_id, document_id, period_end, reporting, description, parser_version)
            select f.event_id, 'standalone', 99, f.entity_id, f.document_id, f.period_end, 'single', 'x', 1 from equity.filing f where f.source_url like '%sc38-b-2026-09-30%'")" 'violates check constraint'
check "T07 a single-segment row can't carry revenue" \
  "$(as_pg "insert into equity.segment_lineup (results_event_id, line_no, entity_id, document_id, period_end, reporting, description, revenue, parser_version)
            select f.event_id, 99, f.entity_id, f.document_id, f.period_end, 'single', 'x', 5, 1 from equity.filing f where f.source_url like '%sc38-b-2026-09-30%'")" 'violates check constraint'
check "T08 a merged flag needs its revenue ratio, and only a merged flag has one" \
  "$(as_pg "insert into equity.segment_change (entity_id, period_end, change_no, kind, segments_before, segments_after, latest_event_id, previous_event_id, previous_period_end, rule_version)
            select entity_id, period_end, 9, 'merged', segments_before, segments_after, latest_event_id, previous_event_id, previous_period_end, 1 from equity.segment_change where entity_id = (select id from core.entity where slug = 'sc38-a')")
     $(as_pg "insert into equity.segment_change (entity_id, period_end, change_no, kind, segments_before, segments_after, revenue_ratio, latest_event_id, previous_event_id, previous_period_end, rule_version)
            select entity_id, period_end, 9, 'dropped', segments_before, '{}', 1, latest_event_id, previous_event_id, previous_period_end, 1 from equity.segment_change where entity_id = (select id from core.entity where slug = 'sc38-a')")" \
  'violates check constraint.*violates check constraint'
check "T09 a kind outside rule 1's list is refused" \
  "$(as_pg "insert into equity.segment_change (entity_id, period_end, change_no, kind, segments_before, segments_after, latest_event_id, previous_event_id, previous_period_end, rule_version)
            select entity_id, period_end, 9, 'added', segments_before, segments_after, latest_event_id, previous_event_id, previous_period_end, 1 from equity.segment_change where entity_id = (select id from core.entity where slug = 'sc38-a')")" 'violates check constraint'
check "T10 the field policy is seeded hidden, labelled As filed" \
  "$(as_pg "select status || ':' || label from core.field_policy where field_key = 'equity.segment_changes'")" '^hidden:As filed$'
check "T11 the view runs as the caller and anon may read it" \
  "$(as_pg "select ('security_invoker=true' = any(c.reloptions))::text || ':' || has_table_privilege('anon', 'api.segment_changes', 'select')::text from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname = 'segment_changes'")" '^true:true$'
check "T12 the tables and the view carry comments" \
  "$(as_pg "select count(*) from (values ('equity.segment_lineup'), ('equity.segment_change'), ('api.segment_changes')) v(r) where obj_description(r::regclass, 'pg_class') is not null")" '^3$'

# --- the gate
check "G01 hidden: anon sees no row in either table or in the view" \
  "$(as_role anon "select (select count(*) from equity.segment_lineup where results_event_id in $EV) || ':' || (select count(*) from equity.segment_change) || ':' || (select count(*) from api.segment_changes where $SC)")" '^0:0:0$'
check "G02 hidden: authenticated sees none either" \
  "$(as_role authenticated "select count(*) from api.segment_changes where $SC")" '^0$'
check "G03 shown: anon sees the line-ups (filed and superseded announcements alike in the base table) and the flag" \
  "$(flip anon "$SHOWN" "select (select count(*) from equity.segment_lineup where results_event_id in $EV) || ':' || (select count(*) from equity.segment_change where entity_id in (select id from core.entity where slug like 'sc38-%'))")" '^50:1$'

# --- the view's states (shown)
check "V01 a: one row, changed, merged, before and after as filed, the ratio, both quarters" \
  "$(flip anon "$SHOWN" "select count(*) over () || ':' || state || ':' || kind || ':' || array_to_string(segments_before, '+') || ':' || array_to_string(segments_after, '+') || ':' || round(revenue_ratio, 3) || ':' || period_end || ':' || previous_period_end || ':' || quarters_compared from api.segment_changes where company_slug = 'sc38-a'")" \
  '^1:changed:merged:Refining\+Petrochemicals:Oil to Chemicals \(O2C\):0\.933:2026-09-30:2026-06-30:4$'
check "V02 a: each side links to its own filing" \
  "$(flip anon "$SHOWN" "select latest_filing_url || ' ' || previous_filing_url || ' ' || (latest_filed_at > previous_filed_at)::text from api.segment_changes where company_slug = 'sc38-a'")" \
  '^https://example.com/sc38/sc38-a-2026-09-30.xml https://example.com/sc38/sc38-a-2026-06-30.xml true$'
check "V03 b: one reportable segment" \
  "$(flip anon "$SHOWN" "select state || ':' || coalesce(kind, '-') || ':' || coalesce(state_reason, '-') from api.segment_changes where company_slug = 'sc38-b'")" '^single_segment:-:-$'
check "V04 c: five quarters compared, none flagged" \
  "$(flip anon "$SHOWN" "select state || ':' || quarters_compared || ':' || period_end from api.segment_changes where company_slug = 'sc38-c'")" '^none_flagged:4:2026-09-30$'
check "V05 d: one earlier quarter has no line-up, so not compared, with the reason" \
  "$(flip anon "$SHOWN" "select state || ':' || state_reason from api.segment_changes where company_slug = 'sc38-d'")" '^not_compared:3 of the 4 earlier quarters have a line-up$'
check "V06 e: a superseded announcement's line-up never counts (the latest compared quarter is Jun 2026)" \
  "$(flip anon "$SHOWN" "select state || ':' || period_end from api.segment_changes where company_slug = 'sc38-e'")" '^none_flagged:2026-06-30$'
check "V07 one row per company without a flag; the source is the latest filing's exchange" \
  "$(flip anon "$SHOWN" "select string_agg(company_slug || '=' || source_key, ',' order by company_slug) from api.segment_changes where $SC")" \
  '^sc38-a=nse,sc38-b=nse,sc38-c=nse,sc38-d=nse,sc38-e=nse,sc38-x=bse$'

# --- the switches
check "S01 suppressing nse hides every NSE company's rows at once; BSE's stay" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'nse'" "select (select string_agg(company_slug, ',') from api.segment_changes where $SC) || ':' || (select count(*) from equity.segment_change) || ':' || (select count(*) from equity.segment_lineup where results_event_id in $EV)")" \
  '^sc38-x:0:10$'
check "S02 suppressing bse hides the BSE company" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'bse'" "select count(*) from api.segment_changes where company_slug = 'sc38-x'")" '^0$'
check "S03 an unpublished latest announcement hides its line-up and the flag (a falls back to Jun 2026, not compared)" \
  "$(flip anon "$SHOWN; update core.event set is_published = false where slug = 'sc38-a-2026-09-30'" "select state || ':' || period_end || ':' || coalesce(kind, '-') from api.segment_changes where company_slug = 'sc38-a'")" \
  '^not_compared:2026-06-30:-$'
check "S04 an unpublished company hides its rows" \
  "$(flip anon "$SHOWN; update core.entity set is_published = false where slug = 'sc38-c'" "select count(*) from api.segment_changes where company_slug = 'sc38-c'")" '^0$'
check "S05 nothing above changed a stored row" \
  "$(as_pg "select (select count(*) from equity.segment_lineup where results_event_id in $EV) || ':' || (select count(*) from equity.segment_change where entity_id in (select id from core.entity where slug like 'sc38-%')) || ':' || (select status from core.field_policy where field_key = 'equity.segment_changes')")" '^50:1:hidden$'

cleanup
echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
