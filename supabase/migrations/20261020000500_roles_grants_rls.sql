-- 0005: roles, grants, row-level security
-- Supabase provides anon, authenticated (the single admin), service_role (bypasses RLS) and postgres.
-- Passwords for the two login roles are set by hand in the SQL editor and stored as secrets,
-- never written into a migration.

create role job_writer     login   noinherit;   -- GitHub Actions jobs, via the shared pooler (session mode)
create role sql_reader     login   noinherit;   -- command bar text-to-SQL, via the shared pooler (transaction mode)
create role semantic_owner nologin noinherit;   -- owns the semantic views; nobody logs in as it
grant semantic_owner to postgres;               -- lets the migration hand view ownership over (postgres is not a superuser on Supabase)

alter role sql_reader set search_path = semantic;
alter role sql_reader set statement_timeout = '3s';
alter role sql_reader set lock_timeout = '1s';
alter role sql_reader set idle_in_transaction_session_timeout = '5s';
alter role sql_reader set default_transaction_read_only = on;
alter role sql_reader connection limit 5;
alter role job_writer set statement_timeout = '15min';
alter role job_writer connection limit 4;

------------------------------------------------------------------ schema access
grant usage on schema core, policy to anon, authenticated, semantic_owner, job_writer, service_role;
grant usage on schema ops                to job_writer, service_role;
grant usage on schema api                to anon, authenticated, service_role;
grant usage, create on schema semantic   to semantic_owner;
grant usage on schema semantic           to sql_reader;   -- the ONLY schema sql_reader can use

------------------------------------------------------------------ table privileges
-- Visitors (anon) and the admin's browser session (authenticated) read published rows through the
-- api views, which run as the caller (security_invoker), so they need SELECT on the base tables too;
-- the base schemas are not exposed through the Data API, and RLS filters every row.
grant select on core.source, core.field_policy, core.entity, core.task_freshness,
                core.event_type, core.event_status, core.event, core.doc_type, core.document,
                core.chunk, core.document_diff, core.series, core.observation, core.series_label,
                core.forecast, core.ai_task, core.ai_output, core.ai_citation,
                policy.meeting, policy.rate_setting, policy.member, policy.member_term,
                policy.vote, policy.market_reaction
  to anon, authenticated;

grant select on all tables in schema core, policy to semantic_owner, service_role;

-- Jobs: write everything, but source data is append-only (no DELETE on observations, revisions,
-- raw files, documents or AI outputs; AI outputs change status instead).
grant select, insert, update on all tables in schema core, policy, ops to job_writer;
grant delete on core.chunk, core.chunk_embedding, core.document_diff, core.forecast,
                core.series_label, core.entity_identifier, core.task_freshness,
                policy.rate_setting, policy.vote, policy.market_reaction, policy.member_term
  to job_writer;
-- The revision log is written only by its (security definer) trigger.
revoke insert, update on core.observation_revision from job_writer;
-- Citations are written once, while their output is a draft.
revoke update on core.ai_citation from job_writer;
-- Editorial switches and catalogues: migrations and the admin only, never a job.
revoke insert, update on core.source, core.field_policy, core.ai_task, core.doc_type,
                         core.event_type, core.event_status from job_writer;
revoke insert, update on ops.setting from job_writer;
grant usage on all sequences in schema core, policy, ops to job_writer;

-- Vercel API routes (admin actions, command-bar counters) use service_role.
grant select, insert, update on all tables in schema ops to service_role;
-- The admin panel's switches (through Vercel routes that check the admin session first).
grant update (is_suppressed) on core.source to service_role;
grant update (status, label, reason, updated_at) on core.field_policy to service_role;
grant usage on all sequences in schema ops to service_role;

------------------------------------------------------------------ row-level security: on everywhere
do $$
declare t record;
begin
  for t in select schemaname, tablename from pg_tables where schemaname in ('ops','core','policy') loop
    execute format('alter table %I.%I enable row level security', t.schemaname, t.tablename);
    execute format('create policy job_writer_all on %I.%I for all to job_writer using (true) with check (true)',
                   t.schemaname, t.tablename);
  end loop;
end $$;
-- ops tables get no other policies: invisible to anon, authenticated, semantic_owner and sql_reader.

-- Public catalogues
create policy read_all on core.source        for select to anon, authenticated, semantic_owner using (true);
create policy read_all on core.field_policy  for select to anon, authenticated, semantic_owner using (true);
create policy read_all on core.task_freshness for select to anon, authenticated, semantic_owner using (true);
create policy read_all on core.event_type    for select to anon, authenticated, semantic_owner using (true);
create policy read_all on core.event_status  for select to anon, authenticated, semantic_owner using (true);
create policy read_all on core.doc_type      for select to anon, authenticated, semantic_owner using (true);
create policy read_all on core.ai_task       for select to anon, authenticated, semantic_owner using (true);
create policy read_all on policy.member      for select to anon, authenticated, semantic_owner using (true);
create policy read_all on policy.member_term for select to anon, authenticated, semantic_owner using (true);

-- Parents: published, and not from a suppressed source
create policy read_published on core.entity for select to anon, authenticated, semantic_owner
  using (is_published);
create policy read_published on core.event for select to anon, authenticated, semantic_owner
  using (is_published and not exists (select 1 from core.source s where s.id = source_id and s.is_suppressed));
create policy read_published on core.document for select to anon, authenticated, semantic_owner
  using (is_published and not exists (select 1 from core.source s where s.id = source_id and s.is_suppressed));
create policy read_published on core.series for select to anon, authenticated, semantic_owner
  using (is_published and not exists (select 1 from core.source s where s.id = source_id and s.is_suppressed));
-- Field policy is enforced here, at the row, not only in views (a view-level gate leaked through
-- error messages in review).
create policy read_published on core.ai_output for select to anon, authenticated, semantic_owner
  using (status = 'published'
     and exists (select 1 from core.field_policy f
                 where f.field_key = 'ai.' || task and f.status in ('shown','labelled'))
     and (document_id is null or exists (select 1 from core.document d where d.id = document_id))
     and (event_id    is null or exists (select 1 from core.event e    where e.id = event_id))
     and (entity_id   is null or exists (select 1 from core.entity n   where n.id = entity_id)));

-- Children: visible when the parent is visible. The subquery is itself filtered by the parent's
-- policy, so publication and suppression rules live in one place.
create policy read_published on core.chunk for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.document d where d.id = document_id));
create policy read_published on core.document_diff for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.document d where d.id = document_id));
create policy read_published on core.observation for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.series s where s.id = series_id));
create policy read_published on core.series_label for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.series s where s.id = series_id));
create policy read_published on core.forecast for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.series s where s.id = series_id)
     and (method <> 'ai_extracted' or exists (select 1 from core.ai_output o where o.id = ai_output_id)));
create policy read_published on core.ai_citation for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.ai_output o where o.id = ai_output_id));
create policy read_published on policy.meeting for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event e where e.id = event_id));
create policy read_published on policy.rate_setting for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event e where e.id = meeting_event_id));
create policy read_published on policy.vote for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event e where e.id = meeting_event_id));
create policy read_published on policy.market_reaction for select to anon, authenticated, semantic_owner
  using (exists (select 1 from core.event e where e.id = meeting_event_id)
     and exists (select 1 from core.series s where s.id = series_id));
-- No visitor policy (so no visitor access) on: core.raw_file, core.chunk_embedding,
-- core.observation_revision, core.entity_identifier, and every ops table.
