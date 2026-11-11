-- 0038: segment line-up changes (issue #409, job DD-01, lane D; Plan 05 §1.2, D6, D8, §0.5 N3, N5; migration and its
-- production run approved by Anand, 11 Nov 2026, about 17:52 IST, recorded on #30).
-- Two `equity` tables: `segment_lineup` (a company's reportable segments in each quarter, from the consolidated results filing
-- 2A stores: VC-05's `segment_revenue` holds one fiscal year per event, Plan 05 §0.5 N5) and `segment_change` (the flags of
-- the rule in `py/src/ft/equity/segments.py`, the latest quarter against the four before it), the field policy
-- `equity.segment_changes` seeded hidden, and `api.segment_changes`. No core change; `equity.segment_revenue`, VC-05's task and
-- `api.segments` are untouched. Equity conventions as 0019 and 0034 (Spec 02A §3.2): RLS on, the `job_writer_all` policy,
-- explicit grants, `delete` for jobs on these two derived tables (marked D: re-derivable rows a run may replace; the filings
-- themselves are never deleted). Visitor policies use inline subqueries only (0001 revokes EXECUTE from PUBLIC).
-- Decided by Claude (standing delegation, 24 Oct 2026), each recorded on #409 with its alternative:
--   1. As 0034's decision 1, both tables' visitor policies also require `equity.segment_changes` to be shown or labelled, so a
--      hidden field leaves no row even in the base tables; the view gates by CASE as well. Alternative: gate in the view only.
--   2. The view's state for a company with no flag is derived from the stored line-ups (single segment, none flagged, or not
--      compared when one of the four earlier quarters has no line-up), the same condition the task's rule uses, so no third
--      table holds a per-company state. Alternative: a status table written by the task.
--   3. The latest quarter shown is the company's latest quarter with a line-up, with its date: a quarter whose filing gave no
--      line-up is named by the task's warning check and never makes the page fall back silently, because the date says which
--      quarter was compared. Alternative: the latest filing's quarter, shown as not compared.

------------------------------------------------------------------ the tables
-- One row per reportable segment of a quarter's consolidated results filing (the latest version on a filed announcement), as
-- filed; a single-segment filer has one row with its filed description and no revenue. Reconciling rows the filer put on the
-- segment axis ("Less: Inter segment revenue", "Unallocated") are not stored.
create table equity.segment_lineup (
  results_event_id bigint  not null references core.event(id),                  -- the results_filing event
  basis            text    not null default 'consolidated' check (basis = 'consolidated'),   -- never filled from standalone
  line_no          integer not null check (line_no >= 1),
  entity_id        integer not null references core.entity(id),
  document_id      bigint  not null references equity.filing(document_id),      -- the consolidated filing read
  period_end       date    not null,                                            -- the quarter's end
  reporting        text    not null check (reporting in ('multi','single')),    -- IsCompanyReportingMultisegmentOrSingleSegment
  description      text    not null,                                            -- as filed
  revenue          numeric,                                                     -- rupees, the quarter's SegmentRevenue as filed
  parser_version   integer not null,
  primary key (results_event_id, basis, line_no),
  check (reporting = 'multi' or revenue is null)
);
create index on equity.segment_lineup (entity_id, period_end);
create index on equity.segment_lineup (document_id);

-- One row per flag: the company's latest compared quarter against the four before it (rule_version 1: dropped, merged,
-- renamed, multi_to_single, single_to_multi, other). The segments on each side are as filed: before from the previous
-- quarter, after from the latest. Both results events are kept, so each side links to its own filing.
create table equity.segment_change (
  entity_id           integer not null references core.entity(id),
  period_end          date    not null,                                         -- the latest quarter compared
  change_no           integer not null check (change_no >= 1),
  kind                text    not null check (kind in ('dropped','merged','renamed','multi_to_single','single_to_multi','other')),
  segments_before     text[]  not null,                                         -- as filed in the previous quarter
  segments_after      text[]  not null,                                         -- as filed in the latest quarter
  revenue_ratio       numeric,                                                  -- merged: new revenue / the absent ones' sum
  latest_event_id     bigint  not null references core.event(id),
  previous_event_id   bigint  not null references core.event(id),
  previous_period_end date    not null,
  rule_version        integer not null check (rule_version >= 1),
  primary key (entity_id, period_end, change_no),
  check (previous_period_end < period_end),
  check ((kind = 'merged') = (revenue_ratio is not null)),
  check (cardinality(segments_before) + cardinality(segments_after) >= 1)
);
create index on equity.segment_change (latest_event_id);
create index on equity.segment_change (previous_event_id);

------------------------------------------------------------------ privileges
grant select on equity.segment_lineup, equity.segment_change to anon, authenticated, semantic_owner, service_role;
grant select, insert, update, delete on equity.segment_lineup, equity.segment_change to job_writer;

------------------------------------------------------------------ row-level security
alter table equity.segment_lineup enable row level security;
alter table equity.segment_change enable row level security;
create policy job_writer_all on equity.segment_lineup for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.segment_change for all to job_writer using (true) with check (true);
-- A line-up row: its filing visible (the company visible and its exchange unsuppressed), its event visible (published, source
-- unsuppressed), and the field policy shown or labelled.
create policy read_published on equity.segment_lineup for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.filing f where f.document_id = segment_lineup.document_id)
     and exists (select 1 from core.event ev where ev.id = segment_lineup.results_event_id)
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.segment_changes' and fp.status in ('shown','labelled')));
-- A flag: its company visible, both results events visible (each event's source switch), and the field policy shown or labelled.
create policy read_published on equity.segment_change for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.entity e where e.id = segment_change.entity_id)
     and exists (select 1 from core.event ev where ev.id = segment_change.latest_event_id)
     and exists (select 1 from core.event ev where ev.id = segment_change.previous_event_id)
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.segment_changes' and fp.status in ('shown','labelled')));

------------------------------------------------------------------ field policy (fails closed; seeded hidden)
insert into core.field_policy (field_key, status, label, reason) values
  ('equity.segment_changes', 'hidden', 'As filed',
   'Held back until a judge session has read every flag against both filings and Anand flips it (Plan 05 §5; DD-03, #411)');

------------------------------------------------------------------ the api view (security_invoker; list queries check their count against the 1,000-row cap)
-- Per company with a stored line-up: its latest compared quarter and either one row per flag (state 'changed') or one row with
-- the state: 'single_segment' (one reportable segment, as filed), 'none_flagged' (the latest quarter and the four before it
-- were compared and no segment of the four is missing from the latest) or 'not_compared' (one of the four earlier quarters has
-- no line-up). Only filed (not superseded) announcements count. Each side of a flag links to its own filing. Gated by
-- equity.segment_changes.
create view api.segment_changes with (security_invoker = true) as
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

grant select on api.segment_changes to anon, authenticated, service_role;

------------------------------------------------------------------ comments
comment on table equity.segment_lineup is 'One row per reportable segment of a quarter''s consolidated results filing (the latest version on a filed announcement): description as filed and the quarter''s segment revenue in rupees; a single-segment filer has one row with no revenue. Reconciling rows filed on the segment axis are not stored. Written by equity.segment_changes.';
comment on table equity.segment_change is 'One row per segment line-up flag: the company''s latest compared quarter against the four before it (kind dropped, merged, renamed, multi_to_single, single_to_multi or other), the segments before (previous quarter) and after (latest quarter) as filed, both results events and the rule version. Visitors reach it only through api.segment_changes.';
comment on view api.segment_changes is 'Changes in a company''s reportable segments, from its consolidated quarterly results as filed: for its latest compared quarter, one row per flag (state changed: the kind, the segments before and after, each side linked to its filing) or one row with the state (single_segment, none_flagged, not_compared with the reason). Never a motive. Gated by equity.segment_changes.';
