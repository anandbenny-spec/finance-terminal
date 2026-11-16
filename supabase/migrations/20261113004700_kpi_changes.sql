-- 0047: the KPI-table pilot (issue #512, job DD-04, lane D; Plan 05 §1.3, D7, D8; tracker #30's decisions of 13 Nov 2026,
-- pick 2 — built now despite Data Audit 10's fail, with a judge confirming each flag before it shows). One migration for the
-- whole pilot (DD-04 the data, DD-05 the flags, DD-06 the panel and the production run); a second would be a stop. Migration
-- 0047 claimed on #30; Anand pushes it to dev and production. Equity conventions as 0019, 0034 and 0038: RLS on, the
-- `job_writer_all` policy and explicit grants on the derived data tables (`delete` for jobs: a run may replace its own rows;
-- the documents themselves are never stored and so never deleted), new api view `security_invoker = true`, visitor policies
-- via inline EXISTS (0001 revokes EXECUTE from PUBLIC), comments kept true. No core change.
--
-- Four tables:
--   equity.kpi_document  one row per (company, quarter, pilot document class): metadata only, NO text (Plan 05 D7, #287).
--   equity.kpi_value     one row per extracted row of a read document, kept or filtered with its reason (DD-04 writes).
--   equity.kpi_change    one flag per (company, dictionary key, quarter): the kind, the documents, the last value, the
--                        corporate-action note, "reason stated" and "an analyst asked" each with a verified quote INTO THE
--                        STORED TRANSCRIPT (never the unstored release), and the evidence hash (DD-05 writes).
--   equity.kpi_review    the judge's verdict per flag, keyed to an evidence hash. Written ONLY by Anand's pasted SQL
--                        (DD-07): no job_writer grant, no visitor write. A flag shows only when a review with the SAME
--                        evidence hash is `confirmed`, so a re-run that changes the documents un-shows it until re-reviewed.
--
-- Decided by Claude (standing delegation, 24 Oct 2026), each with its alternative:
--   1. `equity.kpi_review` has no FK to `equity.kpi_change`: `job_writer` manages the flags (and may delete them on a
--      re-run) but has no privilege on reviews, so it can never destroy a review, and an orphan review simply matches no
--      flag. Alternative: an FK with ON DELETE CASCADE (rejected: a job delete would drop Anand's review).
--   2. `kpi_review` gets a narrow SELECT policy exposing only `confirmed` rows under the same field-policy gate, so the
--      `security_invoker` view can read confirmed verdicts; writes stay with `service_role`. "No visitor policy" is read as
--      no visitor WRITE. Alternative: a `security definer` helper function owning the read so the table needs no anon
--      policy at all (rejected: heavier, and against the §3 inline-EXISTS pattern this file follows).
--   3. No `core.ai_task` row and no `core.ai_output`/`ops.ai_request` accounting for the extraction: those are keyed to
--      `core.document`, and the pilot's documents are deliberately NOT stored (D7/#287), so coupling to them would force
--      storing document stand-ins. The task (`equity.kpi_tables`) prices each `ft/ai/client.py` call, records the cost on
--      `ops.task_run.ai_cost_usd`, and dedups on `kpi_document` (a read document with the same SHA-256 and parser version
--      is not re-extracted). Alternative: a `core.ai_task` row with synthetic documents (rejected).

------------------------------------------------------------------ the documents (metadata only; no text)
-- One row per company and quarter for the pilot document class (the press release, fact sheet or data sheet; never the
-- investor presentation). The file is read and NOT stored (Plan 05 D7): only its URL, the exchange's filing id, the pages
-- read, the SHA-256 and the size are kept. A document that can't be found or cut by a curated rule is `held` and named,
-- never guessed.
create table equity.kpi_document (
  id               bigint generated always as identity primary key,
  entity_id        integer not null references core.entity(id),
  fiscal_label     text    not null,                                            -- 'Q2 FY27'
  period_end       date    not null,                                            -- the quarter's end
  document_class   text    not null check (document_class in ('press_release','fact_sheet','data_sheet')),
  source_url       text,                                                        -- the filing's URL (read docs)
  filing_key       text,                                                        -- the exchange's own id (NSE seq_id, BSE NEWSID)
  exchange         text             check (exchange in ('NSE','BSE')),
  filed_at         timestamptz,
  pages_used       integer[],                                                   -- physical page numbers read
  page_count       integer          check (page_count is null or page_count >= 1),
  image_page_count integer not null default 0 check (image_page_count >= 0),    -- pages read as images (HDFC Bank)
  sha256           bytea            check (sha256 is null or length(sha256) = 32),
  bytes            integer          check (bytes is null or bytes >= 0),
  status           text    not null check (status in ('read','held')),
  held_reason      text,
  parser_version   integer not null,
  extracted_at     timestamptz not null default now(),
  unique (entity_id, period_end, document_class),
  -- a held document names its reason and may lack the file facts; a read document has the file facts and no reason
  check (case status
           when 'held' then held_reason is not null
           when 'read' then held_reason is null and source_url is not null and filing_key is not null
                         and exchange is not null and sha256 is not null and bytes is not null and page_count is not null
         end)
);
create index on equity.kpi_document (entity_id, period_end);

-- One row per extracted row of a read document: the label as printed, the breakdown member, the measure, the value, the
-- unit and the physical page. `dict_key` is the curated metric key (null until DD-05 assigns it). `filter_status` marks a
-- row kept or filtered by the post-filters (finding 1), with the reason; a filtered row is kept, not dropped, so the flag
-- logic and the judge can see why.
create table equity.kpi_value (
  document_id     bigint  not null references equity.kpi_document(id) on delete cascade,
  row_no          integer not null check (row_no >= 1),
  label           text    not null,                                             -- as printed
  member          text,                                                         -- the breakdown member, if any
  measure         text    not null,                                             -- the measure kind
  value           numeric,                                                      -- null for a threshold without a value
  unit            text,
  page            integer not null check (page >= 1),
  dict_key        text,                                                         -- the curated dictionary key (null until DD-05)
  filter_status   text    not null check (filter_status in ('kept','filtered')),
  filter_reason   text,
  parser_version  integer not null,
  primary key (document_id, row_no),
  check ((filter_status = 'filtered') = (filter_reason is not null))
);
create index on equity.kpi_value (document_id) where filter_status = 'kept';

-- One flag per company, curated metric key and quarter (DD-05 writes). `kind`: `missing` (a first absence), `dropped` (the
-- second), or `renamed_or_redefined` (a dropped metric and a new one with matching units in the same quarter). The
-- documents: the previous ones the metric was reported in, and the current one that omits it. "reason stated" and "an
-- analyst asked" each carry a verified quote INTO THE STORED TRANSCRIPT (`core.document`), at most 400 code points, with its
-- page: never the unstored release (CLAUDE.md §1; transcript-privacy, 29 Oct 2026). The evidence hash is the hash of the
-- documents' SHA-256s: a re-run that changes them changes the hash, so the judge's confirmation no longer matches.
create table equity.kpi_change (
  entity_id           integer not null references core.entity(id),
  dict_key            text    not null,
  period_end          date    not null,
  fiscal_label        text    not null,
  metric_label        text    not null,                                         -- the curated display name (the view needs no YAML)
  kind                text    not null check (kind in ('missing','dropped','renamed_or_redefined')),
  previous_documents  bigint[] not null,                                        -- the kpi_document ids the metric was last reported in
  current_document_id bigint  not null references equity.kpi_document(id),
  last_value          numeric,
  last_value_unit     text,
  last_value_quarter  text,
  corporate_action_note text,
  reason_stated       boolean not null,
  reason_document_id  bigint  references core.document(id),                     -- the stored transcript the quote is from
  reason_char_start   integer,
  reason_char_end     integer,
  reason_quote        text    check (reason_quote is null or char_length(reason_quote) <= 400),
  reason_page         integer,
  analyst_asked       boolean not null,
  analyst_document_id bigint  references core.document(id),
  analyst_char_start  integer,
  analyst_char_end    integer,
  analyst_quote       text    check (analyst_quote is null or char_length(analyst_quote) <= 400),
  analyst_page        integer,
  evidence_sha256     bytea   not null check (length(evidence_sha256) = 32),
  rule_version        integer not null check (rule_version >= 1),
  created_at          timestamptz not null default now(),
  primary key (entity_id, dict_key, period_end),
  check (cardinality(previous_documents) >= 1),
  -- "reason stated: yes" carries a full verified quote; "no" carries none
  check ((reason_stated) = (reason_document_id is not null and reason_char_start is not null
          and reason_char_end is not null and reason_quote is not null and reason_page is not null)),
  check (reason_char_start is null or (reason_char_start >= 0 and reason_char_end > reason_char_start)),
  check ((analyst_asked and analyst_document_id is not null) = (analyst_document_id is not null)),  -- a quote only when asked
  check ((analyst_document_id is not null) = (analyst_char_start is not null and analyst_char_end is not null
          and analyst_quote is not null and analyst_page is not null)),
  check (analyst_document_id is null or analyst_asked),
  check (analyst_char_start is null or (analyst_char_start >= 0 and analyst_char_end > analyst_char_start))
);
create index on equity.kpi_change (current_document_id);

-- The judge's verdict per flag, keyed to the evidence hash it was given. Written ONLY by Anand's pasted SQL from DD-07's
-- verdict (service_role): no job_writer grant, no visitor write. A flag shows only when a review with the same evidence
-- hash is `confirmed`.
create table equity.kpi_review (
  entity_id       integer not null references core.entity(id),
  dict_key        text    not null,
  period_end      date    not null,
  evidence_sha256 bytea   not null check (length(evidence_sha256) = 32),
  verdict         text    not null check (verdict in ('confirmed','rejected')),
  judge           text    not null,
  reviewed_at     timestamptz not null default now(),
  primary key (entity_id, dict_key, period_end)
);

------------------------------------------------------------------ privileges
grant select on equity.kpi_document, equity.kpi_value, equity.kpi_change to anon, authenticated, semantic_owner, service_role;
grant select, insert, update, delete on equity.kpi_document, equity.kpi_value, equity.kpi_change to job_writer;
-- reviews: visitors and semantic_owner read (confirmed rows only, via the policy below); writes are Anand's (service_role).
grant select on equity.kpi_review to anon, authenticated, semantic_owner, service_role;
grant insert, update, delete on equity.kpi_review to service_role;

------------------------------------------------------------------ row-level security
alter table equity.kpi_document enable row level security;
alter table equity.kpi_value    enable row level security;
alter table equity.kpi_change   enable row level security;
alter table equity.kpi_review   enable row level security;
create policy job_writer_all on equity.kpi_document for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.kpi_value    for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.kpi_change   for all to job_writer using (true) with check (true);

-- The field-policy gate, shared by every visitor policy below (fails closed: no row, or a hidden status, means hidden).
-- A document row: the field policy shown or labelled, its company published, and its exchange source unsuppressed.
create policy read_published on equity.kpi_document for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.field_policy fp where fp.field_key = 'equity.kpi_changes' and fp.status in ('shown','labelled'))
     and exists (select 1 from core.entity e where e.id = kpi_document.entity_id and e.is_published)
     and (exchange is null or not exists (select 1 from core.source s
            where s.key = lower(kpi_document.exchange) and s.is_suppressed)));
-- A value row: its document visible under the same gate.
create policy read_published on equity.kpi_value for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.kpi_document d where d.id = kpi_value.document_id));
-- A flag row: the field policy shown or labelled, its company published, and its current document's source unsuppressed.
create policy read_published on equity.kpi_change for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.field_policy fp where fp.field_key = 'equity.kpi_changes' and fp.status in ('shown','labelled'))
     and exists (select 1 from core.entity e where e.id = kpi_change.entity_id and e.is_published)
     and exists (select 1 from equity.kpi_document d where d.id = kpi_change.current_document_id));
-- A review row: visitors see ONLY confirmed verdicts, under the same field-policy gate (standing-delegation decision 2).
create policy read_confirmed on equity.kpi_review for select to anon, authenticated, semantic_owner
  using (verdict = 'confirmed'
     and exists (select 1 from core.field_policy fp where fp.field_key = 'equity.kpi_changes' and fp.status in ('shown','labelled')));

------------------------------------------------------------------ field policy (fails closed; seeded hidden)
insert into core.field_policy (field_key, status, label, reason) values
  ('equity.kpi_changes', 'hidden', 'Pilot',
   'Held back until a judge session has confirmed every flag in both documents and Anand flips it (Plan 05 §5; DD-07, #515). A pilot, not a complete list.');

------------------------------------------------------------------ the api view (security_invoker; list queries check their count against the 1,000-row cap)
-- One row per flag, returned only when the field policy is shown/labelled AND a kpi_review with the SAME evidence hash is
-- `confirmed` (a re-run that changes the flag's documents changes evidence_sha256 and un-shows it until re-reviewed). Gated
-- too by the company being published and the current document's exchange source unsuppressed (the base-table policies above
-- already enforce all of this for the invoker; the join to kpi_review adds the confirmation gate). Never a motive (D8).
create view api.kpi_changes with (security_invoker = true) as
select e.slug as company_slug, e.name as company_name,
       c.period_end, c.fiscal_label, c.metric_label, c.dict_key, c.kind,
       c.last_value, c.last_value_unit, c.last_value_quarter,
       c.corporate_action_note,
       c.reason_stated, c.reason_quote, c.reason_page,
       c.analyst_asked, c.analyst_quote, c.analyst_page,
       cur.source_url as current_document_url, cur.filed_at as current_filed_at, cur.fiscal_label as current_quarter,
       (select array_agg(pd.source_url order by pd.period_end)
          from equity.kpi_document pd where pd.id = any(c.previous_documents)) as previous_document_urls,
       c.rule_version,
       lower(cur.exchange) as source_key
from equity.kpi_change c
join core.entity e on e.id = c.entity_id
join equity.kpi_document cur on cur.id = c.current_document_id
join equity.kpi_review r on r.entity_id = c.entity_id and r.dict_key = c.dict_key and r.period_end = c.period_end
                         and r.evidence_sha256 = c.evidence_sha256 and r.verdict = 'confirmed';

grant select on api.kpi_changes to anon, authenticated, service_role;

------------------------------------------------------------------ comments
comment on table equity.kpi_document is 'KPI-table pilot (#512): one row per company, quarter and pilot document class (press release, fact sheet or data sheet). Metadata only — the file is read and not stored (Plan 05 D7): the URL, the exchange filing id, the pages read, the SHA-256 and the size. A document that cannot be found or cut by a curated rule is held with its reason. Written by equity.kpi_tables.';
comment on table equity.kpi_value is 'KPI-table pilot: one row per extracted row of a read document — the label as printed, the breakdown member, the measure, the value, the unit and the physical page. dict_key is the curated metric key (null until DD-05). filter_status marks a row kept or filtered by the post-filters, with the reason; filtered rows are kept, not dropped. Written by equity.kpi_tables.';
comment on table equity.kpi_change is 'KPI-table pilot: one flag per company, curated metric key and quarter (kind missing, dropped or renamed_or_redefined). The documents, the last value and its quarter, a corporate-action note, and "reason stated" and "an analyst asked" each with a verified quote into the stored transcript (at most 400 code points) — never the unstored release, never a motive. evidence_sha256 is the hash of the documents'' SHA-256s. Visitors reach it only through api.kpi_changes, and only when a judge has confirmed it.';
comment on table equity.kpi_review is 'KPI-table pilot: the judge''s verdict per flag, keyed to the evidence hash it was shown. Written only by Anand''s pasted SQL from DD-07''s verdict (service_role); no job_writer grant. A flag shows only when a review with the same evidence hash is confirmed.';
comment on view api.kpi_changes is 'The KPI-table pilot''s flags: a metric reported in a company''s previous quarterly releases and absent from the latest, as filed, with whether the company stated a reason and whether an analyst asked (each shown as a verified transcript quote). A pilot, not a complete list; never a motive. Returned only when equity.kpi_changes is shown/labelled and a judge has confirmed the flag against the same documents (DD-07). Gated by equity.kpi_changes.';
