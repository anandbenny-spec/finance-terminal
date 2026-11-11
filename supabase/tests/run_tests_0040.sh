#!/bin/bash
# Tests for 0040 (api.related_party_top counts a counterparty's spellings as one; issue #420, job VC-05b). Run after the other
# scripts on the same fresh database. One transaction builds three companies (vc40-a, -b: sales and purchases with spellings
# across the half-year and year-end files; vc40-c: a hidden-gate check), reads the view through anon, and rolls back, so no row
# is left. The Python rule and this key are compared in py/tests/test_equity_rpt_name_key.py.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg() { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
FIELD="field_key = 'equity.rpt_counterparties'"

OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into equity.peer_group (key, market, label, source_note) values ('vc40-pg', 'IN', 'Vc40 group', 'test');
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  select 'company', 'vc40-' || s, 'Vc40 ' || upper(s) || ' Ltd', 'vc40-' || s, 'IN', 'INR', true from unnest(array['a','b','c']) as s;
insert into equity.company (entity_id, isin, nse_symbol, bse_code, peer_group_key, valuation_family)
  select e.id, 'INE0040' || lpad(row_number() over (order by e.slug)::text, 5, '0'), 'VC40' || upper(right(e.slug, 1)),
         '9040' || (row_number() over (order by e.slug)), 'vc40-pg', 'operating'
  from core.entity e where e.slug like 'vc40-%';
-- two half-years (filed) for every company, and a superseded event for company a
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
  select 'results_filing', e.id, 'vc40-res-' || e.slug || '-' || t.tag, 'vc40', t.pe, t.pe + 40, t.st,
         (select id from core.source where key = 'nse'), true
  from core.entity e
  cross join (values ('h1', date '2025-09-30', 'filed'), ('h2', date '2026-03-31', 'filed')) as t(tag, pe, st)
  where e.slug like 'vc40-%';
insert into core.event (event_type, entity_id, slug, title, starts_on, occurs_at, status, source_id, is_published)
  select 'results_filing', id, 'vc40-res-vc40-a-old', 'vc40', date '2026-03-31', date '2026-05-10', 'superseded',
         (select id from core.source where key = 'nse'), true from core.entity where slug = 'vc40-a';
-- lines: (event tag, basis, line_no, counterparty, type, entered_by, value)
insert into equity.rpt_line (results_event_id, basis, line_no, counterparty, relationship, txn_type, entering_party, entered_by,
                             value_in_period, period_start, period_end)
  select ev.id, v.basis, v.n, v.cp, 'Associate', v.t, 'Vc40 Ltd', v.by, v.val,
         case when ev.slug like '%-h1' then date '2025-04-01' else date '2025-10-01' end,
         case when ev.slug like '%-h1' then date '2025-09-30' else date '2026-03-31' end
  from (values
    -- company a, sales: Alpha, Beta, Gamma and Zeta are each spelt two ways across the halves; Epsilon is not
    ('vc40-res-vc40-a-h1', 'standalone',   1, 'Alpha Industries Ltd',    'Sale of goods or services',     'company',    100),
    ('vc40-res-vc40-a-h1', 'standalone',   2, 'Beta Trading Pvt. Ltd.',  'Sale of goods or services',     'company',     90),
    ('vc40-res-vc40-a-h1', 'standalone',   3, 'Gamma & Sons Limited',    'Sale of goods or services',     'company',     80),
    ('vc40-res-vc40-a-h1', 'standalone',   4, 'Delta Corp',              'Sale of goods or services',     'company',     70),
    ('vc40-res-vc40-a-h1', 'standalone',   5, 'Epsilon Corp',            'Sale of goods or services',     'company',     60),
    ('vc40-res-vc40-a-h1', 'standalone',   6, 'Zeta Corp',               'Sale of goods or services',     'company',     50),
    ('vc40-res-vc40-a-h2', 'standalone',   1, 'ALPHA INDUSTRIES LIMITED','Sale of goods or services',     'company',     40),
    ('vc40-res-vc40-a-h2', 'standalone',   2, 'Beta Trading Pvt Ltd',    'Sale of goods or services',     'company',     10),
    ('vc40-res-vc40-a-h2', 'standalone',   3, 'Gamma and Sons Ltd',      'Sale of goods or services',     'company',     30),
    ('vc40-res-vc40-a-h2', 'standalone',   4, 'Zeta Corp.',              'Sale of goods or services',     'company',     55),
    -- company a, purchases: its own list; Alpha appears here once; Pur Co is spelt two ways
    ('vc40-res-vc40-a-h1', 'standalone',   7, 'Pur Co Ltd',              'Purchase of goods or services', 'company',     10),
    ('vc40-res-vc40-a-h2', 'standalone',   5, 'PUR CO LIMITED',          'Purchase of goods or services', 'company',      5),
    ('vc40-res-vc40-a-h1', 'standalone',   8, 'Alpha Industries Ltd',    'Purchase of goods or services', 'company',      7),
    -- never counted: a subsidiary's row, another type, the other basis, a superseded event
    ('vc40-res-vc40-a-h1', 'standalone',   9, 'Alpha Industries Ltd',    'Sale of goods or services',     'subsidiary', 9999),
    ('vc40-res-vc40-a-h1', 'standalone',  10, 'Zeta Corp',               'Loan',                          'company',    5000),
    ('vc40-res-vc40-a-h1', 'consolidated', 1, 'Alpha Industries Ltd',    'Sale of goods or services',     'company',    9000),
    ('vc40-res-vc40-a-old', 'standalone',  1, 'Alpha Industries Ltd',    'Sale of goods or services',     'company',    8888),
    -- company b: a reversal nets inside one key; a tie breaks by the shown name; two lines of one key in one filing; names with no
    -- letters or digits are not merged
    ('vc40-res-vc40-b-h1', 'standalone',   1, 'Sigma Ltd',               'Sale of goods or services',     'company',    100),
    ('vc40-res-vc40-b-h2', 'standalone',   1, 'SIGMA LTD.',              'Sale of goods or services',     'company',    -25),
    ('vc40-res-vc40-b-h1', 'standalone',   2, 'Tau Pvt Ltd',             'Sale of goods or services',     'company',     75),
    ('vc40-res-vc40-b-h1', 'standalone',   3, 'Kappa Ltd',               'Sale of goods or services',     'company',     30),
    ('vc40-res-vc40-b-h1', 'standalone',   4, 'KAPPA LIMITED',           'Sale of goods or services',     'company',     20),
    ('vc40-res-vc40-b-h1', 'standalone',   5, '-',                       'Sale of goods or services',     'company',     10),
    ('vc40-res-vc40-b-h1', 'standalone',   6, '--',                      'Sale of goods or services',     'company',      5),
    -- company c: one counterparty, to read through the hidden gate
    ('vc40-res-vc40-c-h1', 'standalone',   1, 'Omega Ltd',               'Sale of goods or services',     'company',     12),
    ('vc40-res-vc40-c-h2', 'standalone',   1, 'OMEGA LIMITED',           'Sale of goods or services',     'company',      3)
  ) as v(tag, basis, n, cp, t, by, val)
  join core.event ev on ev.slug = v.tag;

select 'SETUP|' || count(*) from equity.rpt_line r join core.event ev on ev.id = r.results_event_id where ev.slug like 'vc40-res-%';

-- hidden: ranks stay, names and values are null
update core.field_policy set status = 'hidden' where field_key = 'equity.rpt_counterparties';
set role anon;
select 'R01|' || count(*) || ':' || count(*) filter (where counterparty is not null or value is not null or relationship is not null)
  from api.related_party_top where company_slug like 'vc40%';
reset role;

update core.field_policy set status = 'shown' where field_key = 'equity.rpt_counterparties';
set role anon;
select 'R02|' || string_agg(rank || '=' || counterparty || ':' || value, ',' order by rank) from api.related_party_top
  where company_slug = 'vc40-a' and txn_type = 'Sale of goods or services';
select 'R03|' || string_agg(rank || '=' || counterparty || ':' || value, ',' order by rank) from api.related_party_top
  where company_slug = 'vc40-a' and txn_type = 'Purchase of goods or services';
select 'R04|' || count(*) || ':' || count(*) filter (where counterparty = 'Epsilon Corp') from api.related_party_top
  where company_slug = 'vc40-a' and txn_type = 'Sale of goods or services';
select 'R05|' || string_agg(rank || '=' || counterparty || ':' || value, ',' order by rank) from api.related_party_top
  where company_slug = 'vc40-b';
select 'R06|' || string_agg(rank || '=' || counterparty || ':' || value, ',' order by rank) from api.related_party_top
  where company_slug = 'vc40-c';
select 'R07|' || min(period_from) || ':' || max(period_to) || ':' || min(fy_label) || ':' || max(fy_label) || ':' || min(basis)
  from api.related_party_top where company_slug = 'vc40-a' and txn_type = 'Sale of goods or services';
select 'R08|' || (select count(*) from api.related_party_top where company_slug like 'vc40%' and source_keys <> array['nse']);
reset role;

select 'K01|' || coalesce(string_agg(equity.rpt_name_key(x), ',' order by ord), 'none')
  from unnest(array['Gamma & Sons Limited', 'Gamma and Sons Ltd.', 'ABC LTD', 'XLtd', '  A-B_C  ', '---', '']) with ordinality as u(x, ord);
select 'K02|' || coalesce(equity.rpt_name_key(null), 'null');
rollback;
SQL
)"
get() { grep "^$1|" <<<"$OUT" | head -1 | cut -d'|' -f2-; }

check "S00 the fixture loaded (26 lines in the fixture's events)" "$(get SETUP)" '^26$'
check "R01 hidden: every row keeps its rank, and no name, relationship or value shows" "$(get R01)" '^[0-9]+:0$'
check "R02 sales, spellings merged: Alpha 140, Gamma 110, Zeta 105, Beta 100, Delta 70, each under the latest filing's wording; Epsilon (60) is sixth" \
  "$(get R02)" '^1=ALPHA INDUSTRIES LIMITED:140(\.0*)?,2=Gamma and Sons Ltd:110(\.0*)?,3=Zeta Corp\.:105(\.0*)?,4=Beta Trading Pvt Ltd:100(\.0*)?,5=Delta Corp:70(\.0*)?$'
check "R03 purchases are their own list, merged the same way (Pur Co 15, Alpha 7)" \
  "$(get R03)" '^1=PUR CO LIMITED:15(\.0*)?,2=Alpha Industries Ltd:7(\.0*)?$'
check "R04 sales list company a has exactly five rows, and Epsilon (60, not spelt twice) is not one of them" "$(get R04)" '^5:0$'
check "R05 a reversal nets inside one key (Sigma 100 - 25), a tie breaks by the shown name, one filing's two lines of one key merge (Kappa 50, the larger line's wording), and names with no letters or digits stay apart" \
  "$(get R05)" '^1=SIGMA LTD\.:75(\.0*)?,2=Tau Pvt Ltd:75(\.0*)?,3=Kappa Ltd:50(\.0*)?,4=-:10(\.0*)?,5=--:5(\.0*)?$'
check "R06 company c: two spellings, one row, the latest filing's wording" "$(get R06)" '^1=OMEGA LIMITED:15(\.0*)?$'
check "R07 the period and basis columns are as before (standalone, the full year, FY 2025-26)" \
  "$(get R07)" '^2025-04-01:2026-03-31:FY 2025-26:FY 2025-26:standalone$'
check "R08 every row names the nse source key" "$(get R08)" '^0$'
check "K01 the key: case, &/and, Ltd/Limited, the word only, punctuation, empty" \
  "$(get K01)" '^gammaandsonslimited,gammaandsonslimited,abclimited,xltd,ab_c,,$'
check "K02 null in, null out" "$(get K02)" '^null$'

check "C01 the view's columns and their order are 0034's" \
  "$(as_pg "select string_agg(column_name, ',' order by ordinal_position) from information_schema.columns where table_schema = 'api' and table_name = 'related_party_top'")" \
  '^company_slug,company_name,currency,fy_label,fy_end_year,basis,txn_type,rank,counterparty,relationship,value,period_from,period_to,source_keys$'
check "C02 the view is still security_invoker" \
  "$(as_pg "select coalesce(reloptions::text, '') from pg_class where oid = 'api.related_party_top'::regclass")" 'security_invoker=true'
check "C03 the function pins its search_path, is immutable, and PUBLIC has no EXECUTE" \
  "$(as_pg "select (proconfig::text like '%search_path=pg_catalog%') || ':' || provolatile::text || ':' || (select count(*) from aclexplode(coalesce(proacl, acldefault('f', proowner))) a where a.grantee = 0) from pg_proc where oid = 'equity.rpt_name_key(text)'::regprocedure")" '^true:i:0$'
check "C04 anon, authenticated and service_role can execute it" \
  "$(as_pg "select has_function_privilege('anon', 'equity.rpt_name_key(text)', 'execute') and has_function_privilege('authenticated', 'equity.rpt_name_key(text)', 'execute') and has_function_privilege('service_role', 'equity.rpt_name_key(text)', 'execute')")" '^t$'
check "C05 the view's comment says spellings count as one" \
  "$(as_pg "select obj_description('api.related_party_top'::regclass, 'pg_class') like '%count as one counterparty%'")" '^t$'
check "C06 the field policy is back to hidden and no fixture row is left" \
  "$(as_pg "select (select status from core.field_policy where $FIELD) || ':' || (select count(*) from core.entity where slug like 'vc40%')")" '^hidden:0$'

echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
