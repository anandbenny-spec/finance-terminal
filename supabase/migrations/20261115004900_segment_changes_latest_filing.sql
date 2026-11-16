-- 0049: api.segment_changes gains `latest_filed_period` (issue #569, job DD-01c, lane D; migration approved by Anand, 15 Nov 2026,
-- about 12:36 IST, recorded on #30). From #553's final verdict: the panel shows a flag only on the company's latest results
-- (#553 decision 3), and learnt "latest" from the statements the Financials tab reads. Coal India's Sep 2026 consolidated filing
-- is stored but neither the statements nor the segment task dates it, so the statements stopped at Jun 2026 and the page called
-- June's flag "the latest results". The view now carries the company's latest stored consolidated results quarter, and the panel
-- takes the later of it and the statements' quarter.
-- Decided by Claude (standing delegation, 24 Oct 2026), recorded on #569 with its alternative: the column is the latest
-- `period_end` of the company's consolidated filings on a filed (not superseded) results event, the selection the task itself
-- reads; under security_invoker a visitor's RLS on equity.filing and core.event applies, so a suppressed exchange's filing or an
-- unpublished event doesn't count. Alternative: the latest results event of any basis.
-- Everything else is 0038's view unchanged, column for column and row for row (run_tests_0049.sh compares them); no table, policy,
-- grant or field-policy change. `create or replace view` may only add columns at the end, which is where the new one goes.

------------------------------------------------------------------ the api view (0038's, plus latest_filed_period)
create or replace view api.segment_changes with (security_invoker = true) as
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
       lower(lf.exchange) as source_key,
       (select max(fl.period_end)
          from equity.filing fl
          join core.event fev on fev.id = fl.event_id and fev.status = 'filed'
         where fl.entity_id = r.entity_id and fl.basis = 'consolidated') as latest_filed_period
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

grant select on api.segment_changes to anon, authenticated, service_role;

comment on view api.segment_changes is 'Changes in a company''s reportable segments, from its consolidated quarterly results as filed: for its latest compared quarter, one row per flag (state changed: the kind, the segments before and after, each side linked to its filing) or one row with the state (single_segment, none_flagged, not_compared with the reason). latest_filed_period is the company''s latest stored consolidated results quarter, which can be later than the quarter compared when its filing gave no line-up. Never a motive. Gated by equity.segment_changes.';
