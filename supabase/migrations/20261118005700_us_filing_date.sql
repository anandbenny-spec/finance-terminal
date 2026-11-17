-- 0057 · "Filed" for a US filing is the filing date SEC assigns (issue #448; Anand, Tue 17 Nov 2026, 21:28 IST, "your recommendation").
-- One nullable column, one view body replaced; no other table, view, function or policy changes.
--
-- equity.filing.filing_date: SEC's own filing date for the filing (company facts' `filed`), stored beside the acceptance
-- instant in filed_at. SEC's date already applies the 17:30 ET cut-off, weekends and US holidays, so the web layer does no date
-- arithmetic for US rows. Indian filings leave it null (their day comes from filed_at in IST: #444).
-- It is not backfilled here: SQL can't read SEC's data. The next run of equity_us_filings (a no-AI run, dev first) fills it for
-- every stored filing where it is null; until then a US row has no filing date and the page shows none (held back, not guessed).
--
-- api.company_financials (body: 0043's): `filing_date` appended as the last column (CREATE OR REPLACE VIEW only appends);
-- same rows, same grants, same comments, same field-policy gates.

alter table equity.filing add column filing_date date;
comment on column equity.filing.filing_date is
  'The date SEC assigns the filing (US filings only; null for Indian filings and until equity_us_filings has filled it). filed_at is the acceptance instant.';

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
       src.attribution, fin.filing_date
from core.entity e
cross join g
cross join lateral (   -- the latest filing per (basis, period kind, period end), picked within this company only
  select distinct on (x.basis, x.period_kind, x.period_end) x.*, f.filed_at, f.source_url,
         f.exchange, f.audited, f.format, f.filing_date
  from equity.financials x
  join equity.filing f on f.document_id = x.document_id
  where x.entity_id = e.id
  order by x.basis, x.period_kind, x.period_end, f.filed_at desc nulls last, x.document_id desc
) fin
left join equity.metric_value eb on eb.entity_id = e.id and eb.metric_key = 'ebit_derived'
     and eb.basis = fin.basis and eb.period_kind = fin.period_kind and eb.period_end = fin.period_end
left join core.source src on src.key = lower(fin.exchange);
