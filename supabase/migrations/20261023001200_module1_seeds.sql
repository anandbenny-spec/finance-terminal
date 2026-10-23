-- 0012: module 1's seeds (Spec 01 §1.3; Prompt 01 B1). Rows and comments only: no table, view or grant changes.
-- 0011 is Review 01's fix migration (22 Oct 2026), so the seeds follow as 0012; 0013 (hardening) comes next.

------------------------------------------------------------------ the rest of the tape's series (Spec 01 D3, D6)
-- The four US indices are the published series for the tape, charts and reactions (D3, which supersedes Data
-- Audit 01 #19). Each attribution carries the source line and the copyright line naming the index's owner
-- (Data Audit 07 C2; brief §6.2): S&P Dow Jones Indices LLC for the S&P 500 and the Dow, Nasdaq, Inc. for the
-- Nasdaq Composite and the Nasdaq-100.
-- yf.INR=X is the tape's latest USD/INR only. History, charts and reactions use fred.DEXINUS, and the two are
-- never spliced (D6). It has no fixing time (Spec 01 §1.3).
-- Like every series, they stay unpublished until their first load passes its bounds and lag checks (Spec 01 §2).
insert into core.series (key, name, kind, entity_id, source_id, source_code, attribution, unit, frequency,
                         date_rule, fixing_time, fixing_tz, market, currency, is_published)
select v.key, v.name, 'observed', (select id from core.entity where slug = v.entity), (select id from core.source where key = 'yfinance'),
       v.code, v.attr, v.unit, 'D', 'as_published', v.fix::time, v.tz, v.market, v.cur, false
from (values
  ('yf.^GSPC', 'S&P 500',                      'sp-500',           '^GSPC', 'Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC', 'index_points', '16:00', 'America/New_York', 'US', 'USD'),
  ('yf.^IXIC', 'Nasdaq Composite',             'nasdaq-composite', '^IXIC', 'Source: Nasdaq, Inc., via Yahoo Finance · © Nasdaq, Inc.',                          'index_points', '16:00', 'America/New_York', 'US', 'USD'),
  ('yf.^NDX',  'Nasdaq-100',                   'nasdaq-100',       '^NDX',  'Source: Nasdaq, Inc., via Yahoo Finance · © Nasdaq, Inc.',                          'index_points', '16:00', 'America/New_York', 'US', 'USD'),
  ('yf.^DJI',  'Dow Jones Industrial Average', 'dow-jones',        '^DJI',  'Source: S&P Dow Jones Indices LLC, via Yahoo Finance · © S&P Dow Jones Indices LLC', 'index_points', '16:00', 'America/New_York', 'US', 'USD'),
  ('yf.INR=X', 'USD/INR (Yahoo Finance)',      null,               'INR=X', 'Source: Yahoo Finance',                                                             'inr_per_usd',  null,    null,               'GLOBAL', null)
) as v(key, name, entity, code, attr, unit, fix, tz, market, cur);

-- fred.SP500 (seeded in 0008) stays unpublished for good (D3): it is the blocking cross-check for yf.^GSPC
-- (within 0.05% on every date both have) and the fallback, never shown. This migration leaves it as it is;
-- run_tests_0012.sh asserts it.
-- The DGS2 and DGS10 fixing times (seeded as 15:30 New York, "verify") aren't resolved yet: B4 resolves them,
-- and they go in the next migration (Prompt 01 B1).

------------------------------------------------------------------ comments the text-to-SQL prompt is built from
comment on view semantic.market_daily is
  'Market and macro series used by the policy tracker, one row per series per observation date: S&P 500, Nasdaq Composite, Nasdaq-100 and Dow Jones Industrial Average (US, USD, end-of-day closes); Nifty 50, Nifty Bank and Sensex (India, INR, end-of-day closes); US 2-year and 10-year Treasury yields; India 10-year yield (monthly); USD/INR; dollar index; Fed target range; effective fed funds rate. USD/INR has two series that are never combined: fred.DEXINUS (daily values, published weekly) for history and changes over time, and yf.INR=X (Yahoo Finance) for the latest day only. Never compare levels across currencies.';

comment on column semantic.policy_meetings.rbi_crr_pct is
  'null until Pass 2 (CRR is set in the Governor''s statement, with its own effective dates)';
