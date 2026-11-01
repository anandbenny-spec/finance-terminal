#!/bin/bash
# Tests for 0019 (the equity schema; issue #107, Spec 02A §3). Run after the other scripts on the same fresh
# database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
# The script creates its own rows (entities named eq19-*), checks through the roles, and removes every row it made.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from equity.valuation_scenario; delete from equity.valuation_run; delete from equity.metric_value;
         delete from equity.screener_snapshot; delete from equity.financials; delete from equity.filing;
         delete from equity.shareholding; delete from equity.adjustment_factor; delete from equity.corporate_action;
         delete from equity.price_daily; delete from equity.index_membership; delete from equity.industry_benchmark;
         delete from equity.company; delete from equity.peer_group where key like 'eq19%';
         delete from core.document where external_key like 'eq19%'; delete from core.event where slug like 'eq19%';
         delete from core.raw_file where object_key like 'eq19%';
         delete from core.entity where slug like 'eq19%';
         update core.source set is_suppressed = false where key in ('nse','bse','damodaran','imf');" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind

############################################################ the rows the checks use
psql $H -U postgres -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || echo "FAIL  setup of the test rows"
insert into core.entity (kind, slug, name, market, currency, is_published) values
  ('company','eq19-pub','Eq19 Published Ltd','IN','INR',true),
  ('company','eq19-split2','Eq19 Two Actions Ltd','IN','INR',true),
  ('company','eq19-held','Eq19 Held Ltd','IN','INR',true),
  ('company','eq19-unpub','Eq19 Unpublished Ltd','IN','INR',false);
insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 'eq19-raw', s.id, null, sha256('eq19'::bytea), 1, 'text/csv', now(), (select max(id) from ops.task_run)
from core.source s where s.key = 'nse';
insert into equity.peer_group (key, label, damodaran_industry, source_note) values ('eq19-g', 'Eq19 group', 'Software', 'test');
insert into equity.company (entity_id, isin, nse_symbol, peer_group_key, valuation_family)
select id, 'INE000000019', upper(slug), 'eq19-g', 'operating' from core.entity where slug like 'eq19-%';
-- prices: 1-5 Jan 2026, close 100 on every day, for all four companies
insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close, prev_close, volume, raw_file_id, task_run_id)
select e.id, d::date, 'NSE', 'EQ', 100, 101, 99, 100, 100, 1000,
       (select id from core.raw_file where object_key = 'eq19-raw'), (select max(id) from ops.task_run)
from core.entity e, generate_series('2026-01-01'::date, '2026-01-05', '1 day') d where e.slug like 'eq19-%';
-- corporate actions: pub has a 0.5 split on 3 Jan; split2 has 0.5 and 0.2 on 3 Jan; held has a held factor on 4 Jan
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
select 'corporate_action', e.id, v.slug, v.slug, v.ex::date, 'effective', (select id from core.source where key = 'nse'), true
from (values ('eq19-pub','eq19-pub-split-2026-01-03','2026-01-03'),
             ('eq19-split2','eq19-split2-split-2026-01-03','2026-01-03'),
             ('eq19-split2','eq19-split2-bonus-2026-01-03','2026-01-03'),
             ('eq19-held','eq19-held-demerger-2026-01-04','2026-01-04')) v(ent, slug, ex)
join core.entity e on e.slug = v.ent;
insert into equity.corporate_action (event_id, kind, ex_date, subject, exchange)
select ev.id, case when ev.slug like '%bonus%' then 'bonus' when ev.slug like '%demerger%' then 'demerger' else 'split' end,
       ev.starts_on, 'eq19 ' || ev.slug, 'NSE' from core.event ev where ev.slug like 'eq19-%';
insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method, status, reason)
select ev.id, ev.entity_id, ev.starts_on,
       case when ev.slug like '%-bonus-%' then 0.2 when ev.slug like '%demerger%' then null else 0.5 end,
       case when ev.slug like '%demerger%' then 'special_preopen' else 'ratio' end,
       case when ev.slug like '%demerger%' then 'held' else 'applied' end,
       case when ev.slug like '%demerger%' then 'eq19 held for review' end
from core.event ev where ev.slug like 'eq19-%';
-- one row in each visible-by-entity table, for the published company and the unpublished one
insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
select (select id from core.entity where slug = 'nifty-100'), '2026-01-02', e.id, (select id from core.raw_file where object_key = 'eq19-raw')
from core.entity e where e.slug in ('eq19-pub','eq19-unpub');
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
select 'results_xbrl', e.id, 'eq19 filing', (select id from core.source where key = 'nse'), 'eq19-' || e.slug, 'https://example.invalid/' || e.slug
from core.entity e where e.slug in ('eq19-pub','eq19-unpub');
insert into equity.filing (document_id, entity_id, basis, format, period_end, source_url, exchange)
select d.id, d.entity_id, 'consolidated', 'ind_as', '2025-12-31', d.source_url, 'NSE' from core.document d where d.external_key like 'eq19-%';
insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, revenue)
select f.document_id, f.entity_id, 'consolidated', '2025-10-01', '2025-12-31', 'quarter', 1000 from equity.filing f;
insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value)
select e.id, 'market_cap', 'none', 'market', '2026-01-05', 12345 from core.entity e where e.slug in ('eq19-pub','eq19-unpub');
insert into equity.screener_snapshot (snapshot_date, entity_id, price, market_cap)
select '2026-01-04', e.id, 100, 12345 from core.entity e where e.slug in ('eq19-pub','eq19-unpub');
insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result, inputs, model_version)
select e.id, 'reverse_dcf', '2026-01-05', 'solved', null, 100, 0.12, '{}', 'eq19'
from core.entity e where e.slug in ('eq19-pub','eq19-unpub');
insert into equity.valuation_scenario (run_id, growth, ebit_margin, wacc, value_per_share)
select r.id, 0.1, 0.2, 0.11, 90 from equity.valuation_run r;
insert into equity.industry_benchmark (edition, market, industry, beta, raw_file_id)
values ('2026-01-05', 'IN', 'Software', 1.1, (select id from core.raw_file where object_key = 'eq19-raw'));
SQL

############################################################ structure and catalogues
check "E01 all 20 equity tables (0019's 15, 0023's 5) have RLS on and a job_writer_all policy" \
  "$(as_pg "select count(*) from pg_tables t where t.schemaname = 'equity' and t.rowsecurity
            and exists (select 1 from pg_policies p where p.schemaname = 'equity' and p.tablename = t.tablename
                        and p.policyname = 'job_writer_all' and p.roles = '{job_writer}')")" '^20$'
check "E02 the schema holds exactly those 20 tables and the price_adjusted view" \
  "$(as_pg "select (select count(*) from pg_tables where schemaname = 'equity')||'/'||(select count(*) from pg_views where schemaname = 'equity')")" '^20/1$'
check "E03 four sources with the attribution wording of Spec 02A §3.1, none exportable or suppressed" \
  "$(as_pg "select string_agg(key||'='||attribution||'|'||is_official||'|'||allow_raw_export||'|'||is_suppressed, ';' order by key) from core.source where key in ('nse','bse','damodaran','imf')")" \
  '^bse=Source: BSE\|true\|false\|false;damodaran=Source: Aswath Damodaran, NYU Stern\|false\|false\|false;imf=Source: IMF World Economic Outlook\|true\|false\|false;nse=Source: NSE\|true\|false\|false$'
check "E04 the NSE source's notes name the posture and its date" \
  "$(as_pg "select notes from core.source where key = 'nse'")" 'NSE posture \(Brief §6\.2, 19 Oct 2026\)'
check "E05 the four Nifty index entities are published (IN, INR)" \
  "$(as_pg "select string_agg(slug||'|'||is_published||'|'||market||'|'||currency, ';' order by slug) from core.entity where kind = 'index' and slug in ('nifty-100','nifty-200','nifty-next-50','nifty-midcap-100')")" \
  '^nifty-100\|true\|IN\|INR;nifty-200\|true\|IN\|INR;nifty-midcap-100\|true\|IN\|INR;nifty-next-50\|true\|IN\|INR$'
check "E06 event types and statuses (corporate_action: announced, effective final, withdrawn final; results_filing: filed, superseded final)" \
  "$(as_pg "select string_agg(event_type||'.'||status||'='||is_final, ';' order by event_type, status) from core.event_status where event_type in ('corporate_action','results_filing')")" \
  '^corporate_action\.announced=false;corporate_action\.effective=true;corporate_action\.withdrawn=true;results_filing\.filed=true;results_filing\.superseded=true$'
check "E07 doc types results_xbrl and shareholding_xbrl, and 0023's call_transcript and call_cover_letter (module equity, not diffable)" \
  "$(as_pg "select string_agg(key||'|'||module||'|'||diffable, ';' order by key) from core.doc_type where module = 'equity'")" \
  '^call_cover_letter\|equity\|false;call_transcript\|equity\|false;results_xbrl\|equity\|false;shareholding_xbrl\|equity\|false$'
check "E08 the five valuation-input series: unpublished, percent, no fixing time, right kinds and frequencies" \
  "$(as_pg "select string_agg(key||'|'||kind||'|'||frequency||'|'||unit||'|'||is_published||'|'||coalesce(fixing_time::text,'-'), ';' order by key) from core.series where key like 'damodaran.%' or key like 'imf.%'")" \
  '^damodaran\.ERP_IMPLIED\|observed\|M\|percent\|false\|-;damodaran\.IND_CRP\|observed\|irregular\|percent\|false\|-;damodaran\.IND_DEFAULT_SPREAD\|observed\|irregular\|percent\|false\|-;imf\.NGDP_RPCH\.IND\|forecast\|A\|percent\|false\|-;imf\.PCPIPCH\.IND\|forecast\|A\|percent\|false\|-$'
check "E09 the metric catalogue has the 37 agreed keys" \
  "$(as_pg "select count(*)||'|'||count(*) filter (where key in ('revenue','pat_owners','ebit_derived','ebit_margin','net_margin','effective_tax','debt','cash_and_investments','invested_capital','sales_to_capital','roic','roe','debt_to_equity','fcf','revenue_yoy','pat_owners_yoy','revenue_cagr_3y','revenue_cagr_5y','nii','roa','nim_proxy','gnpa','nnpa','cet1','tier1','shares_today','market_cap','pe_ttm','pb','ev','ev_ebit_ttm','ev_sales_ttm','dividend_yield','return_1y','traded_value_median_3m','beta_regression','beta_bottom_up')) from equity.metric")" '^37\|37$'
check "E10 metric labels: derived (ebit_derived, fcf, both betas), annualised (roa), proxy, annualised (nim_proxy), none elsewhere" \
  "$(as_pg "select string_agg(key||'='||label_kind, ';' order by key) from equity.metric where label_kind is not null")" \
  '^beta_bottom_up=derived;beta_regression=derived;ebit_derived=derived;fcf=derived;nim_proxy=proxy, annualised;roa=annualised$'
check "E11 field policy: four equity fields hidden, six labelled with their labels" \
  "$(as_pg "select string_agg(field_key||'='||status||':'||label, ';' order by field_key) from core.field_policy where field_key like 'equity.%'")" \
  '^equity\.beta=labelled:derived;equity\.cost_of_debt=labelled:derived;equity\.dcf_scenario=hidden:Model-implied value under selected assumptions;equity\.ebit_derived=labelled:derived;equity\.implied_growth=hidden:model-implied;equity\.implied_roe=hidden:model-implied;equity\.nim_proxy=labelled:proxy, annualised;equity\.peer_group=labelled:curated;equity\.rate_sensitivity=hidden:derived;equity\.roa=labelled:annualised$'
check "E12 the schema and every table and the view carry a comment" \
  "$(as_pg "select (select count(*) from pg_namespace n where n.nspname = 'equity' and obj_description(n.oid, 'pg_namespace') is not null)||'/'||count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'equity' and c.relkind in ('r','v') and obj_description(c.oid, 'pg_class') is not null")" '^1/21$'

############################################################ privileges
check "E13 job_writer deletes only from adjustment_factor, financials, shareholding, metric_value, valuation_scenario (and 0023's call_turn, guidance, stated_figure)" \
  "$(as_pg "select string_agg(c.relname, ',' order by c.relname) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'equity' and c.relkind = 'r' and has_table_privilege('job_writer', c.oid, 'delete')")" \
  '^adjustment_factor,call_turn,financials,guidance,metric_value,shareholding,stated_figure,valuation_scenario$'
check "E14 job_writer can't delete from price_daily" \
  "$(as_role job_writer "delete from equity.price_daily where trade_date = '2026-01-01'")" 'permission denied'
check "E15 job_writer can't delete from filing" \
  "$(as_role job_writer "delete from equity.filing")" 'permission denied'
check "E16 job_writer can't delete from screener_snapshot" \
  "$(as_role job_writer "delete from equity.screener_snapshot")" 'permission denied'
check "E17 job_writer can delete a metric_value (derived rows are re-derivable)" \
  "$(as_role job_writer "begin; delete from equity.metric_value where metric_key = 'market_cap' returning metric_key; rollback;")" '^market_cap'
check "E18 job_writer can't insert into the metric catalogue" \
  "$(as_role job_writer "insert into equity.metric (key, label, unit, basis_rule, definition) values ('e18','x','x','x','x')")" 'permission denied'
check "E19 job_writer can't update the metric catalogue" \
  "$(as_role job_writer "update equity.metric set label = 'x'")" 'permission denied'
check "E20 job_writer can't insert into core.source" \
  "$(as_role job_writer "insert into core.source (key, name, attribution) values ('e20','x','x')")" 'permission denied|row-level security'
check "E21 job_writer can't insert into core.field_policy" \
  "$(as_role job_writer "insert into core.field_policy (field_key, status, reason) values ('equity.e21','shown','x')")" 'permission denied|row-level security'
check "E22 sql_reader can't read any equity table" \
  "$(as_role sql_reader "select count(*) from equity.price_daily")" 'permission denied for schema equity'
check "E23 anon can't read the price_adjusted view directly (the api views come in 2A-17)" \
  "$(as_role anon "select count(*) from equity.price_adjusted")" 'permission denied'
check "E24 job_writer, semantic_owner and service_role can read price_adjusted" \
  "$(as_pg "select string_agg(r, ',' order by r) from unnest(array['job_writer','semantic_owner','service_role','anon','authenticated']) r where has_table_privilege(r, 'equity.price_adjusted', 'select')")" \
  '^job_writer,semantic_owner,service_role$'

############################################################ visitor policies
PUB=$(as_pg "select id from core.entity where slug = 'eq19-pub'"); UNPUB=$(as_pg "select id from core.entity where slug = 'eq19-unpub'")
check "E25 anon reads the published company's prices (5 rows) and none of the unpublished company's" \
  "$(as_role anon "select (select count(*) from equity.price_daily where entity_id = $PUB)||'/'||(select count(*) from equity.price_daily where entity_id = $UNPUB)")" '^5/0$'
check "E26 anon sees the unpublished company in no equity table, and the published one in each" \
  "$(as_role anon "select string_agg(x, ' ' order by x) from (
      select 'company='||count(*) filter (where entity_id = $PUB)||'/'||count(*) filter (where entity_id = $UNPUB) x from equity.company where nse_symbol like 'EQ19%'
      union all select 'filing='||count(*) filter (where f.entity_id = $PUB)||'/'||count(*) filter (where f.entity_id = $UNPUB) from equity.filing f
      union all select 'financials='||count(*) filter (where f.entity_id = $PUB)||'/'||count(*) filter (where f.entity_id = $UNPUB) from equity.financials f
      union all select 'metric_value='||count(*) filter (where m.entity_id = $PUB)||'/'||count(*) filter (where m.entity_id = $UNPUB) from equity.metric_value m
      union all select 'snapshot='||count(*) filter (where m.entity_id = $PUB)||'/'||count(*) filter (where m.entity_id = $UNPUB) from equity.screener_snapshot m
      union all select 'run='||count(*) filter (where m.entity_id = $PUB)||'/'||count(*) filter (where m.entity_id = $UNPUB) from equity.valuation_run m
      union all select 'membership='||count(*) filter (where m.entity_id = $PUB)||'/'||count(*) filter (where m.entity_id = $UNPUB) from equity.index_membership m
      union all select 'scenario='||count(*) from equity.valuation_scenario) t")" \
  '^company=1/0 filing=1/0 financials=1/0 membership=1/0 metric_value=1/0 run=1/0 scenario=1 snapshot=1/0$'
check "E27 anon reads the catalogues: 37 metrics and the peer group" \
  "$(as_role anon "select (select count(*) from equity.metric)||'/'||(select count(*) from equity.peer_group where key = 'eq19-g')")" '^37/1$'

# One transaction, rolled back: what the NSE and Damodaran switches hide, and that un-suppressing restores it.
probe() { echo "set local role anon;
select '$1 prices='||(select count(*) from equity.price_daily)||' filing='||(select count(*) from equity.filing)
  ||' financials='||(select count(*) from equity.financials)||' metric_value='||(select count(*) from equity.metric_value)
  ||' snapshot='||(select count(*) from equity.screener_snapshot)||' membership='||(select count(*) from equity.index_membership)
  ||' run='||(select count(*) from equity.valuation_run)||' scenario='||(select count(*) from equity.valuation_scenario)
  ||' bench='||(select count(*) from equity.industry_benchmark)||' company='||(select count(*) from equity.company where nse_symbol like 'EQ19%')
  ||' factors='||(select count(*) from equity.adjustment_factor);
reset role;"; }
out=$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<SQL
begin;
$(probe before)
update core.source set is_suppressed = true where key = 'nse';
$(probe nse_off)
update core.source set is_suppressed = false where key = 'nse';
update core.source set is_suppressed = true where key = 'damodaran';
$(probe damodaran_off)
update core.source set is_suppressed = false where key = 'damodaran';
$(probe after)
rollback;
SQL
)
line() { echo "$out" | grep "^$1 " | head -1; }
check "E28 before any switch: the published company's rows are visible" \
  "$(line before)" '^before prices=15 filing=1 financials=1 metric_value=1 snapshot=1 membership=1 run=1 scenario=1 bench=1 company=3 factors=4$'
check "E29 suppressing nse hides prices, filings, financials, metrics, snapshots, memberships, runs and scenarios at once (companies and Damodaran rows stay; factors go with their NSE events)" \
  "$(line nse_off)" '^nse_off prices=0 filing=0 financials=0 metric_value=0 snapshot=0 membership=0 run=0 scenario=0 bench=1 company=3 factors=0$'
check "E30 suppressing damodaran hides valuation runs, their scenarios and the benchmarks, and nothing else" \
  "$(line damodaran_off)" '^damodaran_off prices=15 filing=1 financials=1 metric_value=1 snapshot=1 membership=1 run=0 scenario=0 bench=0 company=3 factors=4$'
check "E31 un-suppressing restores every row" \
  "$(line after)" '^after prices=15 filing=1 financials=1 metric_value=1 snapshot=1 membership=1 run=1 scenario=1 bench=1 company=3 factors=4$'
check "E32 the switches left nothing suppressed" \
  "$(as_pg "select count(*) from core.source where is_suppressed")" '^0$'

############################################################ price guard
check "E33 the price guard refuses a changed close" \
  "$(as_role job_writer "update equity.price_daily set close = 101 where trade_date = '2026-01-01'")" 'rows are written once'
check "E34 the price guard refuses a changed volume" \
  "$(as_role job_writer "update equity.price_daily set volume = 1 where trade_date = '2026-01-01'")" 'rows are written once'
check "E35 the price guard refuses a changed file" \
  "$(as_pg "update equity.price_daily set raw_file_id = raw_file_id + 1000 where trade_date = '2026-01-01'")" 'rows are written once|violates foreign key'
check "E36 an update that changes no price (the run id, or the same values) is allowed" \
  "$(as_role job_writer "begin; update equity.price_daily set close = close, task_run_id = task_run_id where trade_date = '2026-01-01' returning close; rollback;")" '^100'
check "E37 close must be positive" \
  "$(as_pg "insert into equity.price_daily (entity_id, trade_date, exchange, series, close, raw_file_id, task_run_id)
            select e.id, '2026-02-01', 'NSE', 'EQ', 0, (select id from core.raw_file where object_key = 'eq19-raw'), (select max(id) from ops.task_run) from core.entity e where e.slug = 'eq19-pub'")" 'violates check constraint'

############################################################ the adjusted-price view
adj() { as_role job_writer "select string_agg(coalesce(p.adj_close::text,'null'), ',' order by p.trade_date) from equity.price_adjusted p join core.entity e on e.id = p.entity_id where e.slug = '$1'"; }
check "E38 a split factor of 0.5 (applied) halves the closes before the ex-date and leaves the rest" \
  "$(adj eq19-pub)" '^50(\.0+)?,50(\.0+)?,100(\.0+)?,100(\.0+)?,100(\.0+)?$'
check "E39 two factors on one date multiply (0.5 × 0.2 = 0.1)" \
  "$(adj eq19-split2)" '^10(\.0+)?,10(\.0+)?,100(\.0+)?,100(\.0+)?,100(\.0+)?$'
check "E40 a held factor nulls every earlier close and leaves later ones (ex-date 4 Jan)" \
  "$(adj eq19-held)" '^null,null,null,100(\.0+)?,100(\.0+)?$'
check "E41 the view reads only the company's price exchange (a BSE row for the same day is not used)" \
  "$(as_pg "begin; insert into equity.price_daily (entity_id, trade_date, exchange, series, close, raw_file_id, task_run_id)
            select e.id, '2026-01-02', 'BSE', 'EQ', 777, (select id from core.raw_file where object_key = 'eq19-raw'), (select max(id) from ops.task_run) from core.entity e where e.slug = 'eq19-pub';
            select count(*) from equity.price_adjusted p join core.entity e on e.id = p.entity_id where e.slug = 'eq19-pub'; rollback;" | grep -v '^INSERT')" '^5$'
check "E42 a factor must be positive, and an applied factor needs a number" \
  "$(as_pg "update equity.adjustment_factor set price_factor = 0 where event_id = (select event_id from equity.adjustment_factor limit 1)")" 'violates check constraint'
check "E43 an applied factor without a price_factor is refused" \
  "$(as_pg "update equity.adjustment_factor set price_factor = null where status = 'applied' and price_factor = 0.5")" 'violates check constraint'
check "E44 status and method values are checked" \
  "$(as_pg "update equity.adjustment_factor set status = 'maybe' where status = 'held'")" 'violates check constraint'

############################################################ row checks
check "E45 metric_value: a value without a held_reason is fine; a value with one is refused" \
  "$(as_pg "insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value, held_reason)
            select id, 'pe_ttm', 'consolidated', 'market', '2026-01-05', 12, 'both' from core.entity where slug = 'eq19-pub'")" 'violates check constraint'
check "E46 metric_value: neither a value nor a reason is refused" \
  "$(as_pg "insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end)
            select id, 'pe_ttm', 'consolidated', 'market', '2026-01-05' from core.entity where slug = 'eq19-pub'")" 'violates check constraint'
check "E47 metric_value: a held row (no value, a reason) is accepted" \
  "$(as_pg "begin; insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, held_reason)
            select id, 'pe_ttm', 'consolidated', 'ttm', '2026-01-05', 'loss-making' from core.entity where slug = 'eq19-pub' returning held_reason; rollback;" | grep -v '^INSERT')" '^loss-making$'
check "E48 metric_value: the period kind and the metric key are checked" \
  "$(as_pg "insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value)
            select id, 'no_such_metric', 'none', 'market', '2026-01-05', 1 from core.entity where slug = 'eq19-pub'")" 'violates foreign key'
check "E49 valuation_run: status solved needs a result" \
  "$(as_pg "insert into equity.valuation_run (entity_id, kind, as_of, status, inputs, model_version)
            select id, 'dcf_default', '2026-01-05', 'solved', '{}', 'e49' from core.entity where slug = 'eq19-pub'")" 'violates check constraint'
check "E50 valuation_run: a result with status no_solution is refused" \
  "$(as_pg "insert into equity.valuation_run (entity_id, kind, as_of, status, reason, result, inputs, model_version)
            select id, 'dcf_default', '2026-01-05', 'no_solution', 'x', 0.1, '{}', 'e50' from core.entity where slug = 'eq19-pub'")" 'violates check constraint'
check "E51 valuation_run: not solved needs a reason" \
  "$(as_pg "insert into equity.valuation_run (entity_id, kind, as_of, status, inputs, model_version)
            select id, 'dcf_default', '2026-01-05', 'not_applicable', '{}', 'e51' from core.entity where slug = 'eq19-pub'")" 'violates check constraint'
check "E52 valuation_run: a not_applicable row with a reason and no result is accepted" \
  "$(as_pg "begin; insert into equity.valuation_run (entity_id, kind, as_of, status, reason, inputs, model_version)
            select id, 'dcf_default', '2026-01-05', 'not_applicable', 'lender', '{}', 'e52' from core.entity where slug = 'eq19-pub' returning reason; rollback;" | grep -v '^INSERT')" '^lender$'
check "E53 valuation_run: one run per company, kind, price date and model version" \
  "$(as_pg "insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result, inputs, model_version)
            select id, 'reverse_dcf', '2026-01-05', 'solved', null, 100, 0.2, '{}', 'eq19' from core.entity where slug = 'eq19-pub'")" 'duplicate key'
check "E54 filing.parse_status defaults to pending" \
  "$(as_pg "select distinct parse_status from equity.filing")" '^pending$'
check "E55 shareholding: promoter_pct must be between 0 and 100" \
  "$(as_pg "insert into equity.shareholding (document_id, entity_id, as_of, promoter_pct, source_url, exchange)
            select d.id, d.entity_id, '2025-12-31', 120, 'x', 'NSE' from core.document d where d.external_key = 'eq19-eq19-pub'")" 'violates check constraint'
check "E56 company: the valuation family is checked" \
  "$(as_pg "update equity.company set valuation_family = 'bank' where nse_symbol = 'EQ19-PUB'")" 'violates check constraint'
check "E57 a company entity needs its market and currency (the core guard still holds)" \
  "$(as_pg "insert into core.entity (kind, slug, name) values ('company','eq19-nomarket','x')")" 'violates check constraint'

cleanup
check "E58 the test rows are gone" \
  "$(as_pg "select (select count(*) from core.entity where slug like 'eq19%')+(select count(*) from equity.price_daily)+(select count(*) from equity.company)")" '^0$'

echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
