-- Day-1 check 3 (T22, Architecture 01 §8; GitHub issue #1) on a hosted project. Read-only: it changes nothing.
-- Paste into the project's SQL editor (Dashboard → SQL Editor → New query), run, and copy the result table into
-- issue #1. Dev's result (22 Oct): 360 functions, all in extensions; reset not executable; no pg_net/http/dblink;
-- only semantic.search_chunks in our schemas; both definer functions pin their search path.
with f as (
  select n.nspname, p.oid
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname not in ('pg_catalog', 'information_schema', 'ops', 'core', 'policy', 'api', 'semantic')
    and has_schema_privilege('sql_reader', n.oid, 'usage')
    and has_function_privilege('sql_reader', p.oid, 'execute')
)
select 1 as n, 'T22 functions sql_reader can execute outside pg_catalog and our schemas' as item,
       count(*)::text as result from f
union all
select 2, 'T22 … by schema',
       coalesce((select string_agg(nspname || ' ' || c, ', ' order by nspname)
                 from (select nspname, count(*) as c from f group by nspname) s), 'none')
union all
select 3, 'pg_stat_statements_reset executable by sql_reader',
       coalesce((select string_agg(distinct has_function_privilege('sql_reader', p.oid, 'execute')::text, ',')
                 from pg_proc p where p.proname = 'pg_stat_statements_reset'), 'not installed')
union all
select 4, 'pg_net, http, dblink installed',
       coalesce((select string_agg(extname, ', ') from pg_extension where extname in ('pg_net', 'http', 'dblink')), 'none')
union all
select 5, 'T21 functions in our schemas sql_reader can execute',
       coalesce((select string_agg(n.nspname || '.' || p.proname, ', ')
                 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname in ('ops', 'core', 'policy', 'api', 'semantic')
                   and has_function_privilege('sql_reader', p.oid, 'execute')), 'none')
union all
select 6, 'security definer functions in our schemas and their search_path',
       coalesce((select string_agg(n.nspname || '.' || p.proname || ' [' || coalesce(array_to_string(p.proconfig, ';'), 'NOT PINNED') || ']', ', ')
                 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname in ('ops', 'core', 'policy', 'api', 'semantic') and p.prosecdef), 'none')
union all
select 7, 'functions in our schemas without a pinned search_path (advisor finding 1; 0013 fixes these)',
       coalesce((select string_agg(n.nspname || '.' || p.proname, ', ' order by p.proname)
                 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname in ('ops', 'core', 'policy', 'api', 'semantic')
                   and not coalesce(array_to_string(p.proconfig, ';') like '%search_path=%', false)), 'none')
union all
select 8, 'Postgres and pgvector',
       split_part(version(), ' ', 2) || ' / vector ' || coalesce((select extversion from pg_extension where extname = 'vector'), 'missing')
order by n;
