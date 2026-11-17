-- 0054: cmdbar_route (issue #583, job K-05 part 2; Spec 07 §4, §8, N7, N8). How the command bar's live route reaches `ops`.
-- Approved by Anand, Tue 17 Nov 2026, about 06:35 IST ("Yes to A and B", on #583's question); number claimed on #30.
--
-- Why: 0010 granted the live route (`service_role`) what it needs on ops.cmdbar_usage, ops.cmdbar_cache, ops.cmdbar_log and
-- ops.cmdbar_example, and Spec 07 §2 counted on that. But, as 0016 found for the admin routes, `service_role` can't log in to
-- Postgres and the Data API exposes the `api` schema only (CLAUDE.md §4), so the route can't reach `ops` at all. These seven
-- `security definer` functions in `api` are how it does, called as RPC with the secret key (0016's pattern, Anand's option B on
-- #8): owned by the migration role, `search_path` pinned, executable by `service_role` only (PUBLIC lost EXECUTE on new functions
-- in 0001; anon and authenticated are revoked again here so no default privilege on the hosted project can hand them back).
--
-- What changes: the seven functions and their comments. No table, column, index, policy, view, setting, field_policy row or table
-- grant changes. Nothing here can turn the bar on: the switch is ops.setting.cmdbar_ai_enabled (false since 0052), and the box's
-- field policy site.command_bar has no row (Spec 07 §3).
--
--  * api.cmdbar_state(): what the route's step 0 reads (Spec 07 §4 step 0, N8): the switch (true only when the setting is JSON
--    true), the two limits, the monthly AI budget, and the month's AI spend so far (ops.task_run.ai_cost_usd, every task, plus
--    ops.cmdbar_log.cost_usd, the bar's own answers) since the start of the UTC month, the batch path's month (ft/ai/batch.py).
--    Numbers go out as text, so they stay exact.
--  * api.cmdbar_count(bucket, window_start): one atomic increment of ops.cmdbar_usage (insert ... on conflict ... do update ...
--    returning count). The bucket is 'global' (the IST day) or 'visitor:<64 hex>' (N7's daily-rotating hash, the hour). A window
--    must start within the last 25 hours. Each call also deletes usage windows older than two days: the visitor hashes in them
--    are kept no longer than the limits need (decided by Claude under the standing delegation, 17 Nov 2026, #583: it only
--    tightens; job_writer holds no DELETE on ops.cmdbar_usage, so the retention task can't, and no grant changes here).
--  * api.cmdbar_cache_get(sha), api.cmdbar_cache_put(...), api.cmdbar_cache_hit(sha): the answer cache (Spec 07 §4 step 3), keyed
--    by the hex SHA-256 of the normalised question. An expired entry is never returned. A put replaces the entry, and its expiry
--    is held to at most 24 hours ahead.
--  * api.cmdbar_log(row): one ops.cmdbar_log row from the row's named columns only (Spec 07 §4 step 6). No IP address, user
--    agent or cookie is a column; the visitor hash must be 64 hex characters.
--  * api.cmdbar_examples(): the example answers the admin marked checked (`verified`), in display order (Spec 07 §4, K-09).

------------------------------------------------------------------ step 0: the switch, the limits, the budget (N8)
create function api.cmdbar_state()
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
    'ai_enabled', coalesce((select s.value = 'true'::jsonb from ops.setting s where s.key = 'cmdbar_ai_enabled'), false),
    'daily_cap', (select (s.value #>> '{}')::integer from ops.setting s where s.key = 'cmdbar_daily_cap'),
    'per_visitor_hourly', (select (s.value #>> '{}')::integer from ops.setting s where s.key = 'cmdbar_per_visitor_hourly'),
    'monthly_budget_usd', (select (s.value #>> '{}')::numeric from ops.setting s where s.key = 'ai_monthly_budget_usd')::text,
    'month_spend_usd', ((select coalesce(sum(r.ai_cost_usd), 0) from ops.task_run r
                          where r.started_at >= date_trunc('month', now(), 'UTC'))
                      + (select coalesce(sum(l.cost_usd), 0) from ops.cmdbar_log l
                          where l.at >= date_trunc('month', now(), 'UTC')))::text);
$$;

------------------------------------------------------------------ step 2: the counts
create function api.cmdbar_count(p_bucket text, p_window_start timestamptz)
returns integer
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  n integer;
begin
  if p_bucket is null or not (p_bucket = 'global' or p_bucket ~ '^visitor:[0-9a-f]{64}$') then
    raise exception 'cmdbar_count: the bucket must be global or visitor:<64 hex>' using errcode = '22023';
  end if;
  if p_window_start is null or p_window_start > now() + interval '5 minutes'
     or p_window_start < now() - interval '25 hours' then
    raise exception 'cmdbar_count: the window must start within the last 25 hours' using errcode = '22023';
  end if;
  delete from ops.cmdbar_usage u where u.window_start < now() - interval '2 days';
  insert into ops.cmdbar_usage as u (bucket, window_start, count) values (p_bucket, p_window_start, 1)
  on conflict (bucket, window_start) do update set count = u.count + 1
  returning u.count into n;
  return n;
end;
$$;

------------------------------------------------------------------ step 3: the cache
create function api.cmdbar_cache_get(p_question_sha text)
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object('route', c.route, 'answer', c.answer, 'data_version', c.data_version)
    from ops.cmdbar_cache c
   where c.question_sha = decode(p_question_sha, 'hex') and c.expires_at > now();
$$;

create function api.cmdbar_cache_put(p_question_sha text, p_question text, p_route text, p_answer jsonb,
                                     p_data_version text, p_expires_at timestamptz)
returns void
language sql volatile security definer
set search_path = pg_catalog, ops
as $$
  insert into ops.cmdbar_cache as c (question_sha, question, route, answer, data_version, expires_at)
  values (decode(p_question_sha, 'hex'), p_question, p_route, p_answer, p_data_version,
          least(p_expires_at, now() + interval '24 hours'))
  on conflict (question_sha) do update
     set question = excluded.question, route = excluded.route, answer = excluded.answer,
         data_version = excluded.data_version, hits = 0, created_at = now(), expires_at = excluded.expires_at;
$$;

create function api.cmdbar_cache_hit(p_question_sha text)
returns void
language sql volatile security definer
set search_path = pg_catalog, ops
as $$
  update ops.cmdbar_cache set hits = hits + 1 where question_sha = decode(p_question_sha, 'hex');
$$;

------------------------------------------------------------------ step 6: the log
create function api.cmdbar_log(p_row jsonb)
returns bigint
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  rid bigint;
begin
  if p_row is null or jsonb_typeof(p_row) <> 'object' or coalesce(p_row->>'visitor_hash', '') !~ '^[0-9a-f]{64}$' then
    raise exception 'cmdbar_log: a row with a 64-hex visitor_hash is required' using errcode = '22023';
  end if;
  insert into ops.cmdbar_log (visitor_hash, question, route, cache_hit, generated_sql, sql_valid, reject_reason, rows_returned,
                              answered, latency_ms, models, input_tokens, output_tokens, cost_usd, error_class)
  values (p_row->>'visitor_hash', p_row->>'question', p_row->>'route', coalesce((p_row->>'cache_hit')::boolean, false),
          p_row->>'generated_sql', (p_row->>'sql_valid')::boolean, p_row->>'reject_reason', (p_row->>'rows_returned')::integer,
          (p_row->>'answered')::boolean, (p_row->>'latency_ms')::integer,
          case when jsonb_typeof(p_row->'models') = 'array'
               then array(select jsonb_array_elements_text(p_row->'models')) end,
          (p_row->>'input_tokens')::integer, (p_row->>'output_tokens')::integer, (p_row->>'cost_usd')::numeric,
          p_row->>'error_class')
  returning id into rid;
  return rid;
end;
$$;

------------------------------------------------------------------ the example chips
create function api.cmdbar_examples()
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select coalesce(jsonb_agg(jsonb_build_object('question', e.question, 'module', e.module, 'display_order', e.display_order,
                                               'answer', e.answer, 'verified', e.verified)
                            order by e.display_order, e.id), '[]'::jsonb)
    from ops.cmdbar_example e
   where e.verified;
$$;

------------------------------------------------------------------ who may run them: service_role only
revoke execute on function api.cmdbar_state()                                          from public, anon, authenticated;
revoke execute on function api.cmdbar_count(text, timestamptz)                         from public, anon, authenticated;
revoke execute on function api.cmdbar_cache_get(text)                                  from public, anon, authenticated;
revoke execute on function api.cmdbar_cache_put(text, text, text, jsonb, text, timestamptz) from public, anon, authenticated;
revoke execute on function api.cmdbar_cache_hit(text)                                  from public, anon, authenticated;
revoke execute on function api.cmdbar_log(jsonb)                                       from public, anon, authenticated;
revoke execute on function api.cmdbar_examples()                                       from public, anon, authenticated;
grant  execute on function api.cmdbar_state()                                          to service_role;
grant  execute on function api.cmdbar_count(text, timestamptz)                         to service_role;
grant  execute on function api.cmdbar_cache_get(text)                                  to service_role;
grant  execute on function api.cmdbar_cache_put(text, text, text, jsonb, text, timestamptz) to service_role;
grant  execute on function api.cmdbar_cache_hit(text)                                  to service_role;
grant  execute on function api.cmdbar_log(jsonb)                                       to service_role;
grant  execute on function api.cmdbar_examples()                                       to service_role;

comment on function api.cmdbar_state() is
  'Command bar (Spec 07 §4 step 0, N8): the switch, the two limits, the monthly AI budget and the month''s AI spend so far. service_role only.';
comment on function api.cmdbar_count(text, timestamptz) is
  'Command bar (Spec 07 §4 step 2): one atomic increment of a usage window (global per IST day, visitor:<hash> per hour); returns the count. service_role only.';
comment on function api.cmdbar_cache_get(text) is
  'Command bar (Spec 07 §4 step 3): the unexpired cached answer for a question''s hex SHA-256, or null. service_role only.';
comment on function api.cmdbar_cache_put(text, text, text, jsonb, text, timestamptz) is
  'Command bar (Spec 07 §4 step 3): stores or replaces a cached answer, expiring at most 24 hours ahead. service_role only.';
comment on function api.cmdbar_cache_hit(text) is
  'Command bar (Spec 07 §4 step 3): counts one hit on a cached answer. service_role only.';
comment on function api.cmdbar_log(jsonb) is
  'Command bar (Spec 07 §4 step 6): one ops.cmdbar_log row from its named columns (no IP address or user agent). service_role only.';
comment on function api.cmdbar_examples() is
  'Command bar (Spec 07 §4): the example answers the admin marked checked, in display order. service_role only.';
