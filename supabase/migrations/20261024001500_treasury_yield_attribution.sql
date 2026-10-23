-- 0015: the US 2-year and 10-year yields name their original source (approved by Anand, 24 Oct 2026, 04:14 IST,
-- on B4's open question in GitHub issue #13). Rows only: no table, view or grant changes.
--
-- 0008 seeded fred.DGS2 and fred.DGS10 as "Source: Federal Reserve Board (H.15), via FRED". The H.15 release itself
-- (footnote on nominal Treasury constant maturities, checked 24 Oct 2026: https://www.federalreserve.gov/releases/h15/)
-- ends "Source: U.S. Treasury": the Board republishes yields read from Treasury's curve. The line now names Treasury
-- first and keeps the Board and FRED, which both ask to be cited (Brief §6.2: the Fed's site is public domain with
-- citation asked; FRED's series are public domain with citation requested). CLAUDE.md §1: every figure shows its
-- source line, and a source line is a fact a visitor reads, so it has to be right.
update core.series
   set attribution = 'Source: U.S. Treasury, via Federal Reserve Board (H.15) and FRED'
 where key in ('fred.DGS2', 'fred.DGS10');
