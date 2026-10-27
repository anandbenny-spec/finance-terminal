-- 0018: the `coingecko` source row (issue #106; Data Audit 04 decisions C4 and C9). Seed row only: no structural
-- change. Catalogues change only through migrations, and job_writer can't insert into core.source, so the daily
-- market snapshot task (crypto.coingecko_markets) needs this row before it can store a raw file.

insert into core.source (key, name, homepage_url, terms_url, attribution, is_official, allow_raw_export, notes) values
  ('coingecko', 'CoinGecko', 'https://www.coingecko.com', 'https://www.coingecko.com/en/api_terms',
   'Powered by CoinGecko', false, false,
   'Decision C9 (Data Audit 04): CoinGecko''s free Demo plan, terms version of 5 Sep 2025. Stored daily as raw files only (top 500 coins: market cap, FDV, supply, rank, price, volume), unpublished until module 4 reads them. No raw-data export of any kind; "Powered by CoinGecko" is shown wherever its data is. Stored data is refreshed every 24 hours (terms 6.1).')
on conflict (key) do nothing;
