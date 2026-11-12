-- 0042: api.related_party_top answers within the visitor role's 3 s (issue #446, job VC-06e, lane B; follows #420's 0040).
-- Approved by Anand, 12 Nov 2026 (recorded on #446 and #30). A view only: the same columns in the same order, grants,
-- security_invoker, gate, filters, rank rule and comment as 0040. No table, column, function, policy, field policy or stored row
-- changes, and no task reruns.
--
-- Why. #191's live-page check (12 Nov 2026, 14:55 IST) found the related-party list on no company page: "statement timeout",
-- 3.1 s to 5.1 s, against anon's 3 s. 0040's view reads its CTE `l` (every company's lines) twice, so Postgres materialises it:
-- every line of every company went through the row-level policies of equity.rpt_line and through equity.rpt_name_key before
-- `company_slug = ...` was applied. Nothing wrong was shown (the field was put back to hidden at about 14:58 IST).
--
-- What. One pass, every step keyed by the company's slug, so the planner pushes `company_slug = $1` down to core.entity, then
-- core.event, then equity.rpt_line (results_event_id). Each CTE is read once, so none is materialised. `slug`, `name` and
-- `currency` depend on entity_id, so adding them to the grouping and the partitions changes no group. 0040's `b` (one basis per
-- company and fiscal year, standalone if any line is standalone) is the window `has_standalone` over the same lines. The name key
-- is computed only for the lines that are ranked. The comment on the view stays 0040's.
-- Reaches dev and production in number order, after 0041.

create or replace view api.related_party_top with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.rpt_counterparties'
), l as (
  select e.slug, e.name, e.currency, ev.entity_id, co.fy_end_month, xs.key as source_key,
         r.basis, r.counterparty, r.relationship, r.value_in_period, r.period_start, r.period_end,
         case when extract(month from r.period_end) <= co.fy_end_month then extract(year from r.period_end)::int
              else extract(year from r.period_end)::int + 1 end as fy_end_year,
         case lower(btrim(r.txn_type)) when 'sale of goods or services' then 'Sale of goods or services'
                                       when 'purchase of goods or services' then 'Purchase of goods or services' end as txn_type
  from equity.rpt_line r
  join core.event ev  on ev.id = r.results_event_id and ev.status = 'filed'
  join core.source xs on xs.id = ev.source_id
  join equity.company co on co.entity_id = ev.entity_id
  join core.entity e on e.id = ev.entity_id
  where r.entered_by = 'company'
), lb as (   -- 0040's b, as a window: one basis per company and year, standalone if any line is standalone
  select l.*, bool_or(l.basis = 'standalone') over (partition by l.slug, l.entity_id, l.fy_end_year) as has_standalone
  from l
), k as (    -- the name key only for the lines that are ranked
  select lb.*, coalesce(nullif(equity.rpt_name_key(lb.counterparty), ''), btrim(lb.counterparty)) as name_key
  from lb
  where lb.txn_type is not null
    and lb.basis = case when lb.has_standalone then 'standalone' else 'consolidated' end
), t as (
  select k.slug, k.name, k.currency, k.entity_id, k.fy_end_year, k.fy_end_month, k.basis, k.txn_type, k.name_key,
         (array_agg(btrim(k.counterparty) order by k.period_end desc, k.value_in_period desc, btrim(k.counterparty)))[1] as counterparty,
         (array_agg(k.relationship order by k.value_in_period desc, k.relationship))[1] as relationship,
         sum(k.value_in_period) as value, min(k.period_start) as period_from, max(k.period_end) as period_to,
         array_agg(distinct k.source_key order by k.source_key) as source_keys
  from k
  group by k.slug, k.name, k.currency, k.entity_id, k.fy_end_year, k.fy_end_month, k.basis, k.txn_type, k.name_key
), r as (
  select t.*, row_number() over (partition by t.slug, t.entity_id, t.fy_end_year, t.txn_type
                                 order by t.value desc, t.counterparty) as rank
  from t
)
select r.slug as company_slug, r.name as company_name, r.currency,
       case when r.fy_end_month = 12 then 'FY ' || r.fy_end_year
            else 'FY ' || (r.fy_end_year - 1) || '-' || lpad((r.fy_end_year % 100)::text, 2, '0') end as fy_label,
       r.fy_end_year, r.basis, r.txn_type, r.rank::int as rank,
       case when g.ok then r.counterparty end as counterparty,
       case when g.ok then r.relationship end as relationship,
       case when g.ok then r.value end as value,
       r.period_from, r.period_to, r.source_keys
from r
cross join g
where r.rank <= 5;
