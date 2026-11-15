-- 0040: api.related_party_top counts a counterparty's spellings as one (issue #420, job VC-05b, lane B; follows #190).
-- Approved by Anand, 11 Nov 2026, 22:07 IST (recorded on #420 and #30). A function and a view only: no table, column, grant on a
-- table, field policy or stored row changes, and no task reruns.
--
-- Why. 0034's view sums a company's related-party lines by the trimmed counterparty name as filed, over the fiscal year's stored
-- filings. A counterparty spelt one way in the half-year file and another in the year-end file (case, "&" and "and", "Ltd" and
-- "Limited", punctuation) therefore got two part-totals, ranked separately: understated, listed twice, or out of the top 5. The
-- production run of equity.related_parties (#190) flagged 432 such counterparties; a counts-only query found 67 of them in the
-- sale and purchase lists this view ranks, in 13 companies. The view stays hidden (equity.rpt_counterparties) until a judge passes
-- it, so nothing wrong was public.
--
-- What. 1. equity.rpt_name_key(text): the key `ft.equity.related_parties.norm_name` computes in Python (the `spelling` check
-- uses it), in SQL: lower case, "&" read as " and ", the whole word "ltd" read as "limited", then everything but letters, digits
-- and underscore dropped. For ASCII names the two agree (test in py/tests/test_equity_rpt_name_key.py); a letter outside ASCII is
-- kept or dropped as the database's character classes decide, which only matters for two spellings that differ in such a letter.
-- 2. api.related_party_top: the same columns in the same order, security_invoker, gate, filters and rank rule as 0034, but the
-- sum is per key. The name shown is the spelling on the line with the latest period_end (ties: the larger value, then the name),
-- so the current filing's wording wins. A name whose key is empty (no letters or digits) keeps its own trimmed text as the key,
-- so two different such names are never merged. The relationship shown is still that of the group's largest line.
--
-- Decided by Claude (standing delegation, 24 Oct 2026), recorded on #420:
--   1. The shown spelling is the latest filing's. Alternative: the alphabetically first, which is what the `spelling` check prints
--      (it would show a year-old wording whenever it sorts first).
--   2. The key is a function, not an inline expression, so a test can hold it against the Python rule and the next view that
--      needs it (a second use) reuses it. Alternative: inline in the view, which no test could compare with Python.
-- Reaches dev and production in number order, after 0038 and 0039.

create function equity.rpt_name_key(p_name text) returns text
language sql immutable parallel safe
set search_path = pg_catalog      -- every function in our schemas pins its search_path (0013, test H01); it uses only built-ins
as $$
  select regexp_replace(
           regexp_replace(replace(lower(p_name), '&', ' and '), '\mltd\M', 'limited', 'g'),
           '[^[:alnum:]_]+', '', 'g')
$$;
revoke all on function equity.rpt_name_key(text) from public;
grant execute on function equity.rpt_name_key(text) to anon, authenticated, service_role;
comment on function equity.rpt_name_key(text) is
  'The key two spellings of one counterparty share: lower case, "&" as "and", the word "ltd" as "limited", only letters, digits and underscore kept. The same rule as norm_name in ft/equity/related_parties.py. Null in, null out.';

create or replace view api.related_party_top with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.rpt_counterparties'
), l as (
  select r.basis, r.counterparty, r.relationship, r.value_in_period, r.period_start, r.period_end,
         coalesce(nullif(equity.rpt_name_key(r.counterparty), ''), btrim(r.counterparty)) as name_key,
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
  select l.entity_id, l.fy_end_year, l.fy_end_month, b.basis, l.txn_type, l.name_key,
         (array_agg(btrim(l.counterparty) order by l.period_end desc, l.value_in_period desc, btrim(l.counterparty)))[1] as counterparty,
         (array_agg(l.relationship order by l.value_in_period desc, l.relationship))[1] as relationship,
         sum(l.value_in_period) as value, min(l.period_start) as period_from, max(l.period_end) as period_to,
         array_agg(distinct l.source_key order by l.source_key) as source_keys
  from l
  join b on b.entity_id = l.entity_id and b.fy_end_year = l.fy_end_year and b.basis = l.basis
  where l.txn_type is not null
  group by l.entity_id, l.fy_end_year, l.fy_end_month, b.basis, l.txn_type, l.name_key
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

comment on view api.related_party_top is 'A company''s five largest related-party counterparties for sales and for purchases of goods or services, per fiscal year, from its own transactions in the half-yearly related-party disclosures of its results filings, in rupees, with the period the rows cover. Spellings of one name that differ only by case, punctuation, "&" and "and", or "Ltd" and "Limited" count as one counterparty and the filing''s latest wording is shown. Never the individual lines.';
