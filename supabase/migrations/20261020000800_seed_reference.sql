-- 0008: reference rows for the shared core and module 1 (not data: catalogues and switches)

insert into core.source (key, name, homepage_url, attribution, is_official) values
  ('fed',      'Board of Governors of the Federal Reserve System', 'https://www.federalreserve.gov', 'Source: Federal Reserve Board', true),
  ('rbi',      'Reserve Bank of India', 'https://rbi.org.in', 'Source: Reserve Bank of India', true),
  ('fred',     'FRED, Federal Reserve Bank of St. Louis', 'https://fred.stlouisfed.org', 'Source: FRED, Federal Reserve Bank of St. Louis', true),
  ('yfinance', 'Yahoo Finance (via yfinance)', 'https://finance.yahoo.com', 'Source: Yahoo Finance', false),
  ('curated',  'Curated by Finance Terminal', null, 'Curated', false);

insert into core.doc_type (key, label, module, diffable) values
  ('fomc_statement',           'FOMC statement',                   'policy', true),
  ('fomc_minutes',             'FOMC minutes',                     'policy', false),
  ('fomc_presconf_transcript', 'Fed press conference transcript',  'policy', false),
  ('mpc_resolution',           'MPC resolution',                   'policy', true),
  ('rbi_governor_statement',   'Governor''s statement',            'policy', false),
  ('mpc_minutes',              'MPC minutes',                      'policy', false),
  ('rbi_presconf_transcript',  'RBI press conference transcript',  'policy', false);

insert into core.event_type (key, label, module) values ('policy_meeting', 'Policy meeting', 'policy');
insert into core.event_status (event_type, status, is_final) values
  ('policy_meeting', 'scheduled', false), ('policy_meeting', 'held', true), ('policy_meeting', 'cancelled', true);

insert into core.ai_task (key, label, module, output_kind, requires_citations) values
  ('doc_summary',            'Document summary',              'core',   'text',       true),
  ('statement_diff_summary', 'What changed in the statement', 'policy', 'text',       true),
  ('stance_extraction',      'Stance and guidance',           'policy', 'label',      true),
  ('hawk_dove_score',        'Hawk/dove score',               'policy', 'score',      true),
  ('member_hawk_dove_score', 'Member hawk/dove score',        'policy', 'score',      true),
  ('projection_extraction',  'Projection extraction',         'policy', 'structured', true);

insert into core.field_policy (field_key, status, label, reason) values
  ('ai.doc_summary',            'labelled', 'AI summary',   'Cited AI output'),
  ('ai.statement_diff_summary', 'labelled', 'AI summary',   'Cited AI output'),
  ('ai.stance_extraction',      'labelled', 'AI-extracted', 'Cited AI output'),
  ('ai.projection_extraction',  'labelled', 'AI-extracted', 'Regex first; AI only where the wording varies'),
  ('ai.hawk_dove_score',        'hidden',   null, 'Held back until the hand-labelled evaluation set passes (brief §11)'),
  ('ai.member_hawk_dove_score', 'hidden',   null, 'Held back until the hand-labelled evaluation set passes (brief §11)');

insert into core.entity (kind, slug, name, short_name, market, currency, is_published) values
  ('central_bank', 'fed', 'Federal Reserve', 'Fed', 'US', 'USD', true),
  ('central_bank', 'rbi', 'Reserve Bank of India', 'RBI', 'IN', 'INR', true),
  ('index', 'nifty-50',         'Nifty 50',           'Nifty 50',    'IN', 'INR', true),
  ('index', 'nifty-bank',       'Nifty Bank',         'Nifty Bank',  'IN', 'INR', true),
  ('index', 'sensex',           'S&P BSE Sensex',     'Sensex',      'IN', 'INR', true),
  ('index', 'sp-500',           'S&P 500',            'S&P 500',     'US', 'USD', true),
  ('index', 'nasdaq-composite', 'Nasdaq Composite',   'Nasdaq Comp', 'US', 'USD', true),
  ('index', 'nasdaq-100',       'Nasdaq-100',         'Nasdaq-100',  'US', 'USD', true),
  ('index', 'dow-jones',        'Dow Jones Industrial Average', 'Dow', 'US', 'USD', true);

-- Module 1 series. Fixing times marked (verify) are confirmed against the source in the slice.
insert into core.series (key, name, kind, entity_id, source_id, source_code, attribution, unit, frequency,
                         date_rule, fixing_time, fixing_tz, market, currency, is_published)
select v.key, v.name, v.kind, (select id from core.entity where slug = v.entity), (select id from core.source where key = v.src),
       v.code, v.attr, v.unit, v.freq, v.rule, v.fix::time, v.tz, v.market, v.cur, false
from (values
  ('fred.SP500',            'S&P 500',                          'observed', 'sp-500',    'fred',     'SP500',            'Source: S&P Dow Jones Indices LLC, via FRED', 'index_points', 'D', 'as_published', '16:00', 'America/New_York', 'US', 'USD'),
  ('fred.DGS2',             'US 2-year Treasury yield',         'observed', null,        'fred',     'DGS2',             'Source: Federal Reserve Board (H.15), via FRED', 'percent', 'D', 'as_published', '15:30', 'America/New_York', 'US', null),  -- (verify)
  ('fred.DGS10',            'US 10-year Treasury yield',        'observed', null,        'fred',     'DGS10',            'Source: Federal Reserve Board (H.15), via FRED', 'percent', 'D', 'as_published', '15:30', 'America/New_York', 'US', null),  -- (verify)
  ('fred.DTWEXBGS',         'Broad US dollar index',            'observed', null,        'fred',     'DTWEXBGS',         'Source: Federal Reserve Board (H.10), via FRED', 'index_points', 'D', 'as_published', '12:00', 'America/New_York', 'US', null),
  ('fred.DEXINUS',          'USD/INR',                          'observed', null,        'fred',     'DEXINUS',          'Source: Federal Reserve Board (H.10), via FRED', 'inr_per_usd', 'D', 'as_published', '12:00', 'America/New_York', 'GLOBAL', null),
  ('fred.INDIRLTLT01STM',   'India 10-year government bond yield (monthly)', 'observed', null, 'fred', 'INDIRLTLT01STM', 'Source: OECD, via FRED', 'percent', 'M', 'period_start', null, null, 'IN', null),
  ('fred.DFEDTARU',         'Fed target range, upper bound',    'observed', 'fed',       'fred',     'DFEDTARU',         null, 'percent', 'D', 'as_published', null, null, 'US', null),
  ('fred.DFEDTARL',         'Fed target range, lower bound',    'observed', 'fed',       'fred',     'DFEDTARL',         null, 'percent', 'D', 'as_published', null, null, 'US', null),
  ('fred.EFFR',             'Effective federal funds rate',     'observed', 'fed',       'fred',     'EFFR',             'Source: Federal Reserve Bank of New York, via FRED', 'percent', 'D', 'as_published', null, null, 'US', null),
  ('yf.^NSEI',              'Nifty 50',                         'observed', 'nifty-50',  'yfinance', '^NSEI',            'Source: NSE Indices, via Yahoo Finance', 'index_points', 'D', 'as_published', '15:30', 'Asia/Kolkata', 'IN', 'INR'),
  ('yf.^NSEBANK',           'Nifty Bank',                       'observed', 'nifty-bank','yfinance', '^NSEBANK',         'Source: NSE Indices, via Yahoo Finance', 'index_points', 'D', 'as_published', '15:30', 'Asia/Kolkata', 'IN', 'INR'),
  ('yf.^BSESN',             'Sensex',                           'observed', 'sensex',    'yfinance', '^BSESN',           'Source: BSE Index Services, via Yahoo Finance', 'index_points', 'D', 'as_published', '15:30', 'Asia/Kolkata', 'IN', 'INR'),
  ('rbi.proj_cpi',          'RBI CPI inflation projection',     'forecast', 'rbi',       'rbi',      'mpc_resolution',   null, 'percent', 'irregular', 'as_published', null, null, 'IN', null),
  ('rbi.proj_gdp',          'RBI real GDP growth projection',   'forecast', 'rbi',       'rbi',      'mpc_resolution',   null, 'percent', 'irregular', 'as_published', null, null, 'IN', null)
) as v(key, name, kind, entity, src, code, attr, unit, freq, rule, fix, tz, market, cur);

-- Module 1 refresh units (piece 2 finalises frequencies and due rules).
insert into ops.task (key, module, description, workflow, min_interval, overdue_after, uses_nse) values
  ('policy.fed_documents',  'policy', 'FOMC statements, minutes, press-conference transcripts', 'daily',   '20 hours', '3 days',  false),
  ('policy.rbi_documents',  'policy', 'MPC resolutions, Governor''s statements, minutes, transcripts (old site + RSS)', 'daily', '20 hours', '3 days', false),
  ('policy.market_series',  'policy', 'FRED and yfinance series for the policy tracker and ticker tape', 'daily', '20 hours', '4 days', false),
  ('policy.calendar',       'policy', 'FOMC and MPC meeting calendars', 'monthly', '25 days', '40 days', false),
  ('policy.ai_batch',       'policy', 'Summaries, diffs, stance, scores and projections for new documents', 'daily', '20 hours', '3 days', false),
  ('policy.reactions',      'policy', 'Market reactions around each decision', 'daily', '20 hours', '4 days', false);

insert into core.task_freshness (task_key, label, status)
select key, description, 'stale' from ops.task;
