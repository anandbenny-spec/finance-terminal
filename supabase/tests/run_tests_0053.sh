#!/bin/bash
# Tests for 0053 (cmdbar_us_rows; issue #580, job K-03b; Spec 07 N2). Anand's option A on #580 (15 Nov 2026, 23:25 IST): US
# rows join the command bar's semantic views once published. This script holds the rule that replaces "the semantic views stay
# India-only" from 0053 on: India's rows in every semantic view are byte-identical with US rows published, and US rows appear
# only for published companies, each with market US and currency USD. (run_tests_0041.sh stops at 0041, where its own check
# still holds; see the note by its V07 and V09.)
# Everything happens in a second, throwaway database that is dropped at the end (run_tests_0041.sh's pattern):
#   1. every migration before 0053, the fixture and this script's rows: two Indian companies (published), three US companies
#      (unpublished) and a GLOBAL one (published), each with a filing and statements, the EBIT metric, a weekly snapshot and an
#      index membership;
#   2. every api view (as anon) and every semantic view (as sql_reader) is read in three states (the seeded field policies;
#      every hidden equity field shown; that with NSE suppressed); 0053 is applied, and the same columns are read again:
#      byte-identical, owners, options and grants unchanged;
#   3. inside transactions that roll back, the US companies are published: India's rows in every semantic view don't move;
#      the US rows reach semantic.companies, company_financials and company_valuations with market US and currency USD, and no
#      other view; a US company left unpublished, or one whose source is suppressed, stays out; GLOBAL never shows;
#   4. the appended columns, the grants and the comments (the text-to-SQL prompt's schema description: Arch 01 §3).
HERE="$(cd "$(dirname "$0")" && pwd)"
DB=${PGDATABASE:-ft}
CONN="-h ${PGHOST:-localhost} -p ${PGPORT:-5432}"
T="${DB}_t0053"
P="psql $CONN -U postgres -X -A -t -q -v ON_ERROR_STOP=1"
MIG="$(ls "$HERE"/../migrations/*_cmdbar_us_rows.sql)"
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $(echo "$2" | head -12)"; fail=$((fail+1)); fi; }
q() { $P -d "$T" -c "$1" 2>&1; }
US='t53-us|T53 US'          # every US test row's slug or name matches this; no Indian one does
THREE="'companies','company_financials','company_valuations'"

############################################################ 1. the database before 0053, with the test rows
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

# Indian companies are published, US ones not (the gate, Spec 02A.1 §3); the GLOBAL one is published, to show the market
# filter (0053 names IN and US) keeps any other market out.
ROWS=$(cat <<'SQL'
begin;
create temp table c53 (slug text, name text, mkt text, cur text, grp text, fam text, ex text, k numeric) on commit drop;
insert into c53 values
  ('t53-in-a', 'T53 India A Ltd',  'IN', 'INR', 't53-in-ops',  'operating', 'NSE',    20),
  ('t53-in-b', 'T53 India B Bank', 'IN', 'INR', 't53-in-bank', 'lender',    'NSE',    12),
  ('t53-us-a', 'T53 US A Inc',     'US', 'USD', 't53-us-ops',  'operating', 'NYSE',   40),
  ('t53-us-b', 'T53 US B Bank',    'US', 'USD', 't53-us-bank', 'lender',    'NASDAQ', 10),
  ('t53-us-c', 'T53 US C Inc',     'US', 'USD', 't53-us-ops',  'operating', 'NYSE',   44),
  ('t53-gl-a', 'T53 Global A',     'GLOBAL', 'EUR', 't53-in-ops', 'operating', 'NSE', 30);
insert into equity.peer_group (key, market, label, damodaran_industry, source_note)
select distinct grp, case when grp like 't53-us-%' then 'US' else 'IN' end, 'Group ' || grp, null, 'test 0053' from c53;
insert into core.entity (kind, slug, name, market, currency, is_published)
select 'company', slug, name, mkt, cur, mkt <> 'US' from c53;
insert into equity.company (entity_id, isin, nse_symbol, peer_group_key, valuation_family, fy_end_month, price_exchange, ticker, cik)
select e.id, case when c.mkt <> 'US' then 'INE53' || upper(substr(c.slug, 8, 1)) || upper(substr(c.slug, 5, 1)) || '00001' end,
       case when c.mkt <> 'US' then 'T53' || upper(substr(c.slug, 5, 2)) || upper(substr(c.slug, 8, 1)) end, c.grp, c.fam,
       case c.mkt when 'US' then 12 else 3 end, c.ex,
       case when c.mkt = 'US' then 'T53' || upper(substr(c.slug, 8, 1)) end,
       case when c.mkt = 'US' then (53000 + ascii(substr(c.slug, 8, 1)))::text end
from c53 c join core.entity e on e.slug = c.slug;
insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 't53-raw-' || s.key, s.id, null, sha256(('t53' || s.key)::bytea), 1, 'text/csv', now(), (select max(id) from ops.task_run)
from core.source s where s.key in ('nse', 'curated');
-- a quarter's filing and statements for each (US: SEC, us_gaap, USD, operating income as filed)
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
select 'results_xbrl', e.id, 't53 filing', (select id from core.source where key = case c.mkt when 'US' then 'sec' else 'nse' end),
       't53-doc-' || c.slug, 'https://example.invalid/t53/' || c.slug
from c53 c join core.entity e on e.slug = c.slug;
insert into equity.filing (document_id, entity_id, basis, format, period_end, source_url, exchange, filed_at)
select d.id, d.entity_id, 'consolidated', case c.mkt when 'US' then 'us_gaap' else 'ind_as' end, '2026-09-30', d.source_url,
       case c.mkt when 'US' then 'SEC' else 'NSE' end, '2026-10-20T10:00:00Z'
from core.document d join c53 c on d.external_key = 't53-doc-' || c.slug;
insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, currency, revenue, pbt,
                               pat_owners, operating_income)
select f.document_id, f.entity_id, 'consolidated', '2026-07-01', '2026-09-30', 'quarter', c.cur, c.k * 100, c.k * 20, c.k * 15,
       case c.mkt when 'US' then c.k * 25 end
from equity.filing f join core.document d on d.id = f.document_id join c53 c on d.external_key = 't53-doc-' || c.slug;
-- the EBIT metric for the quarter (US: operating income as filed, stored under ebit_derived, as us.metrics does)
insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value, held_reason)
select e.id, 'ebit_derived', 'consolidated', 'quarter', '2026-09-30', case c.mkt when 'US' then c.k * 25 else c.k * 22 end, null
from c53 c join core.entity e on e.slug = c.slug where c.fam = 'operating';
-- the weekly snapshot, a row per company (US: no promoter holding, no traded value)
insert into equity.screener_snapshot (snapshot_date, entity_id, price, price_date, market_cap, shares, pe_ttm, pb, ev, ev_ebit,
       ev_sales, roe, ebit_margin, revenue_cagr_3y, beta, return_1y, promoter_pct, traded_value_median_3m,
       implied_growth, delivered_growth, implied_roe, delivered_roe, valuation_family, notes)
select '2026-11-07', e.id, c.k * 5, '2026-11-06', c.k * 1000, 1000, c.k, c.k / 10, c.k * 1100, c.k / 2, c.k / 5,
       0.15, 0.2, 0.1, 1.1, 0.12,
       case when c.mkt <> 'US' then 50 end, case when c.mkt <> 'US' then c.k * 7 end,
       case when c.fam = 'operating' then 0.12 end, case when c.fam = 'operating' then 0.1 end,
       case when c.fam = 'lender' then 0.14 end, case when c.fam = 'lender' then 0.1 end, c.fam,
       case when c.mkt = 'US' then '{"promoter_pct": {"reason": "N/A: an Indian disclosure"}}'::jsonb else '{}'::jsonb end
from c53 c join core.entity e on e.slug = c.slug;
-- index membership: India in the Nifty 50, the US companies in the Dow
insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
select (select id from core.entity where kind = 'index' and slug = case c.mkt when 'US' then 'dow-jones' else 'nifty-50' end),
       '2026-11-02', e.id, (select id from core.raw_file where object_key = 't53-raw-' || case c.mkt when 'US' then 'curated' else 'nse' end)
from c53 c join core.entity e on e.slug = c.slug;
commit;
SQL
)

# The read of every view: "<state>|<view>|<rows>|<md5 of the rows, sorted>", one line each. $1 picks the columns (pre: the
# columns each view had before 0053; all: every column now); $2 = filtered keeps only the rows that name no US test company
# (for the semantic views too: from 0053 on, published US rows reach three of them). api views as anon, semantic as sql_reader.
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
  local filter="true"; [ "$3" = filtered ] && filter="t::text !~ '$US'"
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
# Each api and semantic view's owner, options and grants, one line each (the three views' comments change by design: 4.).
meta() {
  q "select n.nspname || '.' || c.relname || '|' || pg_get_userbyid(c.relowner) || '|' || coalesce(array_to_string(c.reloptions, ','), '')
            || '|' || coalesce(c.relacl::text, '')
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname in ('api', 'semantic') and c.relkind = 'v' order by 1"
}
# Every comment 0053 doesn't rewrite, md5'd per view: each view's own comment (but the three's) and each column's comment (but
# the three views' columns named in REWRITTEN).
other_comments() {
  q "select n.nspname || '.' || c.relname || '|' || md5(case when n.nspname = 'semantic' and c.relname in ($THREE) then ''
                                                            else coalesce(obj_description(c.oid, 'pg_class'), '') end
            || coalesce((select string_agg(a.attname || '=' || coalesce(col_description(c.oid, a.attnum), ''), ';' order by a.attnum)
                         from pg_attribute a where a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
                           and not (n.nspname = 'semantic' and c.relname in ($THREE) and a.attname in ($REWRITTEN))), ''))
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname in ('api', 'semantic') and c.relkind = 'v' order by 1"
}
# The columns 0053's comments rewrite or add (any view of the three); every other column comment must stay as it was.
REWRITTEN="'nse_symbol','isin','bse_code','nse_industry','peer_group','market','currency','valuation_family','price_exchange','indices',
           'ticker','cik','basis','period_kind','eps_basic','eps_diluted','face_value','ebit_derived','filed_at','price','market_cap','ev',
           'ev_ebit','ebit_margin','promoter_pct','traded_value_median_3m'"
# The view columns, "<schema>.<view>=<col>, <col>, ...", one line per view.
cols() {
  q "select n.nspname || '.' || c.relname || '=' || string_agg(quote_ident(a.attname), ', ' order by a.attnum)
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
     join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
     where n.nspname in ('api', 'semantic') and c.relkind = 'v' group by n.nspname, c.relname order by 1"
}
# What 0053 added to each view's columns: "<view>:<appended columns>" per view that changed, or what else changed.
added() {
  local line k new old
  while IFS= read -r line; do
    k="${line%%=*}"; new="${line#*=}"; old="$(echo "$COLS" | grep "^$k=" | cut -d= -f2-)"
    if [ -z "$old" ]; then echo "new view $k"
    elif [ "$new" != "$old" ]; then case "$new" in "$old, "*) echo "$k:${new#"$old, "}" ;; *) echo "changed $k" ;; esac
    fi
  done <<< "$1"
  echo "$COLS" | cut -d= -f1 | while read -r k; do echo "$1" | grep -q "^$k=" || echo "dropped $k"; done
}
same() { [ "$1" = "$2" ] && echo "identical ($(echo "$1" | wc -l) lines)" || echo "differ: $(diff <(echo "$1") <(echo "$2") | head -8 | tr '\n' ' ')"; }

if build; then
  ROWS_OUT="$(echo "$ROWS" | $P -d "$T" -f - 2>&1)"
  COLS="$(cols)"
  A="$(reads pre full "")"; META_A="$(meta)"; OC_A="$(other_comments)"
  APPLY="$(sed -E '/^create role /d' "$MIG" | $P -d "$T" -f - 2>&1 | grep -v NOTICE)"
  B="$(reads pre full "")"; META_B="$(meta)"; OC_B="$(other_comments)"; COLS_B="$(cols)"
  built=yes
else
  built=no
fi

############################################################ 2. before and after 0053, with the US companies unpublished
check "S00 the throwaway database was built before 0053 with the test rows, and 0053 applied cleanly" "$built|$ROWS_OUT|$APPLY" '^yes\|\|$'
check "S01 the read is meaningful: India's rows reach the three views 0053 changes (as sql_reader, every equity field shown)" \
  "$(echo "$A" | grep '^SHOWN|semantic\.\(companies\|company_financials\|company_valuations\)|' | cut -d'|' -f2,3 | tr '\n' ' ')" \
  '^semantic\.companies\|[2-9][0-9]* semantic\.company_financials\|[2-9][0-9]* semantic\.company_valuations\|[2-9][0-9]* $'
check "S02 every api and semantic view returns the same rows, byte for byte, before and after 0053, in all three states (US rows loaded, unpublished)" \
  "$(same "$A" "$B")" '^identical \([1-9][0-9]{2,} lines\)$'
check "S03 every api and semantic view keeps its owner, options and grants" \
  "$(same "$META_A" "$META_B")" '^identical \([1-9][0-9]+ lines\)$'
check "S04 the semantic views are still owned by semantic_owner with security_barrier; select on the three is sql_reader's only" \
  "$(q "select count(*) filter (where pg_get_userbyid(c.relowner) <> 'semantic_owner' or not coalesce('security_barrier=true' = any(c.reloptions), false))
               || '|' || count(*) filter (where c.relname in ($THREE) and has_table_privilege('sql_reader', c.oid, 'select'))
               || '|' || count(*) filter (where c.relname in ($THREE) and (has_table_privilege('anon', c.oid, 'select')
                          or has_table_privilege('authenticated', c.oid, 'select') or has_table_privilege('job_writer', c.oid, 'select')))
        from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'semantic' and c.relkind = 'v'")" '^0\|3\|0$'
check "S05 0053 appends the US identifiers (and market where it was missing) to the three views, and adds, drops or moves no other view column" \
  "$(added "$COLS_B" | sort | tr '\n' ';')" \
  '^semantic\.companies:ticker, cik;semantic\.company_financials:market, ticker;semantic\.company_valuations:ticker;$'
check "S06 every other comment is unchanged: the other views', and the three views' columns 0053 doesn't rewrite" \
  "$(same "$OC_A" "$OC_B")" '^identical \([1-9][0-9]+ lines\)$'
# in_state <state> <query>: the query as sql_reader in one of the three states, in a transaction that rolls back.
in_state() { local pre="STATE_$1"; $P -d "$T" 2>&1 <<SQL | grep -v '^SET$\|^BEGIN$\|^ROLLBACK$\|^UPDATE'
begin;
${!pre}
set local role sql_reader;
$2;
rollback;
SQL
}
NO_US="select (select count(*) from semantic.companies where market <> 'IN' or company ~ '$US')
            + (select count(*) from semantic.company_financials where market <> 'IN' or company ~ '$US')
            + (select count(*) from semantic.company_valuations where market <> 'IN' or company ~ '$US')"
check "S07 with the US companies unpublished no US row reaches the three views, in all three states" \
  "$(in_state SEEDED "$NO_US")|$(in_state SHOWN "$NO_US")|$(in_state NSE_OFF "$NO_US")" '^0\|0\|0$'
check "S08 India's rows carry market IN and currency INR in all three views, and null in the new US columns" \
  "$(in_state SHOWN "select (select string_agg(distinct market || currency || coalesce(ticker, '-') || coalesce(cik, '-'), ',') from semantic.companies where company ~ 'T53 India')
             || ' ' || (select string_agg(distinct market || currency || coalesce(ticker, '-'), ',') from semantic.company_financials where company ~ 'T53 India')
             || ' ' || (select string_agg(distinct market || currency || coalesce(ticker, '-'), ',') from semantic.company_valuations where company ~ 'T53 India')")" \
  '^ININR-- ININR- ININR-$'

############################################################ 3. the flip, inside transactions: India doesn't move; the US rows show
FLIP="update core.entity set is_published = true where slug like 't53-us-%';"
# flip <query> [extra SQL] [role]: the US companies published, every equity field shown, then the extra SQL, as sql_reader.
flip() { $P -d "$T" 2>&1 <<SQL | grep -v '^SET$\|^BEGIN$\|^ROLLBACK$\|^UPDATE'
begin;
$FLIP
$STATE_SHOWN
$2
set local role ${3:-sql_reader};
$1;
rollback;
SQL
}
check "S09 India's rows in every api and semantic view are unchanged when the US companies are published (every column, all three states)" \
  "$(same "$(reads all filtered "")" "$(reads all filtered "$FLIP")")" '^identical \([1-9][0-9]{2,} lines\)$'
check "S10 published, the US rows reach the three views, each with market US and currency USD, its ticker and (in companies) its CIK" \
  "$(flip "select (select string_agg(company || ':' || market || ':' || currency || ':' || ticker || ':' || cik || ':' || peer_group || ':' || price_exchange || ':' || indices, ',' order by company) from semantic.companies where company ~ '$US')
         || ' | ' || (select string_agg(company || ':' || market || ':' || currency || ':' || ticker || ':' || period_kind || ':' || revenue, ',' order by company) from semantic.company_financials where company ~ '$US')
         || ' | ' || (select string_agg(company || ':' || market || ':' || currency || ':' || ticker || ':' || price || ':' || coalesce(promoter_pct::text, 'null'), ',' order by company) from semantic.company_valuations where company ~ '$US')")" \
  '^T53 US A Inc:US:USD:T53A:53097:Group t53-us-ops:NYSE:Dow Jones Industrial Average,T53 US B Bank:US:USD:T53B:53098:Group t53-us-bank:NASDAQ:Dow Jones Industrial Average,T53 US C Inc:US:USD:T53C:53099:Group t53-us-ops:NYSE:Dow Jones Industrial Average \| T53 US A Inc:US:USD:T53A:quarter:4000,T53 US B Bank:US:USD:T53B:quarter:1000,T53 US C Inc:US:USD:T53C:quarter:4400 \| T53 US A Inc:US:USD:T53A:200:null,T53 US B Bank:US:USD:T53B:50:null,T53 US C Inc:US:USD:T53C:220:null$'
check "S11 every row of the three views pairs its market with its currency (IN with INR, US with USD), published or not" \
  "$(flip "select (select count(*) from semantic.companies where (market, currency) not in (('IN','INR'), ('US','USD')))
              + (select count(*) from semantic.company_financials where (market, currency) not in (('IN','INR'), ('US','USD')))
              + (select count(*) from semantic.company_valuations where (market, currency) not in (('IN','INR'), ('US','USD')))
         || ' of ' || ((select count(*) from semantic.companies) + (select count(*) from semantic.company_financials) + (select count(*) from semantic.company_valuations))")" \
  '^0 of [1-9][0-9]*$'
# Every other semantic view, as one union of its rows as text (built from the catalogue, so a new view is covered).
OTHER_SEM="$(q "select string_agg(format('select t::text as t from semantic.%I t', c.relname), ' union all ' order by c.relname)
                from pg_class c join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'semantic' and c.relkind = 'v' and c.relname not in ($THREE)")"
check "S12 published, no US company reaches any other semantic view (rate_decision_moves, value_chain and the call views stay India-only)" \
  "$(echo "$OTHER_SEM" | grep -o 'semantic\.[a-z_]*' | wc -l)|$(flip "select count(*) from ($OTHER_SEM) x where x.t ~ '$US'")" '^8\|0$'
check "S13 only published companies show: with US C left unpublished it reaches none of the three; A and B do" \
  "$(flip "select (select string_agg(company, ',' order by company) from semantic.companies where company ~ '$US')
         || ' | ' || (select count(*) from semantic.company_financials where company ~ 'T53 US C') + (select count(*) from semantic.company_valuations where company ~ 'T53 US C')" \
     "update core.entity set is_published = false where slug = 't53-us-c';")" '^T53 US A Inc,T53 US B Bank \| 0$'
check "S14 a published company of another market (GLOBAL) reaches none of the three views (0053 names IN and US only), though api.companies shows it" \
  "$(flip "select (select count(*) from semantic.companies where company ~ 'T53 Global') + (select count(*) from semantic.company_financials where company ~ 'T53 Global')
              + (select count(*) from semantic.company_valuations where company ~ 'T53 Global')") | api: $(flip "select count(*) from api.companies where slug = 't53-gl-a'" "" anon)" \
  '^0 \| api: 1$'
check "S15 the sources still decide: with sec suppressed no US statement shows (India's do); with NSE suppressed India's snapshots go" \
  "$(flip "select (select count(*) from semantic.company_financials where company ~ '$US') || ',' || (select count(*) from semantic.company_financials where company ~ 'T53 India')" \
     "update core.source set is_suppressed = true where key = 'sec';") / $(flip "select count(*) from semantic.company_valuations where company ~ 'T53 India'" \
     "update core.source set is_suppressed = true where key = 'nse';")" '^0,2 / 0$'
check "S16 US EBIT is operating income as filed (the stored metric) while the EBIT field is shown, and null while it is held, as India's" \
  "$(flip "select string_agg(company || '=' || coalesce(ebit_derived::text, 'null'), ',' order by company) from semantic.company_financials where company in ('T53 India A Ltd', 'T53 US A Inc')")/$(flip "select string_agg(company || '=' || coalesce(ebit_derived::text, 'null') || ':' || coalesce(ev_ebit::text, 'null'), ',' order by company)
         from (select f.company, f.ebit_derived, v.ev_ebit from semantic.company_financials f join semantic.company_valuations v using (company)) x where company in ('T53 India A Ltd', 'T53 US A Inc')" \
     "update core.field_policy set status = 'hidden' where field_key = 'equity.ebit_derived';")" \
  '^T53 India A Ltd=440,T53 US A Inc=1000/T53 India A Ltd=null:null,T53 US A Inc=null:null$'

############################################################ 4. the comments: the schema description says what a US row holds
check "S17 every column of every semantic view has a comment, and each view's comment names its row grain" \
  "$(q "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
        join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
        where n.nspname = 'semantic' and c.relkind = 'v' and coalesce(col_description(c.oid, a.attnum), '') = ''")|$(q "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'semantic' and c.relkind = 'v' and coalesce(obj_description(c.oid, 'pg_class'), '') !~* 'one row per'")" '^0\|0$'
check "S18 each of the three views' comments says money is in the row's currency and is never compared, ranked or summed across currencies" \
  "$(q "select count(*) from unnest(array['semantic.companies', 'semantic.company_financials', 'semantic.company_valuations']) v
        where obj_description(v::regclass, 'pg_class') like '%Money is in the row''s currency (INR for India, USD for the US). Never compare, rank or sum money across rows of different currencies%'")" '^3$'
check "S19 no comment on the three views names INR or rupees without USD or the row's currency, and no view comment calls its view India-only" \
  "$(q "select coalesce(string_agg(c.relname || '.' || coalesce(a.attname, '(view)'), ', '), 'none') from pg_class c join pg_namespace n on n.oid = c.relnamespace
        left join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
        cross join lateral (select case when a.attname is null then obj_description(c.oid, 'pg_class') else col_description(c.oid, a.attnum) end as txt) d
        where n.nspname = 'semantic' and c.relname in ($THREE)
          and ((d.txt ~* '(INR|rupee)' and d.txt !~ '(USD|row''s currency)')
               or (a.attname is null and d.txt ~* '(Indian companies only|India-only|Nifty 50 universe)'))")" '^none$'
check "S20 the EBIT comments say what a US row holds: operating income as filed (financials' ebit_derived, valuations' ev_ebit and ebit_margin)" \
  "$(q "select count(*) from (values ('semantic.company_financials', 'ebit_derived'), ('semantic.company_valuations', 'ev_ebit'), ('semantic.company_valuations', 'ebit_margin')) v(r, c)
        where col_description(v.r::regclass, (select attnum from pg_attribute where attrelid = v.r::regclass and attname = v.c)) like '%operating income as filed%'")" '^3$'
check "S21 each view with a money column carries both market and currency (the currency guard)" \
  "$(q "select string_agg(c.relname, ',' order by c.relname) from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'semantic' and c.relname in ($THREE)
          and exists (select 1 from pg_attribute a where a.attrelid = c.oid and a.attname = 'market')
          and exists (select 1 from pg_attribute a where a.attrelid = c.oid and a.attname = 'currency')")" \
  '^companies,company_financials,company_valuations$'

$P -d postgres -c "drop database if exists $T" >/dev/null 2>&1
check "S22 the throwaway database is gone" "$($P -d postgres -c "select count(*) from pg_database where datname = '$T'")" '^0$'
echo "0053 tests: passed $pass, failed $fail"
