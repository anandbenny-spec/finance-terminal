-- 0039: the US slice's schema and catalogue rows (lane U, job US-01, issue #400; Spec 02A.1 §4; Plan 05 §0.5 N3, N7).
-- Approved by Anand, 11 Nov 2026, about 17:52 IST (tracker #30). Only `equity` objects and catalogue rows: no core table
-- or column changes, no view, no field policy. India's rows behave exactly as before under every policy (tests 0039).
--
-- The gate is publication (Plan 05 §0.5 N7): US companies are created with core.entity.is_published = false and no task
-- publishes them, so every equity policy (each requires its entity to be published) keeps them from every visitor view.
-- The identifier schemes `cik` and `ticker` are already in core.entity_identifier's check (0003), so nothing is added
-- for them.

------------------------------------------------------------------ 1. the source
insert into core.source (key, name, homepage_url, terms_url, attribution, is_official, allow_raw_export, notes) values
  ('sec', 'U.S. Securities and Exchange Commission (EDGAR)', 'https://www.sec.gov', null, 'Source: SEC EDGAR', true, false,
   'US company filings: XBRL company facts and the submissions API (Brief §6.2; Spec 02A.1 §4). SEC''s fair-access rules: a declared User-Agent with a contact (the SEC_USER_AGENT secret, never in the repo) and under 10 requests a second (Architecture 02 §1.5). No raw-data export of any kind. US prices use the yfinance source.');

------------------------------------------------------------------ 2. checks widened (India's values kept)
alter table equity.company drop constraint company_price_exchange_check,
  add constraint company_price_exchange_check check (price_exchange in ('NSE','BSE','NYSE','NASDAQ'));
alter table equity.price_daily drop constraint price_daily_exchange_check,
  add constraint price_daily_exchange_check check (exchange in ('NSE','BSE','NYSE','NASDAQ'));
alter table equity.corporate_action drop constraint corporate_action_exchange_check,
  add constraint corporate_action_exchange_check check (exchange in ('NSE','BSE','NYSE','NASDAQ'));
alter table equity.filing drop constraint filing_exchange_check,
  add constraint filing_exchange_check check (exchange in ('NSE','BSE','SEC'));
alter table equity.filing drop constraint filing_format_check,
  add constraint filing_format_check check (format in ('ind_as','bank','nbfc','insurer_life','insurer_general','us_gaap'));
-- `source_adjusted`: a split already in the source's stored prices (Yahoo's Close is split-adjusted when fetched), so its
-- factor is 1 and equity.price_adjusted never applies it twice (Spec 02A.1 §5.3).
alter table equity.adjustment_factor drop constraint adjustment_factor_method_check,
  add constraint adjustment_factor_method_check check (method in ('ratio','special_preopen','terp','reviewed','source_adjusted'));

------------------------------------------------------------------ 3. suppression: the exchange names its source
-- 0019's policies read the source as lower(exchange), which names no source for NYSE, NASDAQ or SEC. Each now maps the
-- exchange to its source: NSE -> nse and BSE -> bse (exactly lower(exchange), so India's rows are unchanged), NYSE and
-- NASDAQ -> yfinance (US prices and actions come from Yahoo Finance), SEC -> sec. Inline CASE only: 0001 revokes EXECUTE
-- on functions, so no policy calls one.
drop policy read_published on equity.price_daily;
create policy read_published on equity.price_daily for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s
                      where s.key = case exchange when 'NSE' then 'nse' when 'BSE' then 'bse'
                                                  when 'NYSE' then 'yfinance' when 'NASDAQ' then 'yfinance' end
                        and s.is_suppressed));

drop policy read_published on equity.filing;
create policy read_published on equity.filing for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s
                      where s.key = case exchange when 'NSE' then 'nse' when 'BSE' then 'bse' when 'SEC' then 'sec' end
                        and s.is_suppressed));

-- Shareholding takes NSE and BSE only (no US row is planned); the mapping is written out so all four read alike.
drop policy read_published on equity.shareholding;
create policy read_published on equity.shareholding for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = entity_id)
     and not exists (select 1 from core.source s
                      where s.key = case exchange when 'NSE' then 'nse' when 'BSE' then 'bse' end
                        and s.is_suppressed));

-- A corporate action is visible through its core.event (published, its source not suppressed), as in 0019, and 0019's
-- rule never asks whether the company is published. A US action (NYSE, NASDAQ) also needs its company published (the
-- event's entity, which anon sees only when published: the gate, Plan 05 §0.5 N7) and hides when yfinance is suppressed,
-- whatever source its event names. India's actions keep 0019's rule exactly (NSE and BSE skip both additions).
-- Decided by Claude (standing delegation, 24 Oct 2026; #400): Spec 02A.1 §4 item 3 names four policies, and this one
-- also needed the entity condition for the gate to hold; it only tightens, and only for US rows.
drop policy read_published on equity.corporate_action;
create policy read_published on equity.corporate_action for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event ev where ev.id = event_id)
     and (exchange in ('NSE','BSE')
          or exists (select 1 from core.event ev join core.entity e on e.id = ev.entity_id where ev.id = event_id))
     and not exists (select 1 from core.source s
                      where s.key = case exchange when 'NYSE' then 'yfinance' when 'NASDAQ' then 'yfinance' end
                        and s.is_suppressed));

-- An adjustment factor is visible through its event, as in 0019. A US factor (its event's source is yfinance: US-03
-- writes US actions' events from that source) also needs its company published; India's factors (events from nse or
-- bse) keep 0019's rule exactly. Same decision as above.
drop policy read_published on equity.adjustment_factor;
create policy read_published on equity.adjustment_factor for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event ev where ev.id = event_id
                   and (not exists (select 1 from core.source s where s.id = ev.source_id and s.key = 'yfinance')
                        or exists (select 1 from core.entity e where e.id = adjustment_factor.entity_id))));

------------------------------------------------------------------ 4. index membership: NSE's condition for the Nifty indices only
-- The Dow list is curated (Data Audit 02 A8), not NSE's, so a Dow row needs both entities published and nothing else.
drop policy read_published on equity.index_membership;
create policy read_published on equity.index_membership for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity i where i.id = index_entity_id
                   and (i.slug not like 'nifty-%'
                        or not exists (select 1 from core.source s where s.key = 'nse' and s.is_suppressed)))
     and exists (select 1 from core.entity e where e.id = entity_id));

------------------------------------------------------------------ 5. the column US statements need
alter table equity.financials add column operating_income numeric;
comment on column equity.financials.operating_income is
  'US filings only: operating income as filed (us-gaap OperatingIncomeLoss), in the filing''s currency. Null where the company files none (banks, for example); never derived from other lines. India''s rows keep it null (their EBIT is derived: equity.metric ebit_derived).';
