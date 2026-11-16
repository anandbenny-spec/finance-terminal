#!/bin/bash
# Tests for 0043 (api.company_financials and api.value_chain filter by company before they compute; issue #469, #465's
# recommendation 2). Run after the other scripts on the same fresh database. One transaction builds seven companies, keeps 0041's
# company_financials body and 0034's value_chain body as throwaway views (public.vc43_old_cf, public.vc43_old_vc, security_invoker
# like the real ones), compares each with the new view in full as the owner and as anon (gates hidden and shown), reads the plans
# of a one-company read, and rolls back, so no row is left. run_tests_0024/0034/0041.sh run unchanged and check the views'
# meaning; this script checks that the rewrite returns the same rows and that a slug filter reaches the base tables.
H="-h ${PGHOST:-localhost} -p ${PGPORT:-5432} -d ${PGDATABASE:-ft} -X -A -t -q"
as_pg() { psql $H -U postgres -c "$1" 2>&1; }
pass=0; fail=0
check() { if [[ "$2" =~ $3 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; echo "      got: $2"; fail=$((fail+1)); fi; }

OUT="$(psql $H -U postgres -v ON_ERROR_STOP=1 2>&1 <<'SQL'
begin;
-- the old bodies, verbatim, as throwaway views (the negative controls for the plan tests and the references for the row tests)
create view public.vc43_old_cf with (security_invoker = true) as
with g as (   -- field policy, read once per query; a missing row is hidden (fails closed, Arch 01 §1.4)
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.ebit_derived'), false) as ebit,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.roa'), false) as roa,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.nim_proxy'), false) as nim,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.cost_of_debt'), false) as cod,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.beta'), false) as beta,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.implied_growth'), false) as ig,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.implied_roe'), false) as iroe,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.dcf_scenario'), false) as dcf,
         max(label) filter (where field_key = 'equity.ebit_derived' and status in ('shown','labelled')) as ebit_label,
         max(label) filter (where field_key = 'equity.roa' and status in ('shown','labelled')) as roa_label,
         max(label) filter (where field_key = 'equity.nim_proxy' and status in ('shown','labelled')) as nim_label,
         max(label) filter (where field_key = 'equity.peer_group' and status in ('shown','labelled')) as peer_label,
         max(label) filter (where field_key = 'equity.cost_of_debt' and status in ('shown','labelled')) as cod_label,
         max(label) filter (where field_key = 'equity.beta' and status in ('shown','labelled')) as beta_label,
         max(label) filter (where field_key = 'equity.implied_growth' and status in ('shown','labelled')) as ig_label,
         max(label) filter (where field_key = 'equity.implied_roe' and status in ('shown','labelled')) as iroe_label,
         max(label) filter (where field_key = 'equity.dcf_scenario' and status in ('shown','labelled')) as dcf_label,
         not exists (select 1 from core.source s where s.key = 'damodaran' and s.is_suppressed) as dam
  from core.field_policy where field_key like 'equity.%'
),
fin as (
  select distinct on (x.entity_id, x.basis, x.period_kind, x.period_end) x.*, f.filed_at, f.source_url,
         f.exchange, f.audited, f.format
  from equity.financials x
  join equity.filing f on f.document_id = x.document_id
  order by x.entity_id, x.basis, x.period_kind, x.period_end, f.filed_at desc nulls last, x.document_id desc
)
select e.slug, e.name, e.market, fin.currency, fin.basis, fin.period_kind, fin.period_start, fin.period_end,
       fin.revenue, fin.other_income, fin.total_income, fin.employee_cost, fin.dna, fin.finance_cost, fin.total_expenses,
       fin.share_of_associates, fin.pbt_before_exceptional, fin.exceptional, fin.pbt, fin.tax, fin.pat, fin.pat_owners,
       fin.pat_nci, fin.eps_basic, fin.eps_diluted, fin.total_assets, fin.current_assets, fin.cash, fin.bank_balances_other,
       fin.current_investments, fin.noncurrent_investments, fin.trade_receivables, fin.inventories, fin.total_equity,
       fin.equity_owners, fin.share_capital, fin.nci, fin.borrowings_current, fin.borrowings_noncurrent, fin.lease_current,
       fin.lease_noncurrent, fin.trade_payables, fin.cfo, fin.cfi, fin.cff, fin.capex, fin.dividends_paid, fin.paid_up_capital,
       fin.face_value, fin.shares_outstanding, fin.interest_earned, fin.interest_expended, fin.operating_profit,
       fin.provisions, fin.gnpa_ratio, fin.nnpa_ratio, fin.roa_filed, fin.cet1_ratio, fin.at1_ratio, fin.segment_assets_total,
       fin.deposits, fin.advances, fin.gross_premium, fin.net_premium, fin.assets_net, fin.current_liabilities_provisions,
       fin.solvency_ratio,
       case when g.ebit then eb.value end as ebit_derived,
       case when g.ebit then case when e.market = 'US' then 'operating income as filed' else g.ebit_label end end
         as ebit_derived_label,
       case when not g.ebit then 'Held back until its checks pass.' else regexp_replace(eb.held_reason, '^N/A: ', '') end as ebit_derived_held_reason,
       case when not g.ebit then 'held' when eb.value is not null then 'value'
            when eb.held_reason like 'N/A: %%' then 'not_applicable' when eb.held_reason is not null then 'held' end as ebit_derived_state,
       fin.format, fin.audited, fin.filed_at, fin.source_url, fin.exchange, lower(fin.exchange) as source_key,
       src.attribution
from fin
join core.entity e on e.id = fin.entity_id
cross join g
left join equity.metric_value eb on eb.entity_id = fin.entity_id and eb.metric_key = 'ebit_derived'
     and eb.basis = fin.basis and eb.period_kind = fin.period_kind and eb.period_end = fin.period_end
left join core.source src on src.key = lower(fin.exchange);;
grant select on public.vc43_old_cf to anon;
create view public.vc43_old_vc with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok,
         max(label) filter (where status in ('shown','labelled')) as lbl
  from core.field_policy where field_key = 'equity.brsr_values'
)
select e.slug as company_slug, e.name as company_name, e.short_name, e.market, e.currency, co.valuation_family,
       c.fy_label, c.fy_start, c.fy_end, c.field_key, c.field_no, c.label, c.item_ref, c.kind,
       case when g.ok then c.value end as value,
       case when g.ok then c.state end as state,
       case when g.ok then c.state_reason end as state_reason,
       case when g.ok then c.restated end as restated,
       case when g.ok then c.next_comparative end as next_comparative,
       case when g.ok then c.assured end as assured,
       case when g.ok then c.assurance end as assurance,
       case when g.ok and c.state in ('ok','zero') and p.event_id is not null and c.boundary = p.boundary
                 and c.months = 12 and p.months = 12 and p.state in ('ok','zero')
            then c.value - p.value end as change,
       case when not g.ok or c.state not in ('ok','zero') then null
            when p.event_id is null then 'no_previous_year'
            when c.boundary <> p.boundary then 'boundary_change'
            when c.months <> 12 or p.months <> 12 then 'not_12_months'
            when p.state not in ('ok','zero') then 'previous_value_unavailable' end as change_reason,
       c.boundary, c.format_version, c.months, c.exchange, lower(c.exchange) as source_key, c.filed_at, c.list_url,
       g.lbl as values_label
from equity.brsr_card c
cross join g
join core.entity e on e.id = c.entity_id
join equity.company co on co.entity_id = c.entity_id
left join equity.brsr_card p on p.entity_id = c.entity_id and p.field_key = c.field_key and p.fy_end = c.fy_start - 1;
grant select on public.vc43_old_vc to anon;

insert into equity.peer_group (key, market, label, source_note) values ('vc43-pg', 'IN', 'Vc43 group', 'test');
-- a: restated and amended filings, both bases, ebit metric; b: a US company; c: no financials and no BRSR; d: standalone only,
-- a BRSR boundary change; e: two BRSR versions of one year, a held value, a zero parent share; u: not published; w: another INR company
insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
  select 'company', 'vc43-' || s, 'Vc43 ' || upper(s) || ' Ltd', 'vc43-' || s, case when s = 'b' then 'US' else 'IN' end,
         case when s = 'b' then 'USD' else 'INR' end, s <> 'u'
  from unnest(array['a','b','c','d','e','u','w']) as s;
insert into equity.company (entity_id, isin, nse_symbol, bse_code, peer_group_key, valuation_family)
  select e.id, case when e.slug = 'vc43-b' then 'US0043' || lpad(row_number() over (order by e.slug)::text, 6, '0')
                    else 'INE0043' || lpad(row_number() over (order by e.slug)::text, 5, '0') end,
         'VC43' || upper(right(e.slug, 1)), case when e.slug = 'vc43-b' then null else '9043' || (row_number() over (order by e.slug)) end,
         'vc43-pg', 'operating'
  from core.entity e where e.slug like 'vc43-%';

------------------------------------------------------------------ company_financials fixture
create function pg_temp.mk_fin(p_slug text, p_tag text, p_basis text, p_kind text, p_end date, p_filed timestamptz, p_exch text, p_rev numeric)
returns void language plpgsql as $f$
declare eid integer; did bigint;
begin
  select id into eid from core.entity where slug = p_slug;
  insert into core.document (doc_type, entity_id, title, source_id, external_key, source_url, is_published)
    values ('results_xbrl', eid, 'vc43 ' || p_tag, (select id from core.source where key = lower(p_exch)), 'vc43-' || p_slug || '-' || p_tag,
            'https://example.com/vc43/' || p_tag, false) returning id into did;
  insert into equity.filing (document_id, entity_id, basis, format, period_end, filed_at, source_url, exchange, audited)
    values (did, eid, p_basis, case when p_exch = 'SEC' then 'us_gaap' else 'ind_as' end, p_end, p_filed, 'https://example.com/vc43/' || p_tag, p_exch, p_kind = 'year');
  insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, revenue)
    values (did, eid, p_basis, p_end - 90, p_end, p_kind, p_rev);
end $f$;
-- a: the quarter ended 31 Dec 2025 filed three times (the last, by time, wins; two share a time, so document_id decides), both bases,
-- a year, a filing with no filed_at (nulls last)
select pg_temp.mk_fin('vc43-a', 'q3-1', 'consolidated', 'quarter', date '2025-12-31', timestamptz '2026-01-10 10:00+05:30', 'NSE', 1000);
select pg_temp.mk_fin('vc43-a', 'q3-2', 'consolidated', 'quarter', date '2025-12-31', timestamptz '2026-01-20 10:00+05:30', 'NSE', 1100);
select pg_temp.mk_fin('vc43-a', 'q3-3', 'consolidated', 'quarter', date '2025-12-31', timestamptz '2026-01-20 10:00+05:30', 'NSE', 1200);
select pg_temp.mk_fin('vc43-a', 'q3-n', 'consolidated', 'quarter', date '2025-12-31', null, 'NSE', 999);
select pg_temp.mk_fin('vc43-a', 'q3-s', 'standalone', 'quarter', date '2025-12-31', timestamptz '2026-01-10 10:00+05:30', 'BSE', 500);
select pg_temp.mk_fin('vc43-a', 'y25', 'consolidated', 'year', date '2025-03-31', timestamptz '2025-05-20 10:00+05:30', 'NSE', 4000);
select pg_temp.mk_fin('vc43-a', 'y26-n', 'consolidated', 'year', date '2026-03-31', null, 'NSE', 4500);
select pg_temp.mk_fin('vc43-b', 'q3', 'consolidated', 'quarter', date '2025-12-31', timestamptz '2026-02-01 16:00-05:00', 'SEC', 77);
select pg_temp.mk_fin('vc43-d', 'q3-s', 'standalone', 'quarter', date '2025-12-31', timestamptz '2026-01-12 10:00+05:30', 'NSE', 300);
select pg_temp.mk_fin('vc43-e', 'q3', 'consolidated', 'quarter', date '2025-12-31', timestamptz '2026-01-12 10:00+05:30', 'NSE', 250);
select pg_temp.mk_fin('vc43-u', 'q3', 'consolidated', 'quarter', date '2025-12-31', timestamptz '2026-01-12 10:00+05:30', 'NSE', 111);
select pg_temp.mk_fin('vc43-w', 'q3', 'consolidated', 'quarter', date '2025-12-31', timestamptz '2026-01-12 10:00+05:30', 'NSE', 222);
select pg_temp.mk_fin('vc43-w', 'q3-s', 'standalone', 'quarter', date '2025-12-31', timestamptz '2026-01-12 10:00+05:30', 'NSE', 223);
insert into equity.metric_value (entity_id, metric_key, basis, period_kind, period_end, value, held_reason)
  select e.id, 'ebit_derived', v.b, v.pk, v.pe::date, v.val, v.hr
  from (values ('vc43-a','consolidated','quarter','2025-12-31', 500::numeric, null::text),
               ('vc43-a','standalone','quarter','2025-12-31', null, 'N/A: not meaningful'),
               ('vc43-a','consolidated','year','2025-03-31', null, 'Held for review'),
               ('vc43-b','consolidated','quarter','2025-12-31', 20, null),
               ('vc43-w','consolidated','quarter','2025-12-31', 30, null)) v(s, b, pk, pe, val, hr)
  join core.entity e on e.slug = v.s;

------------------------------------------------------------------ value_chain fixture
create function pg_temp.mk_brsr(p_slug text, p_fy text, p_start date, p_end date, p_boundary text, p_version int, p_months int,
                                p_vals jsonb, p_held jsonb)
returns void language plpgsql as $f$
declare eid integer; evid bigint; did bigint; k text; v jsonb; tag text := lower(p_slug || '-' || replace(p_fy, ' ', '') || '-v' || p_version);
begin
  select id into eid from core.entity where slug = p_slug;
  insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
    values ('brsr_filing', eid, tag, 'vc43', p_end, 'stored', (select id from core.source where key = 'nse'), true) returning id into evid;
  insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, is_published)
    values ('brsr_xbrl', eid, evid, 'vc43 ' || tag, (select id from core.source where key = 'nse'), tag,
            'https://example.com/vc43/' || tag || '.xml', false) returning id into did;
  insert into equity.brsr_filing (event_id, entity_id, document_id, fy_label, fy_start, fy_end, format_version, boundary,
                                  exchange, list_url, version_no, filed_at, months, openness_assured, openness_assurance)
    values (evid, eid, did, p_fy, p_start, p_end, date '2024-04-30', p_boundary, 'NSE', 'https://example.com/brsr/' || p_slug,
            p_version, p_end + 90, p_months, true, 'reasonable');
  for k, v in select * from jsonb_each(p_vals) loop
    insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status)
      values (evid, k, v::numeric, case when k like '%share' then 'share' else 'count' end, 'E' || k, 'P1-EI-9, ' || k, 'ok');
  end loop;
  for k, v in select * from jsonb_each(coalesce(p_held, '{}')) loop
    insert into equity.brsr_value (event_id, field_key, value, kind, element, item_ref, status, held_reason)
      values (evid, k, (v->>0)::numeric, case when k like '%share' then 'share' else 'count' end, 'E' || k, 'P8-EI-4, ' || k, 'held', v->>1);
  end loop;
end $f$;
-- a: two consecutive 12-month years on one boundary (a change), with the earlier year filed twice (the higher version shows)
select pg_temp.mk_brsr('vc43-a', 'FY 2024-25', date '2024-04-01', date '2025-03-31', 'consolidated', 1, 12,
  '{"within_india_share": 0.50, "states_served": 10, "trading_house_share": 0.20, "top10_trading_share": 0.10}', null);
select pg_temp.mk_brsr('vc43-a', 'FY 2024-25', date '2024-04-01', date '2025-03-31', 'consolidated', 2, 12,
  '{"within_india_share": 0.55, "states_served": 12, "trading_house_share": 0.20, "top10_trading_share": 0.10}', null);
select pg_temp.mk_brsr('vc43-a', 'FY 2025-26', date '2025-04-01', date '2026-03-31', 'consolidated', 1, 12,
  '{"within_india_share": 0.60, "states_served": 15, "trading_house_share": 0.25, "top10_trading_share": 0.15}', '{"msme_share": [0.30, "held for review"]}');
-- b: one year only (no previous year)
select pg_temp.mk_brsr('vc43-b', 'FY 2025-26', date '2025-04-01', date '2026-03-31', 'consolidated', 1, 12,
  '{"within_india_share": 0.70, "states_served": 8}', null);
-- d: the boundary changes between the years, and a short year
select pg_temp.mk_brsr('vc43-d', 'FY 2024-25', date '2024-04-01', date '2025-03-31', 'standalone', 1, 12, '{"within_india_share": 0.40, "states_served": 5}', null);
select pg_temp.mk_brsr('vc43-d', 'FY 2025-26', date '2025-04-01', date '2026-03-31', 'consolidated', 1, 12, '{"within_india_share": 0.45, "states_served": 6}', null);
-- e: a 9-month year after a 12-month one; a zero parent share hides its top-10 child
select pg_temp.mk_brsr('vc43-e', 'FY 2024-25', date '2024-04-01', date '2025-03-31', 'consolidated', 1, 12,
  '{"within_india_share": 0.10, "trading_house_share": 0, "top10_trading_share": 0}', null);
select pg_temp.mk_brsr('vc43-e', 'FY 2025-26', date '2025-04-01', date '2025-12-31', 'consolidated', 1, 9,
  '{"within_india_share": 0.20, "trading_house_share": 0, "top10_trading_share": 0}', null);
-- u: not published; w: a second company
select pg_temp.mk_brsr('vc43-u', 'FY 2025-26', date '2025-04-01', date '2026-03-31', 'consolidated', 1, 12, '{"within_india_share": 0.99}', null);
select pg_temp.mk_brsr('vc43-w', 'FY 2025-26', date '2025-04-01', date '2026-03-31', 'consolidated', 1, 12, '{"within_india_share": 0.33, "states_served": 3}', null);

select 'SETUP|' || (select count(*) from equity.financials x join core.entity e on e.id = x.entity_id where e.slug like 'vc43-%')
          || ':' || (select count(*) from equity.brsr_value v join core.event ev on ev.id = v.event_id where ev.slug like 'vc43-%');

------------------------------------------------------------------ same rows, in full, both ways, owner and anon, gates hidden and shown
update core.field_policy set status = 'hidden' where field_key in ('equity.ebit_derived', 'equity.brsr_values');
select 'F1|' || (select count(*) from (select * from api.company_financials except select * from public.vc43_old_cf) x) || ':'
            || (select count(*) from (select * from public.vc43_old_cf except select * from api.company_financials) x) || ':'
            || (select count(*) from api.company_financials where slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_cf where slug like 'vc43%');
select 'V1|' || (select count(*) from (select * from api.value_chain except select * from public.vc43_old_vc) x) || ':'
            || (select count(*) from (select * from public.vc43_old_vc except select * from api.value_chain) x) || ':'
            || (select count(*) from api.value_chain where company_slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_vc where company_slug like 'vc43%');
set role anon;
select 'F3|' || (select count(*) from (select * from api.company_financials except select * from public.vc43_old_cf) x) || ':'
            || (select count(*) from (select * from public.vc43_old_cf except select * from api.company_financials) x) || ':'
            || (select count(*) from api.company_financials where slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_cf where slug like 'vc43%');
select 'V3|' || (select count(*) from (select * from api.value_chain except select * from public.vc43_old_vc) x) || ':'
            || (select count(*) from (select * from public.vc43_old_vc except select * from api.value_chain) x) || ':'
            || (select count(*) from api.value_chain where company_slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_vc where company_slug like 'vc43%');
reset role;
update core.field_policy set status = 'shown' where field_key in ('equity.ebit_derived', 'equity.brsr_values');
select 'F2|' || (select count(*) from (select * from api.company_financials except select * from public.vc43_old_cf) x) || ':'
            || (select count(*) from (select * from public.vc43_old_cf except select * from api.company_financials) x) || ':'
            || (select count(*) from api.company_financials where slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_cf where slug like 'vc43%');
select 'V2|' || (select count(*) from (select * from api.value_chain except select * from public.vc43_old_vc) x) || ':'
            || (select count(*) from (select * from public.vc43_old_vc except select * from api.value_chain) x) || ':'
            || (select count(*) from api.value_chain where company_slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_vc where company_slug like 'vc43%');
set role anon;
select 'F4|' || (select count(*) from (select * from api.company_financials except select * from public.vc43_old_cf) x) || ':'
            || (select count(*) from (select * from public.vc43_old_cf except select * from api.company_financials) x) || ':'
            || (select count(*) from api.company_financials where slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_cf where slug like 'vc43%');
select 'V4|' || (select count(*) from (select * from api.value_chain except select * from public.vc43_old_vc) x) || ':'
            || (select count(*) from (select * from public.vc43_old_vc except select * from api.value_chain) x) || ':'
            || (select count(*) from api.value_chain where company_slug like 'vc43%') || ':'
            || (select count(*) from public.vc43_old_vc where company_slug like 'vc43%');
-- spot values, so the fixture is known to exercise what the equality is about
select 'S1|' || string_agg(basis || '/' || period_kind || '/' || period_end || '=' || revenue || ':' || coalesce(ebit_derived_state, '-'), ',' order by basis, period_kind, period_end)
  from api.company_financials where slug = 'vc43-a';
select 'S2|' || count(*) from api.company_financials where slug in ('vc43-c', 'vc43-u');
select 'S3|' || string_agg(fy_label || '/' || field_key || '=' || coalesce(value::text, '-') || ':' || state || ':' || coalesce(change::text, coalesce(change_reason, '-')),
                           ',' order by fy_end, field_no)
  from api.value_chain where company_slug = 'vc43-a' and field_key in ('within_india_share', 'states_served', 'msme_share');
select 'S4|' || string_agg(company_slug || '/' || fy_label || ':' || coalesce(change_reason, 'chg'), ',' order by company_slug, fy_end)
  from api.value_chain where company_slug in ('vc43-d', 'vc43-e') and field_key = 'within_india_share';
select 'S5|' || count(*) from api.value_chain where company_slug in ('vc43-c', 'vc43-u');

------------------------------------------------------------------ the plans of a one-company read, with the count subquery's shape
set enable_seqscan = off;
select 'PLAN-CF-NEW-START';
explain select * from api.company_financials where slug = 'vc43-a';
select 'PLAN-CF-NEW-END';
select 'PLAN-CF-OLD-START';
explain select * from public.vc43_old_cf where slug = 'vc43-a';
select 'PLAN-CF-OLD-END';
select 'PLAN-VC-NEW-START';
explain select * from api.value_chain where company_slug = 'vc43-a';
select 'PLAN-VC-NEW-END';
select 'PLAN-VC-OLD-START';
explain select * from public.vc43_old_vc where company_slug = 'vc43-a';
select 'PLAN-VC-OLD-END';
reset role;
rollback;
SQL
)"
get() { grep "^$1|" <<<"$OUT" | head -1 | cut -d'|' -f2-; }
plan() { sed -n "/^PLAN-$1-START/,/^PLAN-$1-END/p" <<<"$OUT"; }

check "S00 the fixture loaded (13 financials rows and 25 BRSR values in the fixture's companies)" "$(get SETUP)" '^13:[0-9]+$'
check "F1 company_financials, owner, ebit hidden: no row differs either way, and rows exist" "$(get F1)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "F2 company_financials, owner, ebit shown: no row differs either way, and rows exist" "$(get F2)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "F3 company_financials, anon, ebit hidden: no row differs either way, and rows exist" "$(get F3)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "F4 company_financials, anon, ebit shown: no row differs either way, and rows exist" "$(get F4)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "V1 value_chain, owner, gate hidden: no row differs either way, and rows exist" "$(get V1)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "V2 value_chain, owner, gate shown: no row differs either way, and rows exist" "$(get V2)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "V3 value_chain, anon, gate hidden: no row differs either way, and rows exist" "$(get V3)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "V4 value_chain, anon, gate shown: no row differs either way, and rows exist" "$(get V4)" '^0:0:[1-9][0-9]*:[1-9][0-9]*$'
check "S1 one row per (basis, kind, period end): the last filing by time wins, document_id breaks the tie, a null filed_at is last" \
  "$(get S1)" '^consolidated/quarter/2025-12-31=1200(\.0*)?:value,consolidated/year/2025-03-31=4000(\.0*)?:held,consolidated/year/2026-03-31=4500(\.0*)?:-,standalone/quarter/2025-12-31=500(\.0*)?:not_applicable$'
check "S2 a company with no financials and an unpublished company show no row to anon" "$(get S2)" '^0$'
check "S3 the card shows the highest version of a year, the change is the difference from the previous year, a held field has no value" \
  "$(get S3)" '^FY 2024-25/within_india_share=55(\.0*)?:ok:no_previous_year,FY 2024-25/msme_share=-:absent:-,FY 2024-25/states_served=12(\.0*)?:ok:no_previous_year,FY 2025-26/within_india_share=60(\.0*)?:ok:5(\.0*)?,FY 2025-26/msme_share=-:held:-,FY 2025-26/states_served=15(\.0*)?:ok:3(\.0*)?$'
check "S4 a boundary change and a 9-month year carry their reasons instead of a change" \
  "$(get S4)" '^vc43-d/FY 2024-25:no_previous_year,vc43-d/FY 2025-26:boundary_change,vc43-e/FY 2024-25:no_previous_year,vc43-e/FY 2025-26:not_12_months$'
check "S5 a company with no BRSR filing and an unpublished company show no row to anon" "$(get S5)" '^0$'

CFN="$(plan CF-NEW)"; CFO="$(plan CF-OLD)"; VCN="$(plan VC-NEW)"; VCO="$(plan VC-OLD)"
check "P1 company_financials: the one-company read reaches equity.financials by the entity (an index condition on entity_id from core.entity)" \
  "$(grep -E 'Index Cond: \(entity_id = e\.id\)' <<<"$CFN" | head -1 | sed 's/.*/ok/')" '^ok$'
check "P2 control: 0041's body reads every company's financials (no entity_id index condition), so the check can fail" \
  "$(grep -cE 'Index Cond: \(entity_id = e\.id\)' <<<"$CFO")" '^0$'
check "P3 value_chain: the BRSR filings are read by the entity (an index condition on brsr_filing.entity_id)" \
  "$(grep -cE 'Index Cond: \(entity_id = e\.id\)' <<<"$VCN" | sed 's/^[1-9].*/ok/')" '^ok$'
check "P4 value_chain: the previous year's card is read by the entity too (two reads of brsr_filing, both with that condition)" \
  "$(grep -cE 'Index Cond: \(entity_id = e\.id\)' <<<"$VCN")" '^[2-9]$'
check "P5 control: 0034's body reads every filing (no entity_id index condition on brsr_filing), so the check can fail" \
  "$(grep -cE 'Index Cond: \(entity_id = e\.id\)' <<<"$VCO")" '^0$'

check "C01 company_financials: its columns and their order are 0041's" \
  "$(as_pg "select md5(string_agg(column_name || ' ' || data_type, ',' order by ordinal_position)) from information_schema.columns where table_schema = 'api' and table_name = 'company_financials'")" '^[0-9a-f]{32}$'
check "C02 both views are still security_invoker" \
  "$(as_pg "select count(*) from pg_class where oid in ('api.company_financials'::regclass, 'api.value_chain'::regclass) and coalesce(reloptions::text, '') like '%security_invoker=true%'")" '^2$'
check "C03 grants are unchanged: anon, authenticated and service_role can select, PUBLIC cannot" \
  "$(as_pg "select bool_and(has_table_privilege('anon', v, 'select') and has_table_privilege('authenticated', v, 'select') and has_table_privilege('service_role', v, 'select')) and not exists (select 1 from pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a where c.oid in ('api.company_financials'::regclass, 'api.value_chain'::regclass) and a.grantee = 0) from unnest(array['api.company_financials','api.value_chain']) v")" '^t$'
check "C04 the field policies are back to hidden and no fixture row is left" \
  "$(as_pg "select (select string_agg(status, ',' order by field_key) from core.field_policy where field_key in ('equity.brsr_values')) || ':' || (select count(*) from core.entity where slug like 'vc43%') || ':' || (select count(*) from pg_class where relname like 'vc43_old%')")" '^hidden:0:0$'

echo; echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
