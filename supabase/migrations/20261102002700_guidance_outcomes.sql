-- 0027: guidance outcomes (issue #266, job 2B-14a; Spec 02B §3.3's `guidance_outcome` row, §3.5, §5.10 and §7's
-- `call_guidance` and `guidance_hit_rates`; Plan 04b decision P7, Anand's standing delegation, 2 Nov 2026).
-- Named `guidance_outcomes`, not `earnings_scores`: it holds only guidance's table, its field policy and two api views.
-- `call_tone` and `call_remarks_change` stay after 18 Nov, in a migration their jobs (#175, #180) name. No `semantic`
-- views before 18 Nov (Spec 02B §13.11 L10). The scoring task that fills the table is #177; the panel is #267.
-- Equity conventions as 0019 and 0023: RLS on, the `job_writer_all` policy, explicit grants, `delete` for jobs on the
-- D table. Visitor policies use inline subqueries only (0001 revokes EXECUTE from PUBLIC). Views: security_invoker, gated
-- columns nulled by a CASE from core.field_policy (Arch 01 §1.4). Nothing here is coloured or reads as a verdict.

------------------------------------------------------------------ §3.3 the table
create table equity.guidance_outcome (
  guidance_id   bigint primary key references equity.guidance(id),              -- one row per guidance row
  target_key    text    not null,                                               -- entity, metric, basis, target_start, target_end
  is_first      boolean not null,                                               -- the earliest stored statement of its target
  revision      text    not null check (revision in ('first','raised','cut','reiterated','restated')),
  horizon_share numeric,                                                        -- days from the period's start to the statement ÷ the period's days
  counted       boolean not null default false,                                 -- in the hit rate
  outcome       text    not null check (outcome in ('delivered','missed','pending','not_scored')),
  relation      text    check (relation in ('within','above','below','at_or_past','short_of','moved_as_said','moved_against')),
  actual        numeric,
  base          numeric,
  reason        text,
  inputs        jsonb   not null,                                               -- every filed row and value used
  map_version   text,
  rule_version  text    not null
);
create index on equity.guidance_outcome (target_key);

grant select on equity.guidance_outcome to anon, authenticated, semantic_owner, service_role;
grant select, insert, update, delete on equity.guidance_outcome to job_writer;   -- D: scores are re-made when a rule version changes

alter table equity.guidance_outcome enable row level security;
create policy job_writer_all on equity.guidance_outcome for all to job_writer using (true) with check (true);
-- Its guidance visible: the guidance row's policy brings its call (event and company, so the event's source switch) and its
-- published output; the document join adds the document's source switch (both sources unsuppressed).
create policy read_published on equity.guidance_outcome for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.guidance g join core.document d on d.id = g.document_id
                 where g.id = guidance_id));

------------------------------------------------------------------ §3.5 field policy (fails closed; seeded hidden)
insert into core.field_policy (field_key, status, label, reason) values
  ('equity.guidance_outcome', 'hidden', 'derived',
   'Held back until all three hand checks pass (Spec 02B §5.11; 2B-15 records them, 2B-22 flips it)');

------------------------------------------------------------------ api.call_guidance
-- One row per target (duplicates left out) of a published guidance_extraction output, only while ai.guidance_extraction is
-- shown or labelled. The outcome columns are null unless equity.guidance_outcome is shown or labelled. No chunk text and
-- no transcript text: a quote comes only through api.ai_citations (ai_output_id, citation_no).
create view api.call_guidance with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.guidance_extraction'), false) as ai_ok,
         max(label)  filter (where field_key = 'ai.guidance_extraction' and status in ('shown','labelled')) as ai_label,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false) as out_ok,
         max(label)  filter (where field_key = 'equity.guidance_outcome' and status in ('shown','labelled')) as out_label,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer
  from core.field_policy
  where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.peer_group')
)
select e.slug as company_slug, e.name as company_name, e.short_name, e.market, e.currency,
       c.peer_group_key,
       case when g.peer then pg.label end as peer_group_label,
       ev.slug as call_slug, ec.fiscal_label, ec.call_date,
       x.metric, x.metric_label, x.kind, x.value, x.low, x.high, x.unit, x.direction, x.comparison,
       x.period_text, x.period_kind, x.target_start, x.target_end, x.basis, x.growth_basis, x.scope_text,
       x.vintage_date, x.ai_output_id, x.citation_no, x.not_scored_reason,
       g.ai_label as extraction_label,
       case when g.out_ok then g.out_label end     as outcome_label,
       case when g.out_ok then o.is_first end      as is_first,
       case when g.out_ok then o.revision end      as revision,
       case when g.out_ok then o.horizon_share end as horizon_share,
       case when g.out_ok then o.counted end       as counted,
       case when g.out_ok then o.outcome end       as outcome,
       case when g.out_ok then o.relation end      as relation,
       case when g.out_ok then o.actual end        as actual,
       case when g.out_ok then o.base end          as base,
       case when g.out_ok then o.reason end        as reason,
       case when g.out_ok then fs.call_slug end    as first_call_slug,
       case when g.out_ok then fs.vintage_date end as first_stated_on
from equity.guidance x
cross join g
join core.ai_output ai on ai.id = x.ai_output_id and ai.task = 'guidance_extraction' and ai.status = 'published'
join equity.earnings_call ec on ec.event_id = x.call_event_id
join core.event ev on ev.id = ec.event_id
join core.entity e on e.id = x.entity_id
left join equity.company c on c.entity_id = x.entity_id
left join equity.peer_group pg on pg.key = c.peer_group_key
left join equity.guidance_outcome o on o.guidance_id = x.id
left join lateral (
  select ev1.slug as call_slug, g1.vintage_date
  from equity.guidance_outcome o1
  join equity.guidance g1 on g1.id = o1.guidance_id
  join core.event ev1 on ev1.id = g1.call_event_id
  where o1.target_key = o.target_key and o1.is_first
  order by g1.vintage_date, g1.id
  limit 1
) fs on true
where g.ai_ok and x.duplicate_of is null;

------------------------------------------------------------------ api.guidance_hit_rates
-- One row per company, per peer group and one for the Nifty 50 (the latest snapshot's members; only once it has a target), over counted targets
-- scored delivered or missed, each target once, on its earliest stored statement. rate = delivered / n as a fraction,
-- null when n < 5 (Spec 02B §5.10). The window is data: first_stated_from, periods_to. No rows while
-- equity.guidance_outcome (or ai.guidance_extraction) is hidden; peer-group rows also need equity.peer_group.
create view api.guidance_hit_rates with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.guidance_extraction'), false)
         and coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false) as ok,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer,
         max(label) filter (where field_key = 'equity.guidance_outcome' and status in ('shown','labelled')) as out_label
  from core.field_policy
  where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.peer_group')
), nifty as (
  select m.entity_id
  from equity.index_membership m
  join core.entity i on i.id = m.index_entity_id and i.slug = 'nifty-50'
  where m.snapshot_date = (select max(x.snapshot_date) from equity.index_membership x where x.index_entity_id = m.index_entity_id)
), t as (
  select x.entity_id, e.slug, e.name, c.peer_group_key, pg.label as peer_label,
         o.outcome, x.vintage_date, x.target_end,
         exists (select 1 from nifty n where n.entity_id = x.entity_id) as in_nifty
  from equity.guidance_outcome o
  join equity.guidance x on x.id = o.guidance_id and x.duplicate_of is null
  join core.ai_output ai on ai.id = x.ai_output_id and ai.task = 'guidance_extraction' and ai.status = 'published'
  join equity.earnings_call ec on ec.event_id = x.call_event_id
  join core.entity e on e.id = x.entity_id
  left join equity.company c on c.entity_id = x.entity_id
  left join equity.peer_group pg on pg.key = c.peer_group_key
  cross join g
  where g.ok and o.counted and o.is_first and o.outcome in ('delivered','missed')
)
select 'company'::text as scope, t.slug as scope_key, t.name as scope_label,
       count(*) filter (where t.outcome = 'delivered') as delivered,
       count(*) filter (where t.outcome = 'missed') as missed,
       count(*) as n,
       case when count(*) >= 5 then (count(*) filter (where t.outcome = 'delivered'))::numeric / count(*) end as rate,
       min(t.vintage_date) as first_stated_from, max(t.target_end) as periods_to,
       (select out_label from g) as outcome_label
from t group by t.slug, t.name
union all
select 'peer_group', t.peer_group_key, t.peer_label,
       count(*) filter (where t.outcome = 'delivered'), count(*) filter (where t.outcome = 'missed'), count(*),
       case when count(*) >= 5 then (count(*) filter (where t.outcome = 'delivered'))::numeric / count(*) end,
       min(t.vintage_date), max(t.target_end), (select out_label from g)
from t where t.peer_group_key is not null and (select peer from g)
group by t.peer_group_key, t.peer_label
union all
select 'nifty50', 'nifty-50', 'Nifty 50',
       count(t.outcome) filter (where t.outcome = 'delivered'), count(t.outcome) filter (where t.outcome = 'missed'), count(t.outcome),
       case when count(t.outcome) >= 5 then (count(t.outcome) filter (where t.outcome = 'delivered'))::numeric / count(t.outcome) end,
       min(t.vintage_date), max(t.target_end), (select out_label from g)
from g left join t on t.in_nifty
where g.ok
group by g.ok
having count(t.outcome) > 0;   -- nothing visible (a suppressed source, no counted target yet) leaves no row at all

grant select on api.call_guidance, api.guidance_hit_rates to anon, authenticated, service_role;

------------------------------------------------------------------ comments
comment on table equity.guidance_outcome is 'One row per guidance row: how its target was scored against the filed figures (derived). Hidden until the hand checks pass (field policy equity.guidance_outcome); inputs holds every filed row and value used.';
comment on view api.call_guidance is 'One row per guidance target management stated on an earnings call (AI-extracted, cited): the target as stated and, only once equity.guidance_outcome is released, its scored outcome. Never transcript text.';
comment on view api.guidance_hit_rates is 'Delivered and missed counts per company, peer group and the Nifty 50, over counted targets on their earliest statement; rate is null below 5 targets. Empty while equity.guidance_outcome is hidden. Informational only.';
