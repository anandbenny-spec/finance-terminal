-- 0044: results-day moves (module 2, release 2B; issue #471 part 1, job 2B-16b; Spec 02B §5.12, Spec 02A §5.10).
-- One `api` view, no table, no change to any other view: api.results_moves, one row per company × results_filing event
-- (from 1 Jan 2025) × window (d1, d5), with the company's move, the Nifty 50's and the excess over it, read from
-- core.event_reaction (written by equity.results_reactions, #179). The pattern is 0026's api.rate_decision_moves.
-- Gate: the field policy `equity.results_move` (seeded hidden by 0030). While it is hidden the view returns NO row
-- (a `where g.ok`, as the page then reads nothing); once shown or labelled it returns rows.
-- Left-out windows keep their row with every figure empty and a reason. equity.results_reactions stores no exclusion
-- (it writes no row for a window it leaves out, #179), so the reason is derived here from what is stored, in the
-- engine's own order:
--   waiting      neither the company nor the Nifty 50 has a row and the release is under 14 days old (no close yet)
--   held_factor  the company has no row, the Nifty 50 has one, and a held adjustment factor has an ex-date after the
--                window's pre date (the adjusted close is null behind it; equity.price_adjusted)
--   dividend     the same, and the company's ex-dividend date falls in the Nifty 50's window (pre < ex <= post)
--   no_price     the company has no row for any other reason (for instance no close before the release)
--   dates_differ both have a row but over different pre or post dates: no excess
--   no_index     the company has a row and the Nifty 50 has none
-- The company filter must reach core.entity and core.event before anything is computed (#343, #446): the view is a
-- plain join (no aggregate, no window), so a PostgREST `company_slug = ...` is pushed down to core.entity's slug.

create view api.results_moves with (security_invoker = true) as
with g as (
  select exists (select 1 from core.field_policy f
                  where f.field_key = 'equity.results_move' and f.status in ('shown','labelled')) as ok
), w(window_key) as (
  values ('d1'), ('d5')
), idx as (
  select id from core.series where key = 'yf.^NSEI'
)
select n.slug as company_slug, n.name as company_name, n.currency,
       e.slug as results_slug, ce.slug as call_slug, ec.fiscal_label,
       e.occurs_at, w.window_key,
       r.pre_date, r.post_date,
       r.change as own_change_pct,
       ir.change as nifty_change_pct,
       case when r.event_id is not null and ir.event_id is not null
             and r.pre_date = ir.pre_date and r.post_date = ir.post_date
            then r.change - ir.change end as excess_pct,
       case
         when r.event_id is not null and ir.event_id is not null
              and r.pre_date = ir.pre_date and r.post_date = ir.post_date then null
         when r.event_id is not null and ir.event_id is not null then 'dates_differ'
         when r.event_id is not null then 'no_index'
         when ir.event_id is null then case when e.occurs_at > now() - interval '14 days' then 'waiting' else 'no_price' end
         when exists (select 1 from equity.adjustment_factor h
                       where h.entity_id = e.entity_id and h.status = 'held' and h.ex_date > ir.pre_date) then 'held_factor'
         when exists (select 1 from equity.corporate_action a join core.event ae on ae.id = a.event_id
                       where ae.entity_id = e.entity_id and a.kind = 'dividend'
                         and a.ex_date > ir.pre_date and a.ex_date <= ir.post_date) then 'dividend'
         else 'no_price'
       end as left_out_reason,
       (select attribution from core.source where key = 'nse') as attribution
  from g
  join core.event e on e.event_type = 'results_filing' and e.status in ('filed','superseded') and e.is_published
                   and e.occurs_at >= timestamptz '2025-01-01 00:00:00+05:30'
  join core.entity n on n.id = e.entity_id
  join equity.company c on c.entity_id = e.entity_id
  cross join w
  left join equity.earnings_call ec on ec.results_event_id = e.id
  left join core.event ce on ce.id = ec.event_id
  left join core.event_reaction r on r.event_id = e.id and r.entity_id = e.entity_id
                                 and r.window_key = w.window_key and r.change_unit = 'pct'
  left join core.event_reaction ir on ir.event_id = e.id and ir.series_id = (select id from idx)
                                  and ir.window_key = w.window_key and ir.change_unit = 'pct'
 where g.ok;

grant select on api.results_moves to anon, authenticated, service_role;

comment on view api.results_moves is 'One row per company, results filing (from 1 Jan 2025) and window (d1: the last close before the release to the first close after it; d5: to the fifth close after): the company''s price move, the Nifty 50''s and the excess (company minus Nifty 50, percentage points, only where both have the same pre and post dates). A window that is left out has no figure and a reason (waiting, held_factor, dividend, no_price, dates_differ, no_index). Price moves excluding dividends; not a forecast. No row while the field policy equity.results_move is hidden.';
