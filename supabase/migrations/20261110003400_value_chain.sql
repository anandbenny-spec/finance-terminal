-- 0034: the value chain card's tables, catalogue rows, field policies and views (issue #187, job VC-02; Spec 02B §12.3, §12.6
-- with the changes the card records: Anand, 29 Oct 2026, 19:52–19:56 and 22:49 IST).
-- Six `equity` tables (`brsr_filing`, `brsr_value`, `brsr_product`, `brsr_group_company`, `rpt_line`, `segment_revenue`), the
-- event type `brsr_filing` and doc type `brsr_xbrl`, four field policies (all hidden), and the `api` and `semantic` views.
-- Equity conventions as 0019 (Spec 02A §3.2): RLS on, the `job_writer_all` policy, explicit grants, `delete` for jobs only on the
-- tables marked D. Visitor policies use inline subqueries only (0001 revokes EXECUTE from PUBLIC).
-- Decided by Claude (standing delegation, 24 Oct 2026), each recorded on #187 with its alternative:
--   1. Every value-bearing table's visitor policy also requires its field policy to be shown or labelled (as 0023's `guidance` does
--      for its output), so a hidden field leaves no row even in the base table; the views gate by CASE as well. Alternative: gate
--      in the views only.
--   2. Two internal views, `equity.brsr_filing_shown` (the filings the card shows) and `equity.brsr_card` (the card's field list
--      and the state of every value), are the one place those rules live for the `api` views. `semantic.value_chain` repeats them
--      against the base tables, because a security_invoker view under a semantic view would run as sql_reader; the tests keep the
--      two equal. Alternative: repeat the rules in each api view.
--   3. `api.value_chain_peers` shows a median only from at least 3 peers on the same boundary AND the same fiscal year as the
--      company's latest filing (a peer that has not filed that year is listed, not counted), and only for shares. Alternative: the
--      boundary alone.
--   4. `api.segments` takes the latest consolidated period of at least 350 days (a fiscal year); a half-year's rows never stand in.
--   5. `api.related_party_top` also shows the periods its rows cover (`period_from`, `period_to`), because VC-05 may store one
--      half-year only and the page must say which.

------------------------------------------------------------------ catalogue rows
insert into core.event_type (key, label, module) values ('brsr_filing', 'BRSR filing', 'equity');
insert into core.event_status (event_type, status, is_final) values
  ('brsr_filing', 'stored',     true),
  ('brsr_filing', 'held',       false),
  ('brsr_filing', 'superseded', true);

-- Unpublished, text-less documents, as 2A's results_xbrl and shareholding_xbrl: the raw file's home and the document-job
-- interface's key for resumability and versions (the card's decision 1 revises Spec 02B §12.12 decision 21).
insert into core.doc_type (key, label, module, diffable) values
  ('brsr_xbrl', 'BRSR filing (XBRL)', 'equity', false);

------------------------------------------------------------------ §12.3 the tables
create table equity.brsr_filing (
  event_id            bigint  primary key references core.event(id),          -- event type brsr_filing
  entity_id           integer not null references core.entity(id),
  document_id         bigint  not null unique references core.document(id),   -- the brsr_xbrl document, which holds the raw file
  fy_label            text    not null check (fy_label ~ '^FY [0-9]{4}-[0-9]{2}$'),   -- 'FY 2025-26'
  fy_start            date    not null,
  fy_end              date    not null,
  format_version      date    not null,                                        -- the entry point's date: 2026-02-28
  boundary            text    check (boundary in ('standalone','consolidated')),
  exchange            text    not null check (exchange in ('NSE','BSE')),
  list_url            text,                                                    -- the exchange's BRSR page for the company
  version_no          smallint not null default 1 check (version_no >= 1),
  filed_at            timestamptz not null,
  revised_at          timestamptz,
  months              smallint not null check (months between 1 and 24),      -- 12; 15 for Nestle India's FY 2023-24
  openness_assured    boolean,                                                 -- the filing's own flag for Principle 1, EI 9
  openness_assurance  text    check (openness_assurance in ('reasonable','limited')),
  status_reason       text,
  parser_version      text,
  unique (entity_id, fy_end, version_no),
  check (fy_end > fy_start),
  check (openness_assurance is null or openness_assured),
  check (revised_at is null or revised_at >= filed_at)
);
create index on equity.brsr_filing (entity_id, fy_end);

create table equity.brsr_value (
  event_id         bigint  not null references equity.brsr_filing(event_id),
  field_key        text    not null check (field_key ~ '^[a-z0-9_]+$'),         -- a key of py/config/equity/brsr_fields.yaml
  value            numeric,                                                     -- as filed: a share is the filed fraction, never rescaled
  kind             text    not null check (kind in ('share','count')),
  element          text    not null,                                            -- the in-capmkt element name
  item_ref         text    not null,                                            -- 'P1-EI-9, Sales (b)'
  status           text    not null check (status in ('ok','held')),
  held_reason      text,
  next_comparative numeric,                                                     -- the next year's filing's comparative, as filed
  restated         boolean not null default false,
  primary key (event_id, field_key),
  check (status = 'held' or value is not null),
  check (status <> 'held' or held_reason is not null),
  -- A share outside 0-1 or a count that is not a whole number >= 0 can only be held (Spec 02B §12.4: never rescaled).
  check (status = 'held'
         or (kind = 'share' and value between 0 and 1)
         or (kind = 'count' and value >= 0 and value = trunc(value))),
  check (not restated or next_comparative is not null)
);

create table equity.brsr_product (
  event_id          bigint  not null references equity.brsr_filing(event_id),
  line_no           integer not null check (line_no >= 1),
  description       text    not null,
  nic_code          text,
  share_of_turnover numeric check (share_of_turnover between 0 and 1),          -- the filed fraction
  primary key (event_id, line_no)
);

create table equity.brsr_group_company (
  event_id   bigint  not null references equity.brsr_filing(event_id),
  line_no    integer not null check (line_no >= 1),
  name       text    not null,
  relation   text,                                                              -- as filed
  share_held numeric check (share_held between 0 and 1),                        -- the filed fraction
  primary key (event_id, line_no)
);

-- One related-party row of a results filing (the half-yearly disclosure). No PAN column: a counterparty's identity number is
-- never read or stored (CLAUDE.md §4). The key includes the basis because one results event has a consolidated and a standalone
-- filing (the card's change 3).
create table equity.rpt_line (
  results_event_id bigint  not null references core.event(id),                  -- the results_filing event
  basis            text    not null check (basis in ('consolidated','standalone')),
  line_no          integer not null check (line_no >= 1),
  counterparty     text    not null,
  relationship     text,                                                        -- as filed
  txn_type         text    not null,                                            -- as filed
  entering_party   text,                                                        -- as filed
  entered_by       text    not null check (entered_by in ('company','subsidiary')),
  value_in_period  numeric not null check (value_in_period >= 0),               -- rupees
  period_start     date,
  period_end       date    not null,
  primary key (results_event_id, basis, line_no),
  check (period_start is null or period_end >= period_start)
);
create index on equity.rpt_line (results_event_id, basis);

-- A results filing's revenue by reportable segment (Anand, 29 Oct 2026, 19:52 IST: 2A stores none, so this table is part of the
-- migration). A single-segment filer gets one row with its filed description and no revenue.
create table equity.segment_revenue (
  results_event_id bigint  not null references core.event(id),                  -- the results_filing event
  basis            text    not null check (basis in ('consolidated','standalone')),
  line_no          integer not null check (line_no >= 1),
  reporting        text    not null check (reporting in ('multi','single')),     -- IsCompanyReportingMultisegmentOrSingleSegment
  description      text    not null,                                            -- as filed
  revenue          numeric,                                                     -- rupees; null for a single-segment filer
  period_start     date    not null,
  period_end       date    not null,
  primary key (results_event_id, basis, line_no),
  check (period_end >= period_start),
  check ((reporting = 'single') = (revenue is null))
);
create index on equity.segment_revenue (results_event_id, basis);

------------------------------------------------------------------ privileges
grant select on equity.brsr_filing, equity.brsr_value, equity.brsr_product, equity.brsr_group_company,
                equity.rpt_line, equity.segment_revenue
  to anon, authenticated, semantic_owner, service_role;
grant select, insert, update on equity.brsr_filing, equity.brsr_value, equity.brsr_product, equity.brsr_group_company,
                                equity.rpt_line, equity.segment_revenue to job_writer;
-- The D tables: re-derivable rows a job may replace. Filings are never deleted.
grant delete on equity.brsr_value, equity.brsr_product, equity.brsr_group_company, equity.rpt_line,
                equity.segment_revenue to job_writer;

------------------------------------------------------------------ row-level security
alter table equity.brsr_filing        enable row level security;
alter table equity.brsr_value         enable row level security;
alter table equity.brsr_product       enable row level security;
alter table equity.brsr_group_company enable row level security;
alter table equity.rpt_line           enable row level security;
alter table equity.segment_revenue    enable row level security;
create policy job_writer_all on equity.brsr_filing        for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.brsr_value         for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.brsr_product       for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.brsr_group_company for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.rpt_line           for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.segment_revenue    for all to job_writer using (true) with check (true);

-- A filing: its event and its company visible (the event's policy brings the source's switch), and its exchange unsuppressed.
create policy read_published on equity.brsr_filing for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event ev where ev.id = event_id)
     and exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s where s.key = lower(exchange) and s.is_suppressed));
-- Values, products and group companies: their filing visible, and their field policy shown or labelled (a hidden field leaves
-- no row even in the base table, as 0023's guidance rows follow their output).
create policy read_published on equity.brsr_value for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.brsr_filing f where f.event_id = brsr_value.event_id)
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.brsr_values' and fp.status in ('shown','labelled')));
create policy read_published on equity.brsr_product for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.brsr_filing f where f.event_id = brsr_product.event_id)
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.brsr_values' and fp.status in ('shown','labelled')));
create policy read_published on equity.brsr_group_company for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.brsr_filing f where f.event_id = brsr_group_company.event_id)
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.group_companies' and fp.status in ('shown','labelled')));
-- Related-party and segment rows: their results filing visible (the card's change 2, like 0019's financials). The views
-- aggregate the related-party rows (a security_invoker view cannot aggregate rows anon cannot read); no view returns the lines.
create policy read_published on equity.rpt_line for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.filing f where f.event_id = rpt_line.results_event_id and f.basis = rpt_line.basis)
     and exists (select 1 from core.event ev where ev.id = rpt_line.results_event_id)
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.rpt_counterparties' and fp.status in ('shown','labelled')));
create policy read_published on equity.segment_revenue for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.filing f where f.event_id = segment_revenue.results_event_id and f.basis = segment_revenue.basis)
     and exists (select 1 from core.event ev where ev.id = segment_revenue.results_event_id)
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.segments' and fp.status in ('shown','labelled')));

------------------------------------------------------------------ field policies (fail closed; seeded hidden)
insert into core.field_policy (field_key, status, label, reason) values
  ('equity.brsr_values',        'hidden', 'As reported by the company',
   'Held back until the value and restatement hand checks pass and Anand flips it (Spec 02B §12.8; job VC-06)'),
  ('equity.group_companies',    'hidden', 'As reported by the company',
   'Held back until the value hand checks pass and Anand flips it (Spec 02B §12.8; job VC-06)'),
  ('equity.rpt_counterparties', 'hidden', 'As filed',
   'Held back until the related-party hand check passes and Anand flips it (Spec 02B §12.8; job VC-06)'),
  ('equity.segments',           'hidden', 'As filed',
   'Held back until the segment rows are checked and Anand flips it (Spec 02B §12.8; job VC-06)');

------------------------------------------------------------------ internal views (read by the api and semantic views)
-- The filings the card shows: stored events (not held, not superseded), one per company and fiscal year (the highest version),
-- entry point 2024-04-30 or later (Data Audit 08 D1: older formats are stored, not shown), the exchange unsuppressed. Row-level
-- security applies as the caller: a filing whose event or company is hidden is not here.
create view equity.brsr_filing_shown with (security_invoker = true) as
select distinct on (bf.entity_id, bf.fy_end)
       bf.event_id, bf.entity_id, ev.slug as event_slug, bf.fy_label, bf.fy_start, bf.fy_end, bf.format_version, bf.boundary,
       bf.exchange, bf.list_url, bf.version_no, bf.filed_at, bf.revised_at, bf.months, bf.openness_assured, bf.openness_assurance
from equity.brsr_filing bf
join core.event ev  on ev.id = bf.event_id and ev.status = 'stored'
join core.source xs on xs.key = lower(bf.exchange) and not xs.is_suppressed
where bf.format_version >= date '2024-04-30' and bf.boundary is not null
order by bf.entity_id, bf.fy_end, bf.version_no desc, bf.filed_at desc;

-- The card: every shown filing x every field. An absent field is a row with state 'absent'. Shares are in percent (x 100, exact
-- numeric). State: 'ok'; 'held' (with its reason); 'absent'; 'zero' (filed as 0); 'not_shown' (a top-10 share whose parent
-- share is 0, held or absent). Unreadable values (held, absent, not shown) carry no number.
create view equity.brsr_card with (security_invoker = true) as
with fields (field_key, field_no, label, item_ref, kind, parent_key) as (
  -- BEGIN brsr fields (py/tests/test_brsr_fields.py keeps this list equal to py/config/equity/brsr_fields.yaml)
  values
    ('within_india_share',   1, 'Inputs sourced directly from within India',                          'P8-EI-4, Within India',     'share', null::text),
    ('msme_share',           2, 'Inputs sourced directly from MSMEs and small producers',             'P8-EI-4, MSMEs',            'share', null),
    ('trading_house_share',  3, 'Purchases from trading houses, share of purchases',                  'P1-EI-9, Purchases (a)',    'share', null),
    ('trading_houses',       4, 'Trading houses purchased from',                                      'P1-EI-9, Purchases (b)',    'count', null),
    ('top10_trading_share',  5, 'Top 10 trading houses, share of purchases from trading houses',      'P1-EI-9, Purchases (c)',    'share', 'trading_house_share'),
    ('dealer_share',         6, 'Sales to dealers and distributors, share of sales',                  'P1-EI-9, Sales (a)',        'share', null),
    ('dealers',              7, 'Dealers and distributors sold to',                                   'P1-EI-9, Sales (b)',        'count', null),
    ('top10_dealer_share',   8, 'Top 10 dealers and distributors, share of sales to dealers',         'P1-EI-9, Sales (c)',        'share', 'dealer_share'),
    ('exports_share',        9, 'Exports, share of turnover',                                         'A-III-19(b)',               'share', null),
    ('states_served',       10, 'States served',                                                      'A-III-19(a), States',       'count', null),
    ('countries_served',    11, 'Countries served',                                                   'A-III-19(a), Countries',    'count', null),
    ('rpt_purchases_share', 12, 'Purchases from related parties, share of purchases',                 'P1-EI-9, RPTs (a)',         'share', null),
    ('rpt_sales_share',     13, 'Sales to related parties, share of sales',                           'P1-EI-9, RPTs (b)',         'share', null)
  -- END brsr fields
), x as (
  select s.event_id, s.entity_id, s.event_slug, s.fy_label, s.fy_start, s.fy_end, s.boundary, s.format_version, s.months,
         s.exchange, s.filed_at, s.list_url, s.version_no, s.openness_assured, s.openness_assurance,
         fl.field_key, fl.field_no, fl.label, fl.item_ref, fl.kind,
         v.value as raw_value, v.held_reason, v.next_comparative as raw_next, v.restated as raw_restated,
         case when v.event_id is null then 'absent'
              when v.status = 'held' then 'held'
              when fl.parent_key is not null and not coalesce(pv.status = 'ok' and pv.value > 0, false) then 'not_shown'
              when v.value = 0 then 'zero'
              else 'ok' end as state,
         case when v.event_id is null then 'not_reported'
              when v.status = 'held' then coalesce(v.held_reason, 'held')
              when fl.parent_key is not null and not coalesce(pv.status = 'ok' and pv.value > 0, false) then 'parent_share_zero_or_absent'
         end as state_reason
  from equity.brsr_filing_shown s
  cross join fields fl
  left join equity.brsr_value v  on v.event_id = s.event_id and v.field_key = fl.field_key
  left join equity.brsr_value pv on pv.event_id = s.event_id and pv.field_key = fl.parent_key
)
select x.event_id, x.entity_id, x.event_slug, x.fy_label, x.fy_start, x.fy_end, x.boundary, x.format_version, x.months,
       x.exchange, x.filed_at, x.list_url, x.version_no,
       x.field_key, x.field_no, x.label, x.item_ref, x.kind,
       case when x.state in ('ok','zero') then case when x.kind = 'share' then x.raw_value * 100 else x.raw_value end end as value,
       x.state, x.state_reason,
       case when x.state in ('ok','zero') then x.raw_restated end as restated,
       case when x.state in ('ok','zero') and x.raw_restated
            then case when x.kind = 'share' then x.raw_next * 100 else x.raw_next end end as next_comparative,
       case when x.item_ref like 'P1-EI-9%' then x.openness_assured end as assured,
       case when x.item_ref like 'P1-EI-9%' then x.openness_assurance end as assurance
from x;

grant select on equity.brsr_filing_shown, equity.brsr_card to anon, authenticated, service_role;

------------------------------------------------------------------ api views (security_invoker; list queries check their count against the 1,000-row cap)
-- api.value_chain: company x fiscal year x field. value is in percent for kind 'share'. change is the difference from the previous
-- own fiscal year (percentage points for a share, a count difference for a count): only between consecutive 12-month years on
-- the same boundary, else null with change_reason (no_previous_year, boundary_change, not_12_months, previous_value_unavailable).
-- Gated by equity.brsr_values.
create view api.value_chain with (security_invoker = true) as
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

-- The company's products and their share of turnover (percent), per shown filing. Gated by equity.brsr_values.
create view api.value_chain_products with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.brsr_values'
)
select e.slug as company_slug, s.fy_label, s.fy_start, s.fy_end, s.boundary, s.exchange, lower(s.exchange) as source_key,
       s.filed_at, s.list_url, p.line_no,
       case when g.ok then p.description end as description,
       case when g.ok then p.nic_code end as nic_code,
       case when g.ok then p.share_of_turnover * 100 end as share_of_turnover_pct
from equity.brsr_product p
join equity.brsr_filing_shown s on s.event_id = p.event_id
join core.entity e on e.id = s.entity_id
cross join g;

-- The group companies and the share held (percent), per shown filing. Gated by equity.brsr_values and equity.group_companies.
create view api.value_chain_group with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.brsr_values'), false)
     and coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.group_companies'), false) as ok
  from core.field_policy where field_key in ('equity.brsr_values','equity.group_companies')
)
select e.slug as company_slug, s.fy_label, s.fy_start, s.fy_end, s.boundary, s.exchange, lower(s.exchange) as source_key,
       s.filed_at, s.list_url, m.line_no,
       case when g.ok then m.name end as name,
       case when g.ok then m.relation end as relation,
       case when g.ok then m.share_held * 100 end as share_held_pct
from equity.brsr_group_company m
join equity.brsr_filing_shown s on s.event_id = m.event_id
join core.entity e on e.id = s.entity_id
cross join g;

-- The company's own top 5 related-party counterparties for sales and purchases of goods or services, per fiscal year. Only rows the
-- company itself entered (entered_by = 'company'), on one basis per company and year (standalone where it carries rows, else
-- consolidated), summed by counterparty over the year's stored filings. Ties are broken by counterparty name. Value in rupees.
-- Gated by equity.rpt_counterparties. No view returns the lines.
create view api.related_party_top with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.rpt_counterparties'
), l as (
  select r.basis, r.counterparty, r.relationship, r.value_in_period, r.period_start, r.period_end,
         ev.entity_id, co.fy_end_month, xs.key as source_key,
         case when extract(month from r.period_end) <= co.fy_end_month then extract(year from r.period_end)::int
              else extract(year from r.period_end)::int + 1 end as fy_end_year,
         case lower(btrim(r.txn_type)) when 'sale of goods or services' then 'Sale of goods or services'
                                       when 'purchase of goods or services' then 'Purchase of goods or services' end as txn_type
  from equity.rpt_line r
  join core.event ev  on ev.id = r.results_event_id and ev.status = 'filed'
  join core.source xs on xs.id = ev.source_id
  join equity.company co on co.entity_id = ev.entity_id
  where r.entered_by = 'company'
), b as (
  select entity_id, fy_end_year,
         case when bool_or(basis = 'standalone') then 'standalone' else 'consolidated' end as basis
  from l group by entity_id, fy_end_year
), t as (
  select l.entity_id, l.fy_end_year, l.fy_end_month, b.basis, l.txn_type, btrim(l.counterparty) as counterparty,
         (array_agg(l.relationship order by l.value_in_period desc, l.relationship))[1] as relationship,
         sum(l.value_in_period) as value, min(l.period_start) as period_from, max(l.period_end) as period_to,
         array_agg(distinct l.source_key order by l.source_key) as source_keys
  from l
  join b on b.entity_id = l.entity_id and b.fy_end_year = l.fy_end_year and b.basis = l.basis
  where l.txn_type is not null
  group by l.entity_id, l.fy_end_year, l.fy_end_month, b.basis, l.txn_type, btrim(l.counterparty)
), r as (
  select t.*, row_number() over (partition by t.entity_id, t.fy_end_year, t.txn_type
                                 order by t.value desc, t.counterparty) as rank
  from t
)
select e.slug as company_slug, e.name as company_name, e.currency,
       case when r.fy_end_month = 12 then 'FY ' || r.fy_end_year
            else 'FY ' || (r.fy_end_year - 1) || '-' || lpad((r.fy_end_year % 100)::text, 2, '0') end as fy_label,
       r.fy_end_year, r.basis, r.txn_type, r.rank::int as rank,
       case when g.ok then r.counterparty end as counterparty,
       case when g.ok then r.relationship end as relationship,
       case when g.ok then r.value end as value,
       r.period_from, r.period_to, r.source_keys
from r
cross join g
join core.entity e on e.id = r.entity_id
where r.rank <= 5;

-- The latest shown year's card fields for the company's peer group (one row per subject company x peer x field; a company's own row
-- is included, is_self). The median is of the peers other than the company that share its boundary and fiscal year, for shares
-- only, and shown only from at least 3 of them (median_n says how many); no ranking. Needs equity.peer_group and
-- equity.brsr_values. A page asks for one company: all companies x peers x fields is above the 1,000-row cap.
create view api.value_chain_peers with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.brsr_values'), false) as ok,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer_ok
  from core.field_policy where field_key in ('equity.brsr_values','equity.peer_group')
), latest as (
  select distinct on (entity_id) event_id, entity_id, fy_label, fy_end, boundary
  from equity.brsr_filing_shown order by entity_id, fy_end desc
), cells as (
  select s.entity_id as subject_id, p.entity_id as peer_id, l.fy_label, l.fy_end, l.boundary,
         c.field_key, c.field_no, c.label, c.item_ref, c.kind, c.value, c.state
  from equity.company s
  join equity.company p on p.peer_group_key = s.peer_group_key
  join latest l on l.entity_id = p.entity_id
  join equity.brsr_card c on c.event_id = l.event_id
  where s.peer_group_key is not null
), med as (
  select c.subject_id, c.field_key, count(*) as n, percentile_cont(0.5) within group (order by c.value) as median
  from cells c
  join latest sj on sj.entity_id = c.subject_id
  where c.peer_id <> c.subject_id and c.kind = 'share' and c.state in ('ok','zero')
    and c.boundary = sj.boundary and c.fy_end = sj.fy_end
  group by c.subject_id, c.field_key
)
select se.slug as company_slug, pe.slug as peer_slug, pe.name as peer_name, pe.short_name as peer_short_name,
       (c.peer_id = c.subject_id) as is_self,
       c.fy_label, c.boundary,
       (c.boundary = sj.boundary and c.fy_end = sj.fy_end) as same_basis,
       c.field_key, c.field_no, c.label, c.item_ref, c.kind,
       case when g.ok then c.value end as value,
       case when g.ok then c.state end as state,
       case when g.ok and c.kind = 'share' then coalesce(m.n, 0) end as median_n,
       case when g.ok and m.n >= 3 then m.median end as median
from cells c
cross join g
join core.entity se on se.id = c.subject_id
join core.entity pe on pe.id = c.peer_id
left join latest sj on sj.entity_id = c.subject_id
left join med m on m.subject_id = c.subject_id and m.field_key = c.field_key
where g.peer_ok;

-- The latest fiscal year's consolidated revenue by reportable segment (rupees) and each segment's share of the reportable segments'
-- total (percent). A single-segment filer has one row, state 'single_segment' ("One reportable segment, as filed"). Gated by
-- equity.segments.
create view api.segments with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.segments'
), s as (
  select sr.*, ev.entity_id, ev.starts_on
  from equity.segment_revenue sr
  join core.event ev on ev.id = sr.results_event_id and ev.status = 'filed'
  where sr.basis = 'consolidated' and sr.period_end - sr.period_start >= 350
), latest as (
  select distinct on (entity_id) entity_id, results_event_id, period_start, period_end
  from s order by entity_id, period_end desc, period_start, starts_on desc, results_event_id desc
), seg as (
  select s.* from s join latest l on l.entity_id = s.entity_id and l.results_event_id = s.results_event_id
                                  and l.period_start = s.period_start and l.period_end = s.period_end
)
select e.slug as company_slug, e.name as company_name, e.currency,
       case when extract(year from r.period_start) = extract(year from r.period_end)
            then 'FY ' || to_char(r.period_end, 'YYYY')
            else 'FY ' || to_char(r.period_start, 'YYYY') || '-' || to_char(r.period_end, 'YY') end as fy_label,
       r.period_start, r.period_end, r.line_no, r.reporting,
       case when g.ok then case when r.reporting = 'single' then 'single_segment' else 'ok' end end as state,
       case when g.ok then r.description end as description,
       case when g.ok then r.revenue end as revenue,
       case when g.ok and r.reporting = 'multi' and sum(r.revenue) over (partition by r.entity_id) > 0
            then r.revenue / sum(r.revenue) over (partition by r.entity_id) * 100 end as share_of_total_pct,
       lower(xs.key) as source_key
from seg r
cross join g
join core.entity e on e.id = r.entity_id
join core.event ev on ev.id = r.results_event_id
join core.source xs on xs.id = ev.source_id;

grant select on api.value_chain, api.value_chain_products, api.value_chain_group, api.related_party_top,
                api.value_chain_peers, api.segments to anon, authenticated, service_role;

------------------------------------------------------------------ semantic view (the command bar's schema)
-- Reads the base tables directly, not the internal views above: a security_invoker view under a semantic view would run as
-- sql_reader, which may read the semantic views only. Same rules as equity.brsr_card (kept equal by the tests): the shown
-- filings, shares in percent, a held value or an absent field is null, a top-10 share is null unless its parent share is above 0.
create view semantic.value_chain with (security_barrier = true) as
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
cross join g;

alter view semantic.value_chain owner to semantic_owner;
revoke all on semantic.value_chain from public;
grant select on semantic.value_chain to sql_reader;

------------------------------------------------------------------ comments
comment on table equity.brsr_filing is 'One row per BRSR filing (a core.event of type brsr_filing): fiscal year, entry point date, reporting boundary, exchange, version, months in the year, the filing''s own assurance flag for Principle 1 EI 9, and the brsr_xbrl document that holds the raw file.';
comment on table equity.brsr_value is 'One row per filing and card field: the value as filed (a share is the filed fraction, never rescaled), its element and BRSR item reference, ok or held with the reason, the next year''s comparative and whether it restated the value.';
comment on table equity.brsr_product is 'One row per product or service line of a BRSR filing: description, NIC code and the filed share of turnover.';
comment on table equity.brsr_group_company is 'One row per holding, subsidiary, associate or joint venture listed in a BRSR filing: name, relation as filed and the share held.';
comment on table equity.rpt_line is 'One row per related-party transaction of a results filing and basis (as filed, no PAN): counterparty, relationship, type, who entered it (the company or a subsidiary) and the value in the period in rupees. Visitors reach it only through api.related_party_top.';
comment on table equity.segment_revenue is 'One row per reportable segment of a results filing and basis: description as filed and revenue in rupees; a single-segment filer has one row with no revenue.';
comment on view equity.brsr_filing_shown is 'The BRSR filings the card shows: stored events, one per company and fiscal year (highest version), entry point 2024-04-30 or later, exchange unsuppressed. Internal: read by the api views.';
comment on view equity.brsr_card is 'Every shown BRSR filing x every card field: the value (shares in percent), its state (ok, held, absent, zero, not_shown) and reason, restatement and assurance. Internal: read by the api views.';
comment on view api.value_chain is 'Where a company buys from, sells through and how much runs through its group, from its own-year BRSR filings: one row per company, fiscal year and field. Shares are in percent as filed; state says ok, held, absent, zero or not_shown; change is the move from the previous own year (null across a boundary change or a year that is not 12 months, with change_reason). As reported by the company; not verified by this site.';
comment on view api.value_chain_products is 'The products and services a company''s BRSR lists, with each one''s share of turnover in percent, per fiscal year.';
comment on view api.value_chain_group is 'The holding, subsidiary, associate and joint-venture companies a company''s BRSR lists, with the share held in percent, per fiscal year.';
comment on view api.related_party_top is 'A company''s five largest related-party counterparties for sales and for purchases of goods or services, per fiscal year, from its own transactions in the half-yearly related-party disclosures of its results filings, in rupees, with the period the rows cover. Never the individual lines.';
comment on view api.value_chain_peers is 'The latest BRSR card fields for a company''s peer group, one row per peer and field with each peer''s boundary and fiscal year; the median is of peers on the same boundary and year, shown only from 3 peers, shares only. No ranking. Ask for one company: all companies together exceed the 1,000-row cap.';
comment on view api.segments is 'A company''s latest fiscal-year consolidated revenue by reportable segment in rupees, with each segment''s share of the total in percent; a single-segment filer has one row with state single_segment.';
comment on view semantic.value_chain is
  'Where each Nifty 50 company buys from, sells through and how much runs through its group, from its own BRSR filing for each fiscal year (FY 2023-24 onward): one row per company and fiscal year. Shares are in percent (0 to 100) as the company filed them; a field the company did not report, a value held back, or a top-10 share whose parent share is zero is null. Standalone and consolidated filings are not comparable: compare companies only on the same boundary, and never rank across boundaries. As reported by the company; informational only, not investment advice.';
comment on column semantic.value_chain.company is 'Company name as listed.';
comment on column semantic.value_chain.nse_symbol is 'NSE trading symbol (current).';
comment on column semantic.value_chain.market is 'The company''s market (IN).';
comment on column semantic.value_chain.currency is 'The company''s reporting currency; the shares here are currency-neutral.';
comment on column semantic.value_chain.valuation_family is 'operating, lender, insurer or mixed. A lender or insurer files 0 dealers and 0 trading houses because the fields do not apply to it.';
comment on column semantic.value_chain.fiscal_year is 'The BRSR''s fiscal year, such as FY 2025-26.';
comment on column semantic.value_chain.fy_start is 'First day of the BRSR''s fiscal year.';
comment on column semantic.value_chain.fy_end is 'Last day of the BRSR''s fiscal year.';
comment on column semantic.value_chain.months is 'Months in the fiscal year: 12, or 15 for a long year; do not compare changes against a year that is not 12 months.';
comment on column semantic.value_chain.boundary is 'standalone or consolidated: the reporting boundary of this BRSR. Shares on different boundaries are not comparable.';
comment on column semantic.value_chain.source_exchange is 'The exchange the filing was taken from, NSE or BSE.';
comment on column semantic.value_chain.filed_at is 'When the filing was made.';
comment on column semantic.value_chain.within_india_share is 'Percent of input material (by value) sourced directly from within India.';
comment on column semantic.value_chain.msme_share is 'Percent of input material (by value) sourced directly from MSMEs and small producers.';
comment on column semantic.value_chain.trading_house_share is 'Percent of purchases made from trading houses.';
comment on column semantic.value_chain.trading_houses is 'Number of trading houses purchases are made from.';
comment on column semantic.value_chain.top10_trading_share is 'Percent of purchases from trading houses that come from the top 10 trading houses; null when the trading-house share is zero or not reported.';
comment on column semantic.value_chain.dealer_share is 'Percent of sales made to dealers and distributors.';
comment on column semantic.value_chain.dealers is 'Number of dealers and distributors sales are made to.';
comment on column semantic.value_chain.top10_dealer_share is 'Percent of sales to dealers and distributors that go to the top 10; null when the dealer share is zero or not reported.';
comment on column semantic.value_chain.exports_share is 'Percent of turnover from exports.';
comment on column semantic.value_chain.states_served is 'Number of Indian States served (Union Territories are not counted).';
comment on column semantic.value_chain.countries_served is 'Number of countries served.';
comment on column semantic.value_chain.rpt_purchases_share is 'Percent of purchases made from related parties.';
comment on column semantic.value_chain.rpt_sales_share is 'Percent of sales made to related parties.';
