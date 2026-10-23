-- 0013: hardening from the day-1 checks (GitHub issue #1; decided by Anand 22–23 Oct 2026; Setup 01 §6).
-- No table, view or row changes.

------------------------------------------------------------------ pinned search paths (advisor finding 1)
-- Supabase's advisor flags functions whose search_path the caller controls (function_search_path_mutable).
-- These six run with the caller's rights (security invoker), qualify every table and call only built-ins,
-- so pinning the path changes nothing they do; it only stops a caller's search_path from redirecting a name.
alter function core.guard_document()            set search_path = pg_catalog, core;
alter function core.check_document_chunks()     set search_path = pg_catalog, core;
alter function core.check_ai_output_citations() set search_path = pg_catalog, core;
alter function core.guard_ai_output()           set search_path = pg_catalog, core;
alter function core.guard_ai_citation()         set search_path = pg_catalog, core;
alter function core.touch_updated_at()          set search_path = pg_catalog, core;

------------------------------------------------------------------ job_writer may use the extensions schema
-- Supabase grants `extensions` to postgres, anon, authenticated and service_role, not to PUBLIC, so job_writer
-- couldn't name extensions.halfvec: the chunk-embedding writes (Prompt 01 B7) failed with "permission denied
-- for schema extensions" (day-1 item 5, 22 Oct; same on the hosted dev project). sql_reader and retrieval_owner
-- were granted it in 0010. USAGE lets job_writer resolve names there; what each function or type allows is unchanged.
grant usage on schema extensions to job_writer;
