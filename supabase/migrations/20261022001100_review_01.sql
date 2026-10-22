-- 0011: fixes from the external review (Review 01, applied 22 Oct 2026). The frozen tables get one column
-- (core.task_freshness.stale_after) and one check (integer hawk/dove scores); the rest are views and comments.
-- Module 1's seed migration (Spec 01 §1.3) follows as 0012.

------------------------------------------------------------------ F17: freshness turns stale by itself
-- core.task_freshness.status is written only by the runner, so if the scheduled runs stop it stays 'ok'.
-- The runner copies each task's overdue_after here; the public view compares it with now().
alter table core.task_freshness add column stale_after interval;
update core.task_freshness f set stale_after = t.overdue_after from ops.task t where t.key = f.task_key;

create or replace view api.task_freshness with (security_invoker = true) as
select task_key, label, last_checked_at, last_success_at,
       case when status = 'ok' and now() > last_success_at + stale_after then 'stale' else status end as status,
       next_due_at
from core.task_freshness;

------------------------------------------------------------------ F15: hawk/dove scores are whole steps
-- The task's JSON schema allows only -2..+2 integers; the database refuses anything else for these tasks.
alter table core.ai_output add constraint hawk_dove_score_integer
  check (task not in ('hawk_dove_score', 'member_hawk_dove_score') or score in (-2, -1, 0, 1, 2));

------------------------------------------------------------------ F22: both bounds of the Fed range
-- Same view as 0006 with two columns appended (create or replace can only add columns at the end).
create or replace view api.policy_meetings with (security_invoker = true) as
select ev.slug, ev.title, b.slug as bank_slug, b.short_name as bank, ev.status,
       ev.starts_on, coalesce(ev.ends_on, ev.starts_on) as decision_date, ev.occurs_at as decision_at,
       m.kind, m.is_rate_decision, m.meeting_no, m.has_press_conference, m.minutes_due_on,
       pr.value_pct  as policy_rate_pct,
       pr.change_bps as policy_rate_change_bps,
       st.label as stance, st.body as stance_text, fst.label as stance_display_label,
       hd.score as hawk_dove_score, fhd.label as hawk_dove_display_label,
       (select count(*) from policy.vote v where v.meeting_event_id = m.event_id and v.is_dissent) as dissent_count,
       src.attribution,
       fl.value_pct as fed_target_lower_pct,
       fu.value_pct as fed_target_upper_pct
from policy.meeting m
join core.event ev on ev.id = m.event_id
join core.entity b on b.id = ev.entity_id
left join core.source src on src.id = ev.source_id
left join policy.rate_setting pr on pr.meeting_event_id = m.event_id
       and pr.instrument = case b.slug when 'fed' then 'fed_target_upper' else 'repo' end
left join policy.rate_setting fl on fl.meeting_event_id = m.event_id and fl.instrument = 'fed_target_lower'
left join policy.rate_setting fu on fu.meeting_event_id = m.event_id and fu.instrument = 'fed_target_upper'
left join core.field_policy fst on fst.field_key = 'ai.stance_extraction' and fst.status in ('shown','labelled')
left join core.ai_output st on fst.field_key is not null and st.event_id = m.event_id
       and st.task = 'stance_extraction' and st.status = 'published' and st.scope = ''
left join core.field_policy fhd on fhd.field_key = 'ai.hawk_dove_score' and fhd.status in ('shown','labelled')
left join core.ai_output hd on fhd.field_key is not null and hd.event_id = m.event_id
       and hd.task = 'hawk_dove_score' and hd.status = 'published' and hd.scope = '';

------------------------------------------------------------------ F24: latest values in one small query
-- The ticker tape needs two points per series; reading them from api.observations runs into the Data
-- API's 1,000-row cap (supabase/config.toml max_rows), which truncates without an error.
create view api.series_latest with (security_invoker = true) as
select s.key as series_key, s.name, s.unit, s.frequency,
       l.obs_date as last_date, l.value as last_value,
       p.obs_date as prev_date, p.value as prev_value,
       l.value - p.value as change
from core.series s
join lateral (select o.obs_date, o.value from core.observation o
              where o.series_id = s.id order by o.obs_date desc limit 1) l on true
left join lateral (select o.obs_date, o.value from core.observation o
                   where o.series_id = s.id and o.obs_date < l.obs_date order by o.obs_date desc limit 1) p on true;

------------------------------------------------------------------ N2: step series as change points
-- FRED's DFEDTARU/L are daily, seven days a week (~2,850 rows each since 2019), so a chart reading them
-- from api.observations is cut at 1,000 rows without an error. A step series only needs the days its value
-- changed, plus its latest observation so the line runs to today. Partitioned by series key, so a filter
-- on series_key is applied before the window function.
create view api.observation_steps with (security_invoker = true) as
select series_key, obs_date, value, next_date is null as is_latest
from (select s.key as series_key, o.obs_date, o.value,
             lag(o.value) over w as prev_value,
             lead(o.obs_date) over w as next_date
      from core.observation o
      join core.series s on s.id = o.series_id
      window w as (partition by s.key order by o.obs_date)) x
where prev_value is distinct from value or next_date is null;

grant select on api.series_latest, api.observation_steps to anon, authenticated, service_role;

------------------------------------------------------------------ F14, F23: comments the text-to-SQL prompt is built from
comment on column semantic.policy_meetings.stance is
  'RBI only: the MPC states a stance (AI-extracted, cited to the resolution). Null for Fed meetings until the Fed guidance task passes its evaluation (Spec 01 D2).';
comment on column semantic.policy_meetings.dissent_count is
  'Members who dissented on the rate or (RBI) on the stance; each member counted once';
comment on column semantic.policy_votes.is_dissent is
  'True if the member dissented on the rate or (RBI) on the stance. Rate dissent: voted_change_bps differs from the decision. Stance dissent: stance_vote differs from the adopted stance. A Fed dissent not about the rate has voted_change_bps null.';
