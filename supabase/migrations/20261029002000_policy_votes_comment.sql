-- 0020: semantic.policy_votes' view comment, made true again (issue #159; Anand, 29 Oct 2026, 07:21 IST). Comment only:
-- no table, column, view definition, grant or row changes (CLAUDE.md §3: the comments are the text-to-SQL prompt's
-- schema description, so they must stay true). The comment said FOMC votes come from the statement's voting line. That
-- stopped being true with the first tally statement (17 Jun 2026): from then the statement names only its dissenters
-- and the minutes name the rest. policy.fed_documents now writes a tally statement's dissenters when it stores the
-- statement, so between the statement and its minutes a meeting has its dissenters' rows only.
--
-- Anand's OK covers this migration (CLAUDE.md §7: a migration no card named): it is part of the recommendation he
-- applied in #159. It reaches dev and production through his `supabase db push`, in number order.

comment on view semantic.policy_votes is
  'One row per voting member per meeting. FOMC votes come from the statement''s voting line to April 2026 and from the minutes from June 2026. From June 2026 the statement names only its dissenters, so until a meeting''s minutes are out only its dissenters have rows (taken from the statement), and a unanimous meeting has none. RBI votes (rate and stance) come from the MPC minutes.';
