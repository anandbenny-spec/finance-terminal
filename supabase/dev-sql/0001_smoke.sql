-- 0001_smoke: proves the dev-sql workflow end to end (#101): the dev_sql login, the pooler, one transaction and a role switch.
-- Every file in this folder starts with `set local role`: the login holds no privileges of its own (anon, service_role
-- or job_writer). The output is one count, never rows.
set local role service_role;
select count(*) as sources from core.source;
