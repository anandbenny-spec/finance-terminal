-- 0023: earnings calls, release 2B-lite (issue #168, job 2B-05; Spec 02B §3.1–§3.5 as revised by §13.4–§13.6).
-- Holds what 2B-lite writes: the `company_web` source row (§3.1); the core addition for private document text (§3.2,
-- OK'd by Anand on 29 Oct 2026, about 14:44 IST: "OK as proposed"), exactly as written; the `equity` tables
-- `earnings_call`, `call_turn`, `guidance` (+ `metric_label`), `stated_figure` and `transcript_page`; the catalogue rows
-- and the two field policies, hidden. Not here (Spec 02B §13.11 L9): `call_tone`, `guidance_outcome`,
-- `call_remarks_change` and their field policies, which come in `earnings_scores` with #175, #177 and #180.
-- Equity conventions as 0019 (Spec 02A §3.2): RLS on, the `job_writer_all` policy, explicit grants, `delete` for jobs only
-- on the tables marked D. Visitor policies use inline subqueries only (0001 revokes EXECUTE from PUBLIC).

------------------------------------------------------------------ §3.1 the source row
insert into core.source (key, name, homepage_url, terms_url, attribution, is_official, allow_raw_export, notes) values
  ('company_web', 'Company websites (transcripts linked from exchange filings)', null, null,
   'Source: the company''s website, linked from its exchange filing', true, false,
   'Statutory disclosures under LODR Regulation 46(2)(oa) (read 22 Oct 2026); fetch only the file the company''s own filing links to, or the one the curated table names; store once; show short cited quotes and a link, never the text (Data Audit 05 D5, D12, D13). A call''s event keeps the exchange that carried the filing as its source, so the exchange''s switch still hides the call (Spec 02B §3.1).');

------------------------------------------------------------------ §3.2 the core addition: private document text
-- Why: a published document's text is readable by any visitor through api.document_chunks (the chunk policy follows the
-- document's), and a citation counts only inside a published document (core.check_ai_output_citations). Transcripts must
-- be published to be cited, and must never be shown in full (Data Audit 05 D5; Brief §6.2).

-- 1. Every existing doc type keeps true, so module 1 and 2A are unchanged.
alter table core.doc_type add column text_public boolean not null default true;
comment on column core.doc_type.text_public is
  'False for documents whose text is never shown (earnings-call transcripts and cover letters): their chunks are invisible to visitors, the admin''s browser session and the semantic views; a verified citation''s quote comes only through core.citation_quote (at most 400 code points). Spec 02B §3.2.';

-- 2. The chunk policy adds the condition that the chunk's document type has public text.
alter policy read_published on core.chunk
  using (exists (select 1 from core.document d join core.doc_type t on t.key = d.doc_type
                 where d.id = chunk.document_id and t.text_public));

-- 3. One function returns a verified citation's quote, and only that span. Owned by retrieval_owner (Spec 02B §10
--    decision 18), which reads published documents' chunks (0010). To look up the citation, its output and the field
--    policy, it gets SELECT on those three tables with policies that show it published outputs, verified passage
--    citations and released field policies only; it still owns no table and can't bypass RLS (A11, A12).
grant select on core.ai_output, core.ai_citation, core.field_policy to retrieval_owner;
create policy retrieval_read on core.ai_output    for select to retrieval_owner using (status = 'published');
create policy retrieval_read on core.ai_citation  for select to retrieval_owner using (verified and kind = 'document_span');
create policy retrieval_read on core.field_policy for select to retrieval_owner using (status in ('shown','labelled'));

create function core.citation_quote(ai_output_id bigint, citation_no smallint)
returns text
language sql stable security definer
set search_path = pg_catalog, core
as $$
  -- The offsets come from the stored citation, never from the caller, so no one can ask for an arbitrary span.
  select left(substr(string_agg(ch.body, '' order by ch.chunk_no),
                     c.char_start - min(ch.char_start) + 1, c.char_end - c.char_start), 400)
  from core.ai_citation c
  join core.ai_output o     on o.id = c.ai_output_id
  join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
  join core.document d      on d.id = c.document_id
  join core.source s        on s.id = d.source_id
  join core.chunk ch        on ch.document_id = c.document_id
                           and ch.char_start < c.char_end and ch.char_end > c.char_start
  where c.ai_output_id = citation_quote.ai_output_id
    and c.citation_no  = citation_quote.citation_no
    and c.verified and c.kind = 'document_span'
    and o.status = 'published'
    and d.is_published and not s.is_suppressed
  group by c.char_start, c.char_end
$$;
-- ALTER ... OWNER needs the new owner to hold CREATE on the schema (postgres isn't a superuser on Supabase): granted for
-- the hand-over only.
grant create on schema core to retrieval_owner;
alter function core.citation_quote(bigint, smallint) owner to retrieval_owner;
revoke create on schema core from retrieval_owner;
revoke all on function core.citation_quote(bigint, smallint) from public;
-- service_role keeps reading api.ai_citations (0006 grants it); it bypasses RLS and reads core.chunk already, so the grant
-- shows it nothing new. Not executable by job_writer or sql_reader.
grant execute on function core.citation_quote(bigint, smallint) to anon, authenticated, semantic_owner, service_role;
comment on function core.citation_quote(bigint, smallint) is
  'The quote of one verified document_span citation, at most 400 code points: only when its output is published, its field policy ai.<task> is shown or labelled, and its document is published from an unsuppressed source; otherwise null. Spec 02B §3.2.';

-- 4. api.ai_citations: public-text documents keep today's quote exactly (the subquery, which RLS now empties for private
--    text); private ones get the function's. text_public is appended (create or replace can only add columns at the end),
--    so CitationPopover hides "Open in document" for a private text.
create or replace view api.ai_citations with (security_invoker = true) as
select c.ai_output_id, c.citation_no, c.claim_path, c.kind,
       c.document_id, d.title as document_title, d.source_url, c.char_start, c.char_end,
       coalesce((select substr(string_agg(ch.body, '' order by ch.chunk_no),
                               c.char_start - min(ch.char_start) + 1, c.char_end - c.char_start)
                   from core.chunk ch
                  where ch.document_id = c.document_id
                    and ch.char_start < c.char_end and ch.char_end > c.char_start),
                core.citation_quote(c.ai_output_id, c.citation_no)) as quote,
       s.key as series_key, c.obs_date, ev.slug as event_slug,
       t.text_public
from core.ai_citation c
join core.ai_output o on o.id = c.ai_output_id
join core.field_policy fp on fp.field_key = 'ai.' || o.task and fp.status in ('shown','labelled')
left join core.document d on d.id = c.document_id
left join core.doc_type t on t.key = d.doc_type
left join core.series s on s.id = c.series_id
left join core.event ev on ev.id = c.event_id
where c.verified;

-- Unchanged (§3.2): semantic.search_chunks (it runs as retrieval_owner, so the command bar's server-side route still
-- retrieves transcript chunks; visitors see only cited quotes), core.document_diff (transcripts aren't diffable), the
-- publish rule.

------------------------------------------------------------------ §3.3, §13.4, §13.6 the equity tables
create table equity.earnings_call (
  event_id                 bigint  primary key references core.event(id),       -- event type earnings_call
  entity_id                integer not null references core.entity(id),
  fiscal_label             text    check (fiscal_label ~ '^Q[1-4] FY[0-9]{2}$'),   -- 'Q2 FY27'; null while unknown (held)
  period_end               date,
  season_key               text    not null check (season_key ~ '^q[1-4]-fy[0-9]{2}$'),  -- of the dissemination date − 7 days
  results_event_id         bigint  references core.event(id),                   -- the results_filing event
  quarter_source           text    check (quarter_source in ('results_link','text')),
  call_date                date,
  call_date_source         text    check (call_date_source in ('text','intimation')),
  filed_at                 timestamptz not null,                                -- the transcript filing's dissemination time
  exchange                 text    not null check (exchange in ('NSE','BSE')),
  filing_url               text,
  filing_key               text,                                                -- the source's own id
  route                    text    not null check (route in ('exchange_pdf','company_link','curated_link','none')),
  transcript_document_id   bigint  references core.document(id),
  cover_letter_document_id bigint  references core.document(id),
  company_url              text,
  words                    integer check (words >= 0),
  unknown_role_share       numeric check (unknown_role_share between 0 and 1),  -- a fraction of the call's words
  guidance_targets         integer check (guidance_targets >= 0),               -- null until extracted
  status_reason            text,
  parser_version           text,
  unique (entity_id, fiscal_label),                                             -- one call per company per fiscal quarter (§4)
  check ((fiscal_label is null) = (period_end is null)),
  check ((fiscal_label is null) = (quarter_source is null)),
  check ((call_date is null) = (call_date_source is null))
);
create index on equity.earnings_call (entity_id, period_end);
create index on equity.earnings_call (transcript_document_id);

create table equity.call_turn (
  document_id bigint  not null references core.document(id),
  turn_no     integer not null check (turn_no >= 0),
  char_start  integer not null check (char_start >= 0),
  char_end    integer not null,
  speaker     text,                                                             -- as labelled
  role        text    not null check (role in ('management','analyst','moderator','unknown')),
  section     text    not null check (section in ('opening','qa','closing')),
  words       integer not null check (words >= 0),
  primary key (document_id, turn_no),
  check (char_end > char_start)
);

create table equity.guidance (
  id                 bigint  generated always as identity primary key,
  call_event_id      bigint  not null references equity.earnings_call(event_id),
  entity_id          integer not null references core.entity(id),
  ai_output_id       bigint  not null references core.ai_output(id),
  citation_no        smallint not null,                                         -- its quote's citation
  document_id        bigint  not null references core.document(id),
  char_start         integer not null check (char_start >= 0),
  char_end           integer not null,
  metric             text    not null check (metric ~ '^[a-z0-9_]+$'),          -- a map key or 'other'
  metric_label       text,                                                      -- from py/config/equity/guidance_metrics.yaml
  metric_text        text    not null,                                          -- as said
  kind               text    not null check (kind in ('point','range','floor','ceiling','direction')),
  value              numeric,
  low                numeric,
  high               numeric,
  unit               text    check (unit in ('pct','inr','other')),
  direction          text    check (direction in ('increase','decrease','maintain')),
  comparison         text    check (comparison in ('previous_period','year_ago')),
  period_text        text,
  period_kind        text    not null check (period_kind in ('quarter','half_year','nine_months','year','multi_year','undated')),
  target_start       date,
  target_end         date,
  basis              text    check (basis in ('consolidated','standalone')),
  growth_basis       text    check (growth_basis in ('yoy','qoq','cagr','level')),
  scope_text         text,                                                      -- a segment, subsidiary or region, if any
  vintage_date       date    not null,                                          -- the call date, else the filing date
  duplicate_of       bigint  references equity.guidance(id),
  not_scored_reason  text    check (not_scored_reason in ('no_filed_figure','currency','undated','vague_figure','scope',
                                                          'structural_break','definition','filed_figures_unavailable',
                                                          'duplicate')),
  forecast_series_id integer references core.series(id),                       -- point and range only (after 18 Nov)
  foreign key (ai_output_id, citation_no) references core.ai_citation (ai_output_id, citation_no),
  check (char_end > char_start),
  check (low is null or high is null or low <= high),
  constraint guidance_period_dates check ((period_kind = 'undated') = (target_start is null)),   -- undated = no dates (§5.9)
  check ((target_start is null) = (target_end is null)),
  check (target_end >= target_start),
  check (forecast_series_id is null or kind in ('point','range'))
);
create index on equity.guidance (call_event_id);
create index on equity.guidance (entity_id, metric);

create table equity.stated_figure (
  id            bigint  generated always as identity primary key,
  call_event_id bigint  not null references equity.earnings_call(event_id),
  entity_id     integer not null references core.entity(id),
  ai_output_id  bigint  not null references core.ai_output(id),
  citation_no   smallint not null,                                              -- its quote's citation
  document_id   bigint  not null references core.document(id),
  char_start    integer not null check (char_start >= 0),
  char_end      integer not null,
  metric        text    not null check (metric ~ '^[a-z0-9_]+$' and metric <> 'other'),   -- a map key (§13.5)
  metric_label  text,
  metric_text   text    not null,
  value         numeric not null,
  unit          text    check (unit in ('pct','inr','other')),
  period_text   text,
  period_kind   text    not null check (period_kind in ('quarter','half_year','nine_months','year')),
  period_start  date    not null,
  period_end    date    not null,
  basis         text    check (basis in ('consolidated','standalone')),
  growth_basis  text    check (growth_basis in ('yoy','qoq','level')),
  scope_text    text,
  duplicate_of  bigint  references equity.stated_figure(id),
  foreign key (ai_output_id, citation_no) references core.ai_citation (ai_output_id, citation_no),
  check (char_end > char_start),
  check (period_end >= period_start)
);
create index on equity.stated_figure (call_event_id);

-- §13.4: one row per physical PDF page that contributes text to the document's canonical text. Keyed by document (a new
-- text is a new version, and its offsets belong to that version). Written once, with the text; jobs never change them.
create table equity.transcript_page (
  document_id bigint   not null references core.document(id),
  pdf_page    smallint not null check (pdf_page >= 1),                           -- the 1-based physical page (#page=N)
  char_start  integer  not null check (char_start >= 0),                         -- code points into the canonical text
  char_end    integer  not null,
  primary key (document_id, pdf_page),
  unique (document_id, char_start),
  check (char_end > char_start)
);

------------------------------------------------------------------ privileges
grant select on equity.earnings_call, equity.guidance, equity.stated_figure, equity.transcript_page
  to anon, authenticated, semantic_owner, service_role;
grant select on equity.call_turn to service_role;                               -- no visitor access at all
grant select, insert, update on equity.earnings_call, equity.call_turn, equity.guidance, equity.stated_figure to job_writer;
grant select, insert on equity.transcript_page to job_writer;                   -- written once: no update, no delete
grant delete on equity.call_turn, equity.guidance, equity.stated_figure to job_writer;   -- the D tables
grant usage on sequence equity.guidance_id_seq, equity.stated_figure_id_seq to job_writer;

------------------------------------------------------------------ row-level security
alter table equity.earnings_call   enable row level security;
alter table equity.call_turn       enable row level security;
alter table equity.guidance        enable row level security;
alter table equity.stated_figure   enable row level security;
alter table equity.transcript_page enable row level security;
create policy job_writer_all on equity.earnings_call   for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.call_turn       for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.guidance        for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.stated_figure   for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.transcript_page for all to job_writer using (true) with check (true);

-- A call: its event and its company visible (the event's policy brings its source's switch).
create policy read_published on equity.earnings_call for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event ev where ev.id = event_id)
     and exists (select 1 from core.entity e where e.id = entity_id));
-- call_turn: no visitor policy. The views read the counts stored on earnings_call.
-- Targets and stated figures: their call visible (Spec 02B §3.3), and also their output visible, as AI-extracted forecasts
-- follow theirs (Arch 01 §1.10 gap 7): so a hidden ai.guidance_extraction leaves no row even in the base table.
create policy read_published on equity.guidance for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.earnings_call c where c.event_id = call_event_id)
     and exists (select 1 from core.ai_output o where o.id = ai_output_id));
create policy read_published on equity.stated_figure for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.earnings_call c where c.event_id = call_event_id)
     and exists (select 1 from core.ai_output o where o.id = ai_output_id));
-- Page offsets are numbers, not text: visible with their document.
create policy read_published on equity.transcript_page for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.document d where d.id = document_id));

------------------------------------------------------------------ §3.4 catalogue rows
insert into core.event_type (key, label, module) values ('earnings_call', 'Earnings call', 'equity');
insert into core.event_status (event_type, status, is_final) values
  ('earnings_call', 'stored',             true),
  ('earnings_call', 'on_company_website', false),
  ('earnings_call', 'unreadable',         false);

insert into core.doc_type (key, label, module, diffable, text_public) values
  ('call_transcript',   'Earnings call transcript',   'equity', false, false),
  ('call_cover_letter', 'Earnings call cover letter', 'equity', false, false);   -- registered, never published

insert into core.ai_task (key, label, module, output_kind, requires_citations) values
  ('call_summary',        'Earnings call summary',                 'equity', 'text',       true),
  ('guidance_extraction', 'Guidance and figures management stated', 'equity', 'structured', true);

------------------------------------------------------------------ §3.5, §13.6 field policies (fail closed; seeded hidden)
insert into core.field_policy (field_key, status, label, reason) values
  ('ai.call_summary',        'hidden', 'AI summary',
   'Held back until Anand has read 10 summaries (Spec 02B §13.8; 2B-15 records it, 2B-22 flips it)'),
  ('ai.guidance_extraction', 'hidden', 'AI-extracted',
   'Held back until the extraction and stated-figure checks pass (Spec 02B §13.8; 2B-15 records them, 2B-22 flips it)');

------------------------------------------------------------------ comments
comment on table equity.earnings_call is 'One row per earnings call (a core.event of type earnings_call): its fiscal quarter, season, call date and its source, the transcript filing, the route to its transcript and the transcript''s counts.';
comment on table equity.call_turn is 'One row per speaker turn of a transcript: span, speaker as labelled, role and section by rules. No visitor access.';
comment on table equity.guidance is 'One row per measurable target management stated on a call (AI-extracted, with its verified quote''s citation and span).';
comment on table equity.stated_figure is 'One row per figure management stated on a call for a period already reported (AI-extracted, with its verified quote''s citation and span).';
comment on table equity.transcript_page is 'One row per physical PDF page of a transcript document: the code-point range of the canonical text it holds, for page links. Written once.';
