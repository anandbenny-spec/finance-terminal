-- Synthetic rows for the disposable #620 test database.
-- The call-summary gate stays in its seeded state. A stored call preceded by
-- a newer website-only call gives a default that differs from the newest call.
begin;
create role calls_reader_test login in role calls_artifact_reader;
create role calls_worker_test login in role calls_artifact_worker;
insert into core.entity (kind, slug, name, market, currency, is_published)
values ('company', 'calls-fixture', 'Calls Fixture Ltd', 'IN', 'INR', true),
       ('company', 'calls-control', 'Calls Control Ltd', 'IN', 'INR', true),
       ('company', 'calls-empty', 'Calls Empty Ltd', 'IN', 'INR', true);
insert into equity.company (entity_id)
select id from core.entity where slug in ('calls-fixture', 'calls-control', 'calls-empty');

insert into core.source (key, name, attribution) values
  ('calls_fixture_event', 'Fixture event source', 'Fixture event source'),
  ('calls_fixture_document', 'Fixture document source', 'Fixture document source');

insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
select 'earnings_call', e.id, v.slug, 'Synthetic call', v.period_end::date, v.status,
       (select id from core.source where key = v.source), v.public
from (values
  ('calls-fixture', 'q1-fy27', '2026-06-30', 'stored', 'calls_fixture_event', true),
  ('calls-fixture', 'q2-fy27', '2026-09-30', 'on_company_website', 'calls_fixture_event', true),
  ('calls-fixture', 'q3-fy27', '2026-12-31', 'stored', 'calls_fixture_event', false),
  ('calls-control', 'q4-fy26', '2026-03-31', 'on_company_website', 'bse', true)
) v(company, slug, period_end, status, source, public)
join core.entity e on e.slug = v.company;

insert into core.document
  (doc_type, entity_id, event_id, title, source_id, external_key, source_url, published_at,
   char_count, text_sha256, chunked_at, is_published)
select 'call_transcript', e.entity_id, e.id, 'Synthetic transcript',
       (select id from core.source where key = 'calls_fixture_document'),
       'calls620-' || e.slug, 'https://example.com/' || e.slug || '.pdf',
       e.starts_on, char_length(t.body), sha256(convert_to(t.body, 'UTF8')), now(), true
from core.event e cross join (values ('The fixture evidence quote is revenue of 1,234 crore.')) t(body)
where e.event_type = 'earnings_call' and e.slug in ('q1-fy27', 'q3-fy27');

insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
select id, 0, 0, char_count, 'Q&A', 'CFO', 'The fixture evidence quote is revenue of 1,234 crore.'
from core.document where external_key like 'calls620-%';
insert into equity.transcript_page (document_id, pdf_page, char_start, char_end)
select id, 1, 0, char_count from core.document where external_key like 'calls620-%';

insert into equity.earnings_call
  (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source,
   filed_at, exchange, route, transcript_document_id, words, unknown_role_share)
select e.id, e.entity_id, upper(replace(e.slug, '-', ' ')), e.starts_on, e.slug, 'results_link',
       e.starts_on + 25, case when c.slug = 'calls-control' then 'BSE' else 'NSE' end,
       'company_link', d.id, 20, 0
from core.event e join core.entity c on c.id = e.entity_id
left join core.document d on d.event_id = e.id
where e.event_type = 'earnings_call' and c.slug in ('calls-fixture', 'calls-control');

insert into core.ai_output (task, document_id, content, body, model, prompt_key, prompt_version, input_sha256)
select 'guidance_extraction', id, '{}', 'Calls 620 fixture output', 'fixture', 'guidance_extraction', 'v1', sha256('fixture'::bytea)
from core.document where external_key in ('calls620-q1-fy27', 'calls620-q3-fy27');
insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
select o.id, 1, '/figures/0', 'document_span', d.id, 0, d.char_count, true
from core.ai_output o join core.document d on d.id = o.document_id where o.body = 'Calls 620 fixture output';
update core.ai_output set status = 'published' where body = 'Calls 620 fixture output';

insert into equity.stated_figure
  (call_event_id, entity_id, ai_output_id, citation_no, document_id, char_start, char_end,
   metric, metric_label, metric_text, value, unit, period_kind, period_start, period_end, basis)
select e.id, e.entity_id, o.id, 1, d.id, 0, d.char_count,
       'revenue', 'Fixture evidence figure', 'Revenue', 1234, 'inr', 'quarter', e.starts_on - 89, e.starts_on, 'consolidated'
from core.ai_output o join core.document d on d.id = o.document_id join core.event e on e.id = d.event_id
where o.body = 'Calls 620 fixture output';

update core.field_policy set status = 'labelled' where field_key = 'ai.guidance_extraction';
commit;
