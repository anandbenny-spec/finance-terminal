#!/bin/bash
# Tests for 0039 (the US slice's schema and catalogue rows: the sec source, the widened checks, the exchange-to-source
# mapping in four policies, index membership's NSE condition for the Nifty indices only, equity.financials.
# operating_income; issue #400, job US-01, Spec 02A.1 §4). Run after the other scripts on the same fresh database.
# The script makes its own rows (entities t39-*: one Indian company on NSE and BSE, one published US company and one
# unpublished, the US entity with the fixed id 987650039), checks through the roles and removes every row it made.
# Suppression happens inside transactions that are rolled back.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }
T39="(select id from core.entity where slug like 't39-%')"

cleanup() {
  as_pg "delete from equity.financials where entity_id in $T39;
         delete from equity.filing where entity_id in $T39;
         delete from equity.shareholding where entity_id in $T39;
         delete from equity.adjustment_factor where entity_id in $T39;
         delete from equity.corporate_action where event_id in (select id from core.event where slug like 't39-%');
         delete from equity.price_daily where entity_id in $T39;
         delete from equity.index_membership where entity_id in $T39;
         delete from equity.metric_value where entity_id in $T39;
         delete from equity.screener_snapshot where entity_id in $T39;
         delete from equity.company where entity_id in $T39;
         delete from equity.peer_group where key like 't39-%';
         delete from core.document where external_key like 't39-%';
         delete from core.event where slug like 't39-%';
         delete from core.entity_identifier where entity_id in $T39;
         delete from core.raw_file where object_key like 't39-%';
         delete from core.entity where slug like 't39-%';
         update core.source set is_suppressed = false where key in ('nse','bse','yfinance','sec');" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind

############################################################ the rows the checks use
SETUP_OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
insert into core.entity (kind, slug, name, market, currency, is_published) values
  ('company', 't39-in', 'T39 India Ltd', 'IN', 'INR', true),
  ('company', 't39-us', 'T39 Shown Holdings Inc', 'US', 'USD', true);
insert into core.entity (id, kind, slug, name, market, currency, is_published) overriding system value values
  (987650039, 'company', 't39-uh', 'T39 Hidden Holdings Inc', 'US', 'USD', false);
insert into core.entity_identifier (entity_id, scheme, value, source_id)
select e.id, v.scheme, v.value, (select id from core.source where key = 'sec')
from (values ('t39-us', 'ticker', 'T39US'), ('t39-us', 'cik', '0000000039'),
             ('t39-uh', 'ticker', 'T39UH'), ('t39-uh', 'cik', '0000000040')) v(slug, scheme, value)
join core.entity e on e.slug = v.slug;
insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 't39-raw-' || s.key, s.id, null, sha256(('t39' || s.key)::bytea), 1, 'text/csv', now(), (select max(id) from ops.task_run)
from core.source s where s.key in ('nse', 'bse', 'yfinance', 'sec', 'curated');
insert into equity.peer_group (key, market, label, damodaran_industry, source_note) values
  ('t39-in', 'IN', 'T39 Indian group', 'Software', 'test'),
  ('t39-us', 'US', 'T39 US group', 'Software (System & Application)', 'test');
insert into equity.company (entity_id, isin, nse_symbol, peer_group_key, valuation_family, fy_end_month, price_exchange)
select id, case slug when 't39-in' then 'INE000000039' end, case slug when 't39-in' then 'T39IN' end,
       case slug when 't39-in' then 't39-in' else 't39-us' end, 'operating',
       case slug when 't39-in' then 3 else 12 end, case slug when 't39-in' then 'NSE' else 'NYSE' end
from core.entity where slug like 't39-%';
-- prices: India on NSE and BSE; the US companies on NYSE and NASDAQ
insert into equity.price_daily (entity_id, trade_date, exchange, series, close, raw_file_id, task_run_id)
select e.id, '2026-11-02', v.ex, 'EQ', 100, (select id from core.raw_file where object_key = 't39-raw-' || v.src),
       (select max(id) from ops.task_run)
from (values ('t39-in', 'NSE', 'nse'), ('t39-in', 'BSE', 'bse'), ('t39-us', 'NYSE', 'yfinance'),
             ('t39-us', 'NASDAQ', 'yfinance'), ('t39-uh', 'NYSE', 'yfinance')) v(slug, ex, src)
join core.entity e on e.slug = v.slug;
-- corporate actions: India's events from nse and bse; the US ones' events from yfinance
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
select 'corporate_action', e.id, 't39-ca-' || lower(v.ex) || '-' || v.slug, 't39 action', '2026-11-03', 'effective',
       (select id from core.source where key = v.src), true
from (values ('t39-in', 'NSE', 'nse'), ('t39-in', 'BSE', 'bse'), ('t39-us', 'NYSE', 'yfinance'),
             ('t39-uh', 'NYSE', 'yfinance')) v(slug, ex, src)
join core.entity e on e.slug = v.slug;
insert into equity.corporate_action (event_id, kind, ex_date, subject, exchange, dividend_per_share)
select ev.id, 'dividend', ev.starts_on, 't39 dividend', upper(split_part(ev.slug, '-', 3)), 1
from core.event ev where ev.slug like 't39-ca-%';
-- adjustment factors: India's NSE action by ratio; the US actions already in Yahoo's prices (source_adjusted, factor 1)
insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method, status)
select ev.id, ev.entity_id, ev.starts_on, case when ev.slug like '%-nse-%' then 0.5 else 1 end,
       case when ev.slug like '%-nse-%' then 'ratio' else 'source_adjusted' end, 'applied'
from core.event ev where ev.slug in ('t39-ca-nse-t39-in', 't39-ca-nyse-t39-us', 't39-ca-nyse-t39-uh');
-- filings: India on NSE and BSE; the US companies on SEC (us_gaap), each with a financials row
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
select 'results_xbrl', e.id, 't39 filing', (select id from core.source where key = v.src), 't39-' || v.slug || '-' || v.ex,
       'https://example.invalid/t39/' || v.slug || '/' || v.ex
from (values ('t39-in', 'NSE', 'nse'), ('t39-in', 'BSE', 'bse'), ('t39-us', 'SEC', 'sec'), ('t39-uh', 'SEC', 'sec')) v(slug, ex, src)
join core.entity e on e.slug = v.slug;
insert into equity.filing (document_id, entity_id, basis, format, period_end, source_url, exchange)
select d.id, d.entity_id, 'consolidated', case when d.external_key like '%-SEC' then 'us_gaap' else 'ind_as' end,
       '2026-09-30', d.source_url, split_part(d.external_key, '-', 4)
from core.document d where d.external_key like 't39-%';
insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, currency, revenue, operating_income)
select f.document_id, f.entity_id, 'consolidated', '2026-07-01', '2026-09-30', 'quarter',
       case f.exchange when 'SEC' then 'USD' else 'INR' end, 1000, case f.exchange when 'SEC' then 250 end
from equity.filing f where f.entity_id in (select id from core.entity where slug like 't39-%');
-- shareholding: India only (NSE and BSE)
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
select 'shareholding_xbrl', e.id, 't39 shareholding', (select id from core.source where key = v.src), 't39-shp-' || v.ex,
       'https://example.invalid/t39/shp/' || v.ex
from (values ('NSE', 'nse'), ('BSE', 'bse')) v(ex, src) cross join core.entity e where e.slug = 't39-in';
insert into equity.shareholding (document_id, entity_id, as_of, promoter_pct, source_url, exchange)
select d.id, d.entity_id, '2026-09-30', 50, d.source_url, split_part(d.external_key, '-', 3)
from core.document d where d.external_key like 't39-shp-%';
-- index membership: India in the Nifty 50, the US companies in the Dow
insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
select (select id from core.entity where kind = 'index' and slug = v.idx), '2026-11-02', e.id,
       (select id from core.raw_file where object_key = 't39-raw-' || v.src)
from (values ('t39-in', 'nifty-50', 'nse'), ('t39-us', 'dow-jones', 'curated'), ('t39-uh', 'dow-jones', 'curated')) v(slug, idx, src)
join core.entity e on e.slug = v.slug;
-- metrics and a snapshot for the hidden US company, so the api sweep below has its rows to find
insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value)
values (987650039, 'market_cap', 'none', 'market', '2026-11-02', 987650039);
insert into equity.screener_snapshot (snapshot_date, entity_id, price, market_cap)
values ('2026-11-02', 987650039, 100, 987650039);
commit;
SQL
)"
check "U00 the test rows are made" "$SETUP_OUT" '^COMMIT$|^$'

############################################################ 1. the source
check "U01 the sec source: SEC EDGAR's attribution, official, no raw export, not suppressed" \
  "$(as_pg "select name||'|'||attribution||'|'||is_official||'|'||allow_raw_export||'|'||is_suppressed from core.source where key = 'sec'")" \
  '^U\.S\. Securities and Exchange Commission \(EDGAR\)\|Source: SEC EDGAR\|true\|false\|false$'
check "U02 its notes name SEC's fair-access rules and the secret, never a value" \
  "$(as_pg "select notes from core.source where key = 'sec'")" 'SEC_USER_AGENT.*10 requests a second'
check "U03 job_writer can't add or change a source (the catalogue changes only through migrations)" \
  "$(as_role job_writer "update core.source set attribution = 'x' where key = 'sec'")" 'permission denied'

############################################################ 2. the widened checks (India's values kept, others refused)
widened() { psql $H -U postgres -c "begin; $1; rollback" 2>&1 | grep -v '^BEGIN$\|^ROLLBACK$\|^INSERT\|^UPDATE'; }
check "U04 companies, prices and corporate actions take NYSE and NASDAQ as well as NSE and BSE" \
  "$(as_pg "select string_agg(conrelid::regclass||'='||pg_get_constraintdef(oid), ' ; ' order by conrelid::regclass::text) from pg_constraint
            where conname in ('company_price_exchange_check','price_daily_exchange_check','corporate_action_exchange_check')")" \
  "^equity\.company=CHECK \(\(price_exchange = ANY \(ARRAY\['NSE'::text, 'BSE'::text, 'NYSE'::text, 'NASDAQ'::text\]\)\)\) ; equity\.corporate_action=CHECK \(\(exchange = ANY \(ARRAY\['NSE'::text, 'BSE'::text, 'NYSE'::text, 'NASDAQ'::text\]\)\)\) ; equity\.price_daily=CHECK \(\(exchange = ANY \(ARRAY\['NSE'::text, 'BSE'::text, 'NYSE'::text, 'NASDAQ'::text\]\)\)\)$"
check "U05 filings take the SEC exchange and the us_gaap format; adjustment factors take source_adjusted" \
  "$(as_pg "select string_agg(pg_get_constraintdef(oid), ' ; ' order by conname) from pg_constraint
            where conname in ('filing_exchange_check','filing_format_check','adjustment_factor_method_check')")" \
  "^CHECK \(\(method = ANY \(ARRAY\['ratio'::text, 'special_preopen'::text, 'terp'::text, 'reviewed'::text, 'source_adjusted'::text\]\)\)\) ; CHECK \(\(exchange = ANY \(ARRAY\['NSE'::text, 'BSE'::text, 'SEC'::text\]\)\)\) ; CHECK \(\(format = ANY \(ARRAY\['ind_as'::text, 'bank'::text, 'nbfc'::text, 'insurer_life'::text, 'insurer_general'::text, 'us_gaap'::text\]\)\)\)$"
check "U06 an exchange outside the list is still refused (a price on LSE)" \
  "$(widened "insert into equity.price_daily (entity_id, trade_date, exchange, series, close, raw_file_id, task_run_id)
              select 987650039, '2026-11-04', 'LSE', 'EQ', 1, (select id from core.raw_file where object_key = 't39-raw-' || 'yfinance'), (select max(id) from ops.task_run)")" \
  'price_daily_exchange_check'
check "U07 a company's price exchange outside the list is still refused" \
  "$(widened "update equity.company set price_exchange = 'SEC' where entity_id = 987650039")" 'company_price_exchange_check'
check "U08 a filing's exchange or format outside the lists is still refused (NYSE as a filing exchange; ifrs)" \
  "$(widened "update equity.filing set exchange = 'NYSE' where entity_id = 987650039")$(widened "update equity.filing set format = 'ifrs' where entity_id = 987650039")" \
  'filing_exchange_check.*filing_format_check'
check "U09 a source_adjusted factor of 1 is accepted for a US action (the test rows hold two)" \
  "$(as_pg "select string_agg(method||'='||price_factor, ',') from equity.adjustment_factor where entity_id in $T39 and method = 'source_adjusted'")" '^source_adjusted=1,source_adjusted=1$'
check "U10 shareholding still takes NSE and BSE only" \
  "$(widened "update equity.shareholding set exchange = 'SEC' where entity_id in $T39")" 'shareholding_exchange_check'

############################################################ 3. India's rows exactly as before, under every switch
# For each switch, the rows of t39-in that anon sees in six tables, against 0019's rules evaluated by the postgres role
# in the same transaction: price_daily, filing and shareholding by lower(exchange); corporate_action and
# adjustment_factor by their event;
# index_membership by both entities and nse.
old_rule() { echo "select 'prices='||(select count(*) from equity.price_daily p join core.entity e on e.id = p.entity_id
                     where e.slug = 't39-in' and e.is_published
                       and not exists (select 1 from core.source s where s.key = lower(p.exchange) and s.is_suppressed))
  ||' filings='||(select count(*) from equity.filing f join core.entity e on e.id = f.entity_id
                   where e.slug = 't39-in' and e.is_published
                     and not exists (select 1 from core.source s where s.key = lower(f.exchange) and s.is_suppressed))
  ||' financials='||(select count(*) from equity.financials x join equity.filing f on f.document_id = x.document_id
                      join core.entity e on e.id = f.entity_id
                     where e.slug = 't39-in' and e.is_published
                       and not exists (select 1 from core.source s where s.key = lower(f.exchange) and s.is_suppressed))
  ||' shareholding='||(select count(*) from equity.shareholding h join core.entity e on e.id = h.entity_id
                        where e.slug = 't39-in' and e.is_published
                          and not exists (select 1 from core.source s where s.key = lower(h.exchange) and s.is_suppressed))
  ||' actions='||(select count(*) from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                   join core.entity e on e.id = ev.entity_id
                  where e.slug = 't39-in' and ev.is_published
                    and not exists (select 1 from core.source s where s.id = ev.source_id and s.is_suppressed))
  ||' factors='||(select count(*) from equity.adjustment_factor a join core.event ev on ev.id = a.event_id
                   where a.entity_id = (select id from core.entity where slug = 't39-in') and ev.is_published
                     and not exists (select 1 from core.source s where s.id = ev.source_id and s.is_suppressed))
  ||' membership='||(select count(*) from equity.index_membership m join core.entity e on e.id = m.entity_id
                      join core.entity i on i.id = m.index_entity_id
                     where e.slug = 't39-in' and e.is_published and i.is_published
                       and not exists (select 1 from core.source s where s.key = 'nse' and s.is_suppressed));"; }
as_anon() { echo "set local role anon;
select 'prices='||(select count(*) from equity.price_daily where entity_id in $T39 and entity_id = (select id from core.entity where slug = 't39-in'))
  ||' filings='||(select count(*) from equity.filing where entity_id = (select id from core.entity where slug = 't39-in'))
  ||' financials='||(select count(*) from equity.financials where entity_id = (select id from core.entity where slug = 't39-in'))
  ||' shareholding='||(select count(*) from equity.shareholding where entity_id = (select id from core.entity where slug = 't39-in'))
  ||' actions='||(select count(*) from equity.corporate_action where event_id in (select id from core.event where slug like 't39-ca-%-t39-in'))
  ||' factors='||(select count(*) from equity.adjustment_factor where entity_id = (select id from core.entity where slug = 't39-in'))
  ||' membership='||(select count(*) from equity.index_membership where entity_id = (select id from core.entity where slug = 't39-in'));
reset role;"; }
for sw in none nse bse yfinance sec nse,bse; do
  set_sw="update core.source set is_suppressed = true where key = any(string_to_array('$sw', ','));"
  want=$(psql $H -U postgres -X -A -t -q -c "begin; $set_sw $(old_rule) rollback;" 2>&1)
  got=$(psql $H -U postgres -X -A -t -q 2>&1 <<SQL
begin;
$set_sw
$(as_anon)
rollback;
SQL
)
  got=$(echo "$got" | grep '^prices=')
  check "U11 India's rows with '$sw' suppressed are exactly 0019's ($want)" "$got" "^$(echo "$want" | sed 's/[][\.*^$]/\\&/g')$"
done
check "U12 the switch sweep was meaningful: with nothing suppressed anon sees both exchanges' rows of every kind" \
  "$(psql $H -U postgres -X -A -t -q -c "$(old_rule)" 2>&1)" '^prices=2 filings=2 financials=2 shareholding=2 actions=2 factors=1 membership=1$'

############################################################ 4. the US rows follow their own sources
us_probe() { echo "set local role anon;
select '$1 prices='||(select count(*) from equity.price_daily where entity_id = (select id from core.entity where slug = 't39-us'))
  ||' filings='||(select count(*) from equity.filing where entity_id = (select id from core.entity where slug = 't39-us'))
  ||' financials='||(select count(*) from equity.financials where entity_id = (select id from core.entity where slug = 't39-us'))
  ||' actions='||(select count(*) from equity.corporate_action where event_id in (select id from core.event where slug = 't39-ca-nyse-t39-us'))
  ||' factors='||(select count(*) from equity.adjustment_factor where entity_id = (select id from core.entity where slug = 't39-us'))
  ||' dow='||(select count(*) from equity.index_membership where entity_id = (select id from core.entity where slug = 't39-us'))
  ||' nifty='||(select count(*) from equity.index_membership where entity_id = (select id from core.entity where slug = 't39-in'));
reset role;"; }
out=$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<SQL
begin;
$(us_probe none)
update core.source set is_suppressed = true where key = 'yfinance';
$(us_probe yfinance_off)
update core.source set is_suppressed = false where key = 'yfinance';
update core.source set is_suppressed = true where key = 'sec';
$(us_probe sec_off)
update core.source set is_suppressed = false where key = 'sec';
update core.source set is_suppressed = true where key = 'nse';
$(us_probe nse_off)
update core.source set is_suppressed = false where key = 'nse';
update core.source set is_suppressed = true where key = 'curated';
$(us_probe curated_off)
rollback;
SQL
)
line() { echo "$out" | grep "^$1 " | head -1; }
check "U13 a published US company: its NYSE and NASDAQ prices, SEC filing, financials, action, factor and Dow row are visible" \
  "$(line none)" '^none prices=2 filings=1 financials=1 actions=1 factors=1 dow=1 nifty=1$'
check "U14 suppressing yfinance hides the US prices (NYSE and NASDAQ), the US corporate action and its factor, and nothing else" \
  "$(line yfinance_off)" '^yfinance_off prices=0 filings=1 financials=1 actions=0 factors=0 dow=1 nifty=1$'
check "U15 suppressing sec hides the US filing and its financials, and nothing else" \
  "$(line sec_off)" '^sec_off prices=2 filings=0 financials=0 actions=1 factors=1 dow=1 nifty=1$'
check "U16 suppressing nse hides the Nifty membership, never a US row or the Dow membership" \
  "$(line nse_off)" '^nse_off prices=2 filings=1 financials=1 actions=1 factors=1 dow=1 nifty=0$'
check "U17 the curated source has no switch over the Dow rows (membership needs both entities published, nothing else)" \
  "$(line curated_off)" '^curated_off prices=2 filings=1 financials=1 actions=1 factors=1 dow=1 nifty=1$'
check "U18 the switches left nothing suppressed" \
  "$(as_pg "select count(*) from core.source where is_suppressed")" '^0$'

############################################################ 5. an unpublished US company is in no table and no api view
check "U19 anon sees the unpublished US company in no equity table (its action and factor too, though their event is published)" \
  "$(as_role anon "select (select count(*) from equity.company where entity_id = 987650039)
      + (select count(*) from equity.price_daily where entity_id = 987650039)
      + (select count(*) from equity.filing where entity_id = 987650039)
      + (select count(*) from equity.financials where entity_id = 987650039)
      + (select count(*) from equity.corporate_action where event_id in (select id from core.event where entity_id = 987650039))
      + (select count(*) from equity.adjustment_factor where entity_id = 987650039)
      + (select count(*) from equity.index_membership where entity_id = 987650039)
      + (select count(*) from equity.metric_value where entity_id = 987650039)
      + (select count(*) from equity.screener_snapshot where entity_id = 987650039)")" '^0$'
# Every api view anon may read, each row as text, searched for the hidden company's slug, name, ticker and id.
SWEEP=$(as_pg "select string_agg(format('select %L as v, count(*) as n from api.%I x where x::text ~ %L', c.relname, c.relname,
                 't39-uh|T39 Hidden|T39UH|987650039'), ' union all ' order by c.relname)
               from pg_class c join pg_namespace n on n.oid = c.relnamespace
              where n.nspname = 'api' and c.relkind = 'v' and has_table_privilege('anon', c.oid, 'select')")
sweep() { psql $H -U postgres -X -A -t -q 2>&1 <<SQL
begin;
$1
set local role anon;
select count(*)||' views; found in '||coalesce(string_agg(v || '=' || n, ',' order by v) filter (where n > 0), 'none') from ($SWEEP) s;
rollback;
SQL
}
check "U20 no api view anon can read shows the unpublished US company (its slug, name, ticker or id)" \
  "$(sweep "" | grep views)" '^[1-9][0-9]* views; found in none$'
check "U21 publishing the company alone (the go-live flip) shows it: the check above can see a US row, its action included" \
  "$(sweep "update core.entity set is_published = true where id = 987650039;" | grep views)" \
  'found in (.*,)?companies=1,company_corporate_actions=1,company_financials=1,(.*,)?company_prices=1,(.*,)?index_members=1,(.*,)?screener=1(,|$)'
check "U22 equity.financials.operating_income is numeric, nullable and commented" \
  "$(as_pg "select data_type||':'||is_nullable||':'||(col_description('equity.financials'::regclass, ordinal_position::int) like 'US filings only%')
            from information_schema.columns where table_schema = 'equity' and table_name = 'financials' and column_name = 'operating_income'")" \
  '^numeric:YES:true$'
check "U23 anon reads operating_income on a published US filing (250) and job_writer may write it" \
  "$(as_role anon "select operating_income from equity.financials where entity_id = (select id from core.entity where slug = 't39-us')")|$(as_pg "select has_column_privilege('job_writer', 'equity.financials', 'operating_income', 'insert')")" \
  '^250\|t$'

cleanup
check "U24 the test rows are gone" "$(as_pg "select count(*) from core.entity where slug like 't39-%'")" '^0$'
echo "0039 tests: passed $pass, failed $fail"
