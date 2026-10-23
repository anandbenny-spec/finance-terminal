-- 0014: the DGS2 and DGS10 fixing times, resolved (Prompt 01 B4; Arch 01 §8 item 6; GitHub issues #13 and #1, item 6).
-- Rows only: no table, view or grant changes.
--
-- 0008 seeded fred.DGS2 and fred.DGS10 at 15:30 America/New_York, marked "(verify)". Checked 24 Oct 2026:
-- - The Fed's H.15 release (22 Oct 2026), footnote on nominal Treasury constant maturities: "Yields on actively
--   traded non-inflation-indexed issues adjusted to constant maturities ... Source: U.S. Treasury"; the yields are
--   read from Treasury's curve, "based on the closing market bid yields on actively traded Treasury securities in the
--   over-the-counter market" (https://www.federalreserve.gov/releases/h15/).
-- - Treasury's yield curve methodology: the inputs are "indicative, bid-side market price quotations (not actual
--   transactions)" "obtained by the Federal Reserve Bank of New York at or near 3:30 PM each trading day"
--   (https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology).
-- So both fix at about 15:30 New York time, as seeded. The update below states the confirmed value (a no-op on a
-- database built from these migrations) and is the record that the "(verify)" mark in 0008 is resolved.
update core.series
   set fixing_time = '15:30', fixing_tz = 'America/New_York'
 where key in ('fred.DGS2', 'fred.DGS10');
