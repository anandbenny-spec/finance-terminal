-- 0043 · api.company_financials and api.value_chain filter by company before they compute (issue #469; #465's recommendation 2).
-- Two view bodies replaced, same columns, same rows, same grants and comments; no table, function, other view or policy changes.
--
-- Why: PostgREST asks for one company as `slug = '...'` plus a count subquery. Both views picked each period's / year's filing with
-- DISTINCT ON over EVERY company's rows and only then joined core.entity on the slug, so the filter reached the base tables
-- only after the whole ranking had run (company_financials: all 8,668 financials rows and 3,798 filing lookups for one company; value_chain:
-- the BRSR card over all 150 filings x 13 fields, then a prior-year nested loop). Under `anon`, RLS adds a filing EXISTS per row.
-- Cold, on a free-plan database, that is the statement timeout visitors saw (#465 section 3).
-- Now each view starts at core.entity and reaches into a LATERAL subquery keyed by that entity, so a slug filter picks the company
-- first and the ranking runs on its rows only. The rule is 0032's and 0042's (compute per company where the slug can reach the base
-- tables); the tie-break order, the field-policy gates and every case expression are unchanged.

-- api.company_financials (body: 0041's, with `fin` as a per-entity lateral subquery)
create or replace view api.company_financials with (security_invoker = true) as
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
from core.entity e
cross join g
cross join lateral (   -- the latest filing per (basis, period kind, period end), picked within this company only
  select distinct on (x.basis, x.period_kind, x.period_end) x.*, f.filed_at, f.source_url,
         f.exchange, f.audited, f.format
  from equity.financials x
  join equity.filing f on f.document_id = x.document_id
  where x.entity_id = e.id
  order by x.basis, x.period_kind, x.period_end, f.filed_at desc nulls last, x.document_id desc
) fin
left join equity.metric_value eb on eb.entity_id = e.id and eb.metric_key = 'ebit_derived'
     and eb.basis = fin.basis and eb.period_kind = fin.period_kind and eb.period_end = fin.period_end
left join core.source src on src.key = lower(fin.exchange);

-- api.value_chain (body: 0034's, with the card and the previous year read per company)
create or replace view api.value_chain with (security_invoker = true) as
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
from core.entity e
join equity.company co on co.entity_id = e.id
cross join g
cross join lateral (   -- offset 0 keeps the subquery whole, so `entity_id = e.id` reaches brsr_filing_shown's DISTINCT ON (its leading column)
  select c0.* from equity.brsr_card c0 where c0.entity_id = e.id offset 0
) c
left join lateral (   -- the previous own fiscal year of the same field, found within this company only
  select p0.* from equity.brsr_card p0
  where p0.entity_id = e.id and p0.field_key = c.field_key and p0.fy_end = c.fy_start - 1 offset 0
) p on true;
