-- 0056 · The Earnings calls tab's reads touch fewer rows as a visitor (issue #620; #185's flip of ai.call_summary held on
-- 17 Nov 2026 when several Calls tabs first-loaded together timed out on api.call_guidance and api.call_figures).
-- Migration 0056 claimed on #30; approved by Anand in #620's session (17 Nov 2026): he pushes 0055 then 0056 to dev, and 0052 to
-- 0056 to production in number order.
-- Two view bodies replaced, same columns in the same order, same rows, same gates, security_invoker, grants and comments; no table,
-- index, policy, function or field-policy change.
--
-- Why (#620 part 1, measured on dev as anon with production's fields, dev-sql 0009): one cold Calls tab touched 150k to 215k
-- buffers, 85 to 95% of it the visitor's row-level checks, which run once for every row a view scans, kept or not:
--  * api.call_guidance computed every target's first statement (the lateral `fs`: guidance_outcome, guidance and event, each with
--    its row-level checks) on every row, though the two columns it feeds (first_call_slug, first_stated_on) are null unless
--    equity.guidance_hit_rate is shown or labelled, and it is hidden on production (0048, #532). Now `g.hit_ok` sits in the
--    lateral's WHERE: it refers only to the outer row, so the planner makes it a one-time filter and the lookup is not run at
--    all while the key is hidden. While it is shown the lookup is 0048's, row for row. (30k to 56k buffers a company before.)
--  * api.citation_pages found a citation's page with a range join on equity.transcript_page (`char_start <= x < char_end`), which
--    reads every page from the transcript's start up to the right one: about 9 pages read for each kept (dev, 17 Nov 2026), each
--    with the document's checks. Now each page is one backward index probe on (document_id, char_start): the last page starting at
--    or before the span's first (or last) code point, kept only if the span's code point lies before that page's end. The page
--    map's ranges never overlap (Spec 02B 13.4; dev on 17 Nov 2026: 7,210 pages, no overlap, no gap), so this is the same page,
--    and a code point no page holds still gives no row. api.call_figures reads it once per figure and gains the same.
--    (43k to 77k buffers for a company's quoted outputs before.)
-- The rule is 0043's and 0046's: a visitor's read touches only the rows it keeps. Not changed: api.ai_citations (a core view,
-- Spec 02B 13.11 L3) and api.results_moves (#577).
--
-- Decided by Claude (standing delegation, 24 Oct 2026; recorded on #620):
--  * The probe keeps 0035's other joins and filters as they are (output published, its field policy, the document's link and
--    source, the call's event and source, call_summary_version_ok), so the gates are untouched. Alternative: a restructured body
--    starting from the output's page map, which #489 measured slower for few outputs.
--  * `limit 1` with `order by char_start desc` and the char_end check outside the probe, so a span past the last page's end costs
--    one probe, not a scan back through every page. Alternative: the char_end check inside the probe (same rows, a longer scan in
--    that case only).
--  * The span's last code point is written `p.char_start < c.char_end` and `c.char_end <= p2.char_end`, not 0035's
--    `c.char_end - 1 >= p.char_start` and `c.char_end - 1 < p.char_end` (the same for integers). Under a visitor's row-level
--    security Postgres 17 won't put a non-leakproof function (`-`, int4mi) into an index condition ahead of the policy, so with
--    `c.char_end - 1` the probe scanned back from the transcript's last page and ran the policy on every page (CI on PR #625:
--    9 pages thrown away in the test's 12 citations). Alternative: keep the subtraction (the same rows, the long scan).

------------------------------------------------------------------ api.call_guidance (body: 0048's, the first-statement lookup gated)
create or replace view api.call_guidance with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'ai.guidance_extraction'), false) as ai_ok,
         max(label)  filter (where field_key = 'ai.guidance_extraction' and status in ('shown','labelled')) as ai_label,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false) as out_ok,
         max(label)  filter (where field_key = 'equity.guidance_outcome' and status in ('shown','labelled')) as out_label,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_outcome'), false)
         and coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.guidance_hit_rate'), false) as hit_ok,
         coalesce(bool_or(status in ('shown','labelled')) filter (where field_key = 'equity.peer_group'), false) as peer
  from core.field_policy
  where field_key in ('ai.guidance_extraction','equity.guidance_outcome','equity.guidance_hit_rate','equity.peer_group')
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
       case when g.hit_ok then o.is_first end      as is_first,
       case when g.hit_ok then o.revision end      as revision,
       case when g.hit_ok then o.horizon_share end as horizon_share,
       case when g.hit_ok then o.counted end       as counted,
       case when g.out_ok then o.outcome end       as outcome,
       case when g.out_ok then o.relation end      as relation,
       case when g.out_ok then o.actual end        as actual,
       case when g.out_ok then o.base end          as base,
       case when g.out_ok then o.reason end        as reason,
       case when g.hit_ok then fs.call_slug end    as first_call_slug,
       case when g.hit_ok then fs.vintage_date end as first_stated_on
from equity.guidance x
cross join g
join core.ai_output ai on ai.id = x.ai_output_id and ai.task = 'guidance_extraction' and ai.status = 'published'
join equity.earnings_call ec on ec.event_id = x.call_event_id
join core.event ev on ev.id = ec.event_id
join core.entity e on e.id = x.entity_id
left join equity.company c on c.entity_id = x.entity_id
left join equity.peer_group pg on pg.key = c.peer_group_key
left join equity.guidance_outcome o on o.guidance_id = x.id
left join lateral (   -- the target's first statement: read only while equity.guidance_hit_rate is shown (0056)
  select ev1.slug as call_slug, g1.vintage_date
  from equity.guidance_outcome o1
  join equity.guidance g1 on g1.id = o1.guidance_id
  join core.event ev1 on ev1.id = g1.call_event_id
  where g.hit_ok and o1.target_key = o.target_key and o1.is_first   -- g.hit_ok first: a one-time filter, never per row
  order by g1.vintage_date, g1.id
  limit 1
) fs on true
where g.ai_ok and x.duplicate_of is null and x.speaker_role = 'management';

------------------------------------------------------------------ api.citation_pages (body: 0035's, each page by one index probe)
create or replace view api.citation_pages with (security_invoker = true) as
select c.ai_output_id, c.citation_no,
       p1.pdf_page as page_first,
       p2.pdf_page as page_last,
       regexp_replace(d.source_url, '#.*$', '') || '#page=' || p1.pdf_page as page_url
from core.ai_citation c
join core.ai_output o on o.id = c.ai_output_id and o.status = 'published'
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
join core.document d on d.id = c.document_id and d.source_url is not null
join core.source ds on ds.id = d.source_id and not ds.is_suppressed
left join core.event ev on ev.id = d.event_id
left join core.source es on es.id = ev.source_id
cross join lateral (   -- the page holding the span's first code point: the last page that starts at or before it
  select p.pdf_page, p.char_end
  from equity.transcript_page p
  where p.document_id = c.document_id and p.char_start <= c.char_start
  order by p.char_start desc
  limit 1
) p1
cross join lateral (   -- the page holding the span's last code point (c.char_end - 1): the last page that starts before c.char_end
  select p.pdf_page, p.char_end
  from equity.transcript_page p
  where p.document_id = c.document_id and p.char_start < c.char_end
  order by p.char_start desc
  limit 1
) p2
where c.verified and c.kind = 'document_span'
  and c.char_start < p1.char_end and c.char_end <= p2.char_end
  and (o.task <> 'call_summary' or core.call_summary_version_ok(o.prompt_version))
  and (d.event_id is null or (ev.id is not null and es.id is not null and not es.is_suppressed));

------------------------------------------------------------------ comments (restated unchanged; grants are kept by create or replace)
comment on view api.call_guidance is 'One row per guidance target management stated on an earnings call (AI-extracted, cited): the target as stated and, only once equity.guidance_outcome is released, its scored outcome (outcome, relation, actual, base, reason). is_first, revision, horizon_share, counted, first_call_slug and first_stated_on need the list of a call''s targets to be complete, so they also wait for equity.guidance_hit_rate. Never transcript text. Management turns only: targets stated by speakers whose role couldn''t be read are listed separately and never scored.';
comment on view api.citation_pages is 'Page of a verified transcript citation: first and last PDF page of its span and a link to the source''s own PDF at that page (never our copy). No row where the document has no page map. Never transcript text.';
