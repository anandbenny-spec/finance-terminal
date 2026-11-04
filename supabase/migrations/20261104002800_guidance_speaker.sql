-- 0028: guidance speaker role (issue #280, job 2B-13a; Spec 02B.1 C4, option C, Anand, 4 Nov 2026).
-- Stores which list a guidance target belongs to: 'management' (extracted and scored) or 'unknown' (a speaker whose role the
-- rules of Spec 02B §5.3 couldn't read: listed separately, never scored, never in a hit rate, never a core.forecast link).
-- The database refuses an outcome row or a forecast-series link for an unknown-speaker target. Both views are 0027's text
-- with one filter added each. No default on equity.guidance.speaker_role, on purpose: the writer (#176) must state the role.
-- Fails on a database where equity.guidance has rows (none exist on dev or production on 4 Nov 2026); never add a default.

alter table equity.guidance
  add column speaker_role text not null check (speaker_role in ('management','unknown')),
  add constraint guidance_forecast_management check (forecast_series_id is null or speaker_role = 'management'),
  add constraint guidance_id_speaker_role unique (id, speaker_role);

alter table equity.guidance_outcome
  add column speaker_role text not null default 'management' check (speaker_role = 'management'),
  add constraint guidance_outcome_management_only
      foreign key (guidance_id, speaker_role) references equity.guidance (id, speaker_role);

------------------------------------------------------------------ api.call_guidance
-- One row per target (duplicates left out) of a published guidance_extraction output, only while ai.guidance_extraction is
-- shown or labelled. The outcome columns are null unless equity.guidance_outcome is shown or labelled. No chunk text and
-- no transcript text: a quote comes only through api.ai_citations (ai_output_id, citation_no).
create or replace view api.call_guidance with (security_invoker = true) as
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
where g.ai_ok and x.duplicate_of is null and x.speaker_role = 'management';

------------------------------------------------------------------ api.guidance_hit_rates
-- One row per company, per peer group and one for the Nifty 50 (the latest snapshot's members; only once it has a target), over counted targets
-- scored delivered or missed, each target once, on its earliest stored statement. rate = delivered / n as a fraction,
-- null when n < 5 (Spec 02B §5.10). The window is data: first_stated_from, periods_to. No rows while
-- equity.guidance_outcome (or ai.guidance_extraction) is hidden; peer-group rows also need equity.peer_group.
create or replace view api.guidance_hit_rates with (security_invoker = true) as
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
    and x.speaker_role = 'management'
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
comment on column equity.guidance.speaker_role is 'management or unknown: the role of the transcript turn the quote lies in, by the rules of Spec 02B §5.3 (never inferred). Unknown rows are listed separately and never scored (Spec 02B.1).';
comment on view api.call_guidance is 'One row per guidance target management stated on an earnings call (AI-extracted, cited): the target as stated and, only once equity.guidance_outcome is released, its scored outcome. Never transcript text. Management turns only: targets stated by speakers whose role couldn''t be read are listed separately and never scored.';
comment on view api.guidance_hit_rates is 'Delivered and missed counts per company, peer group and the Nifty 50, over counted targets on their earliest statement; rate is null below 5 targets. Empty while equity.guidance_outcome is hidden. Informational only. Management turns only: targets stated by speakers whose role couldn''t be read are listed separately and never scored.';
