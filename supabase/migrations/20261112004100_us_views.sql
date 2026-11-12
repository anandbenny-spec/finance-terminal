-- 0041: the US slice's read layer (lane U, job US-05, issue #404; Spec 02A.1 §6; Plan 05 D4). Approved by Anand,
-- 11 Nov 2026, about 17:52 IST (tracker #30). Only views change: no table, column, policy, grant, owner or comment.
-- Each view below is its current definition (0024, 0026, 0034) copied verbatim, changed only where a US row needs it;
-- every change reads `market` (or the US exchanges), so an Indian row's output is byte-identical (tests 0041 compare
-- every api and semantic view before and after this migration). `create or replace` keeps each view's owner, grants and
-- comments; the `with (...)` options are restated as they were.
--
-- The gate is still publication (Spec 02A.1 §3): with the US companies unpublished, no view returns a US row.
--
-- For `market = 'US'` (decided by Claude, standing delegation, 24 Oct 2026, recorded on #404; each keeps every rule):
--  * money metrics: `api.company_metrics.unit` reads the row's currency (`usd`) where the catalogue says `inr`, which
--    there means "money in the company's currency" (Spec 02A.1 §4 item 6);
--  * EBIT: US rows hold operating income as filed (us-gaap OperatingIncomeLoss; US-04, #403), not India's derived
--    figure, so the label that India takes from the `equity.ebit_derived` policy ('derived') reads "operating income as
--    filed" on US rows (EBIT and the figures built from it: EBIT margin, EV/EBIT, return on invested capital), and the
--    metric's name reads "EBIT (operating income as filed)". The field policy still gates them exactly as India's;
--  * source keys name the US sources, never NSE: prices and corporate actions `yfinance`, statements and every figure
--    built from them `sec` (0039's exchange-to-source mapping). The pages add Yahoo Finance's line beside price figures;
--  * `api.company_prices.adj_close` is held while `nse` is suppressed only for NSE and BSE prices (the factors behind
--    them come from NSE's actions); a US price follows yfinance, whose switch hides the row itself (0039);
--  * comps: `api.peer_comps` already pairs a company only with peers in its own market and group; a US group of fewer than
--    three others reads "Too few peers in the Dow 30 for a median";
--  * the priced-in tables rank within one market: `partition by market` (India's ranks can't move when US rows publish).
-- The semantic views stay India-only (Spec 02A.1 §6): each company-level one gains `market = 'IN'`, comments unchanged
-- ("currency INR" stays true). The policy ones (Fed and RBI) hold no company rows and are unchanged.
-- Not here yet: the ticker and CIK (Spec 02A.1 §6), which wait on Anand's answer on #404 (visitors can't read
-- core.entity_identifier, and no other table holds them).

------------------------------------------------------------------ api.companies
create or replace view api.companies with (security_invoker = true) as
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
       case when p0.trade_date is not null then case c.price_exchange when 'NYSE' then 'yfinance' when 'NASDAQ' then 'yfinance' else lower(c.price_exchange) end end as price_source_key
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

------------------------------------------------------------------ api.company_prices
create or replace view api.company_prices with (security_invoker = true) as
select e.slug, e.market, e.currency, p.trade_date, p.exchange, p.close, p.volume, p.traded_value,
       a.adj_close, (a.adj_close is null) as adj_held,
       case p.exchange when 'NYSE' then 'yfinance' when 'NASDAQ' then 'yfinance' else lower(p.exchange) end as source_key
from equity.price_daily p
join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
join core.entity e on e.id = p.entity_id
cross join lateral (
  -- the same rule as equity.price_adjusted (0019): the product of every applied factor with a later ex-date, null while
  -- a held factor has one. Repeated here so visitors never need SELECT on that view (0019's tests pin that); the 0024
  -- tests compare the two row by row.
  select case
           when p.exchange in ('NSE','BSE')
                and exists (select 1 from core.source s where s.key = 'nse' and s.is_suppressed) then null
           when exists (select 1 from equity.adjustment_factor h
                        where h.entity_id = p.entity_id and h.status = 'held' and h.ex_date > p.trade_date) then null
           else p.close * coalesce((select round(exp(sum(ln(f.price_factor))), 12) from equity.adjustment_factor f
                                    where f.entity_id = p.entity_id and f.status = 'applied' and f.ex_date > p.trade_date), 1)
         end as adj_close
) a;

------------------------------------------------------------------ api.company_financials
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
left join core.source src on src.key = lower(fin.exchange);

------------------------------------------------------------------ api.company_metrics
create or replace view api.company_metrics with (security_invoker = true) as
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
select e.slug, e.name, e.market, e.currency, mv.metric_key,
       case when e.market = 'US' and mv.metric_key = 'ebit_derived' then 'EBIT (operating income as filed)'
            else m.label end as label,
       case when e.market = 'US' and m.unit = 'inr' then lower(e.currency) else m.unit end as unit,
       mv.basis, mv.period_kind, mv.period_end,
       mv.period_end as as_of,
       case when k.shown then mv.value end as value,
       case when k.shown then coalesce(case when e.market = 'US' and mv.metric_key in ('ebit_derived','ebit_margin','ev_ebit_ttm','roic')
                                            then 'operating income as filed' else k.label end, m.label_kind)
            else null end as label_kind,
       case when not k.shown then k.reason
            else regexp_replace(mv.held_reason, '^N/A: ', '') end as held_reason,
       case when not k.shown then 'held' when mv.value is not null then 'value'
            when mv.held_reason like 'N/A: %%' then 'not_applicable' else 'held' end as state,
       case when e.market = 'US' then 'sec' else lower(c.price_exchange) end as source_key
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
create or replace view api.company_corporate_actions with (security_invoker = true) as
select e.slug, e.market, e.currency, ca.kind, ca.ex_date, ca.record_date, ca.subject, ca.ratio_new, ca.ratio_old,
       ca.face_value_old, ca.face_value_new, ca.dividend_per_share, ca.rights_price, ca.exchange,
       ev.status, ev.title, ev.source_url,
       case when af.event_id is null then 'none' else af.status end as factor_status,
       case when af.status = 'applied' then af.price_factor end as price_factor,
       case when af.status = 'held' then af.reason end as factor_reason,
       case ca.exchange when 'NYSE' then 'yfinance' when 'NASDAQ' then 'yfinance' else lower(ca.exchange) end as source_key, src.attribution
from equity.corporate_action ca
join core.event ev on ev.id = ca.event_id
join core.entity e on e.id = ev.entity_id
left join equity.adjustment_factor af on af.event_id = ca.event_id
left join core.source src on src.key = case ca.exchange when 'NYSE' then 'yfinance' when 'NASDAQ' then 'yfinance' else lower(ca.exchange) end
where ev.status <> 'withdrawn';

------------------------------------------------------------------ api.screener
create or replace view api.screener with (security_invoker = true) as
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
       case when e.market = 'US' and g.ebit then 'operating income as filed' else g.ebit_label end as ebit_label,
       g.beta_label as beta_label, g.ig_label as implied_growth_label,
       g.iroe_label as implied_roe_label,
       (select array_agg(i.slug order by i.slug) from equity.index_membership m
          join core.entity i on i.id = m.index_entity_id
         where m.entity_id = c.entity_id
           and m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id)) as index_slugs,
       case when e.market = 'US' then 'sec' else 'nse' end as source_key
from latest l
join equity.company c on c.entity_id = l.entity_id
join core.entity e on e.id = l.entity_id
cross join g
left join equity.peer_group pg on pg.key = c.peer_group_key;

------------------------------------------------------------------ api.peer_comps
create or replace view api.peer_comps with (security_invoker = true) as
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
       null::integer as peer_count, null::jsonb as median_n, null::text as median_note,
       case when p.market = 'US' then 'sec' else 'nse' end as source_key
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
       case when count(*) < 3 then case when p.market = 'US' then 'Too few peers in the Dow 30 for a median'
                                        else 'Too few peers in the Nifty 50 for a median' end end,
       case when p.market = 'US' then 'sec' else 'nse' end
from pairs p
group by p.company_slug, p.market, p.currency, p.peer_group_key;

------------------------------------------------------------------ api.company_valuation
create or replace view api.company_valuation with (security_invoker = true) as
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
       case when e.market = 'US' then 'sec' else 'nse' end as source_key
from r
join core.entity e on e.id = r.entity_id
join equity.company c on c.entity_id = r.entity_id
cross join g
cross join lateral (select case r.kind when 'reverse_dcf' then g.ig when 'implied_roe' then g.iroe else g.dcf end as shown, case r.kind when 'reverse_dcf' then g.ig_label when 'implied_roe' then g.iroe_label else g.dcf_label end as label) k;

------------------------------------------------------------------ api.priced_in_operating
create or replace view api.priced_in_operating with (security_invoker = true) as
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
            then rank() over (partition by s.market, (s.state = 'value' and s.gap is not null) order by s.gap desc)
       end as gap_rank,
       case when s.market = 'US' then 'sec' else 'nse' end as source_key
from s;

------------------------------------------------------------------ api.priced_in_lenders
create or replace view api.priced_in_lenders with (security_invoker = true) as
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
            then rank() over (partition by s.market, (s.state = 'value' and s.gap is not null) order by s.gap desc)
       end as gap_rank,
       case when s.market = 'US' then 'sec' else 'nse' end as source_key
from s;

------------------------------------------------------------------ semantic.companies
create or replace view semantic.companies with (security_barrier = true) as
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
left join equity.peer_group pg on pg.key = c.peer_group_key
where e.market = 'IN';

------------------------------------------------------------------ semantic.company_financials
create or replace view semantic.company_financials with (security_barrier = true) as
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
     and eb.basis = fin.basis and eb.period_kind = fin.period_kind and eb.period_end = fin.period_end
where e.market = 'IN';

------------------------------------------------------------------ semantic.company_valuations
create or replace view semantic.company_valuations with (security_barrier = true) as
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
cross join g
where e.market = 'IN';

------------------------------------------------------------------ semantic.rate_decision_moves
create or replace view semantic.rate_decision_moves with (security_barrier = true) as
with g as (
  select exists (select 1 from core.field_policy f
                  where f.field_key = 'equity.rate_sensitivity' and f.status in ('shown','labelled')) as ok
), dec as (
  -- every held rate decision since 1 Jan 2019, classed by the change in its bank's policy rate (the repo rate; the
  -- Fed's upper bound) from the previous held decision
  select m.event_id, ev.slug as event_slug, b.slug as bank, coalesce(ev.ends_on, ev.starts_on) as meeting_date,
         rs.change_bps as policy_change_bps,
         case when rs.change_bps < 0 then 'cut' when rs.change_bps > 0 then 'hike' else 'hold' end as decision
    from policy.meeting m
    join core.event ev on ev.id = m.event_id and ev.status = 'held'
    join core.entity b on b.id = ev.entity_id
    join policy.rate_setting rs on rs.meeting_event_id = m.event_id
         and rs.instrument = case b.slug when 'fed' then 'fed_target_upper' else 'repo' end
   where m.is_rate_decision and coalesce(ev.ends_on, ev.starts_on) >= date '2019-01-01'
), nifty as (
  select r.event_id, r.window_key, r.pre_date, r.post_date, r.change
    from core.event_reaction r join core.series s on s.id = r.series_id
   where s.key = 'yf.^NSEI' and r.change_unit = 'pct'
), co as (
  -- a company's move on a decision; the Nifty 50's move only where it has the same pre and post dates
  select d.*, e.slug as company_slug, e.name as company_name, e.currency, pg.key as peer_key, pg.label as peer_label,
         r.window_key, r.pre_date, r.post_date, r.change as own_change,
         n.change as nifty_change,
         case when n.change is not null then r.change - n.change end as excess
    from dec d
    join core.event_reaction r on r.event_id = d.event_id and r.entity_id is not null and r.change_unit = 'pct'
    join core.entity e on e.id = r.entity_id and e.market = 'IN'
    left join equity.company c on c.entity_id = e.id
    left join equity.peer_group pg on pg.key = c.peer_group_key
    left join nifty n on n.event_id = r.event_id and n.window_key = r.window_key
         and n.pre_date = r.pre_date and n.post_date = r.post_date
), u as (
  select 'company'::text as subject_kind, company_slug as subject_key, company_name as subject_name, currency,
         bank, event_slug, meeting_date, decision, policy_change_bps, window_key,
         pre_date, post_date, own_change, nifty_change, excess, null::bigint as members
    from co
  union all
  -- a peer group's move on an event = the equal-weighted mean of its members' excess moves on that event
  select 'peer_group', peer_key, peer_label, null::text, bank, event_slug, meeting_date, decision, policy_change_bps,
         window_key, null::date, null::date, null::numeric, null::numeric, avg(excess), count(excess)
    from co where peer_key is not null and excess is not null
   group by peer_key, peer_label, bank, event_slug, meeting_date, decision, policy_change_bps, window_key
)
select u.subject_kind, u.subject_key, u.subject_name, u.currency, u.bank, u.event_slug, u.meeting_date, u.decision,
       u.policy_change_bps, u.window_key,
       case when g.ok then u.pre_date end        as pre_date,
       case when g.ok then u.post_date end       as post_date,
       case when g.ok then u.own_change end      as own_change_pct,
       case when g.ok then u.nifty_change end    as nifty_change_pct,
       case when g.ok then u.excess end          as excess_pct,
       case when g.ok then u.members end         as members,
       (select attribution from core.source where key = 'nse') as attribution
  from u cross join g;

------------------------------------------------------------------ semantic.value_chain
create or replace view semantic.value_chain with (security_barrier = true) as
with g as (   -- field policy, read once per query; a missing row is hidden (fails closed, Arch 01 §1.4)
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.brsr_values'
), s as (
  select distinct on (bf.entity_id, bf.fy_end)
         bf.event_id, bf.entity_id, bf.fy_label, bf.fy_start, bf.fy_end, bf.boundary, bf.months, bf.exchange, bf.filed_at
  from equity.brsr_filing bf
  join core.event ev  on ev.id = bf.event_id and ev.status = 'stored'
  join core.source xs on xs.key = lower(bf.exchange) and not xs.is_suppressed
  where bf.format_version >= date '2024-04-30' and bf.boundary is not null
  order by bf.entity_id, bf.fy_end, bf.version_no desc, bf.filed_at desc
), p as (   -- the card's values in percent (shares) or as filed (counts); held values are left out
  select s.*,
         max(v.value * 100) filter (where v.field_key = 'within_india_share')  as within_india_share,
         max(v.value * 100) filter (where v.field_key = 'msme_share')          as msme_share,
         max(v.value * 100) filter (where v.field_key = 'trading_house_share') as trading_house_share,
         max(v.value)       filter (where v.field_key = 'trading_houses')      as trading_houses,
         max(v.value * 100) filter (where v.field_key = 'top10_trading_share') as top10_trading_share,
         max(v.value * 100) filter (where v.field_key = 'dealer_share')        as dealer_share,
         max(v.value)       filter (where v.field_key = 'dealers')             as dealers,
         max(v.value * 100) filter (where v.field_key = 'top10_dealer_share')  as top10_dealer_share,
         max(v.value * 100) filter (where v.field_key = 'exports_share')       as exports_share,
         max(v.value)       filter (where v.field_key = 'states_served')       as states_served,
         max(v.value)       filter (where v.field_key = 'countries_served')    as countries_served,
         max(v.value * 100) filter (where v.field_key = 'rpt_purchases_share') as rpt_purchases_share,
         max(v.value * 100) filter (where v.field_key = 'rpt_sales_share')     as rpt_sales_share
  from s
  left join equity.brsr_value v on v.event_id = s.event_id and v.status = 'ok'
  group by s.event_id, s.entity_id, s.fy_label, s.fy_start, s.fy_end, s.boundary, s.months, s.exchange, s.filed_at
)
select e.name as company, co.nse_symbol, e.market, e.currency, co.valuation_family,
       p.fy_label as fiscal_year, p.fy_start, p.fy_end, p.months, p.boundary, p.exchange as source_exchange, p.filed_at,
       case when g.ok then p.within_india_share end as within_india_share,
       case when g.ok then p.msme_share end as msme_share,
       case when g.ok then p.trading_house_share end as trading_house_share,
       case when g.ok then p.trading_houses end as trading_houses,
       case when g.ok and p.trading_house_share > 0 then p.top10_trading_share end as top10_trading_share,
       case when g.ok then p.dealer_share end as dealer_share,
       case when g.ok then p.dealers end as dealers,
       case when g.ok and p.dealer_share > 0 then p.top10_dealer_share end as top10_dealer_share,
       case when g.ok then p.exports_share end as exports_share,
       case when g.ok then p.states_served end as states_served,
       case when g.ok then p.countries_served end as countries_served,
       case when g.ok then p.rpt_purchases_share end as rpt_purchases_share,
       case when g.ok then p.rpt_sales_share end as rpt_sales_share
from p
join core.entity e on e.id = p.entity_id
join equity.company co on co.entity_id = p.entity_id
cross join g
where e.market = 'IN';
