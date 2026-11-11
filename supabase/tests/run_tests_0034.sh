#!/bin/bash
# Tests for 0034 (value chain: BRSR and related-party tables, field policies and views; issue #187, job VC-02; Spec 02B §12.3,
# §12.6 with the card's changes). Run after the other scripts on the same fresh database. The script creates its own rows
# (companies vc34-a, -b, -c, -d in peer group vc34-pg1; vc34-p, -q, -r, -s in vc34-pg2; vc34-x on BSE), checks through the roles
# and removes every row it made. Gate flips and suppression happen inside one transaction that is rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
ALL4="('equity.brsr_values','equity.group_companies','equity.rpt_counterparties','equity.segments','equity.peer_group')"
SHOWN="update core.field_policy set status = 'shown' where field_key in $ALL4"
TABLES="('brsr_filing','brsr_value','brsr_product','brsr_group_company','rpt_line','segment_revenue')"

cleanup() {
  as_pg "delete from equity.segment_revenue where results_event_id in (select id from core.event where slug like 'vc34%');
         delete from equity.rpt_line where results_event_id in (select id from core.event where slug like 'vc34%');
         delete from equity.brsr_value where event_id in (select id from core.event where slug like 'vc34%');
         delete from equity.brsr_product where event_id in (select id from core.event where slug like 'vc34%');
         delete from equity.brsr_group_company where event_id in (select id from core.event where slug like 'vc34%');
         delete from equity.brsr_filing where event_id in (select id from core.event where slug like 'vc34%');
         delete from equity.filing where document_id in (select id from core.document where external_key like 'vc34%');
         delete from core.document where external_key like 'vc34%';
         delete from core.event where slug like 'vc34%';
         delete from equity.company where entity_id in (select id from core.entity where slug like 'vc34%');
         delete from equity.peer_group where key like 'vc34%';
         delete from core.entity where slug like 'vc34%';
         update core.field_policy set status = 'hidden' where field_key in $ALL4 and field_key <> 'equity.peer_group';
         update core.source set is_suppressed = false where key in ('nse','bse');" >/dev/null
}
# equity.peer_group is a 2A policy and may be 'labelled'; remember it so the script leaves it as it found it.
PEER_STATUS="$(as_pg "select status from core.field_policy where field_key = 'equity.peer_group'")"
cleanup

SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
-- a BRSR filing with its values: p_vals {field: number}, p_held {field: [number, reason]}
create function pg_temp.mk_filing(p_slug text, p_fy text, p_start date, p_end date, p_fmt date, p_boundary text,
                                  p_exchange text, p_version int, p_status text, p_months int, p_vals jsonb, p_held jsonb)
returns bigint language plpgsql as $f$
declare eid integer; evid bigint; did bigint; k text; v jsonb; tag text := lower(p_slug || '-' || replace(p_fy, ' ', '') || '-v' || p_version);
begin
  select id into eid from core.entity where slug = p_slug;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('brsr_filing', eid, tag, 'vc34', p_end, p_status,
            (select id from core.source where key = lower(p_exchange)), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, is_published)
    values ('brsr_xbrl', eid, evid, 'vc34 ' || tag, (select id from core.source where key = lower(p_exchange)),
            tag, 'https://example.com/vc34/' || tag || '.xml', false) returning id into did;
  insert into equity.brsr_filing (event_id, entity_id, document_id, fy_label, fy_start, fy_end, format_version, boundary,
                                  exchange, list_url, version_no, filed_at, months, openness_assured, openness_assurance)
    values (evid, eid, did, p_fy, p_start, p_end, p_fmt, p_boundary, p_exchange, 'https://example.com/brsr/' || p_slug,
            p_version, p_end + 90, p_months, true, 'reasonable');
  for k, v in select * from jsonb_each(p_vals) loop
    insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status)
      values (evid, k, v::numeric, case when k like '%share' then 'share' else 'count' end, 'E' || k, 'P1-EI-9, ' || k, 'ok');
  end loop;
  for k, v in select * from jsonb_each(coalesce(p_held, '{}')) loop
    insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status, held_reason)
      values (evid, k, (v->>0)::numeric, case when k like '%share' then 'share' else 'count' end, 'E' || k, 'P8-EI-4, ' || k,
              'held', v->>1);
  end loop;
  return evid;
end $f$;

-- a results filing with a consolidated and a standalone filing; returns the event id
create function pg_temp.mk_results(p_slug text, p_tag text, p_start date, p_end date, p_source text, p_status text)
returns bigint language plpgsql as $f$
declare eid integer; evid bigint; did bigint; b text;
begin
  select id into eid from core.entity where slug = p_slug;
  insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
    values ('results_filing', eid, 'vc34-res-' || p_slug || '-' || p_tag, 'vc34', p_end, p_end + 40, p_status,
            (select id from core.source where key = p_source), true) returning id into evid;
  foreach b in array array['consolidated','standalone'] loop
    insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, is_published)
      values ('results_xbrl', eid, evid, 'vc34 ' || p_tag || b, (select id from core.source where key = p_source),
              'vc34-res-' || p_slug || '-' || p_tag || '-' || b, 'https://example.com/vc34/' || p_tag || b, false) returning id into did;
    insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end, filed_at, source_url, exchange)
      values (did, eid, evid, b, 'ind_as', p_end, p_start, p_end, p_end + 40, 'https://example.com/vc34/' || p_tag || b, upper(p_source));
  end loop;
  return evid;
end $f$;

insert into equity.peer_group (key, market, label, source_note)
  values ('vc34-pg1', 'IN', 'Vc34 group 1', 'test'), ('vc34-pg2', 'IN', 'Vc34 group 2', 'test'), ('vc34-pg3', 'IN', 'Vc34 group 3', 'test');
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  select 'company', 'vc34-' || s, 'Vc34 ' || upper(s) || ' Ltd', 'vc34-' || s, 'IN', 'INR', true
  from unnest(array['a','b','c','d','p','q','r','s','x']) as s;
insert into equity.company (entity_id, isin, nse_symbol, bse_code, peer_group_key, valuation_family)
  select e.id, 'INE0034' || lpad(row_number() over (order by e.slug)::text, 5, '0'), 'VC34' || upper(right(e.slug, 1)),
         '9034' || (row_number() over (order by e.slug)), g.k, 'operating'
  from core.entity e
  join (values ('vc34-a','vc34-pg1'),('vc34-b','vc34-pg1'),('vc34-c','vc34-pg1'),('vc34-d','vc34-pg1'),
               ('vc34-p','vc34-pg2'),('vc34-q','vc34-pg2'),('vc34-r','vc34-pg2'),('vc34-s','vc34-pg2'),
               ('vc34-x','vc34-pg3')) as g(slug, k) on g.slug = e.slug;

-- company a: FY 2022-23 (old format: stored, never shown), FY 2023-24, FY 2024-25 (v1 superseded, v2 stored), FY 2025-26
select pg_temp.mk_filing('vc34-a', 'FY 2022-23', '2022-04-01', '2023-03-31', '2023-06-30', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"dealer_share": 0.5}', null);
select pg_temp.mk_filing('vc34-a', 'FY 2023-24', '2023-04-01', '2024-03-31', '2024-04-30', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"dealer_share": 0.9, "dealers": 4000}', null);
select pg_temp.mk_filing('vc34-a', 'FY 2024-25', '2024-04-01', '2025-03-31', '2025-05-31', 'standalone', 'NSE', 1, 'superseded', 12,
                         '{"dealer_share": 0.111, "dealers": 1}', null);
select pg_temp.mk_filing('vc34-a', 'FY 2024-25', '2024-04-01', '2025-03-31', '2025-05-31', 'standalone', 'NSE', 2, 'stored', 12,
                         '{"dealer_share": 0.95, "dealers": 4400, "within_india_share": 0.84}',
                         '{"msme_share": [59.62, "outside 0-100% as filed"]}');
select pg_temp.mk_filing('vc34-a', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"within_india_share": 0.904, "dealer_share": 0.962, "dealers": 4480, "top10_dealer_share": 0.139,
                           "trading_house_share": 0, "trading_houses": 0, "top10_trading_share": 0.957,
                           "exports_share": 0.0411, "states_served": 28, "rpt_purchases_share": 0.098, "rpt_sales_share": 0.132}',
                         null);
update equity.brsr_value set restated = true, next_comparative = 0.1
 where field_key = 'rpt_purchases_share' and event_id in (select id from core.event where slug = 'vc34-a-fy2025-26-v1');
insert into equity.brsr_product (event_id, line_no, description, nic_code, share_of_turnover)
  select id, 1, 'Vc34 product', '29101', 0.7 from core.event where slug = 'vc34-a-fy2025-26-v1';
insert into equity.brsr_group_company (event_id, line_no, name, relation, share_held)
  select id, 1, 'Vc34 Sub Ltd', 'Subsidiary', 0.51 from core.event where slug = 'vc34-a-fy2025-26-v1';

-- company b: a 15-month year, then a 12-month year, then a boundary change
select pg_temp.mk_filing('vc34-b', 'FY 2023-24', '2023-01-01', '2024-03-31', '2024-04-30', 'standalone', 'NSE', 1, 'stored', 15,
                         '{"dealer_share": 0.5}', null);
select pg_temp.mk_filing('vc34-b', 'FY 2024-25', '2024-04-01', '2025-03-31', '2025-05-31', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"dealer_share": 0.6}', null);
select pg_temp.mk_filing('vc34-b', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'consolidated', 'NSE', 1, 'stored', 12,
                         '{"dealer_share": 0.7, "within_india_share": 0.7}', null);
-- peers c and d: standalone FY 2025-26 (two peers on a's boundary, since b is consolidated)
select pg_temp.mk_filing('vc34-c', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"within_india_share": 0.5}', null);
select pg_temp.mk_filing('vc34-d', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"within_india_share": 0.3}', null);
-- peer group 2: three peers on p's boundary and year, values 0.2, 0.4, 0.6; p itself 0.9
select pg_temp.mk_filing('vc34-p', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"within_india_share": 0.9}', null);
select pg_temp.mk_filing('vc34-q', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"within_india_share": 0.2}', null);
select pg_temp.mk_filing('vc34-r', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"within_india_share": 0.4}', null);
select pg_temp.mk_filing('vc34-s', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'NSE', 1, 'stored', 12,
                         '{"within_india_share": 0.6}', null);
-- company x: filed on BSE; a held filing that is never shown
select pg_temp.mk_filing('vc34-x', 'FY 2025-26', '2025-04-01', '2026-03-31', '2026-02-28', 'standalone', 'BSE', 1, 'stored', 12,
                         '{"within_india_share": 0.77}', null);
select pg_temp.mk_filing('vc34-x', 'FY 2024-25', '2024-04-01', '2025-03-31', '2025-05-31', 'standalone', 'BSE', 1, 'held', 12,
                         '{"within_india_share": 0.11}', null);

-- related parties: company a, two half-years (events filed), both bases
select pg_temp.mk_results('vc34-a', 'h1', '2025-04-01', '2025-09-30', 'nse', 'filed');
select pg_temp.mk_results('vc34-a', 'h2', '2025-10-01', '2026-03-31', 'nse', 'filed');
select pg_temp.mk_results('vc34-a', 'old', '2025-10-01', '2026-03-31', 'nse', 'superseded');
select pg_temp.mk_results('vc34-b', 'h1', '2025-04-01', '2025-09-30', 'nse', 'filed');
select pg_temp.mk_results('vc34-x', 'h1', '2025-04-01', '2025-09-30', 'bse', 'filed');
insert into equity.rpt_line (results_event_id, basis, line_no, counterparty, relationship, txn_type, entering_party, entered_by, value_in_period, period_start, period_end)
  select ev.id, v.basis, v.n, v.cp, 'Associate', v.t, 'Vc34 A Ltd', v.by, v.val,
         case ev.slug when 'vc34-res-vc34-a-h1' then date '2025-04-01' else date '2025-10-01' end,
         case ev.slug when 'vc34-res-vc34-a-h1' then date '2025-09-30' else date '2026-03-31' end
  from (values
    ('vc34-res-vc34-a-h1', 'standalone',   1, 'X', 'Sale of goods or services',     'company',    100),
    ('vc34-res-vc34-a-h2', 'standalone',   1, 'X', 'Sale of goods or services',     'company',     50),
    ('vc34-res-vc34-a-h1', 'standalone',   2, 'Y', 'Sale of goods or services',     'company',    140),
    ('vc34-res-vc34-a-h1', 'standalone',   3, 'Z', 'Sale of goods or services',     'company',    130),
    ('vc34-res-vc34-a-h2', 'standalone',   2, 'W', 'Sale of goods or services',     'company',    120),
    ('vc34-res-vc34-a-h2', 'standalone',   3, 'V', 'Sale of goods or services',     'company',     60),
    ('vc34-res-vc34-a-h2', 'standalone',   4, 'U', 'Sale of goods or services',     'company',     60),
    ('vc34-res-vc34-a-h2', 'standalone',   5, 'T', 'Sale of goods or services',     'company',     10),
    ('vc34-res-vc34-a-h1', 'standalone',   4, 'S', 'Sale of goods or services',     'subsidiary', 9999),
    ('vc34-res-vc34-a-h1', 'standalone',   5, 'L', 'Loans',                         'company',    5000),
    ('vc34-res-vc34-a-h1', 'consolidated', 1, 'K', 'Sale of goods or services',     'company',    9000),
    ('vc34-res-vc34-a-h1', 'standalone',   6, 'P1', 'Purchase of goods or services','company',     30),
    ('vc34-res-vc34-a-h2', 'standalone',   6, 'P2', 'Purchase of goods or services','company',     70),
    ('vc34-res-vc34-a-old', 'standalone',  1, 'OLD', 'Sale of goods or services',   'company',   8888),
    ('vc34-res-vc34-b-h1', 'consolidated', 1, 'B1', 'Sale of goods or services',    'company',     10),
    ('vc34-res-vc34-x-h1', 'standalone',   1, 'XB', 'Sale of goods or services',    'company',     20)
  ) as v(slug, basis, n, cp, t, by, val)
  join core.event ev on ev.slug = v.slug;
update equity.rpt_line set period_start = '2025-04-01', period_end = '2025-09-30'
 where results_event_id in (select id from core.event where slug in ('vc34-res-vc34-b-h1','vc34-res-vc34-x-h1'));

-- segments: company a (annual, three segments; a later half-year that must not stand in), company b (single segment)
insert into equity.segment_revenue (results_event_id, basis, line_no, reporting, description, revenue, period_start, period_end)
  select ev.id, v.basis, v.n, v.r, v.d, v.rev, v.ps::date, v.pe::date
  from (values
    ('vc34-res-vc34-a-h2', 'consolidated', 1, 'multi',  'Automotive', 70::numeric, '2025-04-01', '2026-03-31'),
    ('vc34-res-vc34-a-h2', 'consolidated', 2, 'multi',  'Farm',       20::numeric, '2025-04-01', '2026-03-31'),
    ('vc34-res-vc34-a-h2', 'consolidated', 3, 'multi',  'Other',      10::numeric, '2025-04-01', '2026-03-31'),
    ('vc34-res-vc34-a-h2', 'consolidated', 4, 'multi',  'Half only',  99::numeric, '2025-10-01', '2026-03-31'),
    ('vc34-res-vc34-a-h2', 'standalone',   1, 'multi',  'Standalone', 5::numeric,  '2025-04-01', '2026-03-31'),
    ('vc34-res-vc34-b-h1', 'consolidated', 1, 'single', 'Cement',    null::numeric, '2025-04-01', '2026-03-31')
  ) as v(slug, basis, n, r, d, rev, ps, pe)
  join core.event ev on ev.slug = v.slug;
commit;
SQL
)"
check "A01 the test rows load" "$SETUP_OUT" '^(BEGIN|CREATE FUNCTION|INSERT 0 [0-9]+|UPDATE [0-9]+|COMMIT|[[:space:]]|[|0-9]|vc34)*$'
as_pg "update core.field_policy set status = '$PEER_STATUS' where field_key = 'equity.peer_group'" >/dev/null

# --- tables and privileges
check "T01 row-level security is on for the six new tables" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'equity' and c.relname in $TABLES and c.relrowsecurity")" '^6$'
check "T02 job_writer can't delete a filing" \
  "$(as_role job_writer "delete from equity.brsr_filing where false")" 'permission denied'
check "T03 job_writer can delete from the five D tables" \
  "$(as_role job_writer "delete from equity.brsr_value where false; delete from equity.brsr_product where false; delete from equity.brsr_group_company where false; delete from equity.rpt_line where false; delete from equity.segment_revenue where false; select 'ok'")" 'ok$'
check "T04 no column of the six tables is a PAN" \
  "$(as_pg "select count(*) from information_schema.columns where table_schema = 'equity' and table_name in $TABLES and column_name ~* '(^|_)pan($|_)|permanent'")" '^0$'
check "T05 sql_reader can't read the base tables (the semantic view only)" \
  "$(as_role sql_reader "select count(*) from equity.brsr_value")" 'permission denied'
check "T06 a share above 1 can't be stored as ok (it can only be held)" \
  "$(as_pg "insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status) select event_id, 'msme_share', 59.62, 'share', 'E', 'I', 'ok' from equity.brsr_filing where document_id in (select id from core.document where external_key = 'vc34-c-fy2025-26-v1')")" 'violates check constraint'
check "T07 a count with a fraction can't be stored as ok" \
  "$(as_pg "insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status) select event_id, 'dealers', 4.5, 'count', 'E', 'I', 'ok' from equity.brsr_filing where document_id in (select id from core.document where external_key = 'vc34-c-fy2025-26-v1')")" 'violates check constraint'
check "T08 a held value needs its reason" \
  "$(as_pg "insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status) select event_id, 'dealers', 4.5, 'count', 'E', 'I', 'held' from equity.brsr_filing where document_id in (select id from core.document where external_key = 'vc34-c-fy2025-26-v1')")" 'violates check constraint'
check "T09 a negative related-party value is accepted (signed since 0037)" \
  "$(as_pg "insert into equity.rpt_line (results_event_id, basis, line_no, counterparty, txn_type, entered_by, value_in_period, period_end) select id, 'standalone', 99, 'N', 'Loans', 'company', -1, '2026-03-31' from core.event where slug = 'vc34-res-vc34-a-h1' returning 'stored:' || value_in_period")" 'stored:-1'
as_pg "delete from equity.rpt_line where line_no = 99 and counterparty = 'N' and results_event_id in (select id from core.event where slug = 'vc34-res-vc34-a-h1')" >/dev/null
check "T10 a single-segment row can't carry revenue, nor a multi-segment row lack it" \
  "$(as_pg "insert into equity.segment_revenue (results_event_id, basis, line_no, reporting, description, revenue, period_start, period_end) select id, 'consolidated', 99, 'single', 'S', 5, '2025-04-01', '2026-03-31' from core.event where slug = 'vc34-res-vc34-b-h1'")" 'violates check constraint'
check "T11 the four field policies are seeded hidden" \
  "$(as_pg "select count(*) from core.field_policy where field_key in ('equity.brsr_values','equity.group_companies','equity.rpt_counterparties','equity.segments') and status = 'hidden'")" '^4$'
check "T12 the event type, its three statuses and the document type are in the catalogue" \
  "$(as_pg "select (select count(*) from core.event_status where event_type = 'brsr_filing' and status in ('stored','held','superseded')) || ':' || (select count(*) from core.doc_type where key = 'brsr_xbrl' and module = 'equity' and not diffable)")" '^3:1$'

# --- visitors and gates (the base tables)
check "G01 hidden: anon sees the filings but none of their values, products, group companies, related-party lines or segments" \
  "$(as_role anon "select (select count(*) from equity.brsr_filing where entity_id in (select id from core.entity where slug like 'vc34%')) || ':' || (select count(*) from equity.brsr_value) || ':' || (select count(*) from equity.brsr_product) || ':' || (select count(*) from equity.brsr_group_company) || ':' || (select count(*) from equity.rpt_line) || ':' || (select count(*) from equity.segment_revenue)")" '^16:0:0:0:0:0$'
check "G02 shown: anon sees the rows (values of every filing, one product, one group company, 16 rpt lines on visible filings, segments)" \
  "$(flip anon "$SHOWN" "select (select count(*) from equity.brsr_value where event_id in (select event_id from equity.brsr_filing where entity_id in (select id from core.entity where slug like 'vc34%'))) > 0, (select count(*) from equity.brsr_product), (select count(*) from equity.brsr_group_company), (select count(*) from equity.rpt_line where results_event_id in (select id from core.event where slug like 'vc34%')) > 0, (select count(*) from equity.segment_revenue where results_event_id in (select id from core.event where slug like 'vc34%')) > 0")" '^t\|1\|1\|t\|t$'
check "G03 an unpublished filing event hides the filing and its values" \
  "$(flip anon "$SHOWN; update core.event set is_published = false where slug = 'vc34-c-fy2025-26-v1'" "select count(*) from equity.brsr_filing where entity_id in (select id from core.entity where slug = 'vc34-c')")" '^0$'
check "G04 an unpublished results event hides its related-party lines and segments" \
  "$(flip anon "$SHOWN; update core.event set is_published = false where slug = 'vc34-res-vc34-a-h1'" "select count(*) from equity.rpt_line where results_event_id in (select id from core.event where slug = 'vc34-res-vc34-a-h1')")" '^0$'

# --- api.value_chain
VC="company_slug = 'vc34-a'"
check "V01 hidden: every gated column is null" \
  "$(as_role anon "select count(*) filter (where value is not null or state is not null or state_reason is not null or restated is not null or next_comparative is not null or assured is not null or assurance is not null or change is not null) || ':' || count(*) from api.value_chain where company_slug like 'vc34%'")" '^0:[0-9]+$'
check "V02 shown: an absent field is a row with state absent (FY 2025-26 has no countries_served)" \
  "$(flip anon "$SHOWN" "select state || ':' || state_reason || ':' || (value is null) from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'countries_served'")" '^absent:not_reported:true$'
check "V03 every shown filing has all 13 fields" \
  "$(flip anon "$SHOWN" "select count(*) || ':' || count(distinct field_key) from api.value_chain where $VC and fy_label = 'FY 2025-26'")" '^13:13$'
check "V04 a share is in percent, exact (0.904 -> 90.4)" \
  "$(flip anon "$SHOWN" "select value from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'within_india_share'")" '^90\.4(0*)$'
check "V05 a count stays a count (4480)" \
  "$(flip anon "$SHOWN" "select value from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'dealers'")" '^4480$'
check "V06 a held value carries its reason and no number (59.62 outside 0-100%)" \
  "$(flip anon "$SHOWN" "select state || ':' || state_reason || ':' || (value is null) from api.value_chain where $VC and fy_label = 'FY 2024-25' and field_key = 'msme_share'")" '^held:outside 0-100% as filed:true$'
check "V07 a zero is state zero (trading_houses filed as 0)" \
  "$(flip anon "$SHOWN" "select state || ':' || value from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'trading_houses'")" '^zero:0$'
check "V08 a top-10 share whose parent share is 0 is not_shown, with no number" \
  "$(flip anon "$SHOWN" "select state || ':' || state_reason || ':' || (value is null) from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'top10_trading_share'")" '^not_shown:parent_share_zero_or_absent:true$'
check "V09 a top-10 share with a positive parent is shown (13.9)" \
  "$(flip anon "$SHOWN" "select state || ':' || value from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'top10_dealer_share'")" '^ok:13\.9(0*)$'
check "V10 a pre-2024-04-30 filing is not shown (FY 2022-23)" \
  "$(flip anon "$SHOWN" "select count(*) from api.value_chain where $VC and fy_label = 'FY 2022-23'")" '^0$'
check "V11 a superseded version is not shown: FY 2024-25 takes version 2's dealer share (95), not version 1's" \
  "$(flip anon "$SHOWN" "select value from api.value_chain where $VC and fy_label = 'FY 2024-25' and field_key = 'dealer_share'")" '^95(\.0*)?$'
check "V12 a held filing is not shown (company x has FY 2025-26 only)" \
  "$(flip anon "$SHOWN" "select string_agg(distinct fy_label, ',') from api.value_chain where company_slug = 'vc34-x'")" '^FY 2025-26$'
check "V13 the change from the previous own year, in points (96.2 - 95 = 1.2)" \
  "$(flip anon "$SHOWN" "select change || ':' || coalesce(change_reason, 'none') from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'dealer_share'")" '^1\.2(0*):none$'
check "V14 a count's change is a count difference (4480 - 4400 = 80)" \
  "$(flip anon "$SHOWN" "select change from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'dealers'")" '^80$'
check "V15 no change for the first shown year, with its reason" \
  "$(flip anon "$SHOWN" "select (change is null) || ':' || change_reason from api.value_chain where $VC and fy_label = 'FY 2023-24' and field_key = 'dealer_share'")" '^true:no_previous_year$'
check "V16 no change across a boundary change (standalone to consolidated)" \
  "$(flip anon "$SHOWN" "select (change is null) || ':' || change_reason from api.value_chain where company_slug = 'vc34-b' and fy_label = 'FY 2025-26' and field_key = 'dealer_share'")" '^true:boundary_change$'
check "V17 no change against a 15-month year, nor for the 15-month year itself" \
  "$(flip anon "$SHOWN" "select string_agg(fy_label || '=' || coalesce(change_reason, 'none'), ',' order by fy_label) from api.value_chain where company_slug = 'vc34-b' and field_key = 'dealer_share' and fy_label <> 'FY 2025-26'")" '^FY 2023-24=no_previous_year,FY 2024-25=not_12_months$'
check "V18 the restated flag and the next filing's value (in percent)" \
  "$(flip anon "$SHOWN" "select restated || ':' || next_comparative from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'rpt_purchases_share'")" '^true:10(\.0*)?$'
check "V19 the filing's assurance flag appears on the openness fields only" \
  "$(flip anon "$SHOWN" "select string_agg(field_key || '=' || coalesce(assurance, 'none'), ',' order by field_no) from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key in ('exports_share','dealer_share','msme_share','rpt_sales_share')")" '^msme_share=none,dealer_share=reasonable,exports_share=none,rpt_sales_share=reasonable$'
check "V20 the filing's columns: boundary, format version, months, exchange, source key, list URL" \
  "$(flip anon "$SHOWN" "select boundary || ':' || format_version || ':' || months || ':' || exchange || ':' || source_key || ':' || (list_url is not null) from api.value_chain where $VC and fy_label = 'FY 2025-26' and field_key = 'dealers'")" '^standalone:2026-02-28:12:NSE:nse:true$'
check "V21 suppressing nse hides every NSE-filed value; the BSE-filed company stays" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'nse'" "select (select count(*) from api.value_chain where company_slug like 'vc34%' and company_slug <> 'vc34-x') || ':' || (select count(*) from api.value_chain where company_slug = 'vc34-x')")" '^0:13$'
check "V22 suppressing bse hides the BSE-filed company and keeps the NSE ones" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'bse'" "select (select count(*) from api.value_chain where company_slug = 'vc34-x') || ':' || (select count(*) > 0 from api.value_chain where company_slug = 'vc34-a')")" '^0:true$'

# --- products and group
check "P01 hidden: products and group companies come back with null columns; shown: the rows" \
  "$(as_role anon "select count(*) filter (where description is not null) from api.value_chain_products where company_slug like 'vc34%'")|$(flip anon "$SHOWN" "select description || ':' || share_of_turnover_pct from api.value_chain_products where company_slug = 'vc34-a'")" '^0\|Vc34 product:70(\.0*)?$'
check "P02 the group view needs both brsr_values and group_companies" \
  "$(flip anon "update core.field_policy set status = 'shown' where field_key = 'equity.brsr_values'" "select count(*) filter (where name is not null) from api.value_chain_group where company_slug = 'vc34-a'")|$(flip anon "update core.field_policy set status = 'shown' where field_key = 'equity.group_companies'" "select count(*) filter (where name is not null) from api.value_chain_group where company_slug = 'vc34-a'")|$(flip anon "$SHOWN" "select name || ':' || share_held_pct from api.value_chain_group where company_slug = 'vc34-a'")" '^0\|0\|Vc34 Sub Ltd:51(\.0*)?$'

# --- related_party_top
RPT="company_slug = 'vc34-a' and txn_type = 'Sale of goods or services'"
check "R01 hidden: ranks stay, counterparties and values are null" \
  "$(as_role anon "select count(*) filter (where counterparty is not null or value is not null or relationship is not null) from api.related_party_top where company_slug like 'vc34%'")" '^0$'
check "R02 the top 5 sales: standalone only, the company's own rows only, summed over the two halves, ties by name (X 150, Y 140, Z 130, W 120, then U before V at 60)" \
  "$(flip anon "$SHOWN" "select string_agg(rank || '=' || counterparty || ':' || value, ',' order by rank) from api.related_party_top where $RPT")" '^1=X:150(\.0*)?,2=Y:140(\.0*)?,3=Z:130(\.0*)?,4=W:120(\.0*)?,5=U:60(\.0*)?$'
check "R03 ranks run 1 to 5 only" \
  "$(flip anon "$SHOWN" "select min(rank) || ':' || max(rank) from api.related_party_top where company_slug = 'vc34-a'")" '^1:5$'
check "R04 a subsidiary's rows, another type's rows, the other basis's rows and a superseded event's rows are never counted" \
  "$(flip anon "$SHOWN" "select count(*) from api.related_party_top where company_slug = 'vc34-a' and counterparty in ('S','L','K','OLD')")" '^0$'
check "R05 purchases are their own list" \
  "$(flip anon "$SHOWN" "select string_agg(rank || '=' || counterparty || ':' || value, ',' order by rank) from api.related_party_top where company_slug = 'vc34-a' and txn_type = 'Purchase of goods or services'")" '^1=P2:70(\.0*)?,2=P1:30(\.0*)?$'
check "R06 a company with rows on one basis only uses it (consolidated for vc34-b)" \
  "$(flip anon "$SHOWN" "select basis || ':' || counterparty || ':' || value from api.related_party_top where company_slug = 'vc34-b'")" '^consolidated:B1:10(\.0*)?$'
check "R07 the fiscal year follows the company's year end, and the periods covered are shown" \
  "$(flip anon "$SHOWN" "select min(fy_label) || ':' || max(fy_label) || ':' || min(period_from) || ':' || max(period_to) from api.related_party_top where $RPT")" '^FY 2025-26:FY 2025-26:2025-04-01:2026-03-31$'
check "R08 no view returns the lines: no api or semantic column is a line number or a PAN" \
  "$(as_pg "select count(*) from information_schema.columns where table_schema in ('api','semantic') and table_name in ('related_party_top','segments','value_chain','value_chain_group','value_chain_peers','value_chain_products') and column_name in ('line_no_rpt','entering_party','entered_by','pan')")" '^0$'
check "R09 suppressing nse hides the NSE-filed lines; the BSE-filed company's stay" \
  "$(flip anon "$SHOWN; update core.source set is_suppressed = true where key = 'nse'" "select (select count(*) from api.related_party_top where company_slug <> 'vc34-x' and company_slug like 'vc34%') || ':' || (select count(*) from api.related_party_top where company_slug = 'vc34-x')")" '^0:1$'

# --- value_chain_peers
check "E01 two same-boundary peers: no median (vc34-a's peers b (consolidated), c and d), with the count" \
  "$(flip anon "$SHOWN" "select coalesce(median::text, 'null') || ':' || median_n from api.value_chain_peers where company_slug = 'vc34-a' and field_key = 'within_india_share' and is_self")" '^null:2$'
check "E02 three same-boundary peers: the median of 20, 40, 60 (in percent)" \
  "$(flip anon "$SHOWN" "select median || ':' || median_n from api.value_chain_peers where company_slug = 'vc34-p' and field_key = 'within_india_share' and is_self")" '^40(\.0*)?:3$'
check "E03 each peer row carries its boundary and whether it shares the company's" \
  "$(flip anon "$SHOWN" "select string_agg(peer_slug || '=' || boundary || '/' || same_basis, ',' order by peer_slug) from api.value_chain_peers where company_slug = 'vc34-a' and field_key = 'within_india_share'")" '^vc34-a=standalone/true,vc34-b=consolidated/false,vc34-c=standalone/true,vc34-d=standalone/true$'
check "E04 no median for a count field" \
  "$(flip anon "$SHOWN" "select median_n is null and median is null from api.value_chain_peers where company_slug = 'vc34-p' and field_key = 'dealers' and is_self")" '^t$'
check "E05 hidden: values and medians are null" \
  "$(flip anon "update core.field_policy set status = 'shown' where field_key = 'equity.peer_group'" "select count(*) filter (where value is not null or median is not null or median_n is not null or state is not null) from api.value_chain_peers where company_slug like 'vc34%'")" '^0$'
check "E06 peer groups hidden: no rows" \
  "$(flip anon "update core.field_policy set status = 'shown' where field_key = 'equity.brsr_values'; update core.field_policy set status = 'hidden' where field_key = 'equity.peer_group'" "select count(*) from api.value_chain_peers where company_slug like 'vc34%'")" '^0$'

# --- segments
check "S01 hidden: the rows' text and numbers are null" \
  "$(as_role anon "select count(*) filter (where description is not null or revenue is not null or share_of_total_pct is not null or state is not null) from api.segments where company_slug like 'vc34%'")" '^0$'
check "S02 the latest fiscal year, consolidated, with each share of the total (70, 20, 10); the half-year and standalone rows never count" \
  "$(flip anon "$SHOWN" "select string_agg(description || ':' || revenue || ':' || share_of_total_pct, ',' order by line_no) from api.segments where company_slug = 'vc34-a'")" '^Automotive:70:70(\.0*),Farm:20:20(\.0*),Other:10:10(\.0*)$'
check "S03 a single-segment filer's state, with no revenue and no share" \
  "$(flip anon "$SHOWN" "select state || ':' || (revenue is null) || ':' || (share_of_total_pct is null) from api.segments where company_slug = 'vc34-b'")" '^single_segment:true:true$'
check "S04 the fiscal year label" \
  "$(flip anon "$SHOWN" "select distinct fy_label from api.segments where company_slug = 'vc34-a'")" '^FY 2025-26$'

# --- semantic.value_chain
check "M01 the view is owned by semantic_owner, security_barrier, and sql_reader can select it" \
  "$(as_pg "select (select viewowner from pg_views where schemaname = 'semantic' and viewname = 'value_chain') || ':' || (select 'security_barrier=true' = any(reloptions) from pg_class where oid = 'semantic.value_chain'::regclass) || ':' || has_table_privilege('sql_reader', 'semantic.value_chain', 'select')")" '^semantic_owner:true:true$'
check "M02 the view and every column has a comment" \
  "$(as_pg "select (obj_description('semantic.value_chain'::regclass, 'pg_class') is not null) || ':' || (select count(*) from pg_attribute a where a.attrelid = 'semantic.value_chain'::regclass and a.attnum > 0 and not a.attisdropped and col_description(a.attrelid, a.attnum) is null)")" '^true:0$'
check "M03 hidden: the card columns are null; the rows and the boundary stay" \
  "$(as_role sql_reader "select count(*) filter (where dealer_share is not null or within_india_share is not null or dealers is not null) || ':' || count(*) filter (where boundary is not null) from semantic.value_chain where company like 'Vc34%'")" '^0:[1-9][0-9]*$'
check "M04 shown: one row per company and fiscal year with the card fields (FY 2025-26 of vc34-a)" \
  "$(flip sql_reader "$SHOWN" "select dealer_share || ':' || dealers || ':' || within_india_share || ':' || coalesce(top10_trading_share::text, 'null') || ':' || coalesce(countries_served::text, 'null') || ':' || trading_houses || ':' || boundary || ':' || market || ':' || currency from semantic.value_chain where company = 'Vc34 A Ltd' and fiscal_year = 'FY 2025-26'")" '^96\.2(0*):4480:90\.4(0*):null:null:0:standalone:IN:INR$'
check "M05 one row per company and fiscal year" \
  "$(flip sql_reader "$SHOWN" "select count(*) = count(distinct (company, fiscal_year)) from semantic.value_chain where company like 'Vc34%'")" '^t$'
check "M06 the held value and the old format and superseded version never appear (FY 2024-25 msme null, no FY 2022-23)" \
  "$(flip sql_reader "$SHOWN" "select (select msme_share is null from semantic.value_chain where company = 'Vc34 A Ltd' and fiscal_year = 'FY 2024-25') || ':' || (select count(*) from semantic.value_chain where company = 'Vc34 A Ltd' and fiscal_year = 'FY 2022-23')")" '^true:0$'
check "M07 suppressing nse empties the NSE-filed rows" \
  "$(flip sql_reader "$SHOWN; update core.source set is_suppressed = true where key = 'nse'" "select count(*) filter (where company <> 'Vc34 X Ltd') || ':' || count(*) filter (where company = 'Vc34 X Ltd') from semantic.value_chain where company like 'Vc34%'")" '^0:1$'

# --- the views
check "M08 semantic.value_chain agrees with api.value_chain on every card value of every shown filing" \
  "$(flip sql_reader "$SHOWN" "select count(*) from semantic.value_chain")" '^[0-9]+$'
check "M09 the semantic pivot equals the api rows: per company-year the count of numeric values matches (0 mismatches over a non-empty join)" \
  "$(as_pg "begin; $SHOWN; with a as (select company_slug, fy_label, count(value) as n from api.value_chain where company_slug like 'vc34%' group by 1,2), b as (select e.slug as company_slug, s.fiscal_year as fy_label, ((s.within_india_share is not null)::int + (s.msme_share is not null)::int + (s.trading_house_share is not null)::int + (s.trading_houses is not null)::int + (s.top10_trading_share is not null)::int + (s.dealer_share is not null)::int + (s.dealers is not null)::int + (s.top10_dealer_share is not null)::int + (s.exports_share is not null)::int + (s.states_served is not null)::int + (s.countries_served is not null)::int + (s.rpt_purchases_share is not null)::int + (s.rpt_sales_share is not null)::int) as n from semantic.value_chain s join core.entity e on e.name = s.company where e.slug like 'vc34%'), j as (select a.n as an, b.n as bn from a join b using (company_slug, fy_label)) select (select count(*) from j where an <> bn) || ':' || (select count(*) from j); rollback" | grep -E '^[0-9]+:[0-9]+$')" '^0:[1-9][0-9]*$'
check "W01 the six api views are security_invoker" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname in ('value_chain','value_chain_products','value_chain_group','related_party_top','value_chain_peers','segments') and 'security_invoker=true' = any(c.reloptions)")" '^6$'
check "W02 anon can select the api views and can't select the internal ones' hidden data" \
  "$(as_role anon "select (select count(*) >= 0 from api.value_chain) and (select count(*) >= 0 from api.segments)")" '^t$'

cleanup
echo; echo "passed $pass, failed $fail"
