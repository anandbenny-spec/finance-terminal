-- 0053: cmdbar_us_rows (issue #580, job K-03b; Spec 07 N2). Approved by Anand, Sun 15 Nov 2026, 23:25 IST, on #580: option A,
-- "US rows join the command bar's views once published, and 0041's semantic-view check changes as option A says".
--
-- What changes (no table, column, index, policy, grant, owner, field_policy row or api view changes):
--  * semantic.companies, semantic.company_financials and semantic.company_valuations read both markets. 0041 gave each
--    `where e.market = 'IN'` (Spec 02A.1 §6: the semantic views stayed India-only); here that becomes
--    `where e.market in ('IN', 'US')`. Publication still decides what shows: the views are owned by semantic_owner, which has
--    no BYPASSRLS, so core.entity's and the equity tables' visitor policies apply (Spec 02A.1 §3). A US company shows only once
--    it is published, which production did for the Dow 30 on 14 Nov 2026 (#407) and dev hasn't.
--    Decided by Claude (standing delegation, 24 Oct 2026; recorded on #580): the filter names both markets instead of being
--    dropped, so a company of any other market (core.entity also takes GLOBAL) can't reach the views, whose comments name INR
--    and USD only. Alternative: no market filter at all, as option A's wording reads. This only tightens.
--  * Each view's definition is 0041's, copied verbatim, with three changes: the market filter above, and new columns appended
--    at the end (`create or replace` can only append), so an Indian row's existing columns are byte-identical:
--      semantic.companies          + ticker, cik     (0041's US identifiers; null for India, as in api.companies)
--      semantic.company_financials + market, ticker  (every company record carries market and currency: CLAUDE.md §1, Spec 07
--                                                     N2; the view had currency only)
--      semantic.company_valuations + ticker
--    Decided by Claude (standing delegation, 24 Oct 2026; recorded on #580): the card's reuse line names 0041's ticker, CIK and
--    currency, and N2 says each row carries market and currency. Alternative: no new columns (a US row would carry no
--    identifier beyond its name, and company_financials no market).
--  * Every comment on the three views that named INR, rupees or India only is rewritten (option A): money is in the row's
--    currency (INR for India, USD for the US), and money is never compared, ranked or summed across rows of different
--    currencies. US EBIT is operating income as filed (us-gaap OperatingIncomeLoss), not India's derived figure, as 0041's api
--    views label it. The comments are the text-to-SQL prompt's schema description (Arch 01 §3), so they say what a US row holds.
--  * Unchanged: semantic.rate_decision_moves and semantic.value_chain (India only, option A), semantic.earnings_calls and
--    semantic.call_guidance (0052: India only; US calls aren't covered, Spec 02A.1 §1), and the policy views.
--  * The cross-currency golds (option A's last line) belong to K-02's set (#581), which isn't built yet: recorded there.

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
           and m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id)) as indices,
       c.ticker, c.cik
from equity.company c
join core.entity e on e.id = c.entity_id
cross join g
left join equity.peer_group pg on pg.key = c.peer_group_key
where e.market in ('IN', 'US');

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
       case when g.ebit then eb.value end as ebit_derived, fin.filed_at,
       e.market, c.ticker
from fin
join core.entity e on e.id = fin.entity_id
join equity.company c on c.entity_id = fin.entity_id
cross join g
left join equity.metric_value eb on eb.entity_id = fin.entity_id and eb.metric_key = 'ebit_derived'
     and eb.basis = fin.basis and eb.period_kind = fin.period_kind and eb.period_end = fin.period_end
where e.market in ('IN', 'US');

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
       l.valuation_family,
       c.ticker
from latest l
join equity.company c on c.entity_id = l.entity_id
join core.entity e on e.id = l.entity_id
cross join g
where e.market in ('IN', 'US');

------------------------------------------------------------------ comments: semantic.companies
comment on view semantic.companies is
  'One row per company of the Nifty 50 (market IN) or the Dow 30 (market US) that the site shows, with its identifiers, curated peer group, market, currency and valuation family. Money is in the row''s currency (INR for India, USD for the US). Never compare, rank or sum money across rows of different currencies: across markets use ratios, margins and growth, or answer for each market separately. Informational only, not investment advice.';
comment on column semantic.companies.nse_symbol is
  'NSE trading symbol (current), for Indian companies; null for US companies (see ticker).';
comment on column semantic.companies.isin is
  'ISIN (current), where known.';
comment on column semantic.companies.bse_code is
  'BSE scrip code, where known (Indian companies only).';
comment on column semantic.companies.nse_industry is
  'Industry as published in NSE''s index list (Indian companies only).';
comment on column semantic.companies.peer_group is
  'Curated peer group label (curated by the project; a group holds companies of one market only; null while that field is held back).';
comment on column semantic.companies.market is
  'Company''s home market: IN for India, US for the United States.';
comment on column semantic.companies.currency is
  'Reporting currency of every money figure for this company: INR for Indian companies, USD for US companies. Never compare, rank or sum money across different currencies.';
comment on column semantic.companies.valuation_family is
  'How the company is valued: operating (EBIT-based DCF; for US companies EBIT is operating income as filed), lender (banks, bank holding companies and NBFCs: book value and ROE), insurer or mixed (no model applies).';
comment on column semantic.companies.price_exchange is
  'Exchange the company''s prices come from: NSE or BSE for Indian companies, NYSE or NASDAQ for US companies.';
comment on column semantic.companies.indices is
  'Names of the indices the company belongs to in the latest membership snapshot, comma separated (Nifty indices for Indian companies, the Dow Jones Industrial Average for US companies).';
comment on column semantic.companies.ticker is
  'Ticker symbol (current), for US companies; null for Indian companies (see nse_symbol).';
comment on column semantic.companies.cik is
  'SEC Central Index Key (CIK), for US companies; null for Indian companies.';

------------------------------------------------------------------ comments: semantic.company_financials
comment on view semantic.company_financials is
  'One row per company, basis, period kind and period: the latest-filed profit and loss, balance sheet, cash flow and bank or insurer figures, consolidated unless the basis column says standalone. Indian figures come from exchange results filings, in full rupees; US figures come from SEC 10-K and 10-Q filings, in full US dollars, consolidated. Money is in the row''s currency (INR for India, USD for the US). Never compare, rank or sum money across rows of different currencies: across markets use ratios, margins and growth, or answer for each market separately. A line a market''s filings do not carry is null (the bank, insurer and share-capital lines are Indian filings''). Indian cash flow is filed for half-years and full years only. Informational only, not investment advice.';
comment on column semantic.company_financials.nse_symbol is
  'NSE trading symbol (current), for Indian companies; null for US companies (see ticker).';
comment on column semantic.company_financials.basis is
  'consolidated or standalone. Consolidated unless this says standalone (Indian banks'' ratios are standalone; US figures are always consolidated).';
comment on column semantic.company_financials.period_kind is
  'quarter, half_year, nine_months, year or instant (a balance sheet at a date). A year is the company''s financial year: April to March for Indian companies, the fiscal year the company reports for US companies.';
comment on column semantic.company_financials.currency is
  'Reporting currency of every money figure in the row: INR (full rupees, not crore or lakh) for Indian companies, USD (full dollars) for US companies. Never compare, rank or sum money across different currencies.';
comment on column semantic.company_financials.eps_basic is
  'Basic earnings per share, in the row''s currency per share, as filed.';
comment on column semantic.company_financials.eps_diluted is
  'Diluted earnings per share, in the row''s currency per share, as filed.';
comment on column semantic.company_financials.face_value is
  'Face value per share, in the row''s currency (Indian companies).';
comment on column semantic.company_financials.ebit_derived is
  'EBIT, in the row''s currency. Indian companies: derived from the filing (profit before exceptional items plus finance costs, less other income and share of associates), labelled derived. US companies: operating income as filed (us-gaap OperatingIncomeLoss), not derived. Null while that field is held back, and not available for lenders and insurers.';
comment on column semantic.company_financials.filed_at is
  'When the filing was disseminated (by the exchange for Indian companies, by the SEC for US companies); the latest-filed figure for each period is shown.';
comment on column semantic.company_financials.market is
  'Company''s home market: IN for India, US for the United States.';
comment on column semantic.company_financials.ticker is
  'Ticker symbol (current), for US companies; null for Indian companies (see nse_symbol).';

------------------------------------------------------------------ comments: semantic.company_valuations
comment on view semantic.company_valuations is
  'One row per company: the latest weekly snapshot of price, multiples, returns, growth, holding and the model-implied columns. Money is in the row''s currency (INR for India, USD for the US). Never compare, rank or sum money across rows of different currencies: across markets use the ratios (multiples, margins, growth, ROE, yields), or answer for each market separately. Ratios (margins, growth, ROE, yields) are fractions, so 0.12 means 12%. Model-implied columns are informational and held back until their checks pass; they are never buy or sell signals. Informational only, not investment advice.';
comment on column semantic.company_valuations.nse_symbol is
  'NSE trading symbol (current), for Indian companies; null for US companies (see ticker).';
comment on column semantic.company_valuations.market is
  'IN for India, US for the United States.';
comment on column semantic.company_valuations.currency is
  'Currency of every money figure in the row: INR for Indian companies, USD for US companies. Never compare, rank or sum money across different currencies.';
comment on column semantic.company_valuations.price is
  'Latest close, in the row''s currency, on the snapshot''s price date.';
comment on column semantic.company_valuations.market_cap is
  'Market capitalisation in the row''s currency: latest close times shares outstanding today.';
comment on column semantic.company_valuations.ev is
  'Enterprise value in the row''s currency: market capitalisation plus borrowings (excluding lease liabilities, which results filings do not report) plus non-controlling interests, less cash and investments. Null for lenders and insurers, and where lease payments are material.';
comment on column semantic.company_valuations.ev_ebit is
  'Enterprise value over trailing-twelve-month EBIT (derived for Indian companies, operating income as filed for US companies); null for lenders and insurers, and while the EBIT field is held back.';
comment on column semantic.company_valuations.ebit_margin is
  'EBIT (derived for Indian companies, operating income as filed for US companies) over revenue, a fraction; null while the EBIT field is held back.';
comment on column semantic.company_valuations.promoter_pct is
  'Promoter holding as a percentage of total shares (0 to 100), latest shareholding pattern; an Indian disclosure, null for US companies.';
comment on column semantic.company_valuations.traded_value_median_3m is
  'Median daily traded value over three months, in the row''s currency; null where the price source gives volume only.';
comment on column semantic.company_valuations.ticker is
  'Ticker symbol (current), for US companies; null for Indian companies (see nse_symbol).';
