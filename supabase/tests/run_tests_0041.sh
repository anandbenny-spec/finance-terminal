#!/bin/bash
# Tests for 0041 (the US slice's read layer; issue #404, job US-05, Spec 02A.1 §6) and Plan 05 D4's test in full ("flag
# off equals today"). Run after the other scripts. Everything happens in a second, throwaway database that is dropped at
# the end (run_tests_0023.sh's pattern), so the shared one is untouched:
#   1. it gets every migration before 0041, the fixture and this script's rows: seven Indian companies (published) with
#      prices, a corporate action, a filing, metrics, snapshots, valuation runs, a decision-day move and Nifty membership;
#   2. every api view (as anon) and every semantic view (as sql_reader) is read in three states (the seeded field
#      policies; every hidden equity field shown; and that with NSE suppressed), 0041 is applied, and the same columns are
#      read again: byte-identical, with the views' owners, options, grants and comments unchanged;
#   3. seven US companies are added, unpublished, with the same kinds of rows: every view, every column, is unchanged
#      (D4: flag off equals today);
#   4. inside transactions that roll back, the US companies are published (the go-live flip): India's rows in every api
#      view, and every semantic view, don't move; the US rows read in dollars from their own sources and are compared,
#      ranked and given medians only within their own market.
HERE="$(cd "$(dirname "$0")" && pwd)"
DB=${PGDATABASE:-ft}
CONN="-h ${PGHOST:-localhost} -p ${PGPORT:-5432}"
T="${DB}_t0041"
P="psql $CONN -U postgres -X -A -t -q -v ON_ERROR_STOP=1"
MIG="$(ls "$HERE"/../migrations/*_us_views.sql)"
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $(echo "$2" | head -12)"; fail=$((fail+1)); fi; }
q() { $P -d "$T" -c "$1" 2>&1; }
US='t41-us|T41 US'          # every US test row's slug or name matches this; no Indian one does

############################################################ 1. the database before 0041, with India's rows
build() {
  $P -d postgres -c "drop database if exists $T" -c "create database $T" >/dev/null 2>&1 || return 1
  sed -E '/^create role /d' "$HERE/mock_supabase_roles.sql" | $P -d "$T" -f - >/dev/null 2>&1 || return 1
  for f in "$HERE"/../migrations/*.sql; do
    [[ "$f" < "$MIG" ]] || break
    sed -E '/^create role /d' "$f" | $P -d "$T" -f - 2>&1 | grep -v NOTICE
    [ "${PIPESTATUS[1]}" -eq 0 ] || { echo "pre-migration failed: $f"; return 1; }
  done
  $P -d "$T" -f "$HERE/test_fixture.sql" >/dev/null || return 1
}

# One market's rows: $1 is the market (IN or US). Indian companies are published, US ones not (the gate, Spec 02A.1 §3).
rows() {
  local M=$1
  cat <<SQL
begin;
create temp table c41 (slug text, name text, grp text, fam text, ex text, pe numeric, gap numeric) on commit drop;
insert into c41 values
$(if [ "$M" = IN ]; then cat <<'V'
  ('t41-in-a', 'T41 India A Ltd', 't41-in-ops',  'operating', 'NSE', 20, 0.01),
  ('t41-in-b', 'T41 India B Ltd', 't41-in-ops',  'operating', 'NSE', 22, 0.03),
  ('t41-in-c', 'T41 India C Ltd', 't41-in-ops',  'operating', 'NSE', 24, 0.05),
  ('t41-in-d', 'T41 India D Ltd', 't41-in-ops',  'operating', 'NSE', 26, 0.07),
  ('t41-in-g', 'T41 India G Ltd', 't41-in-ops',  'operating', 'BSE', 28, 0.09),
  ('t41-in-e', 'T41 India E Bank', 't41-in-bank', 'lender',   'NSE', 12, 0.02),
  ('t41-in-f', 'T41 India F Bank', 't41-in-bank', 'lender',   'NSE', 14, 0.04);
V
else cat <<'V'
  ('t41-us-a', 'T41 US A Inc', 't41-us-tech', 'operating', 'NYSE',   40, 0.02),
  ('t41-us-b', 'T41 US B Inc', 't41-us-tech', 'operating', 'NYSE',   42, 0.04),
  ('t41-us-c', 'T41 US C Inc', 't41-us-tech', 'operating', 'NYSE',   44, 0.06),
  ('t41-us-d', 'T41 US D Inc', 't41-us-tech', 'operating', 'NASDAQ', 46, 0.08),
  ('t41-us-h', 'T41 US H Inc', 't41-us-tech', 'operating', 'NASDAQ', 48, 0.10),
  ('t41-us-e', 'T41 US E Bank', 't41-us-bank', 'lender',   'NYSE',   10, 0.03),
  ('t41-us-f', 'T41 US F Bank', 't41-us-bank', 'lender',   'NASDAQ', 11, 0.05);
V
fi)
insert into equity.peer_group (key, market, label, damodaran_industry, source_note)
select distinct grp, '$M', 'Group ' || grp, null, 'test 0041' from c41;
insert into core.entity (kind, slug, name, market, currency, is_published)
select 'company', slug, name, '$M', case '$M' when 'IN' then 'INR' else 'USD' end, '$M' = 'IN' from c41;
insert into equity.company (entity_id, isin, nse_symbol, peer_group_key, valuation_family, fy_end_month, price_exchange)
select e.id, case when '$M' = 'IN' then 'INE41' || upper(substr(c.slug, 8, 1)) || '000001' end,
       case when '$M' = 'IN' then 'T41IN' || upper(substr(c.slug, 8, 1)) end, c.grp, c.fam,
       case '$M' when 'IN' then 3 else 12 end, c.ex
from c41 c join core.entity e on e.slug = c.slug;
insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 't41-raw-$M-' || s.key, s.id, null, sha256(('t41$M' || s.key)::bytea), 1, 'text/csv', now(), (select max(id) from ops.task_run)
from core.source s where s.key in ('nse', 'bse', 'yfinance', 'sec', 'curated');
-- prices: two sessions on the listing exchange; A also on the other exchange (never its price series)
insert into equity.price_daily (entity_id, trade_date, exchange, series, close, volume, raw_file_id, task_run_id)
select e.id, d.d, x.ex, 'EQ', c.pe * 5 + extract(day from d.d), 1000,
       (select id from core.raw_file where object_key = 't41-raw-$M-' || case x.ex when 'NSE' then 'nse' when 'BSE' then 'bse' else 'yfinance' end),
       (select max(id) from ops.task_run)
from c41 c join core.entity e on e.slug = c.slug
cross join (values (date '2026-11-05'), (date '2026-11-06')) d(d)
cross join lateral (select c.ex union select case c.ex when 'NSE' then 'BSE' when 'NYSE' then 'NASDAQ' end
                    where c.slug like '%-a') x(ex);
-- a dividend with an applied factor for A (from its exchange's source; the US one already in Yahoo's prices)
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
select 'corporate_action', e.id, 't41-ca-' || c.slug, 't41 action', '2026-11-06', 'effective',
       (select id from core.source where key = case c.ex when 'NSE' then 'nse' else 'yfinance' end), true
from c41 c join core.entity e on e.slug = c.slug where c.slug like '%-a';
insert into equity.corporate_action (event_id, kind, ex_date, subject, exchange, dividend_per_share)
select ev.id, 'dividend', ev.starts_on, 't41 dividend', c.ex, 2
from core.event ev join c41 c on ev.slug = 't41-ca-' || c.slug;
insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method, status)
select ev.id, ev.entity_id, ev.starts_on, case when '$M' = 'IN' then 0.98 else 1 end,
       case when '$M' = 'IN' then 'ratio' else 'source_adjusted' end, 'applied'
from core.event ev where ev.slug like 't41-ca-t41-$(echo $M | tr A-Z a-z)-%';
-- a quarter's filing and statements for A
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
select 'results_xbrl', e.id, 't41 filing', (select id from core.source where key = case '$M' when 'IN' then 'nse' else 'sec' end),
       't41-doc-' || c.slug, 'https://example.invalid/t41/' || c.slug
from c41 c join core.entity e on e.slug = c.slug where c.slug like '%-a';
insert into equity.filing (document_id, entity_id, basis, format, period_end, source_url, exchange, filed_at)
select d.id, d.entity_id, 'consolidated', case '$M' when 'IN' then 'ind_as' else 'us_gaap' end, '2026-09-30', d.source_url,
       case '$M' when 'IN' then 'NSE' else 'SEC' end, '2026-10-20T10:00:00Z'
from core.document d where d.external_key like 't41-doc-t41-$(echo $M | tr A-Z a-z)-%';
insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, currency, revenue, pbt,
                               pat_owners, operating_income)
select f.document_id, f.entity_id, 'consolidated', '2026-07-01', '2026-09-30', 'quarter',
       case '$M' when 'IN' then 'INR' else 'USD' end, 1000, 200, 150, case '$M' when 'US' then 250 end
from equity.filing f join core.document d on d.id = f.document_id where d.external_key like 't41-doc-t41-$(echo $M | tr A-Z a-z)-%';
-- metrics for every company: money, ratio, multiple and beta units, and the EBIT family
insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value, held_reason)
select e.id, m.key, m.basis, m.kind, m.pe, case when m.key = 'traded_value_median_3m' and '$M' = 'US' then null else m.v * c.pe end,
       case when m.key = 'traded_value_median_3m' and '$M' = 'US' then 'N/A: Yahoo Finance gives volume, not traded value' end
from c41 c join core.entity e on e.slug = c.slug
cross join (values ('ebit_derived', 'consolidated', 'quarter', date '2026-09-30', 10.0),
                   ('ebit_margin', 'consolidated', 'quarter', date '2026-09-30', 0.01),
                   ('ev_ebit_ttm', 'consolidated', 'market', date '2026-11-06', 1.0),
                   ('roic', 'consolidated', 'year', date '2026-03-31', 0.005),
                   ('revenue', 'consolidated', 'ttm', date '2026-09-30', 100.0),
                   ('market_cap', 'none', 'market', date '2026-11-06', 1000.0),
                   ('traded_value_median_3m', 'none', 'market', date '2026-11-06', 3.0),
                   ('pe_ttm', 'consolidated', 'market', date '2026-11-06', 1.0),
                   ('beta_bottom_up', 'none', 'market', date '2026-11-06', 0.05)) m(key, basis, kind, pe, v);
-- the weekly snapshot (a row per company) and the valuation runs: reverse DCF for operating companies, implied ROE for
-- lenders (gaps interleaved across the markets, so a rank across them would move India's), a default DCF and its grid for A
insert into equity.screener_snapshot (snapshot_date, entity_id, price, price_date, market_cap, shares, pe_ttm, pb, ev, ev_ebit,
       ev_sales, roe, ebit_margin, revenue_cagr_3y, beta, return_1y, promoter_pct, traded_value_median_3m,
       implied_growth, delivered_growth, implied_roe, delivered_roe, valuation_family, notes)
select '2026-11-07', e.id, c.pe * 5, '2026-11-06', c.pe * 1000, 1000, c.pe, c.pe / 10, c.pe * 1100, c.pe / 2, c.pe / 5,
       0.15, 0.2, 0.1, 1.1, 0.12,
       case when '$M' = 'IN' then 50 end, case when '$M' = 'IN' then c.pe * 7 end,
       case when c.fam = 'operating' then 0.1 + c.gap end, case when c.fam = 'operating' then 0.1 end,
       case when c.fam = 'lender' then 0.1 + c.gap end, case when c.fam = 'lender' then 0.1 end, c.fam,
       case when '$M' = 'US' then '{"promoter_pct": {"reason": "N/A: an Indian disclosure"}, "ebit_margin": {"label": "derived"}}'::jsonb
            else '{"ebit_margin": {"label": "derived"}}'::jsonb end
from c41 c join core.entity e on e.slug = c.slug;
insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result, delivered, inputs, model_version)
select e.id, case c.fam when 'operating' then 'reverse_dcf' else 'implied_roe' end, '2026-11-06', 'solved', null, c.pe * 5,
       0.1 + c.gap, 0.1, '{"delivered_years": 5, "beta": 1.1, "cost_of_debt": 0.07}'::jsonb, 't41'
from c41 c join core.entity e on e.slug = c.slug;
insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result, delivered, inputs, model_version)
select e.id, 'dcf_default', '2026-11-06', 'solved', null, c.pe * 5, c.pe * 6, null, '{"beta": 1.1}'::jsonb, 't41'
from c41 c join core.entity e on e.slug = c.slug where c.slug like '%-a';
insert into equity.valuation_scenario (run_id, growth, ebit_margin, wacc, value_per_share)
select v.id, 0.1, 0.2, w, 100 + w * 100 from equity.valuation_run v join core.entity e on e.id = v.entity_id
cross join (values (0.09), (0.10)) x(w) where v.kind = 'dcf_default' and e.slug like 't41-$(echo $M | tr A-Z a-z)-%';
-- index membership: India in the Nifty 50, the US companies in the Dow
insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
select (select id from core.entity where kind = 'index' and slug = case '$M' when 'IN' then 'nifty-50' else 'dow-jones' end),
       '2026-11-02', e.id, (select id from core.raw_file where object_key = 't41-raw-$M-' || case '$M' when 'IN' then 'nse' else 'curated' end)
from c41 c join core.entity e on e.slug = c.slug;
-- A's move on the fixture's held FOMC decision (rate sensitivity), from its prices' source
insert into core.event_reaction (event_id, entity_id, window_key, source_id, pre_date, post_date, pre_value, post_value, change,
                                 change_unit, value_basis, task_run_id)
select (select id from core.event where slug = 'fomc-2026-09-16'), e.id, 'd1',
       (select id from core.source where key = case '$M' when 'IN' then 'nse' else 'yfinance' end),
       '2026-09-16', '2026-09-17', 100, 101, 1, 'pct', 'adjusted_close', (select max(id) from ops.task_run)
from c41 c join core.entity e on e.slug = c.slug where c.slug like '%-a';
commit;
SQL
}

# The read of every view: "<state>|<view>|<rows>|<md5 of the rows, sorted>", one line each. $1 picks the columns (pre:
# the columns each view had before 0041, read from the catalogue then; all: every column now); $2 = filtered keeps only
# the api rows that name no US test company (the semantic views are always read whole). api views are read as anon,
# semantic views as sql_reader.
COLS=""
STATE_SEEDED=""
STATE_SHOWN="update core.field_policy set status = 'shown' where field_key like 'equity.%' and status = 'hidden';"
STATE_NSE_OFF="$STATE_SHOWN update core.source set is_suppressed = true where key = 'nse';"
# reads <pre|all> <full|filtered> <extra SQL run first, inside the transaction>
reads() {
  local api sem
  api="$(reads_sql_for api "$1" "$2")"; sem="$(reads_sql_for semantic "$1" "$2")"
  for st in SEEDED SHOWN NSE_OFF; do
    local pre="STATE_$st"
    $P -d "$T" 2>&1 <<SQL | grep -v '^SET$\|^BEGIN$\|^ROLLBACK$\|^UPDATE' | sed "s/^/$st|/"
begin;
$3
${!pre}
set local role anon;
$api
reset role;
set local role sql_reader;
$sem
rollback;
SQL
  done
}
reads_sql_for() {
  local filter="true"; [ "$3" = filtered ] && [ "$1" = api ] && filter="t::text !~ '$US'"   # semantic views: never filtered
  $P -d "$T" -v cols="$COLS" -v which="$2" -v filt="$filter" -v schema="$1" 2>&1 <<'SQL'
with v as (
  select n.nspname as s, c.relname as r,
         string_agg(quote_ident(a.attname), ', ' order by a.attnum) as cols
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
  where n.nspname = :'schema' and c.relkind = 'v'
    and (n.nspname = 'semantic' or has_table_privilege('anon', c.oid, 'select'))
  group by 1, 2),
pre as (select split_part(l, '=', 1) as k, split_part(l, '=', 2) as cols
        from unnest(string_to_array(:'cols', E'\n')) l where l <> '')
-- one statement per view (a single union of every view takes the planner seconds)
select string_agg(format('select %L || ''|'' || count(*) || ''|'' || md5(coalesce(string_agg(t::text, E''\n'' order by t::text), '''')) from (select %s from %I.%I) t where %s;',
                         v.s || '.' || v.r,
                         case when :'which' = 'pre' then coalesce((select pre.cols from pre where pre.k = v.s || '.' || v.r), v.cols) else v.cols end,
                         v.s, v.r, :'filt'), E'\n' order by v.s, v.r)
from v;
SQL
}
# Each api and semantic view's owner, options, grants, view comment and column comments (md5), one line each.
meta() {
  q "select n.nspname || '.' || c.relname || '|' || pg_get_userbyid(c.relowner) || '|' || coalesce(array_to_string(c.reloptions, ','), '')
            || '|' || coalesce(c.relacl::text, '') || '|' || md5(coalesce(obj_description(c.oid, 'pg_class'), ''))
            || '|' || md5(coalesce((select string_agg(a.attname || '=' || coalesce(col_description(c.oid, a.attnum), ''), ';' order by a.attnum)
                                    from pg_attribute a where a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped), ''))
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname in ('api', 'semantic') and c.relkind = 'v' order by 1"
}
same() { [ "$1" = "$2" ] && echo "identical ($(echo "$1" | wc -l) lines)" || echo "differ: $(diff <(echo "$1") <(echo "$2") | head -8 | tr '\n' ' ')"; }

if build; then
  rows IN | $P -d "$T" -f - >/dev/null 2>&1 || echo "india rows failed"
  COLS="$(q "select n.nspname || '.' || c.relname || '=' || string_agg(quote_ident(a.attname), ', ' order by a.attnum)
             from pg_class c join pg_namespace n on n.oid = c.relnamespace
             join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
             where n.nspname in ('api', 'semantic') and c.relkind = 'v' group by n.nspname, c.relname order by 1")"
  A="$(reads pre full "")"; META_A="$(meta)"
  APPLY="$(sed -E '/^create role /d' "$MIG" | $P -d "$T" -f - 2>&1 | grep -v NOTICE)"
  B="$(reads pre full "")"; META_B="$(meta)"
  B_ALL="$(reads all full "")"
  US_ROWS="$(rows US | $P -d "$T" -f - 2>&1)"
  C_ALL="$(reads all full "")"
  built=yes
else
  built=no
fi

############################################################ 2. India's rows byte-identical across the migration
check "V00 the throwaway database was built before 0041 with India's rows, and 0041 applied cleanly" "$built|$APPLY" '^yes\|$'
check "V01 the read is meaningful: India's rows reach every view 0041 changes (api as anon, semantic as sql_reader)" \
  "$(echo "$A" | grep '^SHOWN|' | grep -E '\|(api\.(companies|company_prices|company_financials|company_metrics|company_corporate_actions|screener|peer_comps|company_valuation|valuation_scenarios|priced_in_operating|priced_in_lenders|index_members|rate_decision_moves)|semantic\.(companies|company_financials|company_valuations|rate_decision_moves))\|' | awk -F'|' '$3 > 0' | wc -l)" '^17$'
check "V02 every api and semantic view returns the same rows, byte for byte, before and after 0041, in all three states" \
  "$(same "$A" "$B")" '^identical \([1-9][0-9]{2,} lines\)$'
check "V03 every api and semantic view keeps its owner, options, grants, view comment and column comments" \
  "$(same "$META_A" "$META_B")" '^identical \([1-9][0-9]+ lines\)$'
check "V04 the api views are still security_invoker; the semantic ones are owned by semantic_owner with security_barrier" \
  "$(q "select count(*) filter (where n.nspname = 'api' and not coalesce('security_invoker=true' = any(c.reloptions), false))
             + count(*) filter (where n.nspname = 'semantic' and (pg_get_userbyid(c.relowner) <> 'semantic_owner'
                                 or not coalesce('security_barrier=true' = any(c.reloptions), false)))
       from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('api','semantic') and c.relkind = 'v'")" '^0$'

############################################################ 3. D4: US rows loaded and unpublished change nothing
check "V05 the US rows load (seven companies, unpublished, each with prices, metrics, a snapshot and a valuation run)" \
  "$US_ROWS|$(q "select count(*) || ' ' || count(*) filter (where is_published) from core.entity where slug like 't41-us-%'")" '^\|7 0$'
check "V06 D4: with the US rows loaded and unpublished, every api and semantic view returns exactly what it did without them (every column, all three states)" \
  "$(same "$B_ALL" "$C_ALL")" '^identical \([1-9][0-9]{2,} lines\)$'

############################################################ 4. the flip, inside transactions: India doesn't move; the US reads in its own terms
FLIP="update core.entity set is_published = true where slug like 't41-us-%';"
check "V07 India's rows in every api view, and every semantic view, are unchanged when the US companies are published" \
  "$(same "$(reads all filtered "")" "$(reads all filtered "$FLIP")")" '^identical \([1-9][0-9]{2,} lines\)$'
# The probes below run in the "shown" state with the US companies published, each in a transaction that rolls back.
flip() { $P -d "$T" 2>&1 <<SQL | grep -v '^SET$\|^BEGIN$\|^ROLLBACK$\|^UPDATE'
begin;
$FLIP
$STATE_SHOWN
$2
set local role ${3:-anon};
$1;
rollback;
SQL
}
check "V08 published, the US rows do show (so V07 compared something): companies, prices, statements, metrics, actions, screener, comps, valuation and both priced-in tables" \
  "$(flip "select (select count(*) from api.companies where market = 'US') || ',' || (select count(*) from api.company_prices where market = 'US')
         || ',' || (select count(*) from api.company_financials where market = 'US') || ',' || (select count(*) from api.company_metrics where market = 'US')
         || ',' || (select count(*) from api.company_corporate_actions where market = 'US') || ',' || (select count(*) from api.screener where market = 'US')
         || ',' || (select count(*) from api.peer_comps where market = 'US') || ',' || (select count(*) from api.company_valuation where market = 'US')
         || ',' || (select count(*) from api.priced_in_operating where market = 'US') || ',' || (select count(*) from api.priced_in_lenders where market = 'US')")" \
  '^7,14,1,63,1,7,29,8,5,2$'
check "V09 the semantic views stay India-only: published, no US company reaches any of them" \
  "$(flip "select (select count(*) from semantic.companies where market <> 'IN' or company ~ '$US') + (select count(*) from semantic.company_financials where company ~ '$US')
         + (select count(*) from semantic.company_valuations where company ~ '$US') + (select count(*) from semantic.value_chain where company ~ '$US')
         + (select count(*) from semantic.rate_decision_moves where subject_name ~ '$US' or subject_key ~ '$US')
         || ' of ' || (select count(*) from semantic.companies where company ~ 'T41 India')" "" sql_reader)" '^0 of 7$'
check "V10 ... while without the market filter they would: api.rate_decision_moves (not India-only) shows US A's move" \
  "$(flip "select count(*) from api.rate_decision_moves where subject_key = 't41-us-a'")" '^1$'
check "V11 comps never cross markets: every peer is in its company's market and currency, and India's groups list only Indian peers" \
  "$(flip "select count(*) filter (where a.market <> b.market or a.currency <> b.currency or p.market <> a.market) || ' crossing; '
                || count(*) filter (where a.market = 'IN') || ' Indian pairs, ' || count(*) filter (where a.market = 'US') || ' US pairs'
         from api.peer_comps p join api.companies a on a.slug = p.company_slug join api.companies b on b.slug = p.peer_slug
         where p.row_kind = 'peer' and p.company_slug like 't41-%'")" '^0 crossing; 22 Indian pairs, 22 US pairs$'
check "V12 a US median comes from US peers only (US A: the other four's P/E, 42 to 48, median 45; India's A: 22 to 28, median 25)" \
  "$(flip "select string_agg(company_slug || '=' || pe_ttm::numeric(6,2) || ':' || peer_count || ':' || currency, ',' order by company_slug)
         from api.peer_comps where row_kind = 'median' and company_slug in ('t41-in-a', 't41-us-a')")" \
  '^t41-in-a=25\.00:4:INR,t41-us-a=45\.00:4:USD$'
check "V13 a US group with fewer than three others reads \"Too few peers in the Dow 30\"; India's note is unchanged" \
  "$(flip "select string_agg(company_slug || '=' || coalesce(median_note, '-'), ',' order by company_slug)
         from api.peer_comps where row_kind = 'median' and company_slug in ('t41-in-e', 't41-us-e')")" \
  '^t41-in-e=Too few peers in the Nifty 50 for a median,t41-us-e=Too few peers in the Dow 30 for a median$'
check "V14 the priced-in tables rank within one market: India's operating ranks 5..1 by gap as before, the US's 1..5 among themselves" \
  "$(flip "select string_agg(slug || '=' || gap_rank, ',' order by market, gap_rank) from api.priced_in_operating where slug like 't41-%'")" \
  '^t41-in-g=1,t41-in-d=2,t41-in-c=3,t41-in-b=4,t41-in-a=5,t41-us-h=1,t41-us-d=2,t41-us-c=3,t41-us-b=4,t41-us-a=5$'
check "V15 ... and the lenders' table the same way" \
  "$(flip "select string_agg(slug || '=' || gap_rank, ',' order by market, gap_rank) from api.priced_in_lenders where slug like 't41-%'")" \
  '^t41-in-f=1,t41-in-e=2,t41-us-f=1,t41-us-e=2$'
check "V16 the test is meaningful: one rank across both markets would have moved India's (its top gap, 0.09, would rank 2nd)" \
  "$(flip "select rank from (select slug, rank() over (order by gap desc) from api.priced_in_operating where slug like 't41-%' and gap is not null) r
         where slug = 't41-in-g'")" '^2$'
check "V17 money metrics read in the row's currency: US 'usd' where the catalogue says 'inr', India's 'inr' unchanged; ratios unchanged" \
  "$(flip "select string_agg(distinct market || ':' || metric_key || '=' || unit, ',' order by market || ':' || metric_key || '=' || unit)
         from api.company_metrics where slug in ('t41-in-a', 't41-us-a') and metric_key in ('market_cap', 'revenue', 'ebit_derived', 'pe_ttm', 'ebit_margin')")" \
  '^IN:ebit_derived=inr,IN:ebit_margin=fraction,IN:market_cap=inr,IN:pe_ttm=multiple,IN:revenue=inr,US:ebit_derived=usd,US:ebit_margin=fraction,US:market_cap=usd,US:pe_ttm=multiple,US:revenue=usd$'
check "V18 US EBIT is labelled operating income as filed (the metric, its family and the statements); India's keep 'derived'" \
  "$(flip "select string_agg(market || ':' || metric_key || '=' || label || '/' || coalesce(label_kind, '-'), ',' order by market, metric_key)
         from api.company_metrics where slug in ('t41-in-a', 't41-us-a') and metric_key in ('ebit_derived', 'ebit_margin', 'ev_ebit_ttm', 'roic', 'market_cap')")" \
  '^IN:ebit_derived=EBIT \(derived\)/derived,IN:ebit_margin=EBIT margin/derived,IN:ev_ebit_ttm=EV to EBIT \(TTM\)/derived,IN:market_cap=Market capitalisation/-,IN:roic=Return on invested capital/derived,US:ebit_derived=EBIT \(operating income as filed\)/operating income as filed,US:ebit_margin=EBIT margin/operating income as filed,US:ev_ebit_ttm=EV to EBIT \(TTM\)/operating income as filed,US:market_cap=Market capitalisation/-,US:roic=Return on invested capital/operating income as filed$'
check "V19 the statements and the screener carry the same EBIT labels (US: operating income as filed; India: derived)" \
  "$(flip "select (select string_agg(market || '=' || ebit_derived_label || ':' || ebit_derived_state, ',' order by market) from api.company_financials where slug in ('t41-in-a','t41-us-a'))
         || ' | ' || (select string_agg(market || '=' || ebit_label, ',' order by market) from api.screener where slug in ('t41-in-a','t41-us-a'))")" \
  '^IN=derived:value,US=operating income as filed:value \| IN=derived,US=operating income as filed$'
check "V20 the US EBIT labels follow the same field policy: hidden, every US EBIT figure and label is held like India's" \
  "$(flip "select (select count(*) from api.company_metrics where slug like 't41-%' and metric_key in ('ebit_derived','ebit_margin','ev_ebit_ttm','roic') and (value is not null or label_kind is not null or state <> 'held'))
         + (select count(*) from api.company_financials where slug like 't41-%' and (ebit_derived is not null or ebit_derived_label is not null))
         + (select count(*) from api.screener where slug like 't41-%' and (ebit_label is not null or ev_ebit is not null or ebit_margin is not null))
         || ' of ' || (select count(*) from api.company_metrics where slug like 't41-%' and metric_key = 'ebit_derived')" \
     "update core.field_policy set status = 'hidden' where field_key = 'equity.ebit_derived';")" '^0 of 14$'
check "V21 source keys name each row's own source: US prices, actions and the price column yfinance (Yahoo Finance's line); US statements and the figures built on them sec; India's nse and bse as before" \
  "$(flip "select (select string_agg(distinct market || ':' || source_key, ',' order by market || ':' || source_key) from api.company_prices where slug like 't41-%')
         || ' | ' || (select string_agg(market || ':' || source_key || ':' || attribution, ',' order by market) from api.company_corporate_actions where slug in ('t41-in-a','t41-us-a'))
         || ' | ' || (select string_agg(slug || ':' || price_source_key, ',' order by slug) from api.companies where slug in ('t41-in-a','t41-in-g','t41-us-a','t41-us-d'))
         || ' | ' || (select string_agg(market || ':' || source_key || ':' || attribution, ',' order by market) from api.company_financials where slug in ('t41-in-a','t41-us-a'))
         || ' | ' || (select string_agg(distinct v || ':' || market || ':' || source_key, ',' order by v || ':' || market || ':' || source_key) from (
                select 'metrics' v, market, source_key from api.company_metrics where slug like 't41-%'
                union all select 'screener', market, source_key from api.screener where slug like 't41-%'
                union all select 'comps', market, source_key from api.peer_comps where company_slug like 't41-%'
                union all select 'valuation', market, source_key from api.company_valuation where slug like 't41-%'
                union all select 'priced', market, source_key from api.priced_in_operating where slug like 't41-%'
                union all select 'priced', market, source_key from api.priced_in_lenders where slug like 't41-%') s)")" \
  '^IN:bse,IN:nse,US:yfinance \| IN:nse:Source: NSE,US:yfinance:Source: Yahoo Finance \| t41-in-a:nse,t41-in-g:bse,t41-us-a:yfinance,t41-us-d:yfinance \| IN:nse:Source: NSE,US:sec:Source: SEC EDGAR \| comps:IN:nse,comps:US:sec,metrics:IN:bse,metrics:IN:nse,metrics:US:sec,priced:IN:nse,priced:US:sec,screener:IN:nse,screener:US:sec,valuation:IN:nse,valuation:US:sec$'
check "V22 NSE's switch holds India's adjusted closes only; a US adjusted close follows yfinance (whose switch hides the row itself)" \
  "$(flip "select string_agg(distinct market || '=' || adj_held, ',' order by market || '=' || adj_held) || ' | US rows: '
                || (select count(*) from api.company_prices where market = 'US')
         from api.company_prices where slug like 't41-%'" "update core.source set is_suppressed = true where key = 'nse';")" \
  '^IN=true,US=false \| US rows: 14$'
check "V23 ... and with yfinance suppressed no US price, action or price key is visible" \
  "$(flip "select (select count(*) from api.company_prices where market = 'US') + (select count(*) from api.company_corporate_actions where market = 'US')
         + (select count(*) from api.companies where market = 'US' and price_source_key is not null)" \
     "update core.source set is_suppressed = true where key = 'yfinance';")" '^0$'
check "V24 the US screener rows read in dollars with their notes passed through (promoter holding N/A), and India's notes unchanged" \
  "$(flip "select string_agg(market || ':' || currency || ':' || coalesce(promoter_pct::text, 'null') || ':' || (notes ? 'promoter_pct'), ',' order by market)
         from api.screener where slug in ('t41-in-a', 't41-us-a')")" '^IN:INR:50:false,US:USD:null:true$'

$P -d postgres -c "drop database if exists $T" >/dev/null 2>&1
check "V25 the throwaway database is gone" "$($P -d postgres -c "select count(*) from pg_database where datname = '$T'")" '^0$'
echo "0041 tests: passed $pass, failed $fail"
