-- 0055: cmdbar_examples_admin (issue #588, job K-09 part 2; Spec 07 §4 Examples, §9 step 4). How the admin page reads and checks the
-- command bar's example answers. Approved by Anand, Tue 17 Nov 2026, 14:13 IST (option A on #588's question); number claimed on #30.
--
-- Why: the admin route holds the secret key, but `service_role` can't log in to Postgres and the Data API exposes the `api` schema
-- only (CLAUDE.md §4; the reason 0054 exists). 0054's api.cmdbar_examples() returns the checked answers only, so the admin page
-- couldn't show an unchecked one, and nothing in `api` could set `verified` (0010's column grant was for this). These two
-- `security definer` functions are how the admin route does both, called as RPC with the secret key (0016's and 0054's pattern):
-- owned by the migration role, `search_path` pinned, executable by `service_role` only. Only the admin route calls them, and it
-- verifies the admin's token first (`require_admin`).
--
-- What changes: the two functions and their comments. No table, column, index, policy, view, setting, field_policy row or table
-- grant changes. Nothing here can show the box or turn the AI on (Spec 07 §3): checking an answer only lets the box's chip list
-- show it once the box itself is shown.
--
--  * api.cmdbar_examples_admin(): every example, checked or not, with its id, question, module, display order, stored answer,
--    data version, `verified` and `updated_at`, in display order.
--  * api.cmdbar_example_check(p_id, p_verified): sets `verified` on one example and nothing else (`updated_at` is the task's: it
--    says when the answer last changed). Returns the row's id, `verified`, data version and `updated_at`, or null for an unknown
--    id. Marking an example checked is refused unless its stored answer is an answer (state `answered`): a missing answer, or a
--    "can't answer" or "not found" one, is never shown as a chip (decided by Claude under the standing delegation, 17 Nov 2026,
--    #588: it only tightens; the alternative was to let the admin check anything). Unchecking is always allowed.

create function api.cmdbar_examples_admin()
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', e.id, 'question', e.question, 'module', e.module,
                                               'display_order', e.display_order, 'answer', e.answer,
                                               'data_version', e.data_version, 'verified', e.verified,
                                               'updated_at', e.updated_at)
                            order by e.display_order, e.id), '[]'::jsonb)
    from ops.cmdbar_example e;
$$;

create function api.cmdbar_example_check(p_id integer, p_verified boolean)
returns jsonb
language plpgsql volatile security definer
set search_path = pg_catalog, ops
as $$
declare
  r ops.cmdbar_example;
begin
  if p_id is null or p_verified is null then
    raise exception 'cmdbar_example_check: an id and true or false are required' using errcode = '22023';
  end if;
  select * into r from ops.cmdbar_example e where e.id = p_id for update;
  if not found then
    return null;
  end if;
  if p_verified and (r.answer is null or jsonb_typeof(r.answer) <> 'object' or r.answer->>'state' is distinct from 'answered') then
    raise exception 'cmdbar_example_check: only an answered example can be marked checked' using errcode = '22023';
  end if;
  update ops.cmdbar_example e set verified = p_verified where e.id = p_id;
  return jsonb_build_object('id', r.id, 'verified', p_verified, 'data_version', r.data_version, 'updated_at', r.updated_at);
end;
$$;

------------------------------------------------------------------ who may run them: service_role only
revoke execute on function api.cmdbar_examples_admin()                from public, anon, authenticated;
revoke execute on function api.cmdbar_example_check(integer, boolean)  from public, anon, authenticated;
grant  execute on function api.cmdbar_examples_admin()                to service_role;
grant  execute on function api.cmdbar_example_check(integer, boolean)  to service_role;

comment on function api.cmdbar_examples_admin() is
  'Command bar (Spec 07 §4, §9): every example answer, checked or not, for the admin page. service_role only.';
comment on function api.cmdbar_example_check(integer, boolean) is
  'Command bar (Spec 07 §9): sets one example''s verified flag and nothing else; only an answered example can be checked. service_role only.';
