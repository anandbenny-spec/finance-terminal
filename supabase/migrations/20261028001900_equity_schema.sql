-- 0019: the `equity` schema (module 2, release 2A; issue #107, Spec 02A §3.1–§3.3, §3.5).
-- Tables, grants, row-level security, the adjusted-price view and the catalogue rows. No change to any core table
-- or column: only rows in core catalogues (sources, indices, event types, doc types, series, field policies), as
-- 0008 and 0012 did. Every later 2A data job writes into these tables, so one `supabase db push` serves them all.
-- Visitor policies use inline subqueries only: 0001 revokes EXECUTE on functions, so no policy calls one.

create schema equity;

grant usage on schema equity to anon, authenticated, semantic_owner, job_writer, service_role;

------------------------------------------------------------------ peer groups and the company master (curated)
create table equity.peer_group (
  key                text primary key check (key ~ '^[a-z0-9_-]+$'),
  market             text not null default 'IN' check (market in ('IN','US','GLOBAL')),
  label              text not null,
  damodaran_industry text,                 -- Damodaran's industry name in betaIndia (null where none maps)
  source_note        text not null         -- where the grouping comes from (labelled curated)
);

create table equity.company (
  entity_id        integer primary key references core.entity(id),
  isin             text check (isin ~ '^[A-Z0-9]{12}$'),
  nse_symbol       text,
  bse_code         text,
  nse_industry     text,                   -- the Nifty list's Industry column, as published
  peer_group_key   text references equity.peer_group(key),
  valuation_family text not null default 'operating' check (valuation_family in ('operating','lender','insurer','mixed')),
  family_note      text,
  fy_end_month     smallint not null default 3 check (fy_end_month between 1 and 12),
  price_exchange   text not null default 'NSE' check (price_exchange in ('NSE','BSE')),
  listed_on        date
);
create index on equity.company (peer_group_key);

create table equity.index_membership (
  index_entity_id integer not null references core.entity(id),
  snapshot_date   date    not null,
  entity_id       integer not null references core.entity(id),
  raw_file_id     bigint  not null references core.raw_file(id),
  primary key (index_entity_id, snapshot_date, entity_id)
);
create index on equity.index_membership (entity_id);

------------------------------------------------------------------ prices (written once)
create table equity.price_daily (
  entity_id    integer not null references core.entity(id),
  trade_date   date    not null,
  exchange     text    not null check (exchange in ('NSE','BSE')),
  series       text    not null,           -- EQ, BE, BZ
  open         numeric,
  high         numeric,
  low          numeric,
  close        numeric not null check (close > 0),
  prev_close   numeric,
  volume       bigint,
  traded_value numeric,
  trades       bigint,
  raw_file_id  bigint  not null references core.raw_file(id),
  task_run_id  bigint  not null references ops.task_run(id),
  primary key (entity_id, trade_date, exchange)
);
create index on equity.price_daily (trade_date);

-- A stored price is never changed: a re-load that differs is a blocking check in the task, never a silent update.
create function equity.guard_price_daily() returns trigger language plpgsql
  set search_path = pg_catalog, equity as $$
begin
  if (new.entity_id, new.trade_date, new.exchange, new.series, new.open, new.high, new.low, new.close,
      new.prev_close, new.volume, new.traded_value, new.trades, new.raw_file_id)
     is distinct from
     (old.entity_id, old.trade_date, old.exchange, old.series, old.open, old.high, old.low, old.close,
      old.prev_close, old.volume, old.traded_value, old.trades, old.raw_file_id) then
    raise exception 'equity.price_daily rows are written once: a changed price, volume or file is refused (% on %)',
      old.entity_id, old.trade_date;
  end if;
  return new;
end $$;
create trigger guard_price_daily before update on equity.price_daily
  for each row execute function equity.guard_price_daily();

------------------------------------------------------------------ corporate actions and adjustment factors
create table equity.corporate_action (
  event_id            bigint primary key references core.event(id),
  kind                text not null check (kind in ('split','bonus','consolidation','dividend','rights',
                                                    'demerger','merger','buyback','other')),
  ex_date             date not null,
  record_date         date,
  subject             text not null,        -- the exchange's wording, verbatim
  ratio_new           numeric check (ratio_new > 0),
  ratio_old           numeric check (ratio_old > 0),
  face_value_old      numeric check (face_value_old > 0),
  face_value_new      numeric check (face_value_new > 0),
  dividend_per_share  numeric check (dividend_per_share >= 0),
  rights_price        numeric check (rights_price > 0),
  exchange            text not null check (exchange in ('NSE','BSE')),
  raw_file_id         bigint references core.raw_file(id)
);
create index on equity.corporate_action (ex_date);

-- adjusted price = raw price × price_factor, for trade dates before ex_date. A held factor may have no usable
-- number yet (a ratio that won't parse): then price_factor is null and the company's earlier prices stay held back.
create table equity.adjustment_factor (
  event_id     bigint primary key references equity.corporate_action(event_id),
  entity_id    integer not null references core.entity(id),
  ex_date      date    not null,
  price_factor numeric check (price_factor > 0),
  method       text    not null check (method in ('ratio','special_preopen','terp','reviewed')),
  status       text    not null check (status in ('applied','held')),
  reason       text,
  evidence     jsonb,
  check (status = 'held' or price_factor is not null)
);
create index on equity.adjustment_factor (entity_id, ex_date);

------------------------------------------------------------------ results filings and statements
create table equity.filing (
  document_id  bigint primary key references core.document(id),
  entity_id    integer not null references core.entity(id),
  event_id     bigint references core.event(id),          -- the results announcement
  basis        text not null check (basis in ('consolidated','standalone')),
  format       text not null check (format in ('ind_as','bank','nbfc','insurer_life','insurer_general')),
  period_end   date not null,
  fy_start     date,
  fy_end       date,
  audited      boolean,
  filed_at     timestamptz,                               -- dissemination time
  source_url   text not null,
  exchange     text not null check (exchange in ('NSE','BSE')),
  parse_status text not null default 'pending' check (parse_status in ('pending','parsed','failed')),
  parse_note   text,
  parser_version text
);
create index on equity.filing (entity_id, period_end);

create table equity.financials (
  document_id   bigint  not null references equity.filing(document_id),
  entity_id     integer not null references core.entity(id),
  basis         text    not null check (basis in ('consolidated','standalone')),
  period_start  date,
  period_end    date    not null,
  period_kind   text    not null check (period_kind in ('quarter','half_year','nine_months','year','instant')),
  currency      text    not null default 'INR' check (currency ~ '^[A-Z]{3}$'),
  -- profit and loss (full rupees)
  revenue numeric, other_income numeric, total_income numeric, employee_cost numeric, dna numeric,
  finance_cost numeric, total_expenses numeric, share_of_associates numeric, pbt_before_exceptional numeric,
  exceptional numeric, pbt numeric, tax numeric, pat numeric, pat_owners numeric, pat_nci numeric,
  eps_basic numeric, eps_diluted numeric,
  -- balance sheet
  total_assets numeric, current_assets numeric, cash numeric, bank_balances_other numeric,
  current_investments numeric, noncurrent_investments numeric, trade_receivables numeric, inventories numeric,
  total_equity numeric, equity_owners numeric, share_capital numeric, nci numeric,
  borrowings_current numeric, borrowings_noncurrent numeric, lease_current numeric, lease_noncurrent numeric,
  trade_payables numeric,
  -- cash flow
  cfo numeric, cfi numeric, cff numeric, capex numeric, dividends_paid numeric,
  -- shares
  paid_up_capital numeric, face_value numeric, shares_outstanding numeric,
  -- banks (ratios as filed, fractions)
  interest_earned numeric, interest_expended numeric, operating_profit numeric, provisions numeric,
  gnpa_ratio numeric, nnpa_ratio numeric, roa_filed numeric, cet1_ratio numeric, at1_ratio numeric,
  segment_assets_total numeric, deposits numeric, advances numeric,
  -- insurers
  gross_premium numeric, net_premium numeric, assets_net numeric, current_liabilities_provisions numeric,
  solvency_ratio numeric,
  tags jsonb,                                              -- column → XBRL element it came from
  primary key (document_id, period_end, period_kind),
  check (period_start is null or period_end >= period_start)
);
create index on equity.financials (entity_id, basis, period_kind, period_end);

create table equity.shareholding (
  document_id    bigint primary key references core.document(id),
  entity_id      integer not null references core.entity(id),
  as_of          date    not null,
  promoter_pct   numeric not null check (promoter_pct between 0 and 100),   -- filed fraction × 100
  promoter_shares numeric,
  total_shares   numeric,
  filed_at       timestamptz,
  source_url     text not null,
  exchange       text not null check (exchange in ('NSE','BSE'))
);
create index on equity.shareholding (entity_id, as_of);

------------------------------------------------------------------ metrics, valuation inputs and runs
-- A catalogue (migrations only, like core.ai_task): job_writer may not add or change a metric.
create table equity.metric (
  key        text primary key check (key ~ '^[a-z0-9_]+$'),
  label      text not null,
  unit       text not null,
  basis_rule text not null,
  label_kind text check (label_kind in ('derived','annualised','proxy, annualised')),
  definition text not null
);

create table equity.metric_value (
  entity_id   integer not null references core.entity(id),
  metric_key  text    not null references equity.metric(key),
  basis       text    not null check (basis in ('consolidated','standalone','none')),
  period_kind text    not null check (period_kind in ('quarter','year','ttm','instant','market')),
  period_end  date    not null,                           -- for 'market': the price date
  value       numeric,
  held_reason text,
  inputs      jsonb,                                      -- the financials rows and values used
  primary key (entity_id, metric_key, basis, period_kind, period_end),
  check ((value is null) = (held_reason is not null))
);
create index on equity.metric_value (metric_key, period_end);

create table equity.industry_benchmark (
  edition                        date    not null,        -- the Damodaran edition (its stated date)
  market                         text    not null check (market in ('IN','US','GLOBAL')),
  industry                       text    not null,
  n_firms                        integer,
  beta                           numeric,
  de_ratio                       numeric,
  effective_tax                  numeric,
  unlevered_beta                 numeric,
  cash_firm_value                numeric,
  unlevered_beta_cash_corrected  numeric,
  raw_file_id                    bigint  not null references core.raw_file(id),
  primary key (edition, market, industry)
);

create table equity.valuation_run (
  id              bigint generated always as identity primary key,
  entity_id       integer not null references core.entity(id),
  kind            text    not null check (kind in ('reverse_dcf','dcf_default','implied_roe')),
  as_of           date    not null,                       -- the price date
  status          text    not null check (status in ('solved','no_solution','not_applicable')),
  reason          text,
  price           numeric,
  result          numeric,
  delivered       numeric,
  delivered_reason text,
  inputs          jsonb   not null,                       -- every input with value, unit, date and source
  model_version   text    not null,
  unique (entity_id, kind, as_of, model_version),
  check ((status = 'solved') = (result is not null)),
  check (status = 'solved' or reason is not null)
);

create table equity.valuation_scenario (
  run_id          bigint  not null references equity.valuation_run(id),
  growth          numeric not null,
  ebit_margin     numeric not null,
  wacc            numeric not null,
  value_per_share numeric,                                -- null where no value exists
  primary key (run_id, growth, ebit_margin, wacc)
);

create table equity.screener_snapshot (
  snapshot_date   date    not null,
  entity_id       integer not null references core.entity(id),
  price           numeric,
  price_date      date,
  market_cap      numeric,
  shares          numeric,
  pe_ttm          numeric,
  pb              numeric,
  ev              numeric,
  ev_ebit         numeric,
  ev_sales        numeric,
  dividend_yield  numeric,
  roe             numeric,
  ebit_margin     numeric,
  revenue_cagr_3y numeric,
  revenue_cagr_5y numeric,
  debt_to_equity  numeric,
  promoter_pct    numeric,
  beta            numeric,
  return_1y       numeric,
  traded_value_median_3m numeric,
  implied_growth  numeric,
  delivered_growth numeric,
  implied_roe     numeric,
  delivered_roe   numeric,
  valuation_family text check (valuation_family in ('operating','lender','insurer','mixed')),
  notes           jsonb,                                  -- held-back reasons and labels per column
  primary key (snapshot_date, entity_id)
);
create index on equity.screener_snapshot (entity_id, snapshot_date);

------------------------------------------------------------------ the adjusted-price view
-- adj_close = close × the product of every APPLIED factor whose ex-date is after the trade date; null when any
-- HELD factor's ex-date is after it (earlier prices are held back until the action is checked). Only the company's
-- price_exchange is used (never mixing exchanges in one series). The product is exp(sum(ln(factor))) rounded to 12
-- places, which is exact for the factors stored here (at most 6 decimals each, a handful per company).
create view equity.price_adjusted with (security_invoker = true) as
select p.entity_id, p.trade_date, p.exchange, p.series, p.open, p.high, p.low, p.close, p.prev_close,
       p.volume, p.traded_value, p.trades,
       case
         when exists (select 1 from equity.adjustment_factor h
                      where h.entity_id = p.entity_id and h.status = 'held' and h.ex_date > p.trade_date)
           then null
         else p.close * coalesce(
                (select round(exp(sum(ln(a.price_factor))), 12) from equity.adjustment_factor a
                  where a.entity_id = p.entity_id and a.status = 'applied' and a.ex_date > p.trade_date), 1)
       end as adj_close
from equity.price_daily p
join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange;

------------------------------------------------------------------ privileges
grant select on all tables in schema equity to anon, authenticated, semantic_owner, service_role;
grant select, insert, update on all tables in schema equity to job_writer;
-- Derived or re-derivable rows only; source data (prices, filings, snapshots) is append-only for jobs.
grant delete on equity.adjustment_factor, equity.financials, equity.shareholding, equity.metric_value,
                equity.valuation_scenario to job_writer;
-- The metric catalogue changes only through migrations.
revoke insert, update on equity.metric from job_writer;
grant usage on all sequences in schema equity to job_writer;
-- The view is read by jobs, the text-to-SQL layer's owner and the service role. The `api` views of 2A-17 grant the
-- visitor roles what they need.
revoke select on equity.price_adjusted from anon, authenticated;

------------------------------------------------------------------ row-level security
do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'equity' loop
    execute format('alter table equity.%I enable row level security', t.tablename);
    execute format('create policy job_writer_all on equity.%I for all to job_writer using (true) with check (true)', t.tablename);
  end loop;
end $$;

-- Catalogues
create policy read_all on equity.peer_group for select to anon, authenticated, semantic_owner using (true);
create policy read_all on equity.metric     for select to anon, authenticated, semantic_owner using (true);

-- Companies and their children: visible when the entity is published (core.entity's own policy decides).
create policy read_published on equity.company for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id));
create policy read_published on equity.index_membership for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity i where i.id = index_entity_id)
     and exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key = 'nse' and s.is_suppressed));
create policy read_published on equity.price_daily for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key = lower(exchange) and s.is_suppressed));
create policy read_published on equity.corporate_action for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event ev where ev.id = event_id));
create policy read_published on equity.adjustment_factor for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event ev where ev.id = event_id));
create policy read_published on equity.filing for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key = lower(exchange) and s.is_suppressed));
create policy read_published on equity.financials for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.filing f where f.document_id = financials.document_id));
create policy read_published on equity.shareholding for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key = lower(exchange) and s.is_suppressed));
create policy read_published on equity.metric_value for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key = 'nse' and s.is_suppressed));
create policy read_published on equity.industry_benchmark for select to anon, authenticated, semantic_owner
  using (not exists (select 1 from core.source s where s.key = 'damodaran' and s.is_suppressed));
create policy read_published on equity.valuation_run for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key in ('nse','damodaran') and s.is_suppressed));
create policy read_published on equity.valuation_scenario for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.valuation_run r where r.id = run_id));
create policy read_published on equity.screener_snapshot for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key = 'nse' and s.is_suppressed));

------------------------------------------------------------------ sources (Spec 02A §3.1)
insert into core.source (key, name, homepage_url, terms_url, attribution, is_official, allow_raw_export, notes) values
  ('nse', 'National Stock Exchange of India', 'https://www.nseindia.com', null, 'Source: NSE', true, false,
   'NSE posture (Brief §6.2, 19 Oct 2026): a non-commercial student project; every NSE-derived figure says "Source: NSE"; no raw-data export of any kind; low-frequency cached jobs only (weekly); the browser never calls NSE; anything NSE objects to is removed at once with this switch, BSE as the fallback. Never visitor-refreshable.'),
  ('bse', 'BSE Ltd', 'https://www.bseindia.com', null, 'Source: BSE', true, false,
   'Same posture as nse (Data Audit 05 D10, Brief §6.2, 19 Oct 2026): non-commercial, cached, no raw export, removed at once on objection. The fallback exchange for a company NSE cannot serve.'),
  ('damodaran', 'Aswath Damodaran, NYU Stern (data sets)', 'https://pages.stern.nyu.edu/~adamodar/', null,
   'Source: Aswath Damodaran, NYU Stern', false, false,
   'Free to use with acknowledgement (Data Audit 02 E3, Oct 2026); the acknowledgement is the attribution line. No raw-data export.'),
  ('imf', 'International Monetary Fund, World Economic Outlook (DataMapper)', 'https://www.imf.org/external/datamapper',
   'https://www.imf.org/en/About/Copyright-and-Terms', 'Source: IMF World Economic Outlook', true, false,
   'Spec 02A §3.1 (28 Oct 2026): job 2A-05 reads the IMF copyright and usage page before the first fetch and stops if it forbids display. No raw-data export.');

------------------------------------------------------------------ indices, event types, document types
insert into core.entity (kind, slug, name, short_name, market, currency, is_published) values
  ('index', 'nifty-100',        'Nifty 100',        'Nifty 100',        'IN', 'INR', true),
  ('index', 'nifty-200',        'Nifty 200',        'Nifty 200',        'IN', 'INR', true),
  ('index', 'nifty-next-50',    'Nifty Next 50',    'Nifty Next 50',    'IN', 'INR', true),
  ('index', 'nifty-midcap-100', 'Nifty Midcap 100', 'Nifty Midcap 100', 'IN', 'INR', true);

insert into core.event_type (key, label, module) values
  ('corporate_action', 'Corporate action', 'equity'),
  ('results_filing',   'Results filing',   'equity');
insert into core.event_status (event_type, status, is_final) values
  ('corporate_action', 'announced',  false),
  ('corporate_action', 'effective',  true),
  ('corporate_action', 'withdrawn',  true),
  ('results_filing',   'filed',      true),
  ('results_filing',   'superseded', true);

insert into core.doc_type (key, label, module, diffable) values
  ('results_xbrl',      'Results filing (XBRL)',            'equity', false),
  ('shareholding_xbrl', 'Shareholding pattern (XBRL)',      'equity', false);

------------------------------------------------------------------ valuation-input series (unpublished until the first load passes)
insert into core.series (key, name, kind, entity_id, source_id, source_code, attribution, unit, frequency,
                         date_rule, fixing_time, fixing_tz, market, currency, is_published)
select v.key, v.name, v.kind, null, (select id from core.source where key = v.src), v.code,
       (select attribution from core.source where key = v.src), 'percent', v.freq, 'as_published', null, null, v.market, null, false
from (values
  ('damodaran.ERP_IMPLIED',      'Implied equity risk premium, mature market (monthly)', 'observed', 'damodaran', 'ERPbymonth', 'M',         'GLOBAL'),
  ('damodaran.IND_DEFAULT_SPREAD','India sovereign default spread (per ctryprem edition)', 'observed', 'damodaran', 'ctryprem',   'irregular', 'IN'),
  ('damodaran.IND_CRP',          'India country risk premium (per ctryprem edition)',     'observed', 'damodaran', 'ctryprem',   'irregular', 'IN'),
  ('imf.NGDP_RPCH.IND',          'India real GDP growth, IMF projection (per WEO edition)', 'forecast', 'imf',      'NGDP_RPCH',  'A',         'IN'),
  ('imf.PCPIPCH.IND',            'India CPI inflation, IMF projection (per WEO edition)',   'forecast', 'imf',      'PCPIPCH',    'A',         'IN')
) as v(key, name, kind, src, code, freq, market);

------------------------------------------------------------------ the metric catalogue (Spec 02A §5.6, §5.8)
insert into equity.metric (key, label, unit, basis_rule, label_kind, definition) values
  ('revenue',                'Revenue (TTM)',                    'inr',      'consolidated; TTM only (quarters and years stay in equity.financials)', null,
   'Sum of revenue from operations over the latest four consecutive quarters on one basis.'),
  ('pat_owners',             'Profit to owners (TTM)',           'inr',      'consolidated; TTM only', null,
   'Sum of profit attributable to owners of the parent over the latest four consecutive quarters on one basis.'),
  ('ebit_derived',           'EBIT (derived)',                   'inr',      'consolidated', 'derived',
   'Profit before exceptional items plus finance costs, less other income and less the share of profit of associates and joint ventures.'),
  ('ebit_margin',            'EBIT margin',                      'fraction', 'consolidated', null,
   'Derived EBIT divided by revenue from operations.'),
  ('net_margin',             'Net margin',                       'fraction', 'consolidated', null,
   'Profit attributable to owners divided by revenue from operations.'),
  ('effective_tax',          'Effective tax rate',               'fraction', 'consolidated', null,
   'Tax expense divided by profit before tax; held back when profit before tax is zero or negative.'),
  ('debt',                   'Debt',                             'inr',      'consolidated; balance sheet', null,
   'Borrowings (current and non-current) plus lease liabilities (current and non-current).'),
  ('cash_and_investments',   'Cash and investments',             'inr',      'consolidated; balance sheet', null,
   'Cash, other bank balances, and current and non-current investments.'),
  ('invested_capital',       'Invested capital',                 'inr',      'consolidated; balance sheet', null,
   'Total equity including non-controlling interests plus debt, less cash and investments.'),
  ('sales_to_capital',       'Sales to capital',                 'multiple', 'consolidated', null,
   'Revenue divided by opening invested capital.'),
  ('roic',                   'Return on invested capital',       'fraction', 'consolidated', null,
   'Derived EBIT times one minus the effective tax rate, divided by opening invested capital.'),
  ('roe',                    'Return on equity',                 'fraction', 'consolidated; financial year', null,
   'Profit attributable to owners divided by the average of opening and closing equity attributable to owners.'),
  ('debt_to_equity',         'Debt to equity',                   'multiple', 'consolidated; balance sheet', null,
   'Debt divided by total equity including non-controlling interests.'),
  ('fcf',                    'Free cash flow',                   'inr',      'consolidated; financial year and half-year TTM', 'derived',
   'Cash flow from operations less capital expenditure.'),
  ('revenue_yoy',            'Revenue growth, year on year',     'fraction', 'consolidated; quarter and financial year', null,
   'Revenue divided by revenue of the same period a year earlier, less one.'),
  ('pat_owners_yoy',         'Profit growth, year on year',      'fraction', 'consolidated; quarter and financial year', null,
   'Profit attributable to owners divided by that of the same period a year earlier, less one; held back when the earlier figure is not positive.'),
  ('revenue_cagr_3y',        'Revenue CAGR, 3 years',            'fraction', 'consolidated; financial years', null,
   'Compound annual growth of financial-year revenue over three years; held back across a structural break (demerger, merger) or without filed figures.'),
  ('revenue_cagr_5y',        'Revenue CAGR, 5 years',            'fraction', 'consolidated; financial years', null,
   'Compound annual growth of financial-year revenue over five years; held back across a structural break or without filed figures.'),
  ('nii',                    'Net interest income',              'inr',      'standalone (banks)', null,
   'Interest earned less interest expended.'),
  ('roa',                    'Return on assets',                 'fraction', 'standalone (banks)', 'annualised',
   'Filed return on assets, scaled to a year once the filing''s own scale (quarterly or already annualised) is classified; held back when the scale is unclear.'),
  ('nim_proxy',              'Net interest margin (proxy)',      'fraction', 'standalone (banks)', 'proxy, annualised',
   'Annualised quarter net interest income divided by the average of consecutive quarter-end total segment assets.'),
  ('gnpa',                   'Gross NPA ratio',                  'fraction', 'standalone (banks)', null,
   'Gross non-performing assets ratio as filed.'),
  ('nnpa',                   'Net NPA ratio',                    'fraction', 'standalone (banks)', null,
   'Net non-performing assets ratio as filed.'),
  ('cet1',                   'CET1 ratio',                       'fraction', 'standalone (banks)', null,
   'Common equity tier 1 capital ratio as filed.'),
  ('tier1',                  'Tier 1 ratio',                     'fraction', 'standalone (banks)', null,
   'CET1 ratio plus additional tier 1 ratio, as filed.'),
  ('shares_today',           'Shares outstanding today',         'shares',   'none (market)', null,
   'Filed shares outstanding divided by the product of the price factors of splits, bonuses and consolidations with ex-dates after the filing''s period end.'),
  ('market_cap',             'Market capitalisation',            'inr',      'none (market)', null,
   'Latest close times shares outstanding today.'),
  ('pe_ttm',                 'Price to earnings (TTM)',          'multiple', 'consolidated; market', null,
   'Market capitalisation divided by TTM profit attributable to owners; held back when that profit is not positive.'),
  ('pb',                     'Price to book',                    'multiple', 'consolidated; market', null,
   'Market capitalisation divided by equity attributable to owners at the latest balance sheet.'),
  ('ev',                     'Enterprise value',                 'inr',      'consolidated; market', null,
   'Market capitalisation plus debt plus non-controlling interests, less cash and investments.'),
  ('ev_ebit_ttm',            'EV to EBIT (TTM)',                 'multiple', 'consolidated; market', null,
   'Enterprise value divided by TTM derived EBIT; not applicable to lenders and insurers.'),
  ('ev_sales_ttm',           'EV to sales (TTM)',                'multiple', 'consolidated; market', null,
   'Enterprise value divided by TTM revenue; not applicable to lenders and insurers.'),
  ('dividend_yield',         'Dividend yield',                   'fraction', 'none (market)', null,
   'Dividends per share with ex-dates in the last 365 days divided by the latest close.'),
  ('return_1y',              'One-year price return',            'fraction', 'none (market)', null,
   'Change in the adjusted close over one year, excluding dividends.'),
  ('traded_value_median_3m', 'Median daily traded value, 3 months', 'inr',   'none (market)', null,
   'Median traded value over the last 63 sessions.'),
  ('beta_regression',        'Beta (regression)',                'beta',     'none (market)', 'derived',
   'Slope of weekly adjusted-close returns against the Nifty 50 over 104 weeks; held back with fewer than 80 weeks of prices.'),
  ('beta_bottom_up',         'Beta (bottom-up)',                 'beta',     'none (market)', 'derived',
   'The Damodaran India industry''s cash-corrected unlevered beta, relevered at the company''s market debt to equity; lenders and insurers use the industry''s levered beta.');

------------------------------------------------------------------ field policy (Spec 02A §3.3; fails closed)
insert into core.field_policy (field_key, status, label, reason) values
  ('equity.ebit_derived',    'labelled', 'derived',                 'EBIT = profit before exceptional items + finance costs - other income (Brief §6.3)'),
  ('equity.roa',             'labelled', 'annualised',              'Scale decided per filing (Brief §6.3)'),
  ('equity.nim_proxy',       'labelled', 'proxy, annualised',       'Net interest income over average total segment assets'),
  ('equity.peer_group',      'labelled', 'curated',                 'Curated peer groups (Data Audit 02, decision 4)'),
  ('equity.cost_of_debt',    'labelled', 'derived',                 'Finance costs over average debt, bounded (Spec 02A §5.7)'),
  ('equity.beta',            'labelled', 'derived',                 'Regression and bottom-up betas (Spec 02A §5.8)'),
  ('equity.implied_growth',  'hidden',   'model-implied',           'Held back until 2A-26''s checks pass and Anand flips it'),
  ('equity.implied_roe',     'hidden',   'model-implied',           'Held back until 2A-26''s checks pass and Anand flips it'),
  ('equity.dcf_scenario',    'hidden',   'Model-implied value under selected assumptions', 'Held back until 2A-26''s checks pass and Anand flips it (Brief §11)'),
  ('equity.rate_sensitivity','hidden',   'derived',                 'Held back until 2A-24''s hand checks pass and Anand flips it');

------------------------------------------------------------------ comments
comment on schema equity is 'Module 2 (equity screener and valuation): companies, prices, corporate actions, filings, statements, metrics, valuation runs and weekly screener snapshots. Reached by visitors only through the api views.';
comment on table equity.peer_group is 'One curated peer group (market, label, Damodaran industry it maps to).';
comment on table equity.company is 'One row per company entity: current identifiers, curated classification, valuation family and price exchange.';
comment on table equity.index_membership is 'One row per index, snapshot date and member company; written only when the member set changes.';
comment on table equity.price_daily is 'One row per company, trade date and exchange: the exchange''s daily prices and volume, written once (a guard refuses changes).';
comment on table equity.corporate_action is 'One row per corporate action (a core.event of type corporate_action): kind, ex-date, the exchange''s wording and parsed ratios.';
comment on table equity.adjustment_factor is 'One row per corporate action with a price effect: the factor that restates earlier prices, applied or held for review.';
comment on table equity.filing is 'One row per results XBRL filing (a core.document): basis, format, period, dissemination time and parse status.';
comment on table equity.financials is 'One row per filing, period and period kind: the parsed profit and loss, balance sheet, cash flow and bank or insurer figures in full rupees.';
comment on table equity.shareholding is 'One row per shareholding-pattern XBRL filing: the promoter holding as a percentage of total shares.';
comment on table equity.metric is 'The catalogue of derived metrics (key, unit, basis rule, label and definition); changes only through migrations.';
comment on table equity.metric_value is 'One row per company, metric, basis, period kind and period end: a derived value with its inputs, or the reason it is held back.';
comment on table equity.industry_benchmark is 'One row per Damodaran edition, market and industry: betas, leverage and tax used for bottom-up betas.';
comment on table equity.valuation_run is 'One row per company, valuation kind, price date and model version: a solved result or the reason there is none, with every input.';
comment on table equity.valuation_scenario is 'One row per grid point (growth, EBIT margin, WACC) of a company''s latest default DCF run: the model-implied value per share.';
comment on table equity.screener_snapshot is 'One row per snapshot date and company: the weekly screener figures with the held-back reasons and labels per column.';
comment on view equity.price_adjusted is 'Daily prices on each company''s price exchange with adj_close restated for applied corporate-action factors; null while an earlier action is held for review.';
