#!/bin/bash
# Tests for 0049 (api.segment_changes gains latest_filed_period: the company's latest stored consolidated results quarter; issue
# #569, job DD-01c). Run after the other scripts on the same fresh database. The script makes its own companies (sc49-a … sc49-e),
# checks through the roles and removes every row it made. Gate flips and suppression happen inside one transaction that is rolled
# back. The row-equality check compares the view with 0038's definition (0049_old_views.sql), as postgres, in one transaction.
HERE="$(cd "$(dirname "$0")" && pwd)"
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
SHOWN="update core.field_policy set status = 'shown' where field_key = 'equity.segment_changes'"
SC="company_slug like 'sc49-%'"
LAST="select string_agg(company_slug || '=' || state || '@' || period_end || '/' || coalesce(latest_filed_period::text, '-'), ',' order by company_slug, change_no) from api.segment_changes where $SC"

cleanup() {
  as_pg "delete from equity.segment_change where entity_id in (select id from core.entity where slug like 'sc49-%');
         delete from equity.segment_lineup where entity_id in (select id from core.entity where slug like 'sc49-%');
         delete from equity.filing where entity_id in (select id from core.entity where slug like 'sc49-%');
         delete from core.document where entity_id in (select id from core.entity where slug like 'sc49-%');
         delete from core.event where slug like 'sc49-%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'sc49-%');
         delete from core.entity where slug like 'sc49-%';
         update core.field_policy set status = 'hidden' where field_key = 'equity.segment_changes';
         update core.source set is_suppressed = false where key in ('nse','bse');" >/dev/null
}
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
-- a quarter's results announcement with one filing of the given basis; returns the event id
create function pg_temp.mk_q(p_slug text, p_end date, p_status text, p_source text, p_basis text) returns bigint language plpgsql as $f$
declare eid integer; evid bigint; did bigint; tag text := p_slug || '-' || p_end || '-' || p_basis;
begin
  select id into eid from core.entity where slug = p_slug;
  insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
    values ('results_filing', eid, tag, 'sc49', p_end, p_end + 40, p_status,
            (select id from core.source where key = p_source), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, is_published)
    values ('results_xbrl', eid, evid, 'sc49 ' || tag, (select id from core.source where key = p_source), tag,
            'https://example.com/sc49/' || tag || '.xml', false) returning id into did;
  insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end, filed_at, source_url, exchange)
    values (did, eid, evid, p_basis, 'ind_as', p_end, null, null, p_end + 40,
            'https://example.com/sc49/' || tag || '.xml', upper(p_source));
  return evid;
end $f$;
-- a quarter's line-up: the names, their revenue, multi or single
create function pg_temp.mk_lines(p_ev bigint, p_names text[], p_rev numeric[], p_reporting text) returns void language plpgsql as $f$
declare i int;
begin
  for i in 1 .. cardinality(p_names) loop
    insert into equity.segment_lineup (results_event_id, line_no, entity_id, document_id, period_end, reporting, description, revenue, parser_version)
      select p_ev, i, f.entity_id, f.document_id, f.period_end, p_reporting, p_names[i], p_rev[i], 2
      from equity.filing f where f.event_id = p_ev and f.basis = 'consolidated';
  end loop;
end $f$;

insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  select 'company', 'sc49-' || s, 'Sc49 ' || upper(s) || ' Ltd', 'sc49-' || s, 'IN', 'INR', true
  from unnest(array['a','b','c','d','e']) as s;
insert into equity.company (entity_id, isin, nse_symbol, fy_end_month)
  select e.id, 'INE0049' || lpad(row_number() over (order by e.slug)::text, 5, '0'), 'SC49' || upper(right(e.slug, 1)), 3
  from core.entity e where e.slug like 'sc49-%';

-- a (Coal India's case): one segment for four quarters, several in Jun 2026 (flagged); its Sep 2026 consolidated filing is
-- stored on a filed announcement but gave no line-up
select pg_temp.mk_lines(pg_temp.mk_q('sc49-a', d::date, 'filed', 'nse', 'consolidated'), array['Mining'], array[null::numeric], 'single')
  from unnest(array['2025-06-30','2025-09-30','2025-12-31','2026-03-31']) as d;
select pg_temp.mk_lines(pg_temp.mk_q('sc49-a', '2026-06-30', 'filed', 'nse', 'consolidated'), array['Coal','Solar'], array[100,1], 'multi');
select pg_temp.mk_q('sc49-a', '2026-09-30', 'filed', 'nse', 'consolidated');
insert into equity.segment_change (entity_id, period_end, change_no, kind, segments_before, segments_after,
                                   latest_event_id, previous_event_id, previous_period_end, rule_version)
  select (select id from core.entity where slug = 'sc49-a'), '2026-06-30', 1, 'single_to_multi', '{}', array['Coal','Solar'],
         (select id from core.event where slug = 'sc49-a-2026-06-30-consolidated'),
         (select id from core.event where slug = 'sc49-a-2026-03-31-consolidated'), '2026-03-31', 2;
-- b: five quarters compared, none flagged; its latest filing is the compared quarter's, and a later standalone filing doesn't count
select pg_temp.mk_lines(pg_temp.mk_q('sc49-b', d::date, 'filed', 'nse', 'consolidated'), array['Cement','Chemicals'], array[10,5], 'multi')
  from unnest(array['2025-09-30','2025-12-31','2026-03-31','2026-06-30','2026-09-30']) as d;
select pg_temp.mk_q('sc49-b', '2026-12-31', 'filed', 'nse', 'standalone');
-- c: five quarters compared, none flagged at Jun 2026; a Sep 2026 consolidated filing on a superseded announcement doesn't count
select pg_temp.mk_lines(pg_temp.mk_q('sc49-c', d::date, 'filed', 'nse', 'consolidated'), array['Power','Gas'], array[7,3], 'multi')
  from unnest(array['2025-06-30','2025-09-30','2025-12-31','2026-03-31','2026-06-30']) as d;
select pg_temp.mk_q('sc49-c', '2026-09-30', 'superseded', 'nse', 'consolidated');
-- d: none flagged at Jun 2026; its Sep 2026 consolidated filing (no line-up) is on BSE, so suppressing BSE hides it
select pg_temp.mk_lines(pg_temp.mk_q('sc49-d', d::date, 'filed', 'nse', 'consolidated'), array['Steel','Mining'], array[10,5], 'multi')
  from unnest(array['2025-06-30','2025-09-30','2025-12-31','2026-03-31','2026-06-30']) as d;
select pg_temp.mk_q('sc49-d', '2026-09-30', 'filed', 'bse', 'consolidated');
-- e: one reportable segment, the latest filing the compared quarter's
select pg_temp.mk_lines(pg_temp.mk_q('sc49-e', d::date, 'filed', 'nse', 'consolidated'), array['Cars'], array[null::numeric], 'single')
  from unnest(array['2026-06-30','2026-09-30']) as d;
commit;
SQL
)"
check "A01 the test rows load" "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|COMMIT|[[:space:]]|[|0-9])*$'

# --- the view
check "V01 the view still runs as the caller, anon may read it, and it keeps its comment" \
  "$(as_pg "select ('security_invoker=true' = any(c.reloptions))::text || ':' || has_table_privilege('anon', 'api.segment_changes', 'select')::text || ':' || (obj_description(c.oid, 'pg_class') like '%latest_filed_period%')::text from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname = 'segment_changes'")" \
  '^true:true:true$'
check "V02 the columns are 0038's in order, then latest_filed_period (a date)" \
  "$(as_pg "select string_agg(attname, ',' order by attnum) || ':' || (select format_type(atttypid, atttypmod) from pg_attribute where attrelid = 'api.segment_changes'::regclass and attname = 'latest_filed_period') from pg_attribute where attrelid = 'api.segment_changes'::regclass and attnum > 0 and not attisdropped")" \
  '^company_slug,company_name,period_end,state,state_reason,quarters_compared,change_no,kind,segments_before,segments_after,revenue_ratio,previous_period_end,latest_filing_url,latest_filed_at,previous_filing_url,previous_filed_at,rule_version,source_key,latest_filed_period:date$'
check "V03 every other column and every row as 0038's view gave them (hidden and shown)" \
  "$(psql $H -U postgres -c "begin; $(cat "$HERE/0049_old_views.sql")
      select (select count(*) from (select company_slug, company_name, period_end, state, state_reason, quarters_compared, change_no, kind, segments_before, segments_after, revenue_ratio, previous_period_end, latest_filing_url, latest_filed_at, previous_filing_url, previous_filed_at, rule_version, source_key from api.segment_changes except all select * from old_segment_changes) x)
          + (select count(*) from (select * from old_segment_changes except all select company_slug, company_name, period_end, state, state_reason, quarters_compared, change_no, kind, segments_before, segments_after, revenue_ratio, previous_period_end, latest_filing_url, latest_filed_at, previous_filing_url, previous_filed_at, rule_version, source_key from api.segment_changes) x)
          || ':' || (select count(*) from old_segment_changes where company_slug like 'sc49-%');
      $SHOWN;
      select (select count(*) from (select company_slug, company_name, period_end, state, state_reason, quarters_compared, change_no, kind, segments_before, segments_after, revenue_ratio, previous_period_end, latest_filing_url, latest_filed_at, previous_filing_url, previous_filed_at, rule_version, source_key from api.segment_changes except all select * from old_segment_changes) x)
          + (select count(*) from (select * from old_segment_changes except all select company_slug, company_name, period_end, state, state_reason, quarters_compared, change_no, kind, segments_before, segments_after, revenue_ratio, previous_period_end, latest_filing_url, latest_filed_at, previous_filing_url, previous_filed_at, rule_version, source_key from api.segment_changes) x)
          || ':' || (select count(*) from old_segment_changes where company_slug like 'sc49-%');
      rollback" 2>&1 | grep -v -E '^(BEGIN|CREATE VIEW|UPDATE 1|ROLLBACK)$' | tr '\n' ' ')" \
  '^0:5 0:5 $'

# --- the gate
check "G01 hidden: anon and authenticated see no row" \
  "$(as_role anon "select count(*) from api.segment_changes where $SC") $(as_role authenticated "select count(*) from api.segment_changes where $SC")" '^0 0$'

# --- latest_filed_period (shown)
check "L01 each company's latest stored consolidated quarter: a's unread Sep 2026 filing counts; b's later standalone, c's superseded filing don't" \
  "$(flip anon "$SHOWN" "$LAST")" \
  '^sc49-a=changed@2026-06-30/2026-09-30,sc49-b=none_flagged@2026-09-30/2026-09-30,sc49-c=none_flagged@2026-06-30/2026-06-30,sc49-d=none_flagged@2026-06-30/2026-09-30,sc49-e=single_segment@2026-09-30/2026-09-30$'
check "L02 suppressing bse hides d's BSE filing, so its latest is the compared quarter; the NSE companies are unchanged" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'bse'" "$LAST")" \
  '^sc49-a=changed@2026-06-30/2026-09-30,sc49-b=none_flagged@2026-09-30/2026-09-30,sc49-c=none_flagged@2026-06-30/2026-06-30,sc49-d=none_flagged@2026-06-30/2026-06-30,sc49-e=single_segment@2026-09-30/2026-09-30$'
check "L03 an unpublished later announcement doesn't count (a's latest falls back to the compared quarter)" \
  "$(flip anon "$SHOWN; update core.event set is_published = false where slug = 'sc49-a-2026-09-30-consolidated'" "select latest_filed_period from api.segment_changes where company_slug = 'sc49-a'")" \
  '^2026-06-30$'
check "L04 nothing above changed a stored row or the field policy" \
  "$(as_pg "select (select count(*) from equity.segment_lineup where entity_id in (select id from core.entity where slug like 'sc49-%')) || ':' || (select count(*) from equity.segment_change where entity_id in (select id from core.entity where slug like 'sc49-%')) || ':' || (select status from core.field_policy where field_key = 'equity.segment_changes')")" \
  '^38:1:hidden$'

cleanup
echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
