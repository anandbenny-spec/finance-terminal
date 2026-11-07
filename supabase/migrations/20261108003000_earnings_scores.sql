-- 0030: earnings scores (issue #180, job 2B-17; Spec 02B §3.3's `call_remarks_change` and `call_tone` rows, §3.4's `call_tone`
-- task, §3.5's three hidden policies; Spec 02B §13.11 L9, "a later migration `earnings_scores` named by their jobs").
-- Decided by Claude (standing delegation, 8 Nov 2026): this migration holds both remaining tables, so #175 (tone) needs no
-- second push. `call_tone` is written later by #175; `call_remarks_change` by #180's `equity.call_scores`.
-- Equity conventions as 0019, 0023 and 0027: RLS on, the `job_writer_all` policy, explicit grants, `delete` for jobs on the D
-- tables. Visitor policies use inline subqueries only. Everything ships hidden (field policy fails closed); no `api` or
-- `semantic` views: nothing reads the tables until the pages do.

------------------------------------------------------------------ §3.3 call_remarks_change
create table equity.call_remarks_change (
  event_id      bigint  primary key references equity.earnings_call(event_id),   -- the call (this quarter's)
  base_event_id bigint  not null references equity.earnings_call(event_id),      -- the previous quarter's call
  words_base    integer not null check (words_base >= 0),                        -- the previous opening's words (masked text)
  words_new     integer not null check (words_new >= 0),
  words_added   integer not null check (words_added >= 0),
  words_removed integer not null check (words_removed >= 0),
  similarity    numeric not null check (similarity between 0 and 1),            -- 1 - (added + removed) / (base + new)
  new_speakers  text[]  not null default '{}',                                  -- management speakers new to the opening, >= 30% of its words
  label         text    check (label in ('largely_rewritten','new_speaker')),
  pairs         jsonb   not null default '[]'
                check (jsonb_typeof(pairs) = 'array' and jsonb_array_length(pairs) <= 5),   -- at most 5, each side at most 400 characters
  rule_version  text    not null,
  check (event_id <> base_event_id)
);
create index on equity.call_remarks_change (base_event_id);

------------------------------------------------------------------ §3.3 call_tone (written by #175)
create table equity.call_tone (
  event_id      bigint  primary key references equity.earnings_call(event_id),
  ai_output_id  bigint  references core.ai_output(id),
  n             integer check (n >= 0),
  mean          numeric,
  sd            numeric,
  opening_n     integer check (opening_n >= 0),
  opening_mean  numeric,
  answers_n     integer check (answers_n >= 0),
  answers_mean  numeric,
  prev_event_id bigint  references equity.earnings_call(event_id),
  delta         numeric,
  se_delta      numeric,
  change        text    check (change in ('more_positive','more_negative','little_changed')),
  held_reason   text,
  rule_version  text    not null
);
create index on equity.call_tone (prev_event_id);

grant select on equity.call_remarks_change, equity.call_tone to anon, authenticated, semantic_owner, service_role;
grant select, insert, update, delete on equity.call_remarks_change, equity.call_tone to job_writer;   -- D: re-made when a rule changes

alter table equity.call_remarks_change enable row level security;
alter table equity.call_tone           enable row level security;
create policy job_writer_all on equity.call_remarks_change for all to job_writer using (true) with check (true);
create policy job_writer_all on equity.call_tone           for all to job_writer using (true) with check (true);
-- Visible with their call (its event, company and source switch).
create policy read_published on equity.call_remarks_change for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.earnings_call c where c.event_id = call_remarks_change.event_id));
create policy read_published on equity.call_tone for select to anon, authenticated, semantic_owner
  using (exists (select 1 from equity.earnings_call c where c.event_id = call_tone.event_id));

------------------------------------------------------------------ §3.4 catalogue row
insert into core.ai_task (key, label, module, output_kind, requires_citations) values
  ('call_tone', 'Earnings call tone', 'equity', 'score', true);

------------------------------------------------------------------ §3.5 field policies (fail closed; seeded hidden)
insert into core.field_policy (field_key, status, label, reason) values
  ('ai.call_tone', 'hidden', 'AI-scored',
   'Held back until the tone gate passes (Spec 02B §5.8) and Anand flips it'),
  ('equity.results_move', 'hidden', 'derived',
   'Held back until 3 hand checks pass (Spec 02B §5.12), as 2A''s rate sensitivity'),
  ('equity.remarks_change', 'hidden', 'derived',
   'Held back until the threshold check passes (Spec 02B §5.13) and Anand has looked');

comment on table equity.call_remarks_change is 'One row per earnings call with a previous call: the opening remarks'' similarity to the previous quarter''s (numbers masked), the five most-changed sentence pairs, and labels. Derived; hidden until field policy equity.remarks_change is released.';
comment on table equity.call_tone is 'One row per earnings call: the AI-scored tone of the call and its change on the previous quarter. Written by the tone job; hidden until field policy ai.call_tone is released.';
