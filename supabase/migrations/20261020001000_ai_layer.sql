-- 0010: AI layer (Architecture 03).
-- ops: batch tracking, command-bar counters/cache/examples/log, evaluation runs.
-- core: a stored text-search column on core.chunk (measured need), its index, retrieval_owner policies,
--       and the public evaluation summary.
-- semantic: the retrieval function, callable only by sql_reader and never by generated SQL.

------------------------------------------------------------------ batch AI
create table ops.ai_batch (
  id                bigint generated always as identity primary key,
  provider          text not null default 'anthropic',
  provider_batch_id text not null unique,
  task_run_id       bigint not null references ops.task_run(id),
  submitted_at      timestamptz not null default now(),
  status            text not null default 'submitted'
                    check (status in ('submitted','ended','collected','failed')),
  request_count     integer not null,
  succeeded         integer,
  errored           integer,
  expired           integer,
  collected_at      timestamptz,
  cost_usd          numeric(10,4)
);

-- One row per request sent, so unchanged inputs are never sent twice and results map back to targets.
create table ops.ai_request (
  custom_id      text primary key,               -- '<task>:<target>:<first 16 hex of input hash>'
  ai_batch_id    bigint references ops.ai_batch(id),   -- null for real-time requests
  ai_task        text not null references core.ai_task(key),
  document_id    bigint references core.document(id),
  event_id       bigint references core.event(id),
  entity_id      integer references core.entity(id),
  scope          text not null default '',
  input_sha256   bytea not null check (length(input_sha256) = 32),
  model          text not null,
  prompt_version text not null,
  status         text not null default 'pending'
                 check (status in ('pending','succeeded','rejected','errored','expired')),
  ai_output_id   bigint references core.ai_output(id),
  reject_reason  text,
  created_at     timestamptz not null default now()
);
create index on ops.ai_request (input_sha256);
create index on ops.ai_request (status) where status = 'pending';

------------------------------------------------------------------ command bar
-- Rate limits and the daily cap, counted per window (serverless functions keep no state).
create table ops.cmdbar_usage (
  bucket       text not null,                    -- 'visitor:<salted hash>' (hourly) or 'global' (daily)
  window_start timestamptz not null,
  count        integer not null default 0,
  primary key (bucket, window_start)
);

create table ops.cmdbar_cache (
  question_sha bytea primary key check (length(question_sha) = 32),   -- of the normalised question
  question     text not null,
  route        text not null check (route in ('page','data','document','unsupported')),
  answer       jsonb not null,                   -- what the UI renders, including SQL and citations
  data_version text not null,                    -- freshness stamp of the modules used; a refresh invalidates
  hits         integer not null default 0,
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null
);

create table ops.cmdbar_example (
  id            integer generated always as identity primary key,
  question      text not null unique,
  module        text not null,
  display_order smallint not null,
  answer        jsonb,                           -- precomputed by the daily job
  data_version  text,
  verified      boolean not null default false,  -- the admin has checked this answer once
  updated_at    timestamptz not null default now()
);

create table ops.cmdbar_log (
  id               bigint generated always as identity primary key,
  at               timestamptz not null default now(),
  visitor_hash     text not null,
  question         text not null,
  route            text,
  cache_hit        boolean not null default false,
  generated_sql    text,
  sql_valid        boolean,
  reject_reason    text,
  rows_returned    integer,
  answered         boolean not null,
  latency_ms       integer,
  models           text[],
  input_tokens     integer,
  output_tokens    integer,
  cost_usd         numeric(10,6),
  error_class      text                          -- never the raw database error text
);
create index on ops.cmdbar_log (at);

------------------------------------------------------------------ evaluation
create table ops.eval_run (
  id             bigint generated always as identity primary key,
  eval_set       text not null,                  -- 'policy_hawk_dove', 'retrieval', 'cmdbar_sql', ...
  set_version    text not null,                  -- git SHA of the labelled set
  model          text not null,
  prompt_version text,
  started_at     timestamptz not null default now(),
  n_items        integer not null,
  metrics        jsonb not null,
  passed         boolean not null,
  notes          text
);
create table ops.eval_result (
  eval_run_id bigint not null references ops.eval_run(id),
  item_key    text not null,
  expected    jsonb not null,
  predicted   jsonb,
  correct     boolean,
  latency_ms  integer,
  cost_usd    numeric(10,6),
  primary key (eval_run_id, item_key)
);

-- The "How it's built" page's numbers: a public projection of passing runs (no items, no prompts).
create table core.eval_summary (
  eval_set    text not null,
  model       text not null,
  metric      text not null,
  value       numeric not null,
  n_items     integer not null,
  run_at      timestamptz not null,
  primary key (eval_set, model, metric)
);

------------------------------------------------------------------ grants and RLS for the new tables
do $$
declare t text;
begin
  foreach t in array array['ops.ai_batch','ops.ai_request','ops.cmdbar_usage','ops.cmdbar_cache',
                           'ops.cmdbar_example','ops.cmdbar_log','ops.eval_run','ops.eval_result','core.eval_summary'] loop
    execute format('alter table %s enable row level security', t);
    execute format('create policy job_writer_all on %s for all to job_writer using (true) with check (true)', t);
  end loop;
end $$;
grant select, insert, update on ops.ai_batch, ops.ai_request, ops.cmdbar_example, ops.eval_run, ops.eval_result,
                                core.eval_summary to job_writer;
grant select, delete on ops.cmdbar_log, ops.cmdbar_cache to job_writer;            -- retention clean-up only
grant usage on all sequences in schema ops to job_writer;
grant select, insert, update on ops.cmdbar_usage, ops.cmdbar_cache, ops.cmdbar_log to service_role;  -- the live route
grant select on ops.cmdbar_example, ops.ai_batch, ops.ai_request, ops.eval_run, ops.eval_result to service_role;
grant update (verified) on ops.cmdbar_example to service_role;                     -- admin marks an answer checked
grant usage on all sequences in schema ops to service_role;
grant select on core.eval_summary to anon, authenticated;
create policy read_all on core.eval_summary for select to anon, authenticated using (true);
create view api.eval_summary with (security_invoker = true) as
  select eval_set, model, metric, value, n_items, run_at from core.eval_summary;
grant select on api.eval_summary to anon, authenticated, service_role;

------------------------------------------------------------------ retrieval
-- Keyword half of hybrid search: a stored text-search column. Ranking recomputed it per matching chunk,
-- and a common term ('repo') matches nearly every RBI chunk: 3.6 s on 5,000 chunks, against 60-130 ms
-- with the stored column (measured 20 Oct). Cost about 1.3 KB per chunk. The one structural change
-- to the frozen core, made for a measured reason (Architecture 03 §1.6).
alter table core.chunk add column tsv tsvector generated always as (to_tsvector('english', body)) stored;
create index chunk_tsv on core.chunk using gin (tsv);

-- The retrieval function has its own owner. Under RLS, Postgres won't evaluate a non-leakproof qual
-- (text search's @@) ahead of the policy, so the keyword half couldn't use its index and took ~3 s on
-- 5,000 chunks (measured). retrieval_owner reads chunks and embeddings without a per-row policy check;
-- publication and suppression are still enforced, by joining every chunk to the RLS-filtered document
-- table inside the function. The semantic views keep semantic_owner and its strict policies.
create role retrieval_owner nologin noinherit;
grant retrieval_owner to postgres;
grant usage on schema core to retrieval_owner;
grant usage, create on schema semantic to retrieval_owner;
grant usage on schema extensions to retrieval_owner;
grant select on core.document, core.entity, core.event, core.source, core.chunk, core.chunk_embedding to retrieval_owner;
create policy retrieval_read on core.chunk           for select to retrieval_owner using (true);
create policy retrieval_read on core.chunk_embedding for select to retrieval_owner using (true);
create policy retrieval_read on core.document for select to retrieval_owner
  using (is_published and not exists (select 1 from core.source s where s.id = source_id and s.is_suppressed));
create policy retrieval_read on core.entity for select to retrieval_owner using (is_published);
create policy retrieval_read on core.event for select to retrieval_owner
  using (is_published and not exists (select 1 from core.source s where s.id = source_id and s.is_suppressed));
create policy retrieval_read on core.source for select to retrieval_owner using (true);

-- Hybrid search: vector and keyword candidates merged by reciprocal-rank fusion, both restricted to
-- the RLS-filtered documents. Only sql_reader may call it, and the text-to-SQL guard never allows it
-- (it is not on the relation or function allowlist).
create function semantic.search_chunks(
    query_embedding extensions.halfvec(512),
    query_text      text,
    banks           text[] default null,     -- entity slugs, e.g. '{rbi}'
    doc_types       text[] default null,
    date_from       date   default null,
    date_to         date   default null,
    k               integer default 8)
returns table (document_id bigint, chunk_no integer, char_start integer, char_end integer, section text,
               speaker text, body text, doc_type text, title text, entity_slug text, event_slug text,
               published_at timestamptz, source_url text, score double precision)
language sql stable security definer
set search_path = pg_catalog, extensions
set hnsw.iterative_scan = relaxed_order       -- keep scanning the vector index until enough rows pass the filters
as $$
  with docs as (                              -- RLS on document/entity/event applies here
    select d.id, d.doc_type, d.title, n.slug as entity_slug, ev.slug as event_slug, d.published_at, d.source_url
    from core.document d
    join core.entity n on n.id = d.entity_id
    left join core.event ev on ev.id = d.event_id
    where (banks is null or n.slug = any(banks))
      and (doc_types is null or d.doc_type = any(doc_types))
      and (date_from is null or d.published_at >= date_from)
      and (date_to is null or d.published_at < date_to + 1)
  ),
  vec as (
    select e.document_id, e.chunk_no, e.embedding <=> query_embedding as dist
    from core.chunk_embedding e
    where e.document_id in (select id from docs)
    order by e.embedding <=> query_embedding
    limit 40
  ),
  kw as (
    select c.document_id, c.chunk_no, ts_rank_cd(c.tsv, q) as rk
    from core.chunk c, websearch_to_tsquery('english', coalesce(query_text, '')) q
    where c.tsv @@ q
      and c.document_id in (select id from docs)
    order by rk desc
    limit 40
  ),
  fused as (
    select document_id, chunk_no, sum(1.0 / (60 + r)) as score
    from (select document_id, chunk_no, row_number() over (order by dist) as r from vec
          union all
          select document_id, chunk_no, row_number() over (order by rk desc) as r from kw) u
    group by document_id, chunk_no
  )
  select c.document_id, c.chunk_no, c.char_start, c.char_end, c.section, c.speaker, c.body,
         d.doc_type, d.title, d.entity_slug, d.event_slug, d.published_at, d.source_url, f.score
  from fused f
  join core.chunk c on c.document_id = f.document_id and c.chunk_no = f.chunk_no
  join docs d on d.id = f.document_id
  order by f.score desc
  limit k
$$;
alter function semantic.search_chunks(extensions.halfvec, text, text[], text[], date, date, integer) owner to retrieval_owner;
revoke all on function semantic.search_chunks(extensions.halfvec, text, text[], text[], date, date, integer) from public;
grant execute on function semantic.search_chunks(extensions.halfvec, text, text[], text[], date, date, integer) to sql_reader;
-- sql_reader needs the vector type's schema to pass the query embedding in.
grant usage on schema extensions to sql_reader;

------------------------------------------------------------------ module 1 AI tasks: field policy unchanged; spend switch
insert into ops.setting (key, value, description) values
  ('ai_monthly_budget_usd', '15', 'Batch AI stops submitting and alerts when month-to-date spend reaches this; the Claude Console limit is the hard stop.'),
  ('ai_realtime_on_decision_days', 'true', 'Decision-day documents use the real-time API so the summary is up within about half an hour.');
