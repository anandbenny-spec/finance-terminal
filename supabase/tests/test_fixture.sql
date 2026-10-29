-- Fixture written AS job_writer (proves the job role's grants and RLS policies are enough to load data)
begin;  -- jobs load each task in one transaction; deferred checks run at commit
set role job_writer;

insert into ops.job_run (workflow, trigger, runner) values ('daily','cli','local');
insert into ops.task_run (job_run_id, task_key) select max(id), 'policy.fed_documents' from ops.job_run;

-- Three Fed meetings: one held and published, one held but unpublished (checks failed), one scheduled
insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at, status, source_id, source_url, is_published)
select 'policy_meeting', (select id from core.entity where slug='fed'), v.slug, v.title, v.s::date, v.e::date, v.at::timestamptz,
       v.st, (select id from core.source where key='fed'), 'https://www.federalreserve.gov/x', v.pub
from (values ('fomc-2026-09-16','FOMC, September 2026','2026-09-15','2026-09-16','2026-09-16 14:00 America/New_York','held',true),
             ('fomc-2026-07-29','FOMC, July 2026','2026-07-28','2026-07-29','2026-07-29 14:00 America/New_York','held',false),
             ('fomc-2036-10-28','FOMC, October 2036','2036-10-27','2036-10-28','2036-10-28 14:00 America/New_York','scheduled',true)) v(slug,title,s,e,at,st,pub);
insert into policy.meeting (event_id, kind, is_rate_decision, has_press_conference)
select id, 'scheduled', true, true from core.event where event_type='policy_meeting';

insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at, task_run_id)
select 'raw/fed/2026/monetary20260916a.htm', (select id from core.source where key='fed'), 'https://www.federalreserve.gov/x',
       sha256('x'::bytea), 12345, 'text/html', now(), (select max(id) from ops.task_run);

insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, raw_file_id, published_at,
                           char_count, text_sha256, chunked_at, is_published)
select 'fomc_statement', e.entity_id, e.id, 'FOMC statement, 16 Sep 2026', e.source_id, '/newsevents/pressreleases/monetary20260916a.htm',
       'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm', (select max(id) from core.raw_file), e.occurs_at,
       char_length(t.full), sha256(convert_to(t.full, 'UTF8')), now(), true
from core.event e,
     (select 'The Committee decided to lower the target range by 1/4 point. Inflation remains somewhat elevated.'::text as full) t
where e.slug='fomc-2026-09-16';
insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, is_published)
select 'fomc_minutes', e.entity_id, e.id, 'FOMC minutes (unpublished)', e.source_id, '/minutes-draft', 'https://www.federalreserve.gov/y', false
from core.event e where e.slug='fomc-2026-09-16';

-- Two contiguous chunks
insert into core.chunk (document_id, chunk_no, char_start, char_end, section, body)
select d.id, 0, 0, char_length(x.b), 'Statement', x.b
from core.document d, (select 'The Committee decided to lower the target range by 1/4 point.'::text b) x where d.doc_type='fomc_statement';
insert into core.chunk (document_id, chunk_no, char_start, char_end, section, body)
select c.document_id, 1, c.char_end, c.char_end + char_length(' Inflation remains somewhat elevated.'), 'Statement', ' Inflation remains somewhat elevated.'
from core.chunk c where c.chunk_no = 0;
insert into core.chunk_embedding (document_id, chunk_no, model, embedding)
select document_id, chunk_no, 'voyage-4-lite@512', array_fill(0.01::real, array[512])::extensions.halfvec(512) from core.chunk;

-- Rates and a vote, traced to spans of the statement
insert into policy.rate_setting (meeting_event_id, instrument, value_pct, change_bps, document_id, char_start, char_end)
select d.event_id, i.instrument, i.v, -25, d.id, 0, 30 from core.document d,
  (values ('fed_target_lower', 3.75), ('fed_target_upper', 4.00)) i(instrument, v) where d.doc_type='fomc_statement';
insert into policy.member (bank, name, slug) values ('FED','Jane Example','jane-example');
insert into policy.vote (meeting_event_id, member_id, voted_change_bps, is_dissent, dissent_text, document_id, char_start, char_end)
select d.event_id, (select id from policy.member), -50, true, 'preferred to lower the target range by 1/2 percentage point', d.id, 0, 10
from core.document d where d.doc_type='fomc_statement';

-- AI outputs: stance (labelled, shown) and hawk/dove (field hidden). Both cited and published.
insert into core.ai_output (task, event_id, content, body, label, status, model, prompt_key, prompt_version, input_sha256)
select 'stance_extraction', e.id, '{"stance":"easing"}', 'Easing: the Committee lowered the range.', 'easing', 'draft',
       'claude-haiku-5-5', 'policy.stance', 'v1', sha256('a'::bytea) from core.event e where e.slug='fomc-2026-09-16';
insert into core.ai_output (task, event_id, content, score, status, model, prompt_key, prompt_version, input_sha256)
select 'hawk_dove_score', e.id, '{"score":-1}', -1, 'draft', 'claude-haiku-5-5', 'policy.hawk_dove', 'v1', sha256('b'::bytea)
from core.event e where e.slug='fomc-2026-09-16';
insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
select o.id, 1, '/stance', 'document_span', d.id, 4, 80, true from core.ai_output o, core.document d where d.doc_type='fomc_statement';
update core.ai_output set status = 'published';

-- Series: DGS2 published, SP500 not
update core.series set is_published = true where key in ('fred.DGS2','rbi.proj_cpi');
insert into core.observation (series_id, obs_date, value, task_run_id)
select s.id, d::date, 3.5 + random()/10, (select max(id) from ops.task_run)
from core.series s, generate_series('2026-09-14'::date, '2026-09-18', '1 day') d where s.key in ('fred.DGS2','fred.SP500');
insert into policy.market_reaction (meeting_event_id, series_id, pre_obs_date, post_obs_date, pre_value, post_value, change, change_unit, is_surprise_measure)
select e.id, s.id, '2026-09-15', '2026-09-16', 3.50, 3.42, -8, 'bps', true
from core.event e, core.series s where e.slug='fomc-2026-09-16' and s.key='fred.DGS2';
insert into core.series_label (series_id, valid, label) select id, daterange(null, '2026-09-16'), 'back-calculated' from core.series where key='fred.DGS2';

-- An RBI-style ranged projection with a span (document reuse is fine for the test)
insert into core.forecast (series_id, vintage_date, target_start, target_end, target_label, value_low, value_high, method, document_id, char_start, char_end, task_run_id)
select s.id, '2026-10-01', '2026-10-01', '2026-12-31', 'Q3 2026-27', 3.9, 4.1, 'parsed', d.id, 0, 20, (select max(id) from ops.task_run)
from core.series s, core.document d where s.key='rbi.proj_cpi' and d.doc_type='fomc_statement';

reset role;
commit;
