-- 0003: core schema — the engines' shared tables

---------------------------------------------------------------- reference
create table core.source (
  id               integer generated always as identity primary key,
  key              text not null unique check (key ~ '^[a-z0-9_]+$'),   -- 'fred', 'nse_archives'
  name             text not null,
  homepage_url     text,
  terms_url        text,
  attribution      text not null,                -- shown next to every figure: 'Source: NSE'
  is_official      boolean not null default false,
  allow_raw_export boolean not null default false,
  is_suppressed    boolean not null default false,  -- one switch hides everything from this source
  notes            text
);

-- Hold back or label a field without a migration (brief §1). Missing row = hidden for gated fields.
create table core.field_policy (
  field_key  text primary key,                    -- 'policy.hawk_dove_score'
  status     text not null check (status in ('shown','labelled','hidden')),
  label      text,                                -- 'annualised', 'proxy', 'AI-extracted'
  reason     text not null,
  updated_at timestamptz not null default now(),
  check (status <> 'labelled' or label is not null)
);

---------------------------------------------------------------- entities
-- Things that have a page and an identity: central banks, companies, indices, coins.
create table core.entity (
  id           integer generated always as identity primary key,
  kind         text not null check (kind in ('central_bank','company','index','coin')),
  slug         text not null check (slug ~ '^[a-z0-9-]+$'),
  name         text not null,
  short_name   text,
  market       text check (market in ('IN','US','GLOBAL')),
  currency     text check (currency ~ '^[A-Z]{3}$'),
  is_published boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (kind, slug),
  -- currency guard at the source: every company carries its market and reporting currency
  check (kind <> 'company' or (market is not null and currency is not null))
);

-- Identifier history: ISINs change after a face-value split, NSE symbols change, CIKs succeed each other.
create table core.entity_identifier (
  entity_id integer not null references core.entity(id),
  scheme    text not null check (scheme in ('isin','nse_symbol','bse_code','cik','ticker',
                                            'yf_symbol','coingecko_id')),
  value     text not null,
  valid     daterange not null default daterange(null, null),
  source_id integer,
  primary key (scheme, value, valid),
  exclude using gist (scheme with =, value with =, valid with &&)   -- one owner per identifier at a time
);
create index on core.entity_identifier (entity_id);

---------------------------------------------------------------- jobs' public projection
-- What visitors may know about freshness ("last updated", status bar). No errors or internals.
create table core.task_freshness (
  task_key        text primary key references ops.task(key),
  label           text not null,
  last_checked_at timestamptz,
  last_success_at timestamptz,
  status          text not null check (status in ('ok','stale','failed')),
  next_due_at     timestamptz
);

---------------------------------------------------------------- event-table engine
create table core.event_type (
  key    text primary key,       -- 'policy_meeting', later 'ipo', 'corporate_action'
  label  text not null,
  module text not null
);
create table core.event_status (
  event_type text not null references core.event_type(key),
  status     text not null,
  is_final   boolean not null,
  primary key (event_type, status)
);

create table core.event (
  id           bigint generated always as identity primary key,
  event_type   text not null,
  entity_id    integer not null references core.entity(id),
  slug         text not null check (slug ~ '^[a-z0-9-]+$'),
  title        text not null,
  starts_on    date not null,
  ends_on      date,
  occurs_at    timestamptz,                 -- exact time when known (policy decision release)
  status       text not null,
  source_id    integer references core.source(id),
  source_url   text,
  is_published boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (event_type, slug),
  foreign key (event_type, status) references core.event_status (event_type, status),
  check (ends_on is null or ends_on >= starts_on)
);
create index on core.event (event_type, starts_on);
create index on core.event (entity_id, starts_on);

---------------------------------------------------------------- raw files and the document engine
-- Every fetched artifact (HTML, PDF, JSON, CSV, zip). Bytes live in object storage via the storage function.
create table core.raw_file (
  id           bigint generated always as identity primary key,
  object_key   text not null unique,      -- opaque key; only the storage function interprets it
  source_id    integer not null references core.source(id),
  source_url   text,
  sha256       bytea not null check (length(sha256) = 32),
  bytes        bigint not null check (bytes >= 0),
  media_type   text not null,
  content_date date,                      -- the date the file is about (bhavcopy trade date)
  fetched_at   timestamptz not null,
  task_run_id  bigint not null references ops.task_run(id),
  unique (source_id, sha256)              -- identical bytes from one source are stored once
);

create table core.doc_type (
  key      text primary key,       -- 'fomc_statement', 'mpc_minutes', later 'results_xbrl', 'ipo_detail'
  label    text not null,
  module   text not null,
  diffable boolean not null default false    -- diffed against the previous document of the same type
);

create table core.document (
  id                bigint generated always as identity primary key,
  doc_type          text not null references core.doc_type(key),
  entity_id         integer not null references core.entity(id),   -- who issued it / who it is about
  event_id          bigint references core.event(id),               -- the meeting, IPO, ... if any
  title             text not null,
  source_id         integer not null references core.source(id),
  external_key      text not null,       -- stable key at the source (URL path, filing id)
  source_url        text not null,       -- public link shown to visitors
  raw_file_id       bigint references core.raw_file(id),
  published_at      timestamptz,         -- official release time
  version_no        smallint not null default 1,
  supersedes_id     bigint references core.document(id),
  language          text not null default 'en',
  extractor_version text,
  char_count        integer,
  text_sha256       bytea check (length(text_sha256) = 32),   -- hash of the full text; fixed once set
  extracted_at      timestamptz,
  chunked_at        timestamptz,
  is_published      boolean not null default false,
  created_at        timestamptz not null default now(),
  unique (source_id, external_key, version_no),
  check (chunked_at is null or text_sha256 is not null),
  check (not is_published or (text_sha256 is not null and char_count is not null))
);
create index on core.document (event_id);
create index on core.document (entity_id, doc_type, published_at);

-- The document's text, cut into contiguous, non-overlapping spans. Concatenating the chunks in order
-- gives the full normalised text, so citations are code-point offsets into that text.
create table core.chunk (
  document_id bigint not null references core.document(id) on delete cascade,
  chunk_no    integer not null check (chunk_no >= 0),
  char_start  integer not null check (char_start >= 0),
  char_end    integer not null,
  section     text,          -- heading path, e.g. 'Minutes > Statement by Dr. Nagesh Kumar'
  speaker     text,          -- transcripts and minutes
  body        text not null,
  token_count integer,
  primary key (document_id, chunk_no),
  check (char_end - char_start = char_length(body))
);

create table core.chunk_embedding (
  document_id bigint not null,
  chunk_no    integer not null,
  model       text not null,                       -- 'voyage-4-lite@512'
  embedding   extensions.halfvec(512) not null,    -- dimension confirmed in piece 3
  embedded_at timestamptz not null default now(),
  primary key (document_id, chunk_no, model),
  foreign key (document_id, chunk_no) references core.chunk on delete cascade
);
create index on core.chunk_embedding using hnsw (embedding extensions.halfvec_cosine_ops);

-- Word-level redline of a document against its baseline (e.g. this FOMC statement vs the previous one).
create table core.document_diff (
  document_id      bigint primary key references core.document(id),
  base_document_id bigint not null references core.document(id),
  segments         jsonb not null,        -- [{"op": "eq|ins|del", "text": "..."}]
  words_added      integer not null,
  words_removed    integer not null,
  diff_version     text not null,
  created_at       timestamptz not null default now(),
  check (document_id <> base_document_id)
);

-- A document version's text is fixed once chunked: re-chunking is allowed, changing the text is not
-- (citations are offsets into it). New text = a new document version.
create function core.guard_document() returns trigger language plpgsql as $$
begin
  if (new.source_id, new.external_key, new.version_no) is distinct from (old.source_id, old.external_key, old.version_no)
     or (old.text_sha256 is not null and (new.text_sha256, new.char_count) is distinct from (old.text_sha256, old.char_count)) then
    raise exception 'document % is immutable here: create a new version instead', old.id;
  end if;
  return new;
end $$;
create trigger guard before update on core.document for each row execute function core.guard_document();

create function core.check_document_chunks() returns trigger language plpgsql as $$
declare d record; agg record;
begin
  for d in select id, char_count, text_sha256 from core.document
           where id in (case when tg_op <> 'INSERT' then old.document_id end,
                        case when tg_op <> 'DELETE' then new.document_id end)
             and text_sha256 is not null loop
    select count(*) as n, min(char_start) as first_start, max(char_end) as last_end,
           bool_and(char_start = prev_end) as contiguous,
           sha256(convert_to(string_agg(body, '' order by chunk_no), 'UTF8')) as h
      into agg
      from (select chunk_no, char_start, char_end, body,
                   coalesce(lag(char_end) over (order by chunk_no), 0) as prev_end
              from core.chunk where document_id = d.id) c;
    if agg.n = 0 or agg.first_start <> 0 or agg.last_end <> d.char_count or not agg.contiguous or agg.h <> d.text_sha256 then
      raise exception 'chunks of document % do not reproduce its text exactly', d.id;
    end if;
  end loop;
  return null;
end $$;
create constraint trigger chunks_reproduce_text after insert or update or delete on core.chunk
  deferrable initially deferred for each row execute function core.check_document_chunks();

---------------------------------------------------------------- time-series engine
create table core.series (
  id           integer generated always as identity primary key,
  key          text not null unique check (key ~ '^[a-z0-9_]+\.[A-Za-z0-9_^.=-]+$'),  -- 'fred.DGS2'
  name         text not null,
  kind         text not null default 'observed' check (kind in ('observed','forecast')),
  entity_id    integer references core.entity(id),
  source_id    integer not null references core.source(id),
  source_code  text not null,
  source_url   text,
  attribution  text,                      -- overrides the source's line: 'Source: S&P Dow Jones Indices via FRED'
  unit         text not null,             -- 'percent', 'index_points', 'inr_per_usd'
  frequency    text not null check (frequency in ('D','W','M','Q','A','irregular')),
  date_rule    text not null default 'as_published',   -- e.g. FRED monthly = period start
  fixing_time  time,                      -- local time of the value (16:00 for a close)
  fixing_tz    text,                      -- IANA zone, e.g. 'America/New_York'
  market       text check (market in ('IN','US','GLOBAL')),
  currency     text check (currency ~ '^[A-Z]{3}$'),
  is_published boolean not null default false,
  created_at   timestamptz not null default now(),
  check ((fixing_time is null) = (fixing_tz is null))
);

-- Plain observed values. Every value fetched is kept; a changed value is logged, never silently lost.
create table core.observation (
  series_id   integer not null references core.series(id),
  obs_date    date not null,
  value       numeric not null,
  task_run_id bigint not null references ops.task_run(id),
  primary key (series_id, obs_date)
);

create table core.observation_revision (
  series_id   integer not null,
  obs_date    date not null,
  old_value   numeric not null,
  new_value   numeric not null,
  replaced_at timestamptz not null default clock_timestamp(),
  task_run_id bigint not null,
  primary key (series_id, obs_date, replaced_at),
  foreign key (series_id, obs_date) references core.observation
);

create function core.log_observation_revision() returns trigger
  language plpgsql security definer set search_path = pg_catalog, core as $$
begin
  if (new.series_id, new.obs_date) is distinct from (old.series_id, old.obs_date) then
    raise exception 'observation keys are fixed; insert a new observation instead';
  end if;
  if new.value is distinct from old.value then
    insert into core.observation_revision (series_id, obs_date, old_value, new_value, task_run_id)
    values (old.series_id, old.obs_date, old.value, new.value, new.task_run_id);
  end if;
  return new;
end $$;
create trigger observation_revision before update on core.observation
  for each row execute function core.log_observation_revision();

-- Labels that apply to part of a series' history ('back-calculated' before an index's launch).
create table core.series_label (
  series_id integer not null references core.series(id),
  valid     daterange not null,
  label     text not null,
  note      text,
  exclude using gist (series_id with =, valid with &&)
);

-- Forecasts keyed by when they were made (vintage) and what period they are for.
-- RBI's CPI/GDP projections (module 1), IMF WEO anchors (module 2), Fed SEP medians (v2).
create table core.forecast (
  series_id    integer not null references core.series(id),
  vintage_date date not null,
  target_start date not null,
  target_end   date not null,
  target_label text not null,             -- 'Q2 2026-27', 'FY 2026-27', '2027'
  value        numeric,
  value_low    numeric,                   -- ranges: RBI published some projections as ranges
  value_high   numeric,
  method       text not null check (method in ('parsed','ai_extracted','api')),
  document_id  bigint references core.document(id),
  char_start   integer,
  char_end     integer,
  ai_output_id bigint,                    -- FK added after core.ai_output exists
  task_run_id  bigint not null references ops.task_run(id),
  primary key (series_id, vintage_date, target_start, target_end),
  check (target_end >= target_start),
  check (value is not null or (value_low is not null and value_high is not null)),
  check (method = 'api' or (document_id is not null and char_end > char_start)),
  check (method <> 'ai_extracted' or ai_output_id is not null)
);

---------------------------------------------------------------- AI outputs with citations
create table core.ai_task (
  key                text primary key,     -- 'doc_summary', 'hawk_dove_score', 'stance_extraction'
  label              text not null,
  module             text not null,
  output_kind        text not null check (output_kind in ('text','score','label','structured')),
  requires_citations boolean not null default true
);

create table core.ai_output (
  id             bigint generated always as identity primary key,
  task           text not null references core.ai_task(key),
  document_id    bigint references core.document(id),
  event_id       bigint references core.event(id),
  entity_id      integer references core.entity(id),
  scope          text not null default '',        -- finer target: 'member:12', 'section:qa'
  content        jsonb not null,                  -- validated against the task's JSON schema
  body           text,                            -- the text shown on the page
  score          numeric,
  label          text,
  status         text not null default 'draft'
                 check (status in ('draft','published','rejected','superseded')),
  status_reason  text,
  model          text not null,
  prompt_key     text not null,
  prompt_version text not null,
  input_sha256   bytea not null check (length(input_sha256) = 32),   -- skip re-runs on unchanged input
  input_tokens   integer,
  output_tokens  integer,
  cost_usd       numeric(10,6),
  batch_id       text,
  task_run_id    bigint references ops.task_run(id),
  created_at     timestamptz not null default now(),
  -- exactly one target, or none plus a scope (e.g. the daily brief: scope 'date:2026-10-20')
  check (num_nonnulls(document_id, event_id, entity_id) = 1
         or (num_nonnulls(document_id, event_id, entity_id) = 0 and scope <> ''))
);
-- At most one published output per task and target: publishing a new one supersedes the old.
create unique index ai_output_one_published on core.ai_output
  (task, coalesce(document_id, 0), coalesce(event_id, 0), coalesce(entity_id, 0), scope)
  where status = 'published';
create index on core.ai_output (event_id) where status = 'published';
create index on core.ai_output (document_id) where status = 'published';

alter table core.forecast add foreign key (ai_output_id) references core.ai_output(id);

create table core.ai_citation (
  ai_output_id bigint not null references core.ai_output(id) on delete cascade,
  citation_no  smallint not null,
  claim_path   text not null,             -- JSON pointer into content: '/sentences/2'
  kind         text not null check (kind in ('document_span','observation','event')),
  document_id  bigint references core.document(id),
  char_start   integer,
  char_end     integer,
  series_id    integer,
  obs_date     date,
  event_id     bigint references core.event(id),
  verified     boolean not null default false,   -- the quoted text was found at these offsets
  primary key (ai_output_id, citation_no),
  foreign key (series_id, obs_date) references core.observation (series_id, obs_date),
  check (case kind
           when 'document_span' then document_id is not null and char_start >= 0 and char_end > char_start
           when 'observation'   then series_id is not null and obs_date is not null
           when 'event'         then event_id is not null
         end)
);

-- Database-level rule: an output whose task needs citations can't be published without a verified one.
-- A citation counts only if verified and, for a passage, inside a published document's text.
create function core.check_ai_output_citations() returns trigger language plpgsql as $$
begin
  if new.status = 'published'
     and (select requires_citations from core.ai_task where key = new.task)
     and not exists (select 1 from core.ai_citation c
                     left join core.document d on d.id = c.document_id
                     where c.ai_output_id = new.id and c.verified
                       and (c.kind <> 'document_span' or (d.is_published and c.char_end <= d.char_count))) then
    raise exception 'ai_output % (%) cannot be published without a verified citation', new.id, new.task;
  end if;
  return null;
end $$;
create constraint trigger ai_output_needs_citation
  after insert or update on core.ai_output
  deferrable initially deferred
  for each row execute function core.check_ai_output_citations();

-- Once published, an output is frozen: it can only be superseded or rejected (with a reason).
create function core.guard_ai_output() returns trigger language plpgsql as $$
begin
  if old.status <> 'draft' and
     (to_jsonb(new) - 'status' - 'status_reason' <> to_jsonb(old) - 'status' - 'status_reason'
      or not (old.status = 'published' and new.status in ('published','superseded','rejected'))) then
    raise exception 'ai_output % is % and can only move to superseded or rejected', old.id, old.status;
  end if;
  return new;
end $$;
create trigger guard before update on core.ai_output for each row execute function core.guard_ai_output();

-- Citations can be written only while their output is a draft.
create function core.guard_ai_citation() returns trigger language plpgsql as $$
begin
  if exists (select 1 from core.ai_output o
             where o.status <> 'draft'
               and o.id in (case when tg_op <> 'INSERT' then old.ai_output_id end,
                            case when tg_op <> 'DELETE' then new.ai_output_id end)) then
    raise exception 'citations of a non-draft ai_output are fixed';
  end if;
  return coalesce(new, old);
end $$;
create trigger guard before insert or update or delete on core.ai_citation
  for each row execute function core.guard_ai_citation();

---------------------------------------------------------------- housekeeping
create function core.touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;
create trigger touch before update on core.entity for each row execute function core.touch_updated_at();
create trigger touch before update on core.event  for each row execute function core.touch_updated_at();
