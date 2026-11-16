-- The definition of api.segment_changes as 0038 left it, copied unchanged but for the name, as a temp view for
-- run_tests_0049.sh's row-equality check (the pattern of 0032_old_views.sql and 0048_old_views.sql).
create temp view old_segment_changes with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok
  from core.field_policy where field_key = 'equity.segment_changes'
), lu as (
  select l.* from equity.segment_lineup l
  join core.event ev on ev.id = l.results_event_id and ev.status = 'filed'
), q as (   -- one row per company and quarter
  select entity_id, period_end, results_event_id, document_id, bool_or(reporting = 'single') as single
  from lu group by entity_id, period_end, results_event_id, document_id
), latest as (
  select distinct on (entity_id) entity_id, period_end, results_event_id, document_id, single
  from q order by entity_id, period_end desc, results_event_id desc
), st as (
  select l.*,
         (select count(distinct q2.period_end) from q q2
           where q2.entity_id = l.entity_id
             and q2.period_end in ((date_trunc('month', l.period_end) - interval '2 months' - interval '1 day')::date,
                                   (date_trunc('month', l.period_end) - interval '5 months' - interval '1 day')::date,
                                   (date_trunc('month', l.period_end) - interval '8 months' - interval '1 day')::date,
                                   (date_trunc('month', l.period_end) - interval '11 months' - interval '1 day')::date))::int
           as quarters_compared,
         exists (select 1 from equity.segment_change c where c.entity_id = l.entity_id and c.period_end = l.period_end) as flagged
  from latest l
), rows_ as (
  select st.*, c.change_no, c.kind, c.segments_before, c.segments_after, c.revenue_ratio, c.previous_event_id,
         c.previous_period_end, c.rule_version
  from st
  left join equity.segment_change c on c.entity_id = st.entity_id and c.period_end = st.period_end
)
select e.slug as company_slug, e.name as company_name,
       r.period_end,
       case when not g.ok then null
            when r.flagged then 'changed'
            when r.single then 'single_segment'
            when r.quarters_compared = 4 then 'none_flagged'
            else 'not_compared' end as state,
       case when g.ok and not r.flagged and not r.single and r.quarters_compared < 4
            then r.quarters_compared || ' of the 4 earlier quarters have a line-up' end as state_reason,
       r.quarters_compared,
       r.change_no,
       case when g.ok then r.kind end as kind,
       case when g.ok then r.segments_before end as segments_before,
       case when g.ok then r.segments_after end as segments_after,
       case when g.ok then r.revenue_ratio end as revenue_ratio,
       r.previous_period_end,
       lf.source_url as latest_filing_url, lf.filed_at as latest_filed_at,
       pf.source_url as previous_filing_url, pf.filed_at as previous_filed_at,
       r.rule_version,
       lower(lf.exchange) as source_key
from rows_ r
cross join g
join core.entity e on e.id = r.entity_id
join equity.filing lf on lf.document_id = r.document_id
left join lateral (
  select f.source_url, f.filed_at
  from equity.segment_lineup pl
  join equity.filing f on f.document_id = pl.document_id
  where pl.results_event_id = r.previous_event_id
  order by f.filed_at desc nulls last, f.document_id desc
  limit 1
) pf on true;
