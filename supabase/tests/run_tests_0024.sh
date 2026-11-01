#!/bin/bash
# Tests for 0024 (the equity read layer: 12 api views and 3 semantic views; issue #123, Spec 02A §7). Run after the
# other scripts on the same fresh database. Connection from the environment (run_all.sh sets PGHOST/PGPORT/PGDATABASE).
# The script creates its own rows (entities named eq24-*), checks through the roles, and removes every row it made.
# Gate flips happen inside one transaction that is rolled back, so the policy rows are never left changed.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg()   { psql $H -U postgres -c "$1" 2>&1; }
as_role() { psql $H -U postgres -c "set role $1; $2" 2>&1 | grep -v '^SET$'; }
# flip ROLE "setup statements" "query": run the setup as postgres, then the query as ROLE, in a transaction rolled back
flip()    { psql $H -U postgres -c "begin; $2; set role $1; $3; rollback" 2>&1 | grep -v '^SET$'; }
SHOW()    { echo "update core.field_policy set status = 'labelled', label = coalesce(label, 'x') where field_key = '$1'"; }
HIDE()    { echo "update core.field_policy set status = 'hidden' where field_key = '$1'"; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

cleanup() {
  as_pg "delete from equity.valuation_scenario; delete from equity.valuation_run; delete from equity.metric_value;
         delete from equity.screener_snapshot; delete from equity.financials; delete from equity.filing;
         delete from equity.shareholding; delete from equity.adjustment_factor; delete from equity.corporate_action;
         delete from equity.price_daily; delete from equity.index_membership; delete from equity.industry_benchmark;
         delete from equity.company; delete from equity.peer_group where key like 'eq24%';
         delete from core.document where external_key like 'eq24%'; delete from core.event where slug like 'eq24%';
         delete from core.raw_file where object_key like 'eq24%';
         delete from core.entity where slug like 'eq24%';
         update core.source set is_suppressed = false where key in ('nse','bse','damodaran','imf');" >/dev/null
}
cleanup   # a failed earlier run leaves nothing behind

############################################################ the rows the checks use
psql $H -U postgres -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL' || echo "FAIL  setup of the test rows"
insert into core.entity (kind, slug, name, short_name, market, currency, is_published) values
  ('company','eq24-a','Eq24 Alpha Ltd','Alpha','IN','INR',true),
  ('company','eq24-b','Eq24 Beta Ltd','Beta','IN','INR',true),
  ('company','eq24-c','Eq24 Gamma Ltd','Gamma','IN','INR',true),
  ('company','eq24-d','Eq24 Delta Ltd','Delta','IN','INR',true),
  ('company','eq24-us','Eq24 American Inc','American','US','USD',true),
  ('company','eq24-l','Eq24 Lender Bank','Lender','IN','INR',true),
  ('company','eq24-m','Eq24 Second Lender','Second','IN','INR',true),
  ('company','eq24-n','Eq24 Third Ltd','Third','IN','INR',true),
  ('company','eq24-ins','Eq24 Insurer Ltd','Insurer','IN','INR',true),
  ('company','eq24-unpub','Eq24 Unpublished Ltd','Unpub','IN','INR',false);
insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 'eq24-raw', s.id, null, sha256('eq24'::bytea), 1, 'text/csv', now(), (select max(id) from ops.task_run)
from core.source s where s.key = 'nse';
insert into equity.peer_group (key, market, label, damodaran_industry, source_note) values
  ('eq24-p4', 'IN', 'Eq24 four-member group', 'Software', 'test'),
  ('eq24-p3', 'IN', 'Eq24 three-member group', 'Banks', 'test');
insert into equity.company (entity_id, isin, nse_symbol, bse_code, peer_group_key, valuation_family)
select e.id, 'INE0000024' || lpad((row_number() over (order by e.slug))::text, 2, '0'), upper(e.slug), '5240', v.grp, v.fam
from core.entity e join (values
  ('eq24-a','eq24-p4','operating'), ('eq24-b','eq24-p4','operating'), ('eq24-c','eq24-p4','operating'),
  ('eq24-d','eq24-p4','operating'), ('eq24-us','eq24-p4','operating'), ('eq24-l','eq24-p3','lender'),
  ('eq24-m','eq24-p3','lender'), ('eq24-n','eq24-p3','operating'), ('eq24-ins',null,'insurer'),
  ('eq24-unpub','eq24-p4','operating')) v(slug, grp, fam) on v.slug = e.slug;
-- index memberships: nifty-50 now (a, b) and earlier (c); nifty-100 (a)
insert into equity.index_membership (index_entity_id, snapshot_date, entity_id, raw_file_id)
select (select id from core.entity where slug = v.ix), v.d::date, (select id from core.entity where slug = v.ent),
       (select id from core.raw_file where object_key = 'eq24-raw')
from (values ('nifty-50','2026-01-02','eq24-a'), ('nifty-50','2026-01-02','eq24-b'), ('nifty-50','2025-12-01','eq24-c'),
             ('nifty-100','2026-01-02','eq24-a')) v(ix, d, ent);
-- prices: a closes 100, 110, 120 on 2-4 Jan with a 0.5 split applied on 3 Jan; b has a held factor on 4 Jan
insert into equity.price_daily (entity_id, trade_date, exchange, series, open, high, low, close, prev_close, volume, raw_file_id, task_run_id)
select e.id, v.d::date, 'NSE', 'EQ', v.c, v.c, v.c, v.c, v.c, 1000,
       (select id from core.raw_file where object_key = 'eq24-raw'), (select max(id) from ops.task_run)
from (values ('eq24-a','2026-01-02',100), ('eq24-a','2026-01-03',110), ('eq24-a','2026-01-04',120),
             ('eq24-b','2026-01-02',50), ('eq24-b','2026-01-03',55), ('eq24-b','2026-01-04',60),
             ('eq24-c','2026-01-04',70)) v(ent, d, c) join core.entity e on e.slug = v.ent;
insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, source_url, is_published)
select 'corporate_action', e.id, v.slug, v.slug, v.ex::date, v.st, (select id from core.source where key = 'nse'),
       'https://example.invalid/' || v.slug, true
from (values ('eq24-a','eq24-a-split','2026-01-03','effective'), ('eq24-a','eq24-a-div-ann','2026-03-01','announced'),
             ('eq24-a','eq24-a-withdrawn','2026-02-01','withdrawn'), ('eq24-b','eq24-b-demerger','2026-01-04','effective')) v(ent, slug, ex, st)
join core.entity e on e.slug = v.ent;
insert into equity.corporate_action (event_id, kind, ex_date, subject, exchange, ratio_new, ratio_old, dividend_per_share)
select ev.id, case when ev.slug like '%split' then 'split' when ev.slug like '%demerger' then 'demerger' else 'dividend' end,
       ev.starts_on, 'eq24 ' || ev.slug, 'NSE', case when ev.slug like '%split' then 2 end, case when ev.slug like '%split' then 1 end,
       case when ev.slug like '%div-ann' or ev.slug like '%withdrawn' then 4 end
from core.event ev where ev.slug like 'eq24-%';
insert into equity.adjustment_factor (event_id, entity_id, ex_date, price_factor, method, status, reason)
select ev.id, ev.entity_id, ev.starts_on, case when ev.slug like '%demerger' then null else 0.5 end,
       case when ev.slug like '%demerger' then 'special_preopen' else 'ratio' end,
       case when ev.slug like '%demerger' then 'held' else 'applied' end,
       case when ev.slug like '%demerger' then 'eq24 held for review' end
from core.event ev where ev.slug in ('eq24-a-split','eq24-b-demerger');
-- filings: a's quarter ended 31 Dec 2025 filed twice (the revision replaces the first); i's N/A metric; one standalone
insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url)
select 'results_xbrl', e.id, 'eq24 filing ' || v.k, (select id from core.source where key = 'nse'), 'eq24-' || v.k,
       'https://example.invalid/' || v.k
from (values ('eq24-a','a1'), ('eq24-a','a2'), ('eq24-ins','ins1')) v(ent, k) join core.entity e on e.slug = v.ent;
insert into equity.filing (document_id, entity_id, basis, format, period_end, filed_at, source_url, exchange)
select d.id, d.entity_id, 'consolidated', case when d.external_key = 'eq24-ins1' then 'insurer_life' else 'ind_as' end,
       '2025-12-31', case d.external_key when 'eq24-a1' then '2026-01-01 10:00+05:30' else '2026-01-05 10:00+05:30' end::timestamptz,
       d.source_url, 'NSE'
from core.document d where d.external_key like 'eq24-%';
insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, revenue, pat_owners, tags)
select f.document_id, f.entity_id, 'consolidated', '2025-10-01', '2025-12-31', 'quarter',
       case d.external_key when 'eq24-a1' then 1000 else 1100 end, 100, '{"revenue":"x"}'
from equity.filing f join core.document d on d.id = f.document_id;
-- metrics
insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value, held_reason)
select e.id, v.k, v.b, v.pk, v.pe::date, v.val, v.hr
from (values
  ('eq24-a','ebit_derived','consolidated','quarter','2025-12-31', 500::numeric, null::text),
  ('eq24-a','ebit_margin','consolidated','ttm','2025-12-31', 0.2, null),
  ('eq24-a','ev_ebit_ttm','consolidated','market','2026-01-04', 12, null),
  ('eq24-a','roic','consolidated','ttm','2025-12-31', 0.15, null),
  ('eq24-a','roa','standalone','quarter','2025-12-31', 0.01, null),
  ('eq24-a','nim_proxy','standalone','quarter','2025-12-31', 0.03, null),
  ('eq24-a','beta_regression','none','market','2026-01-04', 1.0, null),
  ('eq24-a','beta_bottom_up','none','market','2026-01-04', 1.1, null),
  ('eq24-a','revenue','consolidated','ttm','2025-12-31', 4000, null),
  ('eq24-a','pat_owners','consolidated','ttm','2025-12-31', null, 'Profit is not positive.'),
  ('eq24-ins','ebit_derived','consolidated','quarter','2025-12-31', null, 'N/A: not meaningful for an insurer')) v(ent, k, b, pk, pe, val, hr)
join core.entity e on e.slug = v.ent;
-- snapshots: an older one for a, then the week's for everyone
insert into equity.screener_snapshot (snapshot_date, entity_id, price, price_date, market_cap, shares, pe_ttm, pb, ev, ev_ebit,
    ev_sales, dividend_yield, roe, ebit_margin, revenue_cagr_3y, revenue_cagr_5y, debt_to_equity, promoter_pct, beta, return_1y,
    traded_value_median_3m, implied_growth, delivered_growth, implied_roe, delivered_roe, valuation_family, notes)
select v.d::date, e.id, 120, '2026-01-04', 1e9, 1e7, v.pe, 3, 2e9, 12, 2, 0.01, 0.18, 0.2, 0.1, 0.09, 0.5, 51, 1.1, 0.2, 5e7,
       v.ig, v.dg, v.ir, v.dr, c.valuation_family,
       jsonb_build_object('implied_growth', jsonb_build_object('label','model-implied','reason',null),
                          'ebit_margin', jsonb_build_object('label','derived','reason',null),
                          'pe_ttm', jsonb_build_object('label',null,'reason',null))
from (values
  ('eq24-a','2026-01-02', 5::numeric, 0.01::numeric, 0.01::numeric, null::numeric, null::numeric),
  ('eq24-a','2026-01-09', 10, 0.12, 0.08, null, null),
  ('eq24-b','2026-01-09', 20, 0.20, 0.05, null, null),
  ('eq24-c','2026-01-09', 30, 0.07, 0.06, null, null),
  ('eq24-d','2026-01-09', 40, null, null, null, null),
  ('eq24-us','2026-01-09', 999, null, null, null, null),
  ('eq24-l','2026-01-09', 8, null, null, 0.15, 0.12),
  ('eq24-m','2026-01-09', 9, null, null, null, null),
  ('eq24-n','2026-01-09', 11, null, null, null, null),
  ('eq24-ins','2026-01-09', 12, null, null, null, null)) v(ent, d, pe, ig, dg, ir, dr)
join core.entity e on e.slug = v.ent join equity.company c on c.entity_id = e.id;
-- valuation runs
insert into equity.valuation_run (entity_id, kind, as_of, status, reason, price, result, delivered, delivered_reason, inputs, model_version)
select e.id, v.k, v.d::date, v.st, v.rs, 120, v.res, v.dl, null, v.inp::jsonb, 'eq24'
from (values
  ('eq24-a','reverse_dcf','2025-12-01','solved',null,0.30,0.30,'{"delivered_years":1}'),
  ('eq24-a','reverse_dcf','2026-01-04','solved',null,0.12,0.08,'{"cost_of_debt":{"value":0.08},"beta":{"value":1.1},"wacc":{"value":0.11},"delivered_years":5}'),
  ('eq24-a','dcf_default','2026-01-04','solved',null,150,null,'{"wacc":{"value":0.11}}'),
  ('eq24-b','reverse_dcf','2026-01-04','no_solution','At its return on capital, faster growth lowers the model''s value.',null,null,'{}'),
  ('eq24-c','reverse_dcf','2026-01-04','solved',null,0.07,0.06,'{"delivered_years":5}'),
  ('eq24-ins','reverse_dcf','2026-01-04','not_applicable','N/A: book value does not capture an insurer''s value.',null,null,'{}'),
  ('eq24-l','implied_roe','2026-01-04','solved',null,0.15,0.12,'{"delivered_years":4}'),
  ('eq24-m','implied_roe','2026-01-04','no_solution','The price implies a return below range.',null,null,'{}')
) v(ent, k, d, st, rs, res, dl, inp) join core.entity e on e.slug = v.ent;
insert into equity.valuation_scenario (run_id, growth, ebit_margin, wacc, value_per_share)
select r.id, 0.10, 0.20, w.wacc, w.v from equity.valuation_run r,
  (values (0.11::numeric, 140::numeric), (0.12::numeric, null::numeric)) w(wacc, v) where r.kind = 'dcf_default';
SQL
SETUP_OK=$(as_pg "select count(*) from equity.company c join core.entity e on e.id = c.entity_id where e.slug like 'eq24%'")
check "A00 the test rows were created (10 companies)" "$SETUP_OK" '^10$'

ALLV="companies index_members company_prices company_financials company_metrics company_corporate_actions screener peer_comps company_valuation valuation_scenarios priced_in_operating priced_in_lenders"

############################################################ reads, grants and structure
Q=""; for v in $ALLV; do Q="$Q(select count(*) from api.$v)||'/'||"; done; Q="${Q%||\'/\'||}"
check "A01 anon reads every one of the 12 new api views (and gets rows from each)" "$(as_role anon "select $Q")" '^[1-9][0-9]*(/[1-9][0-9]*){11}$'
check "A02 authenticated and service_role read them too" \
  "$(as_role authenticated "select count(*) from api.screener")/$(as_role service_role "select count(*) from api.peer_comps")" '^[1-9][0-9]*/[1-9][0-9]*$'
check "A03 every new api view is security_invoker; every new semantic view is a security barrier owned by semantic_owner" \
  "$(as_pg "select (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'api' and c.relname in ('companies','index_members','company_prices','company_financials','company_metrics','company_corporate_actions','screener','peer_comps','company_valuation','valuation_scenarios','priced_in_operating','priced_in_lenders') and c.reloptions @> array['security_invoker=true'])
                   ||'/'||(select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'semantic' and c.relname in ('companies','company_financials','company_valuations') and c.reloptions @> array['security_barrier=true'] and pg_get_userbyid(c.relowner) = 'semantic_owner')")" '^12/3$'
check "A04 anon holds no grant on the semantic views or on equity.price_adjusted (0019's rule stands)" \
  "$(as_pg "select count(*) from (values ('semantic.companies'),('semantic.company_financials'),('semantic.company_valuations'),('equity.price_adjusted')) v(r) where has_table_privilege('anon', v.r, 'select')")" '^0$'

############################################################ companies, index members, prices
check "A05 api.companies: the 9 published companies (not the unpublished one), each with market and currency" \
  "$(as_role anon "select count(*)||'/'||count(*) filter (where market is null or currency is null) from api.companies where slug like 'eq24-%'")" '^9/0$'
check "A06 api.companies: latest and previous close with their dates; the group label is shown with its curated label" \
  "$(as_role anon "select last_close||'|'||last_close_date||'|'||previous_close||'|'||previous_close_date||'|'||peer_group_label||'|'||peer_group_label_kind from api.companies where slug = 'eq24-a'")" \
  '^120\|2026-01-04\|110\|2026-01-03\|Eq24 four-member group\|curated$'
check "A07 api.companies: index chips come from each index's latest snapshot only (c left nifty-50 before it)" \
  "$(as_role anon "select coalesce(array_to_string(index_slugs, ','), 'none') from api.companies where slug = 'eq24-a' union all select coalesce(array_to_string(index_slugs, ','), 'none') from api.companies where slug = 'eq24-c'")" '^nifty-100,nifty-50\s+none$'
check "A08 api.index_members: nifty-50's latest members are a and b" \
  "$(as_role anon "select string_agg(slug, ',' order by slug) from api.index_members where index_slug = 'nifty-50' and slug like 'eq24-%'")" '^eq24-a,eq24-b$'
check "A09 api.company_prices matches equity.price_adjusted's adj_close on every row (the rule isn't allowed to drift)" \
  "$(as_pg "select count(*)||'/'||count(*) filter (where p.adj_close is distinct from v.adj_close) from api.company_prices p join equity.price_adjusted v on v.entity_id = (select id from core.entity where slug = p.slug) and v.trade_date = p.trade_date where p.slug like 'eq24-%'")" '^7/0$'
check "A10 adjusted close: 50 before the split, raw after; the held factor nulls b's earlier prices and flags them held" \
  "$(as_role anon "select string_agg(slug||trade_date||'='||coalesce(adj_close::text, 'null')||'/'||adj_held, ' ' order by slug, trade_date) from api.company_prices where slug in ('eq24-a','eq24-b')")" \
  '^eq24-a2026-01-02=50\.0+/false eq24-a2026-01-03=110(\.0+)?/false eq24-a2026-01-04=120(\.0+)?/false eq24-b2026-01-02=null/true eq24-b2026-01-03=null/true eq24-b2026-01-04=60(\.0+)?/false$'

############################################################ financials, metrics, corporate actions
check "A11 company_financials: the revised filing replaces the first for the same period (revenue 1100), with its date and link" \
  "$(as_role anon "select count(*)||'|'||max(revenue)||'|'||max(source_url)||'|'||max(attribution)||'|'||max(currency) from api.company_financials where slug = 'eq24-a'")" '^1\|1100\|https://example\.invalid/a2\|Source: NSE\|INR$'
check "A12 company_financials: the derived EBIT is labelled (derived) and returns the value; held-back and N/A states come from the stored reason" \
  "$(as_role anon "select ebit_derived||'|'||ebit_derived_label||'|'||ebit_derived_state from api.company_financials where slug = 'eq24-a'")" '^500\|derived\|value$'
check "A13 company_financials: with equity.ebit_derived hidden the EBIT is null and held; shown again after the flip" \
  "$(flip anon "$(HIDE equity.ebit_derived)" "select coalesce(ebit_derived::text,'null')||'|'||ebit_derived_state||'|'||ebit_derived_held_reason from api.company_financials where slug = 'eq24-a'")" \
  '^null\|held\|Held back until its checks pass\.$'
check "A14 company_metrics: an N/A reason becomes state not_applicable with the prefix stripped; an ordinary reason is held" \
  "$(as_role anon "select string_agg(slug||'.'||metric_key||'='||state||':'||coalesce(held_reason,''), ' ' order by slug, metric_key) from api.company_metrics where (slug = 'eq24-ins') or (slug = 'eq24-a' and metric_key = 'pat_owners')")" \
  '^eq24-a\.pat_owners=held:Profit is not positive\. eq24-ins\.ebit_derived=not_applicable:not meaningful for an insurer$'
for pair in "equity.ebit_derived:ebit_derived,ebit_margin,ev_ebit_ttm,roic" "equity.roa:roa" "equity.nim_proxy:nim_proxy" "equity.beta:beta_regression,beta_bottom_up"; do
  key="${pair%%:*}"; mets="${pair#*:}"; inlist="'${mets//,/\',\'}'"
  check "A15 $key hidden nulls ${mets} in company_metrics (state held); the labelled policy returns them" \
    "$(flip anon "$(HIDE $key)" "select string_agg(metric_key||'='||coalesce(value::text,'null')||'/'||state, ' ' order by metric_key) from api.company_metrics where slug = 'eq24-a' and metric_key in ($inlist)")/$(as_role anon "select count(*) filter (where value is not null) from api.company_metrics where slug = 'eq24-a' and metric_key in ($inlist)")" \
    "^([a-z_0-9]+=null/held ?)+/[1-9][0-9]*$"
done
check "A16 company_metrics carries the policy label (ebit_derived: derived) and the metric catalogue's own label otherwise (roa: annualised)" \
  "$(as_role anon "select string_agg(metric_key||'='||coalesce(label_kind,'-'), ' ' order by metric_key) from api.company_metrics where slug = 'eq24-a' and metric_key in ('ebit_derived','roa','revenue','nim_proxy')")" \
  '^ebit_derived=derived nim_proxy=proxy, annualised revenue=- roa=annualised$'
check "A17 company_corporate_actions: the withdrawn announcement is left out; announced sits beside effective; factor status shown" \
  "$(as_role anon "select string_agg(slug||':'||kind||':'||status||':'||factor_status||':'||coalesce(price_factor::text,'-')||':'||coalesce(factor_reason,'-'), ' ' order by slug, ex_date) from api.company_corporate_actions where slug in ('eq24-a','eq24-b')")" \
  '^eq24-a:split:effective:applied:0\.5[0-9]*:- eq24-a:dividend:announced:none:-:- eq24-b:demerger:effective:held:-:eq24 held for review$'

############################################################ screener
check "A18 screener: one row per company from its latest snapshot (a's 9 Jan, not 2 Jan), market and currency on every row" \
  "$(as_role anon "select count(*)||'|'||count(*) filter (where market is null or currency is null)||'|'||(select snapshot_date from api.screener where slug = 'eq24-a') from api.screener where slug like 'eq24-%'")" '^9\|0\|2026-01-09$'
check "A19 screener: implied growth, delivered growth and the gap are null while equity.implied_growth is hidden, and the notes drop their entries" \
  "$(as_role anon "select coalesce(implied_growth::text,'null')||'|'||coalesce(delivered_growth::text,'null')||'|'||coalesce(growth_gap::text,'null')||'|'||(notes ? 'implied_growth')||'|'||(notes ? 'pe_ttm') from api.screener where slug = 'eq24-a'")" '^null\|null\|null\|false\|true$'
check "A20 …and return once the policy is flipped (gap = implied - delivered); ROE columns stay null (their own policy)" \
  "$(flip anon "$(SHOW equity.implied_growth)" "select implied_growth||'|'||delivered_growth||'|'||growth_gap||'|'||(notes ? 'implied_growth')||'|'||coalesce(implied_roe::text,'null') from api.screener where slug = 'eq24-a'")" '^0\.12\|0\.08\|0\.04\|true\|null$'
check "A21 …and the lender's ROE pair with equity.implied_roe flipped" \
  "$(flip anon "$(SHOW equity.implied_roe)" "select implied_roe||'|'||delivered_roe||'|'||roe_gap from api.screener where slug = 'eq24-l'")" '^0\.15\|0\.12\|0\.03$'
check "A22 screener: ebit_margin and ev_ebit follow equity.ebit_derived; beta follows equity.beta" \
  "$(flip anon "$(HIDE equity.ebit_derived); $(HIDE equity.beta)" "select coalesce(ebit_margin::text,'null')||'|'||coalesce(ev_ebit::text,'null')||'|'||coalesce(beta::text,'null')||'|'||(notes ? 'ebit_margin') from api.screener where slug = 'eq24-a'")/$(as_role anon "select ebit_margin||'|'||ev_ebit||'|'||beta from api.screener where slug = 'eq24-a'")" \
  '^null\|null\|null\|false/0\.2\|12\|1\.1$'

############################################################ comps
check "A23 peer_comps: a's four-member group has three other Indian members (the US member is excluded) and the median of those three" \
  "$(as_role anon "select (select count(*) from api.peer_comps where company_slug = 'eq24-a' and row_kind = 'peer')||'|'||(select string_agg(peer_slug, ',' order by peer_slug) from api.peer_comps where company_slug = 'eq24-a' and row_kind = 'peer')||'|'||m.pe_ttm||'|'||m.peer_count||'|'||coalesce(m.median_note,'-')||'|'||(m.median_n->>'pe_ttm') from api.peer_comps m where m.company_slug = 'eq24-a' and m.row_kind = 'median'")" \
  '^3\|eq24-b,eq24-c,eq24-d\|30(\.0+)?\|3\|-\|3$'
check "A24 peer_comps: a metric with fewer than three values has no median (roe is the same everywhere: 3 values here, so it has one; ev_sales likewise)" \
  "$(as_role anon "select coalesce(roe::text,'null')||'|'||coalesce(ev_sales::text,'null') from api.peer_comps where company_slug = 'eq24-a' and row_kind = 'median'")" '^0\.18\|2$'
check "A25 peer_comps: a three-member group gives no median (two others), says why and still lists the peers" \
  "$(as_role anon "select (select count(*) from api.peer_comps where company_slug = 'eq24-l' and row_kind = 'peer')||'|'||coalesce(m.pe_ttm::text,'null')||'|'||m.peer_count||'|'||m.median_note from api.peer_comps m where m.company_slug = 'eq24-l' and m.row_kind = 'median'")" \
  '^2\|null\|2\|Too few peers in the Nifty 50 for a median$'
check "A26 peer_comps: no group row for a company without a peer group, and none at all while equity.peer_group is hidden" \
  "$(as_role anon "select count(*) from api.peer_comps where company_slug = 'eq24-ins'")/$(flip anon "$(HIDE equity.peer_group)" "select count(*) from api.peer_comps")" '^0/0$'
check "A27 peer_comps: ev_ebit follows equity.ebit_derived" \
  "$(flip anon "$(HIDE equity.ebit_derived)" "select count(*) filter (where ev_ebit is not null) from api.peer_comps where company_slug = 'eq24-a'")/$(as_role anon "select count(*) filter (where ev_ebit is not null) from api.peer_comps where company_slug = 'eq24-a'")" '^0/4$'

############################################################ valuation
check "A28 company_valuation: all three kinds read as held (nulls, no inputs) while their policies are hidden; the latest run per kind is the one read" \
  "$(as_role anon "select string_agg(kind||'='||state||'/'||coalesce(result::text,'null')||'/'||(inputs is null), ' ' order by kind) from api.company_valuation where slug = 'eq24-a'")" \
  '^dcf_default=held/null/true reverse_dcf=held/null/true$'
check "A29 …the reverse DCF returns its result, delivered and the latest as-of once equity.implied_growth is flipped; the older run is not read" \
  "$(flip anon "$(SHOW equity.implied_growth)" "select state||'|'||result||'|'||delivered||'|'||as_of||'|'||result_label from api.company_valuation where slug = 'eq24-a' and kind = 'reverse_dcf'")" '^value\|0\.12\|0\.08\|2026-01-04\|model-implied$'
check "A30 …a hidden cost of debt removes that input only; a hidden beta removes the beta input only" \
  "$(flip anon "$(SHOW equity.implied_growth); $(HIDE equity.cost_of_debt)" "select string_agg(k, ',' order by k) from api.company_valuation v, jsonb_object_keys(v.inputs) k where slug = 'eq24-a' and kind = 'reverse_dcf'")/$(flip anon "$(SHOW equity.implied_growth); $(HIDE equity.beta)" "select string_agg(k, ',' order by k) from api.company_valuation v, jsonb_object_keys(v.inputs) k where slug = 'eq24-a' and kind = 'reverse_dcf'")/$(flip anon "$(SHOW equity.implied_growth)" "select string_agg(k, ',' order by k) from api.company_valuation v, jsonb_object_keys(v.inputs) k where slug = 'eq24-a' and kind = 'reverse_dcf'")" \
  '^beta,delivered_years,wacc/cost_of_debt,delivered_years,wacc/beta,cost_of_debt,delivered_years,wacc$'
check "A31 …the default DCF value follows equity.dcf_scenario" \
  "$(flip anon "$(SHOW equity.dcf_scenario)" "select state||'|'||result from api.company_valuation where slug = 'eq24-a' and kind = 'dcf_default'")" '^value\|150$'
check "A32 valuation_scenarios: the grid reads null and held while equity.dcf_scenario is hidden; shown after the flip, a grid point with no value is not_applicable" \
  "$(as_role anon "select string_agg(state||'/'||coalesce(growth::text,'null')||'/'||coalesce(value_per_share::text,'null'), ' ' order by wacc nulls last) from api.valuation_scenarios where slug = 'eq24-a'")|$(flip anon "$(SHOW equity.dcf_scenario)" "select string_agg(state||'/'||growth||'/'||coalesce(value_per_share::text,'null'), ' ' order by wacc) from api.valuation_scenarios where slug = 'eq24-a'")" \
  '^held/null/null held/null/null\|value/0\.10*/140(\.0+)? not_applicable/0\.10*/null$'
check "A33 priced-in tables are never mixed: no company is in both; insurers and no-solution rows sit in the operating table as held / not_applicable" \
  "$(as_pg "select count(*) from api.priced_in_operating o join api.priced_in_lenders l on l.slug = o.slug")|$(as_role anon "select string_agg(slug||'='||state, ' ' order by slug) from api.priced_in_operating")|$(as_role anon "select string_agg(slug||'='||state, ' ' order by slug) from api.priced_in_lenders")" \
  '^0\|eq24-a=held eq24-b=held eq24-c=held eq24-ins=held\|eq24-l=held eq24-m=held$'
check "A34 priced-in: with the policies flipped the operating table ranks by gap (a's 0.04 first, c's 0.01 second), the N/A and no-solution rows have no rank, n of years shown" \
  "$(flip anon "$(SHOW equity.implied_growth)" "select string_agg(slug||'='||state||'/'||coalesce(gap_rank::text,'-')||'/'||coalesce(delivered_years::text,'-'), ' ' order by slug) from api.priced_in_operating")" \
  '^eq24-a=value/1/5 eq24-b=held/-/- eq24-c=value/2/5 eq24-ins=not_applicable/-/-$'
check "A35 priced-in lenders: implied ROE minus delivered ROE after the flip, ranked; the no-solution lender is held without rank" \
  "$(flip anon "$(SHOW equity.implied_roe)" "select string_agg(slug||'='||state||'/'||coalesce(gap::text,'-')||'/'||coalesce(gap_rank::text,'-')||'/'||coalesce(delivered_years::text,'-'), ' ' order by slug) from api.priced_in_lenders")" \
  '^eq24-l=value/0\.03[0]*/1/4 eq24-m=held/-/-/-$'

############################################################ source switches
check "A36 Damodaran suppressed: the valuation views empty (the runs vanish), the bottom-up beta and the model-implied columns read null in every view, everything else stays" \
  "$(flip anon "update core.source set is_suppressed = true where key = 'damodaran'; $(SHOW equity.implied_growth); $(SHOW equity.implied_roe); $(SHOW equity.dcf_scenario)" "select (select count(*) from api.company_valuation)||'|'||(select count(*) from api.valuation_scenarios)||'|'||(select count(*) from api.priced_in_operating)||'|'||(select count(*) from api.priced_in_lenders)||'|'||(select coalesce(implied_growth::text,'null')||'/'||coalesce(beta::text,'null')||'/'||pe_ttm from api.screener where slug = 'eq24-a')||'|'||(select state||'/'||coalesce(value::text,'null') from api.company_metrics where slug = 'eq24-a' and metric_key = 'beta_bottom_up')||'|'||(select coalesce(value::text,'null') from api.company_metrics where slug = 'eq24-a' and metric_key = 'beta_regression')")" \
  '^0\|0\|0\|0\|null/null/10\|held/null\|1\.0+$'
check "A37 NSE suppressed: no NSE-derived row in any of the 11 other new api views; api.companies keeps its 9 identity rows with the price and chips empty" \
  "$(flip anon "update core.source set is_suppressed = true where key = 'nse'" "select (select count(*) from api.index_members)+(select count(*) from api.company_prices)+(select count(*) from api.company_financials)+(select count(*) from api.company_metrics)+(select count(*) from api.company_corporate_actions)+(select count(*) from api.screener)+(select count(*) from api.peer_comps)+(select count(*) from api.company_valuation)+(select count(*) from api.valuation_scenarios)+(select count(*) from api.priced_in_operating)+(select count(*) from api.priced_in_lenders)||'|'||(select count(*)||'/'||count(last_close)||'/'||count(index_slugs) from api.companies where slug like 'eq24-%')")" \
  '^0\|9/0/0$'
check "A38 suppressing the BSE source changes nothing here (the test companies are NSE-priced)" \
  "$(flip anon "update core.source set is_suppressed = true where key = 'bse'" "select count(*) from api.company_prices where slug like 'eq24-%'")" '^7$'

############################################################ semantic views (sql_reader)
check "A39 sql_reader reads the three semantic views (the unpublished company is absent) and no equity table or api view" \
  "$(as_role sql_reader "select (select count(*) from semantic.companies where company like 'Eq24%')||'|'||(select count(*) from semantic.company_financials where company like 'Eq24%')||'|'||(select count(*) from semantic.company_valuations where company like 'Eq24%')")|$(as_role sql_reader "select count(*) from equity.company")|$(as_role sql_reader "select count(*) from api.screener")" \
  '^9\|2\|9\|ERROR:  permission denied[^|]*\|ERROR:  permission denied'
check "A40 semantic.companies: identifiers, curated group, market, currency and the index names" \
  "$(as_role sql_reader "select nse_symbol||'|'||peer_group||'|'||market||'|'||currency||'|'||valuation_family||'|'||indices from semantic.companies where company = 'Eq24 Alpha Ltd'")" '^EQ24-A\|Eq24 four-member group\|IN\|INR\|operating\|Nifty 100, Nifty 50$'
check "A41 semantic.company_financials: the revised figure only, in INR, with the derived EBIT (labelled) and gated by its policy" \
  "$(as_role sql_reader "select count(*)||'|'||max(revenue)||'|'||max(currency)||'|'||max(ebit_derived) from semantic.company_financials where company = 'Eq24 Alpha Ltd'")/$(flip sql_reader "$(HIDE equity.ebit_derived)" "select coalesce(max(ebit_derived)::text,'null') from semantic.company_financials where company = 'Eq24 Alpha Ltd'")" '^1\|1100\|INR\|500/null$'
check "A42 semantic.company_valuations: the model-implied columns are null while hidden and return when flipped (same CASE as the api views)" \
  "$(as_role sql_reader "select coalesce(implied_growth::text,'null')||'|'||pe_ttm from semantic.company_valuations where company = 'Eq24 Alpha Ltd'")/$(flip sql_reader "$(SHOW equity.implied_growth)" "select implied_growth||'|'||growth_gap from semantic.company_valuations where company = 'Eq24 Alpha Ltd'")" '^null\|10/0\.12\|0\.04$'
check "A43 semantic views: suppressing nse empties the valuation and financial views; the company list stays" \
  "$(flip sql_reader "update core.source set is_suppressed = true where key = 'nse'" "select (select count(*) from semantic.company_valuations)||'|'||(select count(*) from semantic.company_financials)||'|'||(select count(*) from semantic.companies where company like 'Eq24%')")" '^0\|0\|9$'
check "A44 every semantic view and column has a comment" \
  "$(as_pg "select (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'semantic' and c.relkind = 'v' and c.relname in ('companies','company_financials','company_valuations') and obj_description(c.oid, 'pg_class') is null)
            ||'|'||(select count(*) from pg_attribute a join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'semantic' and c.relkind = 'v' and c.relname in ('companies','company_financials','company_valuations') and a.attnum > 0 and not a.attisdropped and col_description(c.oid, a.attnum) is null)")" '^0\|0$'
check "A45 the semantic comments state units and the currency rule (INR, consolidated unless basis says otherwise, no cross-currency sums)" \
  "$(as_pg "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'semantic' and c.relname in ('companies','company_financials','company_valuations') and obj_description(c.oid, 'pg_class') ~* 'currenc'")" '^3$'

############################################################ the currency guard, across the new views
check "A46 every company row in every new api view carries market and currency" \
  "$(as_role anon "select count(*) from (
      select market, currency from api.companies union all select market, currency from api.index_members
      union all select market, currency from api.company_prices union all select market, currency from api.company_financials
      union all select market, currency from api.company_metrics union all select market, currency from api.company_corporate_actions
      union all select market, currency from api.screener union all select market, currency from api.peer_comps
      union all select market, currency from api.company_valuation union all select market, currency from api.valuation_scenarios
      union all select market, currency from api.priced_in_operating union all select market, currency from api.priced_in_lenders) x
      where x.market is null or x.currency is null")" '^0$'
check "A47 sql_reader still cannot read the core tables (the semantic views run as their owner; RLS stays in force)" \
  "$(as_role sql_reader "select count(*) from core.entity")" 'ERROR:  permission denied'

cleanup
check "A48 the test rows are gone" \
  "$(as_pg "select (select count(*) from core.entity where slug like 'eq24%')+(select count(*) from equity.price_daily)+(select count(*) from equity.company)+(select count(*) from equity.peer_group where key like 'eq24%')")" '^0$'
check "A49 the field policy rows are as seeded (the flips were rolled back)" \
  "$(as_pg "select string_agg(field_key||'='||status, ' ' order by field_key) from core.field_policy where field_key like 'equity.%'")" \
  '^equity\.beta=labelled equity\.cost_of_debt=labelled equity\.dcf_scenario=hidden equity\.ebit_derived=labelled equity\.implied_growth=hidden equity\.implied_roe=hidden equity\.nim_proxy=labelled equity\.peer_group=labelled equity\.rate_sensitivity=hidden equity\.roa=labelled$'

echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
