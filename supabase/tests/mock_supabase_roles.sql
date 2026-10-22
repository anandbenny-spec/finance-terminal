-- TEST ONLY (local and CI): stands in for the roles and schema Supabase already provides. Never run on Supabase.
create role anon nologin noinherit;
create role authenticated nologin noinherit;
create role service_role nologin noinherit bypassrls;
create role authenticator login noinherit;
grant anon, authenticated, service_role to authenticator;
create schema extensions;
create extension vector schema extensions;
create extension btree_gist schema extensions;
grant usage on schema extensions to public;
