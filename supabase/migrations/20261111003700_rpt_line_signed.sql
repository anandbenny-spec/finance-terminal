-- 0037: related-party amounts are stored signed (issue #190; Anand, 11 Nov 2026: "apply your recommendation").
-- Real filings carry negative amounts (reversals and adjustments): 29 of the 58 saved H1/H2 FY 2025-26 filings have at least one,
-- 9 of them in the two types the top 5 shows. 0034's check `value_in_period >= 0` would stop the whole run on the first such
-- company. api.related_party_top already sums by counterparty, so a reversal nets out exactly. No structural change beyond
-- dropping the check; the column stays `numeric not null` (in rupees, as filed).
alter table equity.rpt_line drop constraint rpt_line_value_in_period_check;
comment on column equity.rpt_line.value_in_period is 'Rupees as filed, signed: a negative amount is a reversal or adjustment and nets out in api.related_party_top.';
