-- 0036: api.remarks_changes (issue #388, job 2B-19d; Spec 02B §7's row for `api.remarks_changes`, §5.13, §8).
-- One new view over equity.call_remarks_change (0030), nothing else: no table, policy, grant on a table or field_policy row
-- changes. The field policy `equity.remarks_change` stays `hidden`; flipping it is Anand's. Fails closed: while the field is
-- hidden, or has no row, the view returns no rows.
--  * security_invoker, so the base table's visitor policy (visible with its call) and the schemas' grants apply to the reader;
--  * both calls (this quarter's and the previous one's) must be published with their sources unsuppressed: each call's event
--    source, and its transcript document with that document's source (the same filters as api.earnings_calls);
--  * one row per call that has a previous call (the table's own shape);
--  * the sentence pairs exactly as stored: at most 5, each side at most 400 characters, never widened. They are the only
--    transcript text any api view returns, and they are shown only after Anand's ruling (CLAUDE.md §1 vs Spec 02B §5.13).

create view api.remarks_changes with (security_invoker = true) as
with g as (
  select coalesce(bool_or(status in ('shown','labelled')), false) as ok,
         max(label) filter (where status = 'labelled') as policy_label
  from core.field_policy
  where field_key = 'equity.remarks_change'
)
select e.slug       as company_slug,
       ev.slug      as call_slug,
       ec.fiscal_label,
       ec.period_end,
       ec.filed_at,
       ec.exchange,
       bev.slug     as base_call_slug,
       bec.fiscal_label as base_fiscal_label,
       bec.period_end   as base_period_end,
       r.words_base,
       r.words_new,
       r.words_added,
       r.words_removed,
       r.similarity,
       r.new_speakers,
       r.label,
       r.pairs,
       g.policy_label
from equity.call_remarks_change r
cross join g
join equity.earnings_call ec on ec.event_id = r.event_id
join core.event ev on ev.id = ec.event_id
join core.source es on es.id = ev.source_id and not es.is_suppressed
join core.entity e on e.id = ec.entity_id
join core.document d on d.id = ec.transcript_document_id
join core.source ds on ds.id = d.source_id and not ds.is_suppressed
join equity.earnings_call bec on bec.event_id = r.base_event_id
join core.event bev on bev.id = bec.event_id
join core.source bes on bes.id = bev.source_id and not bes.is_suppressed
join core.document bd on bd.id = bec.transcript_document_id
join core.source bds on bds.id = bd.source_id and not bds.is_suppressed
where g.ok;

grant select on api.remarks_changes to anon, authenticated, service_role;

comment on view api.remarks_changes is 'One row per earnings call that has a previous call: how much of the opening remarks'' wording changed against the previous quarter''s (similarity, numbers masked; words added and removed), the management speakers new to the opening, a label, and the five most-changed sentence pairs as filed (each side at most 400 characters). Derived, not AI. Empty while field policy equity.remarks_change is hidden. Informational only.';
