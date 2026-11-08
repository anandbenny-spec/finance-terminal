-- 0031: the labelling route's one read of transcript text (issue #175, job 2B-12; Spec 02B §3.2, §5.8; approved by Anand on
-- 8 Nov 2026: option 1 on #175).
--
-- The tone gate needs Anand to read 30 management passages. Transcript text is private (CLAUDE.md §1): no table with it is in the
-- Data API's `api` schema, and the api service has no database login but `sql_reader`, which sees the `semantic` views only. So the
-- admin route `GET /api/admin/labelling/items` reaches `core.chunk` the way 0016's routes reach `ops`: one `security definer`
-- function in `api`, called as RPC with the secret key, executable by `service_role` alone (PUBLIC lost EXECUTE on new functions in
-- 0001; anon and authenticated are revoked again here so no default privilege on the hosted project can hand it back).
--
-- It returns the text of exactly the chunks asked for, `[{document_id, chunk_no, text}, ...]` in document and chunk order, for at
-- most 60 chunks a call (more, a missing or non-array argument, or no match: `[]`). It reads and writes nothing else, so it is no way
-- to read a transcript wholesale. An element that is not `{document_id, chunk_no}` with whole numbers is an error.

create function api.admin_labelling_passages(p_items jsonb)
returns jsonb
language sql stable security definer
set search_path = pg_catalog, core
as $$
  select coalesce(jsonb_agg(jsonb_build_object('document_id', k.document_id, 'chunk_no', k.chunk_no, 'text', k.body)
                            order by k.document_id, k.chunk_no), '[]'::jsonb)
    from jsonb_to_recordset(case when jsonb_typeof(p_items) = 'array' and jsonb_array_length(p_items) <= 60
                                 then p_items else '[]'::jsonb end) as i(document_id bigint, chunk_no integer)
    join core.chunk k on k.document_id = i.document_id and k.chunk_no = i.chunk_no
$$;

revoke execute on function api.admin_labelling_passages(jsonb) from public, anon, authenticated;
grant  execute on function api.admin_labelling_passages(jsonb) to service_role;

comment on function api.admin_labelling_passages(jsonb) is
  'Admin labelling (Spec 02B §3.2): the text of the chunks asked for ([{document_id, chunk_no}], at most 60), as [{document_id, chunk_no, text}]. service_role only; the route behind it needs the admin role.';
