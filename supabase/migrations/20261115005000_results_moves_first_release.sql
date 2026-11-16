-- 0050: results-day moves tied to the first release of a quarter's results (issue #573, job 2B-16d; Spec 02B §5.12;
-- #567's re-judge, finding d). One `create or replace view api.results_moves`: the columns (names, order, types), the gate,
-- the left-out reasons, the grants and security_invoker are 0044's, unchanged. Only which filing carries a call's
-- `call_slug` and `fiscal_label` changes: before, the filing the call was linked to (`earnings_call.results_event_id`),
-- which after a revision is the later version (Asian Paints Q4 FY25: the -r2 of 14 Jun 2025, not the results of
-- 8 May 2025); now the first release of that quarter. The linked later version keeps its rows with both null.
-- The company filter's pushdown (#343, #446) stays: the tie is a lateral subquery keyed on the filing's own entity.

create or replace view api.results_moves with (security_invoker = true) as
with g as (
  select exists (select 1 from core.field_policy f
                  where f.field_key = 'equity.results_move' and f.status in ('shown','labelled')) as ok
), w(window_key) as (
  values ('d1'), ('d5')
), idx as (
  select id from core.series where key = 'yf.^NSEI'
)
select n.slug as company_slug, n.name as company_name, n.currency,
       e.slug as results_slug, cl.call_slug, cl.fiscal_label,
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
  left join lateral (
    -- the call whose linked filing's quarter was first released by e: the quarter is the set of this company's published
    -- results filings sharing a period_end (equity.filing) with the linked one, the first release its earliest occurs_at
    -- (ties: the lowest event id); a filing with no equity.filing row is its own quarter. One call per row: a call linked to
    -- e itself first, then the lowest call event id.
    select ce.slug as call_slug, ec.fiscal_label
      from equity.earnings_call ec
      join core.event l on l.id = ec.results_event_id
                       and l.event_type = 'results_filing' and l.status in ('filed','superseded') and l.is_published
      join core.event ce on ce.id = ec.event_id
     where ec.entity_id = e.entity_id
       and e.id = (select q.id
                     from core.event q
                    where q.entity_id = l.entity_id and q.event_type = 'results_filing'
                      and q.status in ('filed','superseded') and q.is_published
                      and (q.id = l.id
                           or exists (select 1 from equity.filing f1
                                        join equity.filing f2 on f2.entity_id = f1.entity_id and f2.period_end = f1.period_end
                                       where f1.event_id = l.id and f2.event_id = q.id))
                    order by q.occurs_at, q.id
                    limit 1)
     order by (ec.results_event_id = e.id) desc, ec.event_id
     limit 1
  ) cl on true
  left join core.event_reaction r on r.event_id = e.id and r.entity_id = e.entity_id
                                 and r.window_key = w.window_key and r.change_unit = 'pct'
  left join core.event_reaction ir on ir.event_id = e.id and ir.series_id = (select id from idx)
                                  and ir.window_key = w.window_key and ir.change_unit = 'pct'
 where g.ok;

comment on view api.results_moves is 'One row per company, results filing (from 1 Jan 2025) and window (d1: the last close before the release to the first close after it; d5: to the fifth close after): the company''s price move, the Nifty 50''s and the excess (company minus Nifty 50, percentage points, only where both have the same pre and post dates). A call is tied to the first release of its quarter''s results, not to a later revision. A window that is left out has no figure and a reason (waiting, held_factor, dividend, no_price, dates_differ, no_index). Price moves excluding dividends; not a forecast. No row while the field policy equity.results_move is hidden.';
