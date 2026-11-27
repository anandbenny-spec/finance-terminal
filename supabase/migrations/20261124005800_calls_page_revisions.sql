-- #620: private, transactional invalidation and prepared response delivery.
-- Provision separate logins as members of these capability roles at deployment.
create role calls_artifact_reader nologin;
create role calls_artifact_worker nologin;
create schema calls_delivery;
revoke all on schema calls_delivery from public;
grant usage on schema calls_delivery to calls_artifact_reader, calls_artifact_worker;

-- A revision ahead of the installed manifest is the durable rebuild queue.
-- @shared covers the shell and visibility changes with indirect dependencies.
create table calls_delivery.revision (
  scope text primary key check (scope in ('@shared', '@earnings') or scope ~ '^[a-z0-9-]+$'),
  revision bigint not null default 1,
  changed_at timestamptz not null default clock_timestamp(),
  changed_xid xid8 not null default pg_current_xact_id()
);

create table calls_delivery.render_slot (
  singleton boolean primary key default true check (singleton),
  token bigint not null default 0,
  expires_at timestamptz not null default '-infinity'
);
insert into calls_delivery.render_slot default values;

create table calls_delivery.manifest (
  build_id text not null,
  scope text not null references calls_delivery.revision(scope),
  revision bigint not null,
  shared_revision bigint not null,
  discovery_revision bigint,
  company_exists boolean not null,
  render_token bigint not null,
  valid_until timestamptz not null,
  completed_at timestamptz not null default clock_timestamp(),
  primary key (build_id, scope)
);

create table calls_delivery.response (
  build_id text not null,
  scope text not null,
  path text not null,
  variant text not null check (variant in ('html', 'rsc') or variant like 'segment:/%'),
  selected_call text,
  digest text not null check (digest ~ '^[a-f0-9]{64}$'),
  body bytea not null,
  primary key (build_id, scope, path, variant),
  foreign key (build_id, scope) references calls_delivery.manifest(build_id, scope) on delete cascade
);

create view calls_delivery.current_manifest as
select m.*
from calls_delivery.manifest m
join calls_delivery.revision r on r.scope = m.scope and r.revision = m.revision
join calls_delivery.revision shared on shared.scope = '@shared' and shared.revision = m.shared_revision
join calls_delivery.revision list on list.scope = '@earnings'
where (m.discovery_revision is null or m.discovery_revision = list.revision)
  and m.valid_until > statement_timestamp();

-- Sample before rendering. Crossing a time boundary withdraws even a manifest
-- whose source rows have not changed. Only the worker reads these dependencies.
create view calls_delivery.next_deadline as
select least(
  (select min(last_success_at + stale_after) from core.task_freshness
    where status = 'ok' and last_success_at + stale_after > statement_timestamp()),
  (select min(occurs_at + interval '14 days') from core.event
    where event_type = 'results_filing' and is_published
      and occurs_at + interval '14 days' > statement_timestamp()),
  (current_date + 1)::timestamptz,
  (date_trunc('day', statement_timestamp() at time zone 'Asia/Kolkata') + interval '1 day') at time zone 'Asia/Kolkata'
) as valid_until;
grant select on calls_delivery.next_deadline to calls_artifact_worker;

create view calls_delivery.current_response as
select r.*, m.revision, m.shared_revision, m.render_token, m.completed_at
from calls_delivery.current_manifest m
join calls_delivery.response r using (build_id, scope);

grant select (scope) on calls_delivery.revision to calls_artifact_reader;
grant select on calls_delivery.current_manifest, calls_delivery.current_response to calls_artifact_reader;
grant select, update on calls_delivery.render_slot to calls_artifact_worker;
-- PostgreSQL requires UPDATE privilege on a column to acquire a row lock.
grant select, update (scope) on calls_delivery.revision to calls_artifact_worker;
grant select, insert, delete on calls_delivery.manifest, calls_delivery.response to calls_artifact_worker;

create function calls_delivery.invalidate(scopes text[]) returns void
language sql set search_path = pg_catalog as $$
  insert into calls_delivery.revision as r (scope)
  select distinct key from unnest(scopes) as key where key is not null order by key
  on conflict (scope) do update
    set revision = r.revision + 1, changed_at = clock_timestamp(), changed_xid = pg_current_xact_id()
    where r.changed_xid <> pg_current_xact_id();
$$;
revoke all on function calls_delivery.invalidate(text[]) from public;

-- Resolve the owners of a call, linked results release, or cited document. A
-- prior quarter's figure, quote or remarks can appear on every company page.
create function calls_delivery.owners(event_key bigint, document_key bigint, output_key bigint)
returns setof integer language sql stable set search_path = pg_catalog as $$
  select c.entity_id from equity.earnings_call c
    where c.event_id = event_key or c.results_event_id = event_key
       or c.transcript_document_id = document_key or c.cover_letter_document_id = document_key
  union
  select c.entity_id from core.ai_output o
    join equity.earnings_call c on c.event_id = o.event_id or c.transcript_document_id = o.document_id
    where o.task in ('call_summary', 'guidance_extraction', 'call_tone')
      and (o.id = output_key or o.event_id = event_key or o.document_id = document_key
        or exists (select 1 from core.ai_citation a where a.ai_output_id = o.id
                   and (a.document_id = document_key or a.event_id = event_key)))
  union
  select o.entity_id from core.ai_output o
    where o.task in ('call_summary', 'guidance_extraction', 'call_tone') and o.id = output_key
$$;
revoke all on function calls_delivery.owners(bigint, bigint, bigint) from public;

-- Runs in the transaction that changes the underlying data, including direct
-- SQL changes. The trigger owner can write the private queue for existing job
-- roles without granting those jobs access to prepared bytes.
create function calls_delivery.changed() returns trigger
language plpgsql security definer set search_path = pg_catalog as $$
declare
  before_row jsonb;
  after_row jsonb;
  row_data jsonb;
  company_ids integer[] := '{}';
  scopes text[] := '{}';
  keys text[] := '{}';
  list_changed boolean := false;
  shared_changed boolean := false;
  row_id bigint;
  company_id integer;
begin
  if tg_op <> 'INSERT' then before_row := to_jsonb(old); end if;
  if tg_op <> 'DELETE' then after_row := to_jsonb(new); end if;
  -- Jobs can refresh bookkeeping without changing any displayed data.
  if before_row - 'updated_at' is not distinct from after_row - 'updated_at' then return null; end if;

  foreach row_data in array array[before_row, after_row] loop
    continue when row_data is null;
    company_id := (row_data->>'entity_id')::integer;
    row_id := (row_data->>'id')::bigint;

    case tg_argv[0]
    when 'gate' then
      shared_changed := shared_changed or row_data->>'field_key' in (
        'ai.call_summary', 'ai.guidance_extraction', 'ai.call_tone', 'equity.guidance_outcome',
        'equity.guidance_hit_rate', 'equity.remarks_change', 'equity.results_move',
        'equity.peer_group', 'site.guided_tour');
    when 'source' then
      -- Source RLS extends through citations, prior calls, index reactions and
      -- the shared shell. Use the shared revision for a visibility change.
      shared_changed := (before_row->'is_suppressed', before_row->'key', before_row->'attribution')
        is distinct from (after_row->'is_suppressed', after_row->'key', after_row->'attribution');
    when 'shared' then
      shared_changed := true;
    when 'entity' then
      if row_data->>'kind' = 'company' then
        keys := array_append(keys, row_data->>'slug');
        list_changed := true;
      elsif row_data->>'kind' in ('index', 'central_bank') then
        shared_changed := true;
      end if;
    when 'company', 'call', 'header' then
      company_ids := array_append(company_ids, company_id);
      list_changed := list_changed or tg_argv[0] <> 'header';
    when 'peer' then
      company_ids := company_ids || array(
        select c.entity_id from equity.company c where c.peer_group_key = row_data->>'key');
    when 'event' then
      if row_data->>'event_type' in ('earnings_call', 'results_filing', 'corporate_action') then
        company_ids := array_append(company_ids, company_id);
        list_changed := list_changed or row_data->>'event_type' = 'earnings_call';
      elsif row_data->>'event_type' = 'policy_meeting' then
        shared_changed := true;
      end if;
      company_ids := company_ids || array(select calls_delivery.owners(row_id, null, null));
    when 'document', 'document_child' then
      if tg_argv[0] = 'document' then
        if row_data->>'doc_type' in ('call_transcript', 'call_cover_letter') then
          company_ids := array_append(company_ids, company_id);
        end if;
        company_ids := company_ids || array(select calls_delivery.owners(null, row_id, null));
      else
        company_ids := company_ids || array(
          select calls_delivery.owners(null, (row_data->>'document_id')::bigint, null));
      end if;
      list_changed := list_changed or cardinality(company_ids) > 0;
    when 'ai_output' then
      if row_data->>'task' in ('call_summary', 'guidance_extraction', 'call_tone') then
        company_ids := array_append(company_ids, company_id) || array(
          select calls_delivery.owners((row_data->>'event_id')::bigint, (row_data->>'document_id')::bigint, row_id));
        list_changed := true;
      end if;
    when 'citation' then
      company_ids := company_ids || array(
        select calls_delivery.owners(null, null, (row_data->>'ai_output_id')::bigint));
    when 'corporate_action' then
      company_ids := company_ids || array(
        select entity_id from core.event where id = (row_data->>'event_id')::bigint);
    when 'event_child' then
      company_ids := array_append(company_ids, company_id) || array(
        select calls_delivery.owners((row_data->>'event_id')::bigint, null, null));
    when 'reaction' then
      company_ids := array_append(company_ids, company_id);
      if company_id is null then
        company_ids := company_ids || array(
          select e.entity_id from core.event e where e.id = (row_data->>'event_id')::bigint and e.event_type = 'results_filing');
      end if;
    when 'outcome' then
      company_ids := company_ids || array(
        select g.entity_id from equity.guidance g where g.id = (row_data->>'guidance_id')::bigint);
    end case;
  end loop;

  if tg_argv[0] = 'gate' and (before_row->'field_key', before_row->'status', before_row->'label')
    is not distinct from (after_row->'field_key', after_row->'status', after_row->'label') then
    return null;
  end if;
  -- With this gate open, the guidance page includes aggregate hit rates.
  if cardinality(company_ids) > 0 and tg_argv[0] in ('call', 'event', 'ai_output', 'document', 'outcome')
    and exists (select 1 from core.field_policy
                where field_key = 'equity.guidance_hit_rate' and status in ('shown','labelled')) then
    shared_changed := true;
  end if;

  scopes := keys || array(select e.slug from core.entity e where e.kind = 'company' and e.id = any(company_ids));
  if list_changed then scopes := array_append(scopes, '@earnings'); end if;
  if shared_changed then scopes := array_append(scopes, '@shared'); end if;
  if cardinality(scopes) > 0 then perform calls_delivery.invalidate(scopes); end if;
  return null;
end $$;
revoke all on function calls_delivery.changed() from public;

do $$
declare
  entry record;
begin
  for entry in select * from (values
    ('core.field_policy', 'gate'),
    ('core.source', 'source'),
    ('core.entity', 'entity'),
    ('core.event', 'event'),
    ('core.document', 'document'),
    ('core.chunk', 'document_child'),
    ('core.doc_type', 'shared'),
    ('core.ai_output', 'ai_output'),
    ('core.ai_citation', 'citation'),
    ('core.task_freshness', 'shared'),
    ('core.series', 'shared'),
    ('core.observation', 'shared'),
    ('core.event_reaction', 'reaction'),
    ('policy.meeting', 'shared'),
    ('equity.company', 'company'),
    ('equity.peer_group', 'peer'),
    ('equity.index_membership', 'shared'),
    ('equity.price_daily', 'header'),
    ('equity.corporate_action', 'corporate_action'),
    ('equity.adjustment_factor', 'header'),
    ('equity.filing', 'header'),
    ('equity.earnings_call', 'call'),
    ('equity.guidance', 'call'),
    ('equity.stated_figure', 'call'),
    ('equity.guidance_outcome', 'outcome'),
    ('equity.transcript_page', 'document_child'),
    ('equity.call_remarks_change', 'event_child'),
    ('equity.call_tone', 'event_child')
  ) as hooks(relation, dependency) loop
    execute format('create trigger calls_revision after insert or update or delete on %s
                    for each row execute function calls_delivery.changed(%L)', entry.relation::regclass, entry.dependency);
  end loop;
end $$;

select calls_delivery.invalidate(array['@shared', '@earnings'] ||
  array(select slug from core.entity where kind = 'company'));
