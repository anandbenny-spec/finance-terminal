#!/bin/bash
# Tests for 0042 (api.related_party_top answers within the visitor role's 3 s; issue #446, job VC-06e). Run after the other
# scripts on the same fresh database. One transaction builds seven companies, keeps 0040's view body as a throwaway view
# (public.vc42_old, security_invoker like the real one), compares it with the new view as the owner and as anon, with the
# field hidden and labelled, reads the plan of a one-company read, and rolls back, so no row is left. run_tests_0040.sh runs
# unchanged and checks the view's meaning; this script checks that the rewrite returns the same rows and can be filtered early.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg() { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
FIELD="field_key = 'equity.rpt_counterparties'"

OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
-- 0040's body, verbatim, as a throwaway view (the negative control for the plan test and the reference for the row tests)
create view public.vc42_old with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.rpt_counterparties'
), l as (
  select r.basis, r.counterparty, r.relationship, r.value_in_period, r.period_start, r.period_end,
         coalesce(nullif(equity.rpt_name_key(r.counterparty), ''), btrim(r.counterparty)) as name_key,
         ev.entity_id, co.fy_end_month, xs.key as source_key,
         case when extract(month from r.period_end) <= co.fy_end_month then extract(year from r.period_end)::int
              else extract(year from r.period_end)::int + 1 end as fy_end_year,
         case lower(btrim(r.txn_type)) when 'sale of goods or services' then 'Sale of goods or services'
                                       when 'purchase of goods or services' then 'Purchase of goods or services' end as txn_type
  from equity.rpt_line r
  join core.event ev  on ev.id = r.results_event_id and ev.status = 'filed'
  join core.source xs on xs.id = ev.source_id
  join equity.company co on co.entity_id = ev.entity_id
  where r.entered_by = 'company'
), b as (
  select entity_id, fy_end_year,
         case when bool_or(basis = 'standalone') then 'standalone' else 'consolidated' end as basis
  from l group by entity_id, fy_end_year
), t as (
  select l.entity_id, l.fy_end_year, l.fy_end_month, b.basis, l.txn_type, l.name_key,
         (array_agg(btrim(l.counterparty) order by l.period_end desc, l.value_in_period desc, btrim(l.counterparty)))[1] as counterparty,
         (array_agg(l.relationship order by l.value_in_period desc, l.relationship))[1] as relationship,
         sum(l.value_in_period) as value, min(l.period_start) as period_from, max(l.period_end) as period_to,
         array_agg(distinct l.source_key order by l.source_key) as source_keys
  from l
  join b on b.entity_id = l.entity_id and b.fy_end_year = l.fy_end_year and b.basis = l.basis
  where l.txn_type is not null
  group by l.entity_id, l.fy_end_year, l.fy_end_month, b.basis, l.txn_type, l.name_key
), r as (
  select t.*, row_number() over (partition by t.entity_id, t.fy_end_year, t.txn_type
                                 order by t.value desc, t.counterparty) as rank
  from t
)
select e.slug as company_slug, e.name as company_name, e.currency,
       case when r.fy_end_month = 12 then 'FY ' || r.fy_end_year
            else 'FY ' || (r.fy_end_year - 1) || '-' || lpad((r.fy_end_year % 100)::text, 2, '0') end as fy_label,
       r.fy_end_year, r.basis, r.txn_type, r.rank::int as rank,
       case when g.ok then r.counterparty end as counterparty,
       case when g.ok then r.relationship end as relationship,
       case when g.ok then r.value end as value,
       r.period_from, r.period_to, r.source_keys
from r
cross join g
join core.entity e on e.id = r.entity_id
where r.rank <= 5;
grant select on public.vc42_old to anon;

insert into equity.peer_group (key, market, label, source_note) values ('vc42-pg', 'IN', 'Vc42 group', 'test');
-- a, b, c: as 0040's fixture; d: December year end, half-years on different bases; g: the basis changes between fiscal years;
-- u: not published; w: a second company with the same counterparties as a (so a one-company read has something to skip)
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  select 'company', 'vc42-' || s, 'Vc42 ' || upper(s) || ' Ltd', 'vc42-' || s, 'IN', 'INR', s <> 'u'
  from unnest(array['a','b','c','d','g','u','w']) as s;
insert into equity.company (entity_id, isin, nse_symbol, bse_code, peer_group_key, valuation_family, fy_end_month)
  select e.id, 'INE0042' || lpad(row_number() over (order by e.slug)::text, 5, '0'), 'VC42' || upper(right(e.slug, 1)),
         '9042' || (row_number() over (order by e.slug)), 'vc42-pg', 'operating', case when e.slug = 'vc42-d' then 12 else 3 end
  from core.entity e where e.slug like 'vc42-%';
-- five filed events for every company (tag: period end) and a superseded one for a
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
  select 'results_filing', e.id, 'vc42-res-' || e.slug || '-' || t.tag, 'vc42', t.pe, t.pe + 40, 'filed',
         (select id from core.source where key = 'nse'), true
  from core.entity e
  cross join (values ('h1', date '2025-09-30'), ('h2', date '2026-03-31'), ('d1', date '2025-06-30'), ('d2', date '2025-12-31'),
                     ('g1', date '2026-09-30'), ('g2', date '2027-03-31')) as t(tag, pe)
  where e.slug like 'vc42-%';
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
  select 'results_filing', id, 'vc42-res-vc42-a-old', 'vc42', date '2026-03-31', date '2026-05-10', 'superseded',
         (select id from core.source where key = 'nse'), true from core.entity where slug = 'vc42-a';
with d as (
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, is_published)
    select 'results_xbrl', ev.entity_id, ev.id, 'vc42 ' || ev.slug || '-' || b, ev.source_id, 'vc42-' || ev.slug || '-' || b,
           'https://example.com/vc42/' || ev.slug || '-' || b, false
    from core.event ev cross join unnest(array['consolidated', 'standalone']) as b where ev.slug like 'vc42-res-%'
    returning id, entity_id, event_id, external_key)
insert into equity.filing (document_id, entity_id, event_id, basis, format, period_end, fy_start, fy_end, filed_at, source_url, exchange)
  select d.id, d.entity_id, d.event_id, case when d.external_key like '%-consolidated' then 'consolidated' else 'standalone' end,
         'ind_as', ev.starts_on, date '2025-04-01', ev.starts_on, ev.occurs_at, 'https://example.com/vc42/' || d.external_key, 'NSE'
  from d join core.event ev on ev.id = d.event_id;

-- lines: (event, basis, line_no, counterparty, type, entered_by, value)
create temp table vc42_lines (tag text, basis text, n int, cp text, t text, by text, val numeric) on commit drop;
insert into vc42_lines values
  -- a (0040's company a: spellings across the halves, never-counted rows)
  ('vc42-res-vc42-a-h1', 'standalone',   1, 'Alpha Industries Ltd',    'Sale of goods or services',     'company',    100),
  ('vc42-res-vc42-a-h1', 'standalone',   2, 'Beta Trading Pvt. Ltd.',  'Sale of goods or services',     'company',     90),
  ('vc42-res-vc42-a-h1', 'standalone',   3, 'Gamma & Sons Limited',    'Sale of goods or services',     'company',     80),
  ('vc42-res-vc42-a-h1', 'standalone',   4, 'Delta Corp',              'Sale of goods or services',     'company',     70),
  ('vc42-res-vc42-a-h1', 'standalone',   5, 'Epsilon Corp',            'Sale of goods or services',     'company',     60),
  ('vc42-res-vc42-a-h1', 'standalone',   6, 'Zeta Corp',               'Sale of goods or services',     'company',     50),
  ('vc42-res-vc42-a-h2', 'standalone',   1, 'ALPHA INDUSTRIES LIMITED','Sale of goods or services',     'company',     40),
  ('vc42-res-vc42-a-h2', 'standalone',   2, 'Beta Trading Pvt Ltd',    'Sale of goods or services',     'company',     10),
  ('vc42-res-vc42-a-h2', 'standalone',   3, 'Gamma and Sons Ltd',      'Sale of goods or services',     'company',     30),
  ('vc42-res-vc42-a-h2', 'standalone',   4, 'Zeta Corp.',              'Sale of goods or services',     'company',     55),
  ('vc42-res-vc42-a-h1', 'standalone',   7, 'Pur Co Ltd',              'Purchase of goods or services', 'company',     10),
  ('vc42-res-vc42-a-h2', 'standalone',   5, 'PUR CO LIMITED',          'Purchase of goods or services', 'company',      5),
  ('vc42-res-vc42-a-h1', 'standalone',   8, 'Alpha Industries Ltd',    'Purchase of goods or services', 'company',      7),
  ('vc42-res-vc42-a-h1', 'standalone',   9, 'Alpha Industries Ltd',    'Sale of goods or services',     'subsidiary', 9999),
  ('vc42-res-vc42-a-h1', 'standalone',  10, 'Zeta Corp',               'Loan',                          'company',    5000),
  ('vc42-res-vc42-a-h1', 'consolidated', 1, 'Alpha Industries Ltd',    'Sale of goods or services',     'company',    9000),
  ('vc42-res-vc42-a-old', 'standalone',  1, 'Alpha Industries Ltd',    'Sale of goods or services',     'company',    8888),
  -- b: a reversal nets inside one key, a tie, two lines of one key, names with no letters or digits
  ('vc42-res-vc42-b-h1', 'standalone',   1, 'Sigma Ltd',               'Sale of goods or services',     'company',    100),
  ('vc42-res-vc42-b-h2', 'standalone',   1, 'SIGMA LTD.',              'Sale of goods or services',     'company',    -25),
  ('vc42-res-vc42-b-h1', 'standalone',   2, 'Tau Pvt Ltd',             'Sale of goods or services',     'company',     75),
  ('vc42-res-vc42-b-h1', 'standalone',   3, 'Kappa Ltd',               'Sale of goods or services',     'company',     30),
  ('vc42-res-vc42-b-h1', 'standalone',   4, 'KAPPA LIMITED',           'Sale of goods or services',     'company',     20),
  ('vc42-res-vc42-b-h1', 'standalone',   5, '-',                       'Sale of goods or services',     'company',     10),
  ('vc42-res-vc42-b-h1', 'standalone',   6, '--',                      'Sale of goods or services',     'company',      5),
  -- c: one counterparty
  ('vc42-res-vc42-c-h1', 'standalone',   1, 'Omega Ltd',               'Sale of goods or services',     'company',     12),
  ('vc42-res-vc42-c-h2', 'standalone',   1, 'OMEGA LIMITED',           'Sale of goods or services',     'company',      3),
  -- d (December year end, FY 2025): the first half is consolidated only, the second half is standalone: the year reads standalone
  ('vc42-res-vc42-d-d1', 'consolidated', 1, 'Rho Ltd',                 'Sale of goods or services',     'company',    500),
  ('vc42-res-vc42-d-d1', 'standalone',   1, 'Rho Ltd',                 'Sale of goods or services',     'company',     50),
  ('vc42-res-vc42-d-d2', 'standalone',   1, 'RHO LIMITED',             'Sale of goods or services',     'company',     25),
  ('vc42-res-vc42-d-d2', 'consolidated', 2, 'Phi Ltd',                 'Purchase of goods or services', 'company',    900),
  -- g: FY 2025-26 has consolidated lines only; FY 2026-27 has both, so it reads standalone
  ('vc42-res-vc42-g-h1', 'consolidated', 1, 'Chi Ltd',                 'Sale of goods or services',     'company',    300),
  ('vc42-res-vc42-g-h2', 'consolidated', 1, 'Chi Limited',             'Sale of goods or services',     'company',    200),
  ('vc42-res-vc42-g-g1', 'consolidated', 1, 'Chi Ltd',                 'Sale of goods or services',     'company',    999),
  ('vc42-res-vc42-g-g1', 'standalone',   1, 'Psi Ltd',                 'Sale of goods or services',     'company',     40),
  ('vc42-res-vc42-g-g2', 'standalone',   1, 'PSI LTD',                 'Sale of goods or services',     'company',     60),
  -- u: not published; w: the same counterparties as a, other values
  ('vc42-res-vc42-u-h1', 'standalone',   1, 'Upsilon Ltd',             'Sale of goods or services',     'company',     77),
  ('vc42-res-vc42-w-h1', 'standalone',   1, 'Alpha Industries Ltd',    'Sale of goods or services',     'company',     11),
  ('vc42-res-vc42-w-h2', 'standalone',   1, 'ALPHA INDUSTRIES LIMITED','Sale of goods or services',     'company',     12);
insert into equity.rpt_line (results_event_id, basis, line_no, counterparty, relationship, txn_type, entering_party, entered_by,
                             value_in_period, period_start, period_end)
  select ev.id, v.basis, v.n, v.cp, 'Associate', v.t, 'Vc42 Ltd', v.by, v.val,
         ev.starts_on - (case when ev.slug like '%-h1' or ev.slug like '%-h2' then 182 else 91 end), ev.starts_on
  from vc42_lines v join core.event ev on ev.slug = v.tag;

select 'SETUP|' || count(*) from equity.rpt_line r join core.event ev on ev.id = r.results_event_id where ev.slug like 'vc42-res-%';

-- same rows: the new view against 0040's body, both ways, as the owner and as anon, hidden and labelled
update core.field_policy set status = 'hidden' where field_key = 'equity.rpt_counterparties';
select 'E1|' || (select count(*) from (select * from api.related_party_top except select * from public.vc42_old) x) || ':'
            || (select count(*) from (select * from public.vc42_old except select * from api.related_party_top) x) || ':'
            || (select count(*) from api.related_party_top where company_slug like 'vc42%') || ':'
            || (select count(*) from public.vc42_old where company_slug like 'vc42%');
set role anon;
select 'E3|' || (select count(*) from (select * from api.related_party_top except select * from public.vc42_old) x) || ':'
            || (select count(*) from (select * from public.vc42_old except select * from api.related_party_top) x) || ':'
            || (select count(*) from api.related_party_top where company_slug like 'vc42%') || ':'
            || (select count(*) from public.vc42_old where company_slug like 'vc42%');
reset role;
update core.field_policy set status = 'labelled' where field_key = 'equity.rpt_counterparties';
select 'E2|' || (select count(*) from (select * from api.related_party_top except select * from public.vc42_old) x) || ':'
            || (select count(*) from (select * from public.vc42_old except select * from api.related_party_top) x) || ':'
            || (select count(*) from api.related_party_top where company_slug like 'vc42%') || ':'
            || (select count(*) from public.vc42_old where company_slug like 'vc42%');
set role anon;
select 'E4|' || (select count(*) from (select * from api.related_party_top except select * from public.vc42_old) x) || ':'
            || (select count(*) from (select * from public.vc42_old except select * from api.related_party_top) x) || ':'
            || (select count(*) from api.related_party_top where company_slug like 'vc42%') || ':'
            || (select count(*) from public.vc42_old where company_slug like 'vc42%');
-- spot values, so the fixture is known to exercise what the equality is about
select 'S1|' || string_agg(rank || '=' || counterparty || ':' || value, ',' order by rank) from api.related_party_top
  where company_slug = 'vc42-a' and txn_type = 'Sale of goods or services';
select 'S2|' || string_agg(fy_label || '/' || basis || '=' || counterparty || ':' || value, ',' order by fy_end_year, txn_type, rank)
  from api.related_party_top where company_slug = 'vc42-d';
select 'S3|' || string_agg(fy_label || '/' || basis || '=' || counterparty || ':' || value, ',' order by fy_end_year, rank)
  from api.related_party_top where company_slug = 'vc42-g';
select 'S4|' || count(*) from api.related_party_top where company_slug = 'vc42-u';

-- the plan of a one-company read: the filter reaches the lines' index, and nothing is materialised
set enable_seqscan = off;
select 'PLAN-NEW-START';
explain select * from api.related_party_top where company_slug = 'vc42-a';
select 'PLAN-NEW-END';
select 'PLAN-OLD-START';
explain select * from public.vc42_old where company_slug = 'vc42-a';
select 'PLAN-OLD-END';
reset role;
rollback;
SQL
)"
get() { grep "^$1|" <<<"$OUT" | head -1 | cut -d'|' -f2-; }
plan() { sed -n "/^PLAN-$1-START/,/^PLAN-$1-END/p" <<<"$OUT"; }

check "S00 the fixture loaded (38 lines in the fixture's events)" "$(get SETUP)" '^38$'
check "E1 owner, field hidden: no row differs either way, and rows exist" "$(get E1)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "E2 owner, field labelled: no row differs either way, and rows exist" "$(get E2)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "E3 anon, field hidden: no row differs, and both views show none (the policy hides every line, 0040's R01a)" "$(get E3)" '^0:0:0:0$'
check "E4 anon, field labelled: no row differs either way, and rows exist" "$(get E4)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "S1 the merged spellings of 0040 still hold in the new view (Alpha 140 ... Beta 100, Delta 70)" "$(get S1)" '^1=ALPHA INDUSTRIES LIMITED:140(\.0*)?,2=Gamma and Sons Ltd:110(\.0*)?,3=Zeta Corp\.:105(\.0*)?,4=Beta Trading Pvt Ltd:100(\.0*)?,5=Delta Corp:70(\.0*)?$'
check "S2 a December year end with half-years on different bases reads the standalone lines for the whole year (Rho 75; Phi's consolidated-only purchase is not shown)" \
  "$(get S2)" '^FY 2025/standalone=RHO LIMITED:75(\.0*)?$'
check "S3 a basis that changes between fiscal years: FY 2025-26 consolidated (Chi 500), FY 2026-27 standalone (Psi 100)" \
  "$(get S3)" '^FY 2025-26/consolidated=Chi Limited:500(\.0*)?,FY 2026-27/standalone=PSI LTD:100(\.0*)?$'
check "S4 an unpublished company shows no row to anon" "$(get S4)" '^0$'
NEWPLAN="$(plan NEW)"; OLDPLAN="$(plan OLD)"
check "P1 the one-company read has an index condition on rpt_line.results_event_id" \
  "$(grep -c 'rpt_line' <<<"$NEWPLAN")$(grep -E 'Index Cond.*results_event_id' <<<"$NEWPLAN" | head -1 | sed 's/.*/ok/')" '^[1-9][0-9]*ok$'
check "P2 the new plan has no CTE Scan (nothing is materialised)" "$(grep -c 'CTE Scan' <<<"$NEWPLAN")" '^0$'
check "P3 control: 0040's body does materialise (a CTE Scan), so the check can fail" "$(grep -c 'CTE Scan' <<<"$OLDPLAN")" '^[1-9][0-9]*$'

check "C01 the view's columns and their order are 0040's" \
  "$(as_pg "select string_agg(column_name || ' ' || data_type, ',' order by ordinal_position) from information_schema.columns where table_schema = 'api' and table_name = 'related_party_top'")" \
  '^company_slug text,company_name text,currency text,fy_label text,fy_end_year integer,basis text,txn_type text,rank integer,counterparty text,relationship text,value numeric,period_from date,period_to date,source_keys ARRAY$'
check "C02 the view is still security_invoker" \
  "$(as_pg "select coalesce(reloptions::text, '') from pg_class where oid = 'api.related_party_top'::regclass")" 'security_invoker=true'
check "C03 grants are 0040's: anon, authenticated and service_role can select, PUBLIC cannot" \
  "$(as_pg "select has_table_privilege('anon','api.related_party_top','select') and has_table_privilege('authenticated','api.related_party_top','select') and has_table_privilege('service_role','api.related_party_top','select') and not exists (select 1 from aclexplode(coalesce(relacl, acldefault('r', relowner))) a where a.grantee = 0) from pg_class where oid = 'api.related_party_top'::regclass")" '^t$'
check "C04 the view's comment is still 0040's (spellings count as one counterparty)" \
  "$(as_pg "select obj_description('api.related_party_top'::regclass, 'pg_class') like '%count as one counterparty%' and obj_description('api.related_party_top'::regclass, 'pg_class') like '%Never the individual lines.'")" '^t$'
check "C05 the field policy is back to hidden and no fixture row is left" \
  "$(as_pg "select (select status from core.field_policy where $FIELD) || ':' || (select count(*) from core.entity where slug like 'vc42%') || ':' || (select count(*) from pg_class where relname = 'vc42_old')")" '^hidden:0:0$'

echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
