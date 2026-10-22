-- 0001: extensions, schemas, function hygiene
-- On Supabase the `extensions` schema already exists.
create extension if not exists vector with schema extensions;      -- pgvector (halfvec needs 0.7+)
create extension if not exists btree_gist with schema extensions;  -- exclusion constraints on date ranges

create schema ops;       -- jobs, settings, refresh requests: never visible to visitors or text-to-SQL
create schema core;      -- the four engines' shared tables
create schema policy;    -- module 1: Fed vs RBI policy tracker
create schema api;       -- the ONLY schema exposed through Supabase's Data API (views for the site)
create schema semantic;  -- views for text-to-SQL (not exposed through the Data API)

-- Postgres grants EXECUTE on new functions to PUBLIC. A per-schema ALTER DEFAULT PRIVILEGES cannot
-- remove that built-in default (tested: it is a no-op), so revoke it for every function the migration
-- role creates, and grant EXECUTE explicitly where a role needs it.
alter default privileges revoke execute on functions from public;
