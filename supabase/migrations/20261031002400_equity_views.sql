-- 0024: the read layer for module 2's pages and the command bar (release 2A, job 2A-17; issue #123; Spec 02A §7).
-- Twelve `api` views (security_invoker: the visitor's own RLS applies) and three `semantic` views (owned by
-- semantic_owner, security_barrier, every view and column commented: the comments are the text-to-SQL prompt's
-- schema description). No table, column or policy changes: only views, grants and comments.
--
-- Gating (Arch 01 §1.4): a gated column is nulled by a CASE inside the view, from `core.field_policy` read in the
-- view's own `g` query; a missing policy row is hidden (fails closed). It is never gated by a join alone.
--   equity.ebit_derived  EBIT, and (decided by Claude, standing delegation, 31 Oct 2026: it only tightens) the figures
--                        built from it: EBIT margin, EV/EBIT, return on invested capital
--   equity.roa, equity.nim_proxy, equity.peer_group (peer comps and the group label), equity.cost_of_debt (the
--                        valuation inputs' `cost_of_debt`), equity.beta (regression and bottom-up betas)
--   equity.implied_growth (reverse-DCF results, the screener's implied growth, delivered growth and gap, the operating
--                        priced-in table), equity.implied_roe (implied ROE, the lenders' table), equity.dcf_scenario
--                        (`dcf_default` and the scenario grid)
--   equity.rate_sensitivity is 2A-23's. Hidden fields read as state 'held' with the reason "Held back until its checks
--   pass."; a labelled field carries its policy label.
-- Damodaran's switch (decided by Claude, standing delegation, 31 Oct 2026: it only tightens): while `damodaran` is
-- suppressed, the bottom-up beta and every model-implied column (implied growth, implied ROE, the gaps, the beta
-- columns) read as null in every view here, as `equity.valuation_run`'s rows already disappear (their row policy).
-- NSE's switch is the base tables' row policies (0019 §3.2): prices, filings, metrics, snapshots, memberships and
-- valuation runs vanish; `api.companies` keeps its identity rows with the price and membership columns empty.
--
-- Decided by Claude (standing delegation, 31 Oct 2026), recorded on #123:
--  * a row's `state` is 'value', 'held' (a stored reason, or a hidden field) or 'not_applicable' (the stored reason
--    began with "N/A: "; the prefix is stripped, 2A-11's convention), so pages never parse text;
--  * `equity.screener_snapshot.notes` is read as {"<column>": {"label": ..., "reason": ...}} (2A-15 writes it); the
--    view passes it through with the entries of gated columns removed;
--  * `equity.valuation_run.inputs` is read as an object: a hidden `equity.cost_of_debt` removes its `cost_of_debt` key,
--    a hidden `equity.beta` removes `beta`; "n of years" in the priced-in tables is `inputs ->> 'delivered_years'`
--    (null when 2A-14 doesn't write it);
--  * `api.company_prices.adj_close` is also null while `nse` is suppressed (the factors come from NSE's corporate
--    actions, whose events vanish with it; a BSE-priced company must not read as unadjusted).


------------------------------------------------------------------ api.companies
create view api.companies with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer
       , max(label) filter (where field_key = 'equity.peer_group' and status in ('shown','labelled')) as peer_label
  from core.field_policy where field_key like 'equity.%'
)
select e.slug, e.name, e.short_name, e.market, e.currency, c.isin, c.nse_symbol, c.bse_code, c.nse_industry,
       c.peer_group_key,
       case when g.peer then pg.label end as peer_group_label,
       case when g.peer then g.peer_label end as peer_group_label_kind,
       c.valuation_family, c.family_note, c.fy_end_month, c.price_exchange, c.listed_on,
       ix.index_slugs, ix.index_as_of,
       p0.close as last_close, p0.trade_date as last_close_date,
       p1.close as previous_close, p1.trade_date as previous_close_date,
       case when p0.trade_date is not null then lower(c.price_exchange) end as price_source_key
from equity.company c
join core.entity e on e.id = c.entity_id
cross join g
left join equity.peer_group pg on pg.key = c.peer_group_key
left join lateral (
  select array_agg(i.slug order by i.slug) as index_slugs, max(m.snapshot_date) as index_as_of
  from equity.index_membership m
  join core.entity i on i.id = m.index_entity_id
  where m.entity_id = c.entity_id
    and m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id)
) ix on true
left join lateral (select d.close, d.trade_date from equity.price_daily d
                   where d.entity_id = c.entity_id and d.exchange = c.price_exchange
                   order by d.trade_date desc limit 1) p0 on true
left join lateral (select d.close, d.trade_date from equity.price_daily d
                   where d.entity_id = c.entity_id and d.exchange = c.price_exchange
                   order by d.trade_date desc limit 1 offset 1) p1 on true;

------------------------------------------------------------------ api.index_members
create view api.index_members with (security_invoker = true) as
select i.slug as index_slug, i.name as index_name, m.snapshot_date, e.slug, e.name, e.short_name, c.nse_symbol,
       e.market, e.currency
from equity.index_membership m
join core.entity i on i.id = m.index_entity_id
join core.entity e on e.id = m.entity_id
join equity.company c on c.entity_id = m.entity_id
where m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id);

------------------------------------------------------------------ api.company_prices
-- A company's full history is about 1,950 rows: the page pages it by date (CLAUDE.md §6).
create view api.company_prices with (security_invoker = true) as
select e.slug, e.market, e.currency, p.trade_date, p.exchange, p.close, p.volume, p.traded_value,
       a.adj_close, (a.adj_close is null) as adj_held, lower(p.exchange) as source_key
from equity.price_daily p
join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
join core.entity e on e.id = p.entity_id
cross join lateral (
  -- the same rule as equity.price_adjusted (0019): the product of every applied factor with a later ex-date, null while
  -- a held factor has one. Repeated here so visitors never need SELECT on that view (0019's tests pin that); the 0024
  -- tests compare the two row by row.
  select case
           when exists (select 1 from core.source s where s.key = 'nse' and s.is_suppressed) then null
           when exists (select 1 from equity.adjustment_factor h
                        where h.entity_id = p.entity_id and h.status = 'held' and h.ex_date > p.trade_date) then null
           else p.close * coalesce((select round(exp(sum(ln(f.price_factor))), 12) from equity.adjustment_factor f
                                    where f.entity_id = p.entity_id and f.status = 'applied' and f.ex_date > p.trade_date), 1)
         end as adj_close
) a;

------------------------------------------------------------------ api.company_financials
-- The latest-filed figure per company x basis x period kind x period (a revised filing replaces the earlier one).
create view api.company_financials with (security_invoker = true) as
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
       case when g.ebit then g.ebit_label end as ebit_derived_label,
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
left join core.source src on src.key = lower(fin.exchange);

------------------------------------------------------------------ api.company_metrics
create view api.company_metrics with (security_invoker = true) as
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
select e.slug, e.name, e.market, e.currency, mv.metric_key, m.label, m.unit, mv.basis, mv.period_kind, mv.period_end,
       mv.period_end as as_of,
       case when k.shown then mv.value end as value,
       case when k.shown then coalesce(k.label, m.label_kind) else null end as label_kind,
       case when not k.shown then k.reason
            else regexp_replace(mv.held_reason, '^N/A: ', '') end as held_reason,
       case when not k.shown then 'held' when mv.value is not null then 'value'
            when mv.held_reason like 'N/A: %%' then 'not_applicable' else 'held' end as state,
       lower(c.price_exchange) as source_key
from equity.metric_value mv
join equity.metric m on m.key = mv.metric_key
join core.entity e on e.id = mv.entity_id
join equity.company c on c.entity_id = mv.entity_id
cross join g
cross join lateral (
  select case mv.metric_key
           when 'ebit_derived' then g.ebit when 'ebit_margin' then g.ebit when 'ev_ebit_ttm' then g.ebit
           when 'roic' then g.ebit when 'roa' then g.roa when 'nim_proxy' then g.nim
           when 'beta_regression' then g.beta when 'beta_bottom_up' then g.beta and g.dam else true end as shown,
         case mv.metric_key
           when 'ebit_derived' then g.ebit_label when 'ebit_margin' then g.ebit_label when 'ev_ebit_ttm' then g.ebit_label
           when 'roic' then g.ebit_label when 'roa' then g.roa_label when 'nim_proxy' then g.nim_label
           when 'beta_regression' then g.beta_label when 'beta_bottom_up' then g.beta_label end as label,
         case when mv.metric_key = 'beta_bottom_up' and g.beta and not g.dam then 'Held back while its data source is withdrawn.' else 'Held back until its checks pass.' end as reason
) k;

------------------------------------------------------------------ api.company_corporate_actions
-- A withdrawn announcement is left out (final in core.event_status); announced ones sit beside effective ones.
create view api.company_corporate_actions with (security_invoker = true) as
select e.slug, e.market, e.currency, ca.kind, ca.ex_date, ca.record_date, ca.subject, ca.ratio_new, ca.ratio_old,
       ca.face_value_old, ca.face_value_new, ca.dividend_per_share, ca.rights_price, ca.exchange,
       ev.status, ev.title, ev.source_url,
       case when af.event_id is null then 'none' else af.status end as factor_status,
       case when af.status = 'applied' then af.price_factor end as price_factor,
       case when af.status = 'held' then af.reason end as factor_reason,
       lower(ca.exchange) as source_key, src.attribution
from equity.corporate_action ca
join core.event ev on ev.id = ca.event_id
join core.entity e on e.id = ev.entity_id
left join equity.adjustment_factor af on af.event_id = ca.event_id
left join core.source src on src.key = lower(ca.exchange)
where ev.status <> 'withdrawn';

------------------------------------------------------------------ api.screener
create view api.screener with (security_invoker = true) as
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
latest as (select distinct on (s.entity_id) s.* from equity.screener_snapshot s
                order by s.entity_id, s.snapshot_date desc)
select e.slug, e.name, e.short_name, c.nse_symbol, e.market, e.currency, c.peer_group_key,
       case when g.peer then pg.label end as peer_group_label,
       l.snapshot_date, l.price, l.price_date, l.market_cap, l.shares, l.pe_ttm, l.pb, l.ev,
       case when g.ebit then l.ev_ebit end as ev_ebit, l.ev_sales, l.dividend_yield, l.roe,
       case when g.ebit then l.ebit_margin end as ebit_margin, l.revenue_cagr_3y, l.revenue_cagr_5y,
       l.debt_to_equity, l.promoter_pct,
       case when g.beta and g.dam then l.beta end as beta, l.return_1y, l.traded_value_median_3m,
       case when g.ig and g.dam then l.implied_growth end as implied_growth,
       case when g.ig then l.delivered_growth end as delivered_growth,
       case when g.ig and g.dam and l.implied_growth is not null and l.delivered_growth is not null
            then l.implied_growth - l.delivered_growth end as growth_gap,
       case when g.iroe and g.dam then l.implied_roe end as implied_roe,
       case when g.iroe then l.delivered_roe end as delivered_roe,
       case when g.iroe and g.dam and l.implied_roe is not null and l.delivered_roe is not null
            then l.implied_roe - l.delivered_roe end as roe_gap,
       l.valuation_family,
       case when l.notes is null then null else l.notes - array_remove(array[
         case when not g.ebit then 'ev_ebit' end, case when not g.ebit then 'ebit_margin' end,
         case when not (g.beta and g.dam) then 'beta' end,
         case when not (g.ig and g.dam) then 'implied_growth' end, case when not g.ig then 'delivered_growth' end,
         case when not (g.iroe and g.dam) then 'implied_roe' end, case when not g.iroe then 'delivered_roe' end],
         null) end as notes,
       g.ebit_label as ebit_label, g.beta_label as beta_label, g.ig_label as implied_growth_label,
       g.iroe_label as implied_roe_label,
       (select array_agg(i.slug order by i.slug) from equity.index_membership m
          join core.entity i on i.id = m.index_entity_id
         where m.entity_id = c.entity_id
           and m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id)) as index_slugs,
       'nse' as source_key
from latest l
join equity.company c on c.entity_id = l.entity_id
join core.entity e on e.id = l.entity_id
cross join g
left join equity.peer_group pg on pg.key = c.peer_group_key;

------------------------------------------------------------------ api.peer_comps (Spec 02A §5.11)
-- Rows: row_kind 'peer' (each other member of the company's curated group, same market, from its latest snapshot)
-- and row_kind 'median' (one per company: the median of those others, per metric, only with at least three others
-- holding a value; else null with the note, and the peers stay listed).
create view api.peer_comps with (security_invoker = true) as
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
latest as (select distinct on (s.entity_id) s.* from equity.screener_snapshot s
                order by s.entity_id, s.snapshot_date desc),
members as (
  select c.entity_id, c.peer_group_key, e.slug, e.name, e.short_name, c.nse_symbol, e.market, e.currency,
         l.snapshot_date, l.pe_ttm, l.pb, case when g.ebit then l.ev_ebit end as ev_ebit, l.ev_sales, l.roe,
         case when g.ebit then l.ebit_margin end as ebit_margin, l.revenue_cagr_3y
  from equity.company c
  join core.entity e on e.id = c.entity_id
  join latest l on l.entity_id = c.entity_id
  cross join g
  where g.peer and c.peer_group_key is not null
),
pairs as (
  select a.slug as company_slug, b.*
  from members a
  join members b on b.peer_group_key = a.peer_group_key and b.market = a.market and b.entity_id <> a.entity_id
)
select p.company_slug, p.market, p.currency, p.peer_group_key, 'peer'::text as row_kind, p.slug as peer_slug,
       p.name as peer_name, p.short_name as peer_short_name, p.nse_symbol as peer_nse_symbol, p.snapshot_date,
       p.pe_ttm, p.pb, p.ev_ebit, p.ev_sales, p.roe, p.ebit_margin, p.revenue_cagr_3y,
       null::integer as peer_count, null::jsonb as median_n, null::text as median_note, 'nse' as source_key
from pairs p
union all
select p.company_slug, p.market, p.currency, p.peer_group_key, 'median', null, null, null, null, max(p.snapshot_date),
       case when count(p.pe_ttm) >= 3 then percentile_cont(0.5) within group (order by p.pe_ttm) end,
       case when count(p.pb) >= 3 then percentile_cont(0.5) within group (order by p.pb) end,
       case when count(p.ev_ebit) >= 3 then percentile_cont(0.5) within group (order by p.ev_ebit) end,
       case when count(p.ev_sales) >= 3 then percentile_cont(0.5) within group (order by p.ev_sales) end,
       case when count(p.roe) >= 3 then percentile_cont(0.5) within group (order by p.roe) end,
       case when count(p.ebit_margin) >= 3 then percentile_cont(0.5) within group (order by p.ebit_margin) end,
       case when count(p.revenue_cagr_3y) >= 3 then percentile_cont(0.5) within group (order by p.revenue_cagr_3y) end,
       count(*)::integer,
       jsonb_build_object('pe_ttm', count(p.pe_ttm), 'pb', count(p.pb), 'ev_ebit', count(p.ev_ebit),
                          'ev_sales', count(p.ev_sales), 'roe', count(p.roe), 'ebit_margin', count(p.ebit_margin),
                          'revenue_cagr_3y', count(p.revenue_cagr_3y)),
       case when count(*) < 3 then 'Too few peers in the Nifty 50 for a median' end,
       'nse'
from pairs p
group by p.company_slug, p.market, p.currency, p.peer_group_key;

------------------------------------------------------------------ api.company_valuation
-- The latest run of each kind per company, gated per kind (reverse_dcf: equity.implied_growth, implied_roe:
-- equity.implied_roe, dcf_default: equity.dcf_scenario). A hidden run keeps its row: state 'held', every
-- model-derived column null.
create view api.company_valuation with (security_invoker = true) as
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
r as (select distinct on (v.entity_id, v.kind) v.* from equity.valuation_run v
      order by v.entity_id, v.kind, v.as_of desc, v.id desc)
select e.slug, e.name, e.market, e.currency, c.valuation_family, r.kind, r.as_of,
       case when k.shown then r.status end as status,
       case when not k.shown then 'held' when r.status = 'solved' then 'value'
            when r.status = 'not_applicable' then 'not_applicable' else 'held' end as state,
       case when k.shown then regexp_replace(r.reason, '^N/A: ', '') else 'Held back until its checks pass.' end as reason,
       case when k.shown then r.price end as price,
       case when k.shown then r.result end as result,
       case when k.shown then k.label end as result_label,
       case when k.shown then r.delivered end as delivered,
       case when k.shown then r.delivered_reason end as delivered_reason,
       case when not k.shown then null
            when jsonb_typeof(r.inputs) = 'object' then r.inputs - array_remove(array[
                 case when not g.cod then 'cost_of_debt' end, case when not g.beta then 'beta' end], null)
            else r.inputs end as inputs,
       case when k.shown then r.model_version end as model_version,
       'nse' as source_key
from r
join core.entity e on e.id = r.entity_id
join equity.company c on c.entity_id = r.entity_id
cross join g
cross join lateral (select case r.kind when 'reverse_dcf' then g.ig when 'implied_roe' then g.iroe else g.dcf end as shown, case r.kind when 'reverse_dcf' then g.ig_label when 'implied_roe' then g.iroe_label else g.dcf_label end as label) k;

------------------------------------------------------------------ api.valuation_scenarios
-- The latest dcf_default run's grid (growth x EBIT margin x WACC). Hidden by equity.dcf_scenario: every grid
-- column reads null and the state says held.
create view api.valuation_scenarios with (security_invoker = true) as
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
r as (select distinct on (v.entity_id) v.* from equity.valuation_run v where v.kind = 'dcf_default'
      order by v.entity_id, v.as_of desc, v.id desc)
select e.slug, e.name, e.market, e.currency, r.as_of,
       case when g.dcf then sc.growth end as growth,
       case when g.dcf then sc.ebit_margin end as ebit_margin,
       case when g.dcf then sc.wacc end as wacc,
       case when g.dcf and g.dam then sc.value_per_share end as value_per_share,
       case when not (g.dcf and g.dam) then 'held' when sc.value_per_share is null then 'not_applicable' else 'value' end as state,
       case when not (g.dcf and g.dam) then 'Held back until its checks pass.' when sc.value_per_share is null then 'No value exists at these assumptions.' end as reason,
       g.dcf_label as label
from r
join equity.valuation_scenario sc on sc.run_id = r.id
join core.entity e on e.id = r.entity_id
cross join g;

------------------------------------------------------------------ api.priced_in_operating
-- Operating companies by growth gap (implied minus delivered revenue growth); held-back and N/A rows (insurers and mixed companies included) carry their reason and no rank.
create view api.priced_in_operating with (security_invoker = true) as
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
r as (select distinct on (v.entity_id) v.* from equity.valuation_run v where v.kind = 'reverse_dcf'
      order by v.entity_id, v.as_of desc, v.id desc),
s as (
  select e.slug, e.name, e.short_name, c.nse_symbol, e.market, e.currency, c.peer_group_key, c.valuation_family, r.as_of,
         case when not (g.ig and g.dam) then 'held' when r.status = 'solved' then 'value'
              when r.status = 'not_applicable' then 'not_applicable' else 'held' end as state,
         case when (g.ig and g.dam) then regexp_replace(r.reason, '^N/A: ', '') else 'Held back until its checks pass.' end as reason,
         case when (g.ig and g.dam) then r.result end as implied,
         case when (g.ig and g.dam) then r.delivered end as delivered,
         case when (g.ig and g.dam) and r.result is not null and r.delivered is not null then r.result - r.delivered end as gap,
         case when (g.ig and g.dam) and r.inputs ->> 'delivered_years' ~ '^[0-9]+$' then (r.inputs ->> 'delivered_years')::integer end as delivered_years,
         case when (g.ig and g.dam) then r.delivered_reason end as delivered_reason,
         (g.ig_label) as label
  from r
  join core.entity e on e.id = r.entity_id
  join equity.company c on c.entity_id = r.entity_id
  cross join g
  where c.valuation_family in ('operating','insurer','mixed')
)
select s.slug, s.name, s.short_name, s.nse_symbol, s.market, s.currency, s.peer_group_key, s.valuation_family, s.as_of,
       s.state, s.reason, s.implied, s.delivered, s.gap, s.delivered_years, s.delivered_reason, s.label,
       case when s.state = 'value' and s.gap is not null
            then rank() over (partition by (s.state = 'value' and s.gap is not null) order by s.gap desc) end as gap_rank,
       'nse' as source_key
from s;

------------------------------------------------------------------ api.priced_in_lenders
-- Lenders by ROE gap (implied minus delivered ROE). Never mixed with api.priced_in_operating: the units differ.
create view api.priced_in_lenders with (security_invoker = true) as
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
r as (select distinct on (v.entity_id) v.* from equity.valuation_run v where v.kind = 'implied_roe'
      order by v.entity_id, v.as_of desc, v.id desc),
s as (
  select e.slug, e.name, e.short_name, c.nse_symbol, e.market, e.currency, c.peer_group_key, c.valuation_family, r.as_of,
         case when not (g.iroe and g.dam) then 'held' when r.status = 'solved' then 'value'
              when r.status = 'not_applicable' then 'not_applicable' else 'held' end as state,
         case when (g.iroe and g.dam) then regexp_replace(r.reason, '^N/A: ', '') else 'Held back until its checks pass.' end as reason,
         case when (g.iroe and g.dam) then r.result end as implied,
         case when (g.iroe and g.dam) then r.delivered end as delivered,
         case when (g.iroe and g.dam) and r.result is not null and r.delivered is not null then r.result - r.delivered end as gap,
         case when (g.iroe and g.dam) and r.inputs ->> 'delivered_years' ~ '^[0-9]+$' then (r.inputs ->> 'delivered_years')::integer end as delivered_years,
         case when (g.iroe and g.dam) then r.delivered_reason end as delivered_reason,
         (g.iroe_label) as label
  from r
  join core.entity e on e.id = r.entity_id
  join equity.company c on c.entity_id = r.entity_id
  cross join g
  where c.valuation_family = 'lender'
)
select s.slug, s.name, s.short_name, s.nse_symbol, s.market, s.currency, s.peer_group_key, s.valuation_family, s.as_of,
       s.state, s.reason, s.implied, s.delivered, s.gap, s.delivered_years, s.delivered_reason, s.label,
       case when s.state = 'value' and s.gap is not null
            then rank() over (partition by (s.state = 'value' and s.gap is not null) order by s.gap desc) end as gap_rank,
       'nse' as source_key
from s;

------------------------------------------------------------------ semantic views (the command bar's schema)
create view semantic.companies with (security_barrier = true) as
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
select e.name as company, e.short_name, c.nse_symbol, c.isin, c.bse_code, c.nse_industry,
       case when g.peer then pg.label end as peer_group, e.market, e.currency, c.valuation_family, c.family_note,
       c.price_exchange,
       (select string_agg(i.name, ', ' order by i.name) from equity.index_membership m
          join core.entity i on i.id = m.index_entity_id
         where m.entity_id = c.entity_id
           and m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id)) as indices
from equity.company c
join core.entity e on e.id = c.entity_id
cross join g
left join equity.peer_group pg on pg.key = c.peer_group_key;

create view semantic.company_financials with (security_barrier = true) as
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
  select distinct on (x.entity_id, x.basis, x.period_kind, x.period_end) x.*, f.filed_at
  from equity.financials x
  join equity.filing f on f.document_id = x.document_id
  order by x.entity_id, x.basis, x.period_kind, x.period_end, f.filed_at desc nulls last, x.document_id desc
)
select e.name as company, c.nse_symbol, fin.basis, fin.period_kind, fin.period_start, fin.period_end, fin.currency,
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
       case when g.ebit then eb.value end as ebit_derived, fin.filed_at
from fin
join core.entity e on e.id = fin.entity_id
join equity.company c on c.entity_id = fin.entity_id
cross join g
left join equity.metric_value eb on eb.entity_id = fin.entity_id and eb.metric_key = 'ebit_derived'
     and eb.basis = fin.basis and eb.period_kind = fin.period_kind and eb.period_end = fin.period_end;

create view semantic.company_valuations with (security_barrier = true) as
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
latest as (select distinct on (s.entity_id) s.* from equity.screener_snapshot s
                order by s.entity_id, s.snapshot_date desc)
select e.name as company, c.nse_symbol, e.market, e.currency,
       l.snapshot_date, l.price, l.price_date, l.market_cap, l.shares, l.pe_ttm, l.pb, l.ev,
       case when g.ebit then l.ev_ebit end as ev_ebit, l.ev_sales, l.dividend_yield, l.roe,
       case when g.ebit then l.ebit_margin end as ebit_margin, l.revenue_cagr_3y, l.revenue_cagr_5y,
       l.debt_to_equity, l.promoter_pct,
       case when g.beta and g.dam then l.beta end as beta, l.return_1y, l.traded_value_median_3m,
       case when g.ig and g.dam then l.implied_growth end as implied_growth,
       case when g.ig then l.delivered_growth end as delivered_growth,
       case when g.ig and g.dam and l.implied_growth is not null and l.delivered_growth is not null
            then l.implied_growth - l.delivered_growth end as growth_gap,
       case when g.iroe and g.dam then l.implied_roe end as implied_roe,
       case when g.iroe then l.delivered_roe end as delivered_roe,
       case when g.iroe and g.dam and l.implied_roe is not null and l.delivered_roe is not null
            then l.implied_roe - l.delivered_roe end as roe_gap,
       l.valuation_family
from latest l
join equity.company c on c.entity_id = l.entity_id
join core.entity e on e.id = l.entity_id
cross join g;

alter view semantic.companies          owner to semantic_owner;
alter view semantic.company_financials owner to semantic_owner;
alter view semantic.company_valuations owner to semantic_owner;
revoke all on semantic.companies, semantic.company_financials, semantic.company_valuations from public;
grant select on semantic.companies, semantic.company_financials, semantic.company_valuations to sql_reader;

-- grants
grant select on api.companies, api.index_members, api.company_prices, api.company_financials, api.company_metrics, api.company_corporate_actions, api.screener, api.peer_comps, api.company_valuation, api.valuation_scenarios, api.priced_in_operating, api.priced_in_lenders
  to anon, authenticated, service_role;

comment on view semantic.companies is
  'The Nifty 50 universe: one row per company with its identifiers, curated peer group, market, currency and valuation family. Every company reports in its own currency (the currency column): never add, rank or compare figures across currencies; use ratios and margins across markets. Informational only, not investment advice.';
comment on column semantic.companies.company is
  'Company name as listed.';
comment on column semantic.companies.short_name is
  'Short display name, where one is set.';
comment on column semantic.companies.nse_symbol is
  'NSE trading symbol (current).';
comment on column semantic.companies.isin is
  'ISIN (current).';
comment on column semantic.companies.bse_code is
  'BSE scrip code, where known.';
comment on column semantic.companies.nse_industry is
  'Industry as published in NSE''s index list.';
comment on column semantic.companies.peer_group is
  'Curated peer group label (curated by the project; null while that field is held back).';
comment on column semantic.companies.market is
  'Company''s home market: IN for India.';
comment on column semantic.companies.currency is
  'Reporting currency of every rupee figure for this company: INR for Indian companies.';
comment on column semantic.companies.valuation_family is
  'How the company is valued: operating (EBIT-based DCF), lender (banks and NBFCs: book value and ROE), insurer or mixed (no model applies).';
comment on column semantic.companies.family_note is
  'Why a company sits in a family other than operating, where noted.';
comment on column semantic.companies.price_exchange is
  'Exchange the company''s prices come from: NSE or BSE.';
comment on column semantic.companies.indices is
  'Names of the indices the company belongs to in the latest membership snapshot, comma separated.';

comment on view semantic.company_financials is
  'One row per company, basis, period kind and period: the latest-filed profit and loss, balance sheet, cash flow and bank or insurer figures, in full rupees (currency INR), consolidated unless the basis column says standalone. Cash flow is filed for half-years and full years only. Never add or compare rupee figures across companies without noting they are in INR; Indian companies only. Informational only, not investment advice.';
comment on column semantic.company_financials.company is
  'Company name as listed.';
comment on column semantic.company_financials.nse_symbol is
  'NSE trading symbol (current).';
comment on column semantic.company_financials.basis is
  'consolidated or standalone. Consolidated unless this says standalone (banks'' ratios are standalone).';
comment on column semantic.company_financials.period_kind is
  'quarter, half_year, nine_months, year (a financial year, April to March) or instant (a balance sheet at a date).';
comment on column semantic.company_financials.period_start is
  'First day of the period; null for a balance sheet at a date.';
comment on column semantic.company_financials.period_end is
  'Last day of the period (or the balance-sheet date).';
comment on column semantic.company_financials.currency is
  'Reporting currency: INR. Every rupee figure in this view is in full rupees, not crore or lakh.';
comment on column semantic.company_financials.revenue is
  'Revenue from operations.';
comment on column semantic.company_financials.other_income is
  'Other income.';
comment on column semantic.company_financials.total_income is
  'Total income.';
comment on column semantic.company_financials.employee_cost is
  'Employee benefit expense.';
comment on column semantic.company_financials.dna is
  'Depreciation and amortisation.';
comment on column semantic.company_financials.finance_cost is
  'Finance costs.';
comment on column semantic.company_financials.total_expenses is
  'Total expenses.';
comment on column semantic.company_financials.share_of_associates is
  'Share of profit of associates and joint ventures.';
comment on column semantic.company_financials.pbt_before_exceptional is
  'Profit before exceptional items and tax.';
comment on column semantic.company_financials.exceptional is
  'Exceptional items.';
comment on column semantic.company_financials.pbt is
  'Profit before tax.';
comment on column semantic.company_financials.tax is
  'Tax expense.';
comment on column semantic.company_financials.pat is
  'Profit for the period.';
comment on column semantic.company_financials.pat_owners is
  'Profit attributable to owners of the parent.';
comment on column semantic.company_financials.pat_nci is
  'Profit attributable to non-controlling interests.';
comment on column semantic.company_financials.eps_basic is
  'Basic earnings per share, in INR per share, as filed.';
comment on column semantic.company_financials.eps_diluted is
  'Diluted earnings per share, in INR per share, as filed.';
comment on column semantic.company_financials.total_assets is
  'Total assets (balance sheet).';
comment on column semantic.company_financials.current_assets is
  'Current assets.';
comment on column semantic.company_financials.cash is
  'Cash and cash equivalents.';
comment on column semantic.company_financials.bank_balances_other is
  'Bank balances other than cash and cash equivalents.';
comment on column semantic.company_financials.current_investments is
  'Current investments.';
comment on column semantic.company_financials.noncurrent_investments is
  'Non-current investments.';
comment on column semantic.company_financials.trade_receivables is
  'Trade receivables.';
comment on column semantic.company_financials.inventories is
  'Inventories.';
comment on column semantic.company_financials.total_equity is
  'Total equity including non-controlling interests.';
comment on column semantic.company_financials.equity_owners is
  'Equity attributable to owners of the parent.';
comment on column semantic.company_financials.share_capital is
  'Equity share capital.';
comment on column semantic.company_financials.nci is
  'Non-controlling interests.';
comment on column semantic.company_financials.borrowings_current is
  'Current borrowings.';
comment on column semantic.company_financials.borrowings_noncurrent is
  'Non-current borrowings.';
comment on column semantic.company_financials.lease_current is
  'Current lease liabilities, where the filing tags them (results filings mostly do not).';
comment on column semantic.company_financials.lease_noncurrent is
  'Non-current lease liabilities, where tagged (mostly not).';
comment on column semantic.company_financials.trade_payables is
  'Trade payables.';
comment on column semantic.company_financials.cfo is
  'Cash flow from operating activities (filed for half-years and full years only).';
comment on column semantic.company_financials.cfi is
  'Cash flow from investing activities.';
comment on column semantic.company_financials.cff is
  'Cash flow from financing activities.';
comment on column semantic.company_financials.capex is
  'Capital expenditure.';
comment on column semantic.company_financials.dividends_paid is
  'Dividends paid.';
comment on column semantic.company_financials.paid_up_capital is
  'Paid-up equity share capital.';
comment on column semantic.company_financials.face_value is
  'Face value per share, in INR.';
comment on column semantic.company_financials.shares_outstanding is
  'Shares outstanding as filed, in shares.';
comment on column semantic.company_financials.interest_earned is
  'Banks: interest earned.';
comment on column semantic.company_financials.interest_expended is
  'Banks: interest expended.';
comment on column semantic.company_financials.operating_profit is
  'Banks: operating profit before provisions.';
comment on column semantic.company_financials.provisions is
  'Banks: provisions and contingencies.';
comment on column semantic.company_financials.gnpa_ratio is
  'Banks: gross non-performing assets ratio as filed, a fraction (0.02 is 2%).';
comment on column semantic.company_financials.nnpa_ratio is
  'Banks: net non-performing assets ratio as filed, a fraction.';
comment on column semantic.company_financials.roa_filed is
  'Banks: return on assets as filed (its scale varies by filing; the labelled, annualised figure is on the site).';
comment on column semantic.company_financials.cet1_ratio is
  'Banks: CET1 capital ratio as filed, a fraction.';
comment on column semantic.company_financials.at1_ratio is
  'Banks: additional tier 1 ratio as filed, a fraction.';
comment on column semantic.company_financials.segment_assets_total is
  'Banks: total segment assets.';
comment on column semantic.company_financials.deposits is
  'Banks: deposits.';
comment on column semantic.company_financials.advances is
  'Banks: advances.';
comment on column semantic.company_financials.gross_premium is
  'Insurers: gross premium.';
comment on column semantic.company_financials.net_premium is
  'Insurers: net premium.';
comment on column semantic.company_financials.assets_net is
  'Insurers: net assets.';
comment on column semantic.company_financials.current_liabilities_provisions is
  'Insurers: current liabilities and provisions.';
comment on column semantic.company_financials.solvency_ratio is
  'Insurers: solvency ratio as filed.';
comment on column semantic.company_financials.ebit_derived is
  'EBIT derived from the filing: profit before exceptional items plus finance costs, less other income and share of associates. INR. Labelled derived; null while that field is held back, and not available for banks, NBFCs and insurers.';
comment on column semantic.company_financials.filed_at is
  'When the filing was disseminated by the exchange; the latest-filed figure for each period is shown.';

comment on view semantic.company_valuations is
  'One row per company: the latest weekly snapshot of price, multiples, returns, growth, holding and the model-implied columns, in INR for Indian companies. Ratios (margins, growth, ROE, yields) are fractions, so 0.12 means 12%. Model-implied columns are informational and held back until their checks pass; they are never buy or sell signals. Never rank or sum rupee figures across currencies. Informational only, not investment advice.';
comment on column semantic.company_valuations.company is
  'Company name as listed.';
comment on column semantic.company_valuations.nse_symbol is
  'NSE trading symbol (current).';
comment on column semantic.company_valuations.market is
  'IN for India.';
comment on column semantic.company_valuations.currency is
  'INR: every rupee figure here.';
comment on column semantic.company_valuations.snapshot_date is
  'Date of the weekly snapshot these figures belong to.';
comment on column semantic.company_valuations.price is
  'Latest close in INR on the snapshot''s price date.';
comment on column semantic.company_valuations.price_date is
  'Trading date of the price.';
comment on column semantic.company_valuations.market_cap is
  'Market capitalisation in INR: latest close times shares outstanding today.';
comment on column semantic.company_valuations.shares is
  'Shares outstanding today, adjusted for later splits and bonuses.';
comment on column semantic.company_valuations.pe_ttm is
  'Price to earnings on trailing-twelve-month profit to owners, consolidated; null when profit is not positive.';
comment on column semantic.company_valuations.pb is
  'Price to book: market capitalisation over equity attributable to owners at the latest balance sheet.';
comment on column semantic.company_valuations.ev is
  'Enterprise value in INR: market capitalisation plus borrowings (excluding lease liabilities, which results filings do not report) plus non-controlling interests, less cash and investments. Null for lenders and insurers, and where lease payments are material.';
comment on column semantic.company_valuations.ev_ebit is
  'Enterprise value over trailing-twelve-month derived EBIT; null for lenders and insurers, and while the derived-EBIT field is held back.';
comment on column semantic.company_valuations.ev_sales is
  'Enterprise value over trailing-twelve-month revenue; null for lenders and insurers.';
comment on column semantic.company_valuations.dividend_yield is
  'Dividends per share with ex-dates in the last 365 days over the latest close, a fraction (0.01 is 1%).';
comment on column semantic.company_valuations.roe is
  'Return on equity, a fraction: profit to owners over average equity to owners, financial year.';
comment on column semantic.company_valuations.ebit_margin is
  'Derived EBIT over revenue, a fraction; null while the derived-EBIT field is held back.';
comment on column semantic.company_valuations.revenue_cagr_3y is
  'Revenue compound annual growth over three financial years, a fraction; null across a demerger or merger.';
comment on column semantic.company_valuations.revenue_cagr_5y is
  'Revenue compound annual growth over five financial years, a fraction; null across a demerger or merger.';
comment on column semantic.company_valuations.debt_to_equity is
  'Borrowings over total equity (including non-controlling interests), a multiple; leases excluded.';
comment on column semantic.company_valuations.promoter_pct is
  'Promoter holding as a percentage of total shares (0 to 100), latest shareholding pattern.';
comment on column semantic.company_valuations.beta is
  'Bottom-up beta (labelled derived): industry beta relevered at the company''s debt to equity; null while held back.';
comment on column semantic.company_valuations.return_1y is
  'One-year price return excluding dividends, a fraction.';
comment on column semantic.company_valuations.traded_value_median_3m is
  'Median daily traded value over three months, in INR.';
comment on column semantic.company_valuations.implied_growth is
  'Model-implied: the yearly revenue growth (a fraction) that makes the model''s value equal the price; null while that field is held back. A model output, not a forecast.';
comment on column semantic.company_valuations.delivered_growth is
  'The delivered five-year revenue growth (a fraction) it is compared with; null while held back.';
comment on column semantic.company_valuations.growth_gap is
  'Implied minus delivered growth, in fractions per year (0.01 is one percentage point); null while held back.';
comment on column semantic.company_valuations.implied_roe is
  'Model-implied, lenders only: the ROE (a fraction) the price-to-book implies; null while that field is held back.';
comment on column semantic.company_valuations.delivered_roe is
  'Delivered five-year average ROE, lenders (a fraction); null while held back.';
comment on column semantic.company_valuations.roe_gap is
  'Implied minus delivered ROE, in fractions; null while held back.';
comment on column semantic.company_valuations.valuation_family is
  'operating, lender, insurer or mixed: which model applies.';