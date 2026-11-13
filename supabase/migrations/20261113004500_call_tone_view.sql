-- 0045: api.call_tone (issue #482, job 2B-12b; Spec 02B §7's row for `api.call_tone`, §5.7, §8).
-- One new view over equity.call_tone (0030), nothing else: no table, policy, grant on a table or field_policy row changes. The
-- field policy `ai.call_tone` stays `hidden`; flipping it is Anand's, after the judge (#482 part 2). Fails closed: while the field
-- is hidden, or has no row, the view returns no rows.
--  * security_invoker, so the base tables' visitor policies (a call is visible with its event and company) and the schemas'
--    grants apply to the reader;
--  * the call's event source, and its transcript document with that document's source, must be unsuppressed (the same filters
--    as api.earnings_calls and api.remarks_changes);
--  * the previous call (prev_event_id) is joined only if it is published with its sources unsuppressed; otherwise the previous
--    label, delta, standard error and change label come back null (a comparison with a call visitors can't see isn't shown);
--  * one row per published call that has an equity.call_tone row, held calls included (held_reason passes through, the mean is
--    null); a role-held call has no tone row and so no row here;
--  * the company's slug filters on core.entity, the first relation, so a company page's `company_slug = '…'` picks the company
--    before anything else is read (#343, #446, #469: the answer must come inside anon's 3 s);
--  * the evidence quotes are not in this view: the page reads them through api.ai_outputs (content.evidence) and
--    api.ai_citations (core.citation_quote, at most 400 code points), gated by the same field policy. No transcript text here.

create view api.call_tone with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok,
         max(label) filter (where status = 'labelled') as policy_label
  from core.field_policy
  where field_key = 'ai.call_tone'
)
select e.slug         as company_slug,
       ev.slug        as call_slug,
       ec.fiscal_label,
       ec.period_end,
       ec.call_date,
       ec.filed_at,
       ec.exchange,
       t.ai_output_id,
       t.n,
       t.mean,
       t.sd,
       t.opening_n,
       t.opening_mean,
       t.answers_n,
       t.answers_mean,
       pv.slug        as prev_call_slug,
       pv.fiscal_label as prev_fiscal_label,
       case when pv.event_id is not null then t.delta end    as delta,
       case when pv.event_id is not null then t.se_delta end as se_delta,
       case when pv.event_id is not null then t.change end   as change,
       t.held_reason,
       t.rule_version,
       g.policy_label
from core.entity e
cross join g
join equity.earnings_call ec on ec.entity_id = e.id
join equity.call_tone t on t.event_id = ec.event_id
join core.event ev on ev.id = ec.event_id
join core.source es on es.id = ev.source_id and not es.is_suppressed
join core.document d on d.id = ec.transcript_document_id
join core.source ds on ds.id = d.source_id and not ds.is_suppressed
left join lateral (   -- the previous call, only where visitors can see it
  select p.event_id, p.fiscal_label, pe.slug
  from equity.earnings_call p
  join core.event pe on pe.id = p.event_id
  join core.source pes on pes.id = pe.source_id and not pes.is_suppressed
  join core.document pd on pd.id = p.transcript_document_id
  join core.source pds on pds.id = pd.source_id and not pds.is_suppressed
  where p.event_id = t.prev_event_id
) pv on true
where g.ok;

grant select on api.call_tone to anon, authenticated, service_role;

comment on view api.call_tone is 'One row per published earnings call that has a tone score: the mean of management''s passage scores on a -2 to +2 scale with its passage count and standard deviation, the opening remarks'' and the answers'' own means and counts, the previous call and the change against it (delta, its standard error and a label: more_positive, more_negative or little_changed, null where either call is held or the previous call is not published), and the output id whose evidence quotes api.ai_outputs and api.ai_citations give. AI-scored. Empty while field policy ai.call_tone is hidden. Never transcript text. Informational only.';
