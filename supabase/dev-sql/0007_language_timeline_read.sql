-- 0007_language_timeline_read: #509 (the language timeline on /policy), step 1's measurement. DEV ONLY, through the dev-sql workflow.
-- Times the read the section makes as a visitor (role anon, the Data API's 3 s limit): each bank's decision documents' stored diffs,
-- `segments->version` and `segments->pairs`, twelve at a time in document order (web/lib/queries/policy.ts, readDecisionDiffPairs),
-- and the exact count PostgREST takes first. The ids are each meeting's latest decision document (the FOMC statement, the MPC
-- resolution). Each page is run as the JSON PostgREST would send (json_agg), and its size is that text's bytes in UTF-8.
--
-- The limits (the job card, build step 1): a page of 12 diffs must read inside 3 s, and its response must stay under 2 MB
-- (2000000 bytes). Over either, the job stops with needs-anand: a slim view would need a migration.
--
-- Read only: no field policy, no row and no setting is changed, and the file ends with ROLLBACK anyway. It selects counts, sizes and
-- times, never rows. The one notice holds, for each bank, the number of decision documents, the number of stored diffs, the count
-- query's time, then each page as ms/bytes ('none' past the last page, 'TIMEOUT (3 s)' if the limit cancels a page), then the
-- slowest page's time and the biggest page's bytes. No slug, no id, no row content.
set local role anon;
set local statement_timeout = '3s';

do $$
begin
  perform set_config('t.ids_rbi', coalesce((select string_agg(id::text, ',' order by id) from (
    select distinct on (event_slug) id from api.documents
    where doc_type = 'mpc_resolution' and event_slug is not null order by event_slug, version_no desc) d), ''), true);
end $$;

do $$
begin
  perform set_config('t.ids_fed', coalesce((select string_agg(id::text, ',' order by id) from (
    select distinct on (event_slug) id from api.documents
    where doc_type = 'fomc_statement' and event_slug is not null order by event_slug, version_no desc) d), ''), true);
end $$;

do $$
declare t0 timestamptz; n int;
begin
  if current_setting('t.ids_rbi', true) = '' then
    raise exception 'dev holds no rbi decision documents to time';
  end if;
  perform set_config('t.docs_rbi', array_length(string_to_array(current_setting('t.ids_rbi'), ','), 1)::text, true);
  t0 := clock_timestamp();
  execute format('select count(*) from api.document_diffs where document_id in (%s)', current_setting('t.ids_rbi')) into n;
  perform set_config('t.n_rbi', n::text, true);
  perform set_config('t.c_rbi', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms', true);
exception when query_canceled then
  perform set_config('t.n_rbi', '0', true);
  perform set_config('t.c_rbi', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; n int;
begin
  if current_setting('t.ids_fed', true) = '' then
    raise exception 'dev holds no fed decision documents to time';
  end if;
  perform set_config('t.docs_fed', array_length(string_to_array(current_setting('t.ids_fed'), ','), 1)::text, true);
  t0 := clock_timestamp();
  execute format('select count(*) from api.document_diffs where document_id in (%s)', current_setting('t.ids_fed')) into n;
  perform set_config('t.n_fed', n::text, true);
  perform set_config('t.c_fed', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms', true);
exception when query_canceled then
  perform set_config('t.n_fed', '0', true);
  perform set_config('t.c_fed', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_rbi')::int <= 0 then
    perform set_config('t.rbi_p0', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 0 limit 12) t', current_setting('t.ids_rbi')) into bytes;
  perform set_config('t.rbi_p0', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.rbi_p0', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_rbi')::int <= 12 then
    perform set_config('t.rbi_p1', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 12 limit 12) t', current_setting('t.ids_rbi')) into bytes;
  perform set_config('t.rbi_p1', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.rbi_p1', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_rbi')::int <= 24 then
    perform set_config('t.rbi_p2', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 24 limit 12) t', current_setting('t.ids_rbi')) into bytes;
  perform set_config('t.rbi_p2', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.rbi_p2', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_rbi')::int <= 36 then
    perform set_config('t.rbi_p3', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 36 limit 12) t', current_setting('t.ids_rbi')) into bytes;
  perform set_config('t.rbi_p3', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.rbi_p3', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_rbi')::int <= 48 then
    perform set_config('t.rbi_p4', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 48 limit 12) t', current_setting('t.ids_rbi')) into bytes;
  perform set_config('t.rbi_p4', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.rbi_p4', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_rbi')::int <= 60 then
    perform set_config('t.rbi_p5', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 60 limit 12) t', current_setting('t.ids_rbi')) into bytes;
  perform set_config('t.rbi_p5', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.rbi_p5', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_fed')::int <= 0 then
    perform set_config('t.fed_p0', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 0 limit 12) t', current_setting('t.ids_fed')) into bytes;
  perform set_config('t.fed_p0', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.fed_p0', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_fed')::int <= 12 then
    perform set_config('t.fed_p1', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 12 limit 12) t', current_setting('t.ids_fed')) into bytes;
  perform set_config('t.fed_p1', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.fed_p1', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_fed')::int <= 24 then
    perform set_config('t.fed_p2', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 24 limit 12) t', current_setting('t.ids_fed')) into bytes;
  perform set_config('t.fed_p2', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.fed_p2', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_fed')::int <= 36 then
    perform set_config('t.fed_p3', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 36 limit 12) t', current_setting('t.ids_fed')) into bytes;
  perform set_config('t.fed_p3', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.fed_p3', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_fed')::int <= 48 then
    perform set_config('t.fed_p4', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 48 limit 12) t', current_setting('t.ids_fed')) into bytes;
  perform set_config('t.fed_p4', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.fed_p4', 'TIMEOUT (3 s)', true);
end $$;

do $$
declare t0 timestamptz; bytes int;
begin
  if current_setting('t.n_fed')::int <= 60 then
    perform set_config('t.fed_p5', 'none', true);
    return;
  end if;
  t0 := clock_timestamp();
  execute format('select octet_length(coalesce(json_agg(t), ''[]'')::text) from (select document_id, base_document_id, segments->''version'' as version, segments->''pairs'' as pairs from api.document_diffs where document_id in (%s) order by document_id offset 60 limit 12) t', current_setting('t.ids_fed')) into bytes;
  perform set_config('t.fed_p5', round(extract(epoch from clock_timestamp() - t0) * 1000)::text || 'ms/' || bytes || 'B', true);
exception when query_canceled then
  perform set_config('t.fed_p5', 'TIMEOUT (3 s)', true);
end $$;

set local role service_role;
copy (select '::notice title=#509 language-timeline pairs read on dev (anon, 3 s limit, 12 per page)::' || 'RBI: ' || current_setting('t.docs_rbi') || ' decision documents, ' || current_setting('t.n_rbi') || ' stored diffs, count ' || current_setting('t.c_rbi') || ' | pages (ms/bytes): p0 ' || coalesce(current_setting('t.rbi_p0', true), '-') || ' | p1 ' || coalesce(current_setting('t.rbi_p1', true), '-') || ' | p2 ' || coalesce(current_setting('t.rbi_p2', true), '-') || ' | p3 ' || coalesce(current_setting('t.rbi_p3', true), '-') || ' | p4 ' || coalesce(current_setting('t.rbi_p4', true), '-') || ' | p5 ' || coalesce(current_setting('t.rbi_p5', true), '-') || ' | ' || (select 'slowest ' || coalesce(max(split_part(v, 'ms/', 1)::int)::text, '-') || 'ms, biggest ' || coalesce(max(replace(split_part(v, 'ms/', 2), 'B', '')::int)::text, '-') || ' B, timeouts ' || (select count(*) from unnest(array[current_setting('t.rbi_p0', true), current_setting('t.rbi_p1', true), current_setting('t.rbi_p2', true), current_setting('t.rbi_p3', true), current_setting('t.rbi_p4', true), current_setting('t.rbi_p5', true)]) w where w like 'TIMEOUT%') from unnest(array[current_setting('t.rbi_p0', true), current_setting('t.rbi_p1', true), current_setting('t.rbi_p2', true), current_setting('t.rbi_p3', true), current_setting('t.rbi_p4', true), current_setting('t.rbi_p5', true)]) v where v ~ '^[0-9]') || ' ; ' || 'Fed: ' || current_setting('t.docs_fed') || ' decision documents, ' || current_setting('t.n_fed') || ' stored diffs, count ' || current_setting('t.c_fed') || ' | pages (ms/bytes): p0 ' || coalesce(current_setting('t.fed_p0', true), '-') || ' | p1 ' || coalesce(current_setting('t.fed_p1', true), '-') || ' | p2 ' || coalesce(current_setting('t.fed_p2', true), '-') || ' | p3 ' || coalesce(current_setting('t.fed_p3', true), '-') || ' | p4 ' || coalesce(current_setting('t.fed_p4', true), '-') || ' | p5 ' || coalesce(current_setting('t.fed_p5', true), '-') || ' | ' || (select 'slowest ' || coalesce(max(split_part(v, 'ms/', 1)::int)::text, '-') || 'ms, biggest ' || coalesce(max(replace(split_part(v, 'ms/', 2), 'B', '')::int)::text, '-') || ' B, timeouts ' || (select count(*) from unnest(array[current_setting('t.fed_p0', true), current_setting('t.fed_p1', true), current_setting('t.fed_p2', true), current_setting('t.fed_p3', true), current_setting('t.fed_p4', true), current_setting('t.fed_p5', true)]) w where w like 'TIMEOUT%') from unnest(array[current_setting('t.fed_p0', true), current_setting('t.fed_p1', true), current_setting('t.fed_p2', true), current_setting('t.fed_p3', true), current_setting('t.fed_p4', true), current_setting('t.fed_p5', true)]) v where v ~ '^[0-9]')) to stdout;

rollback;
