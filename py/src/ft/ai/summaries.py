"""Module 1's cited summaries (Spec 01 §3.2-§3.3; Architecture 03 §1.2, §1.4): `doc_summary` (3-6 bullets on one
document) and `statement_diff_summary` (2-5 bullets on what changed from the previous statement or resolution).
Both use the citations route: the documents go as plain-text document blocks, and the reply's text blocks carry the
passages they cite.

**Checking a reply** (`check`), before anything is stored:
- Bullets are the reply's lines that start with "- " (or another list marker); text outside them (a preamble) is
  ignored and never stored. A bullet's citations are those of the reply's text blocks that overlap it.
- Each citation is mapped into our copy of the document (`citations.map_api_citation`): the API's offsets if our text
  there equals the cited text, else the cited text found exactly once; otherwise it isn't evidence.
- A bullet is kept only with at least one verified citation, and only if every figure it writes is a figure in its
  cited passages ("no number that isn't in a cited passage", Spec 01 §3.3). Figures are compared whole, as written:
  "3/4" doesn't match "3-3/4", "Q2" doesn't match "Q3", and a signed figure needs the same sign (`figures`).
- Text after the first bullet that isn't a bullet (a closing remark) fails the reply: it would sit under the last
  bullet's citation. A preamble before the first bullet is ignored and never stored. For `statement_diff_summary` a citation
  counts only if it falls on a sentence the redline marks changed, added or removed: so additions cite the new text
  and removals the old, as only the new text holds an addition and only the old a removal.
- The bullet count must be in range (3-6, 2-5).
- For `statement_diff_summary` from prompt v4 on (#445, R05-2; Anand's option 1, 12 Nov 2026; `drop_copies`), before
  these checks: a cited text block that is a word-for-word copy of its own cited text comes out of the bullet's text, and
  its citations stay on the bullet (`mark_copies`, `settle_copies`). The checks then run on the final text, unchanged.
- For `statement_diff_summary` from prompt v7 on (#445, round 6; Anand's decision of 12 Nov 2026, 22:36 IST;
  `check_quotes`), one more check, a tightening: every stretch a bullet puts in double quotation marks is found in that
  bullet's own cited passages, and its quotation marks are balanced (`quote_problem`). v7 also runs on Sonnet 5.5
  (`DIFF_MODEL_OF`).
- From v8 on (#445, round 7; `strict_copies`, `check_repeats`): only a copy that stands as a whole sentence comes out, and
  every other copy goes in quotation marks; and a bullet that repeats a stretch of its own words fails (`repeat_problem`).
- From v10 on (#445, round 9; `check_ends`): a bullet that doesn't end as a sentence ends fails (`end_problem`). v10 runs at
  effort medium (`DIFF_EFFORT_OF`).
- From v11 on (#445, round 10; Anand's decision of 13 Nov 2026, by 07:18 IST): the structured route (`DIFF_JSON_FROM`). The reply
  is the schema's JSON, each bullet its text and its quotes by document; `check_diff_json` finds each quote in that
  document's changed sentences, cites them, and gives the bullet the checks above (figures, quotations, repeats, ends).
- From v12 on (#445, round 11; `DIFF_STRICT_FROM`): also no run of a statement's words outside quotation marks, and no two
  quotations side by side.
- From v13 on (#445, round 12; Anand's decision of 13 Nov 2026, 13:40 IST; `DIFF_WHOLE_FROM`): also no claim in the bullet's
  own words that something stayed the same, and no quotation cut in the middle of a phrase.
- From v14 on (#445, round 13; Anand's pick of 13 Nov 2026, before 17:12 IST; `DIFF_SIDES_FROM`): also no "again" or "as
  before" in the bullet's own words, and no claim about the previous document without a quote from it.
- From v15 on (#445, round 14; Anand's pick of 13 Nov 2026, before 17:36 IST; `DIFF_FP_FIX_FROM`): v14's text, with two false
  positives of the v13 and v14 checks fixed.
- From v16 on (#445, round 15; Anand's decision of 14 Nov 2026, about 00:38 IST; `DIFF_FIT_FROM`): also no quotation set where
  its grammar can't fit, no quotation the bullet's own words run on from with the statement's next words, and no claim
  resting on a sentence next to a quoted one that the bullet doesn't quote.
- From v17 on (#445, round 16; Anand's decision of 14 Nov 2026, 14:48 IST; `DIFF_VERIFY_FROM`): also no run of five or more of a
  statement's words outside quotation marks (code first moves a quotation's mark over such a run, or quotes it, where the words
  are a cited passage's), no "adds" for words the previous statement already had, no claim resting on the sentence a quoted one
  refers back to, and no claim about who voted for the action without its voting sentence; then a second model (Haiku 5.5)
  reads each bullet against its cited sentences and both statements (`verify_request`, `apply_verdicts`; `policy.ai_batch`
  sends it). A target whose two replies are rejected gets an output built by code from the changed sentences
  (`fallback_bullets`).
- From v20 on (#445, round 17; Anand's decision of 14 Nov 2026, about 22:19 IST; `DIFF_OPEN_END_FROM`): also no quotation that
  ends on a word such as "a", "the" or "to" in the middle of a phrase, even where the bullet goes on in its own words.
- From v23 on (#445, round 20; Anand's decision of 15 Nov 2026, 12:10 IST; `DIFF_PACK_FROM`): the replies are checked as v22's;
  the fallback packs up to two changes of one kind into a bullet while bullets are scarce (`_pack`).
- From v24 on (#445, round 21; Anand's decision of 15 Nov 2026, about 13:25 IST; `DIFF_RESTATED_FROM`): the replies are checked
  as v22's; the fallback reads a further decision sentence that also states the stance as the stance.
A reply with any dropped bullet or the wrong count isn't published: the request is retried once, with a note naming
the problems (so the retry is a new input, with its own ops.ai_request row), then rejected with an alert
(`policy.ai_batch`). The database checks the published output's citations again at commit (Architecture 01 §1.4).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from itertools import pairwise

from ft.ai.citations import map_api_citation
from ft.ai.client import HAIKU, SONNET, Prompt, Reply, Request, Source, TextPart, load_schema
from ft.engines.document.diff import PAIR_MIN
from ft.engines.document.text import words as text_words

BULLETS = {"doc_summary": (3, 6), "statement_diff_summary": (2, 5)}
# The model of a statement_diff_summary prompt version that doesn't run on the task's (Haiku 5.5). v7 is Sonnet 5.5's (#445,
# round 6; Anand's decision of 12 Nov 2026, 22:36 IST): the judge failed Haiku's round 5 (v6) on 7 of 10 targets, mostly on
# the model's own sentences, and CLAUDE.md §6 steps up to Sonnet where an evaluation shows Haiku falls short (as call_tone's
# Sonnet round, #175). The task's effort and max_tokens stay. A version that isn't here runs on Haiku, so v1 to v6 are unchanged.
DIFF_MODEL_OF = {"v7": SONNET, "v8": SONNET, "v9": SONNET, "v10": SONNET, "v11": SONNET, "v12": SONNET,
                 "v13": SONNET, "v14": SONNET, "v15": SONNET, "v16": SONNET, "v17": SONNET, "v18": SONNET, "v19": SONNET, "v20": SONNET, "v21": SONNET, "v22": SONNET, "v23": SONNET, "v24": SONNET, "v25": SONNET, "v26": SONNET,
                 "v27": SONNET, "v28": SONNET, "v29": SONNET}   # v8: v7's text, marking the copies rule's revision (round 7); v9: v8 plus three rules
# (round 8); v10: v9 plus a rule on citation carriers (round 9); v11: the json route (round 10); v12: v11 plus a rule, with
# two more checks (round 11); v13: v12 plus two rules, with two more checks (round 12); v14: v13 plus two rules, with one
# more check and a wider same-claim check (round 13); v15: v14's text, with two false positives fixed (round 14); v16: v15's
# text, with three more checks (round 15); v17: v16 plus three rules, with four more checks, a second model's check and a
# fallback built by code (round 16); v18: v17's text, marking those checks' revision before the judge (round 16's second run); v19: v17's
# text again, marking the third run's (a narrower "adds" check, the fallback's trivial changes and repeated sentences).
# v20 to v22: v19's text again, marking rounds 17 to 19's revisions; v23: v22's text, marking round 20's packed fallback
# (`DIFF_PACK_FROM`); v24: v23's text, marking round 21's (`DIFF_RESTATED_FROM`); v25: v24's text, marking round 22's
# (`DIFF_COUNTERPART_FROM`); v26: v25's text, marking round 23's (`DIFF_KIND_JOIN_FROM`, `DIFF_V1_PAIRS_FROM`); v27: v26's
# text, marking round 24's (`DIFF_V1_ALL_FROM`); v28: v27 plus four rules and a list of the changes to report, with the
# coverage check (`DIFF_MUST_FROM`, round 25); v29: v28 plus a sentence on lists of decisions, with the fallback's room
# (`DIFF_ROOM_FROM`, round 26).
# The effort of a version that doesn't run at the task's (low): v10 runs at medium (round 9, 13 Nov 2026), so Sonnet plans
# its bullets; at low it wrote words only to hold a citation ("Previous new") in round 8.
DIFF_EFFORT_OF = {"v10": "medium", "v11": "medium", "v12": "medium", "v13": "medium", "v14": "medium", "v15": "medium",
                  "v16": "medium", "v17": "medium", "v18": "medium", "v19": "medium", "v20": "medium", "v21": "medium", "v22": "medium", "v23": "medium", "v24": "medium",
                  "v25": "medium", "v26": "medium", "v27": "medium", "v28": "medium", "v29": "medium"}
# From v11 (#445, round 10; Anand's decision of 13 Nov 2026, by 07:18 IST, after rounds 6 to 9 on the citations route kept
# showing words written only to hold a citation): the json route, as call_summary v6. The reply is v11.schema.json's JSON and
# our code places the quotes (`check_diff_json`), so the model has no text to carry a citation with.
DIFF_JSON_FROM = 11
DIFF_MAX_QUOTES = 6                    # quotes a bullet may rest on
# From v12 (#445, round 11; tightenings): a bullet fails when it copies UNQUOTED_MIN_WORDS or more of a statement's words in a
# row outside quotation marks (at least COPY_PLAIN_WORDS of them ordinary words: members are named plainly), or sets two quotations side
# by side with nothing of its own between them (`unquoted_copy_problem`, `side_by_side_problem`). Round 10 (v11 on dev):
# "…were of the view that the stance be retained at neutral." (2530), "…will slow the pace of decline of its securities
# holdings" (2533), "at least at the current pace" "over coming months" (2529).
DIFF_STRICT_FROM = 12
UNQUOTED_MIN_WORDS = 8
COPY_PLAIN_WORDS = 5                   # of which at least this many aren't names, initials or capitalised words
# From v13 (#445, round 12; Anand's decision of 13 Nov 2026, 13:40 IST, after the judge failed round 11; tightenings): a
# bullet fails when its own words (outside quotation marks) say that something stayed the same (`same_problem`): on this route
# its quotes come only from changed sentences, so nothing it cites can show that ("The repo rate sentence is not among the
# changes.", 2541; "The policy repo rate decision is unchanged, but …", 2538). It also fails when a quotation it doesn't go
# on from (punctuation or the bullet's end follows it, not a word of its own) stops where its cited sentence goes on with a
# word, if the quotation starts where that sentence starts or ends on a word such as "the" or "to" (`cut_quote_problem`):
# round 11's front-page bullet ended a sentence it had started quoting at "…determine the policy stance appropriate" (2546),
# as round 10's did at "…the policy stance" (2537). A quotation the bullet goes on from ("…would continue to increase"
# them, 2544) passes, as the judge passed it.
DIFF_WHOLE_FROM = 13
# From v14 (#445, round 13; Anand's pick of 13 Nov 2026, before 17:12 IST, after the build session's own read of round 12;
# tightenings): the same-claim check also fails "again" and "as before" in the bullet's own words ("…again voted to keep the
# policy rate unchanged", 2556), and a bullet whose own words describe the previous document ("moved from", "previously",
# "was removed", "instead of" …) fails unless one of its quotes is from the previous document (`previous_problem`; "Loretta
# J. Mester moved from voting for the action to dissent", 2552, quoting only the new statement; round 10's Bullard, Waller
# and Schmid bullets were the same). The new document's side has no check: no bullet in rounds 10 to 12 described it
# without quoting it.
DIFF_SIDES_FROM = 14
# From v15 (#445, round 14; Anand's pick of 13 Nov 2026, before 17:36 IST, after round 13 rejected one target on a false
# positive): v14's text, with two false positives fixed. A quotation that starts where its sentence starts may stop right
# before that sentence's verb (`_VERB_NEXT`): the quoted words are then the sentence's whole subject ("…listed her among those
# "Voting for the monetary policy action"", where the statement goes on "were Jerome H. Powell, …"; fomc-2024-12-18).
# And "before" ahead of a quotation is a conjunction ("…"1.6 per cent" in July 2025 before "rising to 2.1 per cent""),
# not a clause-final "before" about the previous document (mpc-2025-10-01). Replies made with v13 or v14 keep their checks.
DIFF_FP_FIX_FROM = 15
# From v16 (#445, round 15; Anand's decision of 14 Nov 2026, about 00:38 IST, after the judge failed round 14 on three
# patterns: "a check that rejects the reply and retries, not prompt wording alone"; tightenings): v15's text, and a bullet
# also fails on each of the three, so the reply is retried with the reason, then rejected (the old output stays):
# - a quotation set where its grammar can't fit (`fit_problem`): "…holding the target range at "maintain the target range
#   …"", "…citing "in light of the shift in the balance of risks"" (fomc-2025-09-17, 2578);
# - a quotation the bullet's own words run on from with the statement's next words (`runs_on_problem`): "…a new sentence
#   says that "Holding the target range steady at this meeting" allows the Committee to assess additional information"
#   (fomc-2023-06-14, 2579);
# - a claim resting on a sentence next to a quoted one that the bullet doesn't quote (`neighbour_problem`): "…Prof.
#   Jayanth R. Varma, who "expressed reservations on this part of the resolution", is the exception to the others", where
#   "except" is in the sentence before (mpc-2021-08-06, 2577).
# Measured on dev's stored v11 to v15 outputs (61 outputs, 247 bullets): the three catch those three bullets, and flag 2 of
# the 85 bullets the round 11 and round 14 judges passed (both a quotation followed by five of the statement's next words).
DIFF_FIT_FROM = 16
# From v17 (#445, round 16; Anand's decision of 14 Nov 2026, 14:48 IST, after the judge failed round 15 on three bullets the
# pattern checks let through, about 7% of 41, and one target left on v1; built to the strictest reading of Part 2, so the next
# judge's reading can't decide the round; tightenings):
# - no run of RUN_MIN_WORDS or more of a statement's words outside quotation marks, names, figure labels and lead-ins included
#   (`long_run_problem`): the pack's own stretch measure (`ft.eval.diff_quotes`), as a gate at 0, counted against both whole
#   statements. Before the checks, code moves a quotation's mark over such a run where the run touches the quotation and the
#   two together are words of a cited passage, or puts the run in quotation marks where it is a cited passage's words on its own
#   (`settle_runs`), with no model call: fomc-2026-10-28's 3376 ended a quotation at "…determine the policy stance appropriate"
#   and went on in the statement's words;
# - "adds", "new sentence" and the like for words the previous statement already had in a changed sentence, unless the bullet
#   quotes that sentence too (`added_problem`): mpc-2023-12-08's 3375 said the new statement "adds" that policy "must continue
#   to be actively disinflationary", which the previous one had said in shorter words;
# - a claim resting on the sentence before a quoted one that opens by referring back ("Accordingly, they …"), however common
#   its words (`neighbour_problem`'s `refers_back`): mpc-2020-10-09's 3383 called inflation pressures "supply shocks", from the
#   sentence before the cited one; and a claim about who voted for the action that quotes no voting-for sentence
#   (`vote_for_problem`: fomc-2025-12-10's 3384, a close call);
# - then a second model, Haiku 5.5 (the generator is Sonnet 5.5), reads each bullet with only its cited sentences and the two
#   statements, and answers three questions: is every claim in the cited sentences, is every "added", "removed" or "changed"
#   true against the other statement, and does it read as plain English, its quotations fitting its grammar (verify2 on)? A
#   "no" drops the bullet with the objection (`verify_request`, `apply_verdicts`;
#   `policy.ai_batch` sends it through the wrapper as its own request, never twice).
# A target whose two replies are rejected gets an output built by code from the changed sentences, each quoted in full and
# labelled previous or new (`fallback_bullets`; `policy.ai_batch`), so no target is left on an older version.
DIFF_VERIFY_FROM = 17
# The second model's prompt: config/prompts/statement_diff_summary/<VERIFY_VERSION>.md and its schema. verify1 ran in round 16's
# first dev run; verify2 adds a third question, whether each bullet reads as plain English (Anand's decision of 14 Nov 2026,
# about 17:05 IST, to fix and re-run before the judge).
VERIFY_VERSION = "verify2"
VERIFY_MODEL = HAIKU
VERIFY_EFFORT = "medium"
VERIFY_MAX_TOKENS = 16_000            # thinking and one verdict per bullet; a stop at the limit is a failed check, not a pass
# From v20 (#445, round 17; Anand's decision of 14 Nov 2026, about 22:19 IST, after round 16's third dev run; a tightening):
# v19's text, and `cut_quote_problem` also fails a quotation ending on an open word (`_OPEN_END`: "a", "the", "to" …) where its
# passage goes on, even when the bullet goes on after it in its own words: mpc-2026-10-07's 3449 quoted "action ahead can only
# be a" and went on "hike or a pause", the statement's "rate hike or a pause" with a word left out, which the run-on check
# (`runs_on_problem`: the passage's next words, word for word) let through. Round 15's judge failed that shape (3376).
DIFF_OPEN_END_FROM = 20
# From v23 (#445, round 20; Anand's decision of 15 Nov 2026, 12:10 IST, after round 19's dev run, whose likely failures were
# both the fallback's): v22's text, and the fallback packs (`fallback_bullets`' `packed`). Rounds 16 to 19 lost targets on the
# fallback, which shows one change per bullet in at most five bullets while v1 (a model) often puts two changes in a bullet,
# so a fallback could miss a change v1 had and Part 2's rule (c) compares with v1:
# - a bullet carries up to FALLBACK_PER_BULLET changes of one kind, the inflation and growth projections counting as one
#   kind (`_pack`): mpc-2020-03-27's 3561 spent two of its five bullets on restated projections and left out the global
#   standstill and the liquidity operations that v1 (346) had;
# - the balance sheet's words rank before the stance's (`_kind`'s `packed`), so fomc-2025-10-29's old "will continue
#   reducing its holdings" (read as guidance) and new "decided to conclude the reduction of its aggregate securities
#   holdings" are one change: its fallback (3557) quoted the old sentence alone, as removed, and left out the new one.
# Fallbacks of v22 or earlier keep their rules.
DIFF_PACK_FROM = 23
# From v24 (#445, round 21; Anand's decision of 15 Nov 2026, about 13:25 IST, after round 20's dev run): v23's text, and the
# packed fallback reads a change of the decision's other than the one it shows, when it also states the stance, as the stance
# (`fallback_bullets`' `restated`): mpc-2021-02-05's 3592 left out "decided to continue with an accommodative stance of
# monetary policy till the prospects of a sustained recovery are well secured", which restates the rate decision too, so it
# was a second decision, and the decision is shown once; v1 (397) has it. Fallbacks of v23 keep their rules.
DIFF_RESTATED_FROM = 24
# From v25 (#445, round 22; Anand's decision of 16 Nov 2026, 22:12 IST, after round 21's verdict failed four RBI fallbacks on
# rule (b)): v24's text, and the packed fallback (`fallback_bullets`' `counterparts`, `_counterparts`):
# - pairs a removed and an added sentence as one change only when the second is what the first became: in the same stretch
#   between two sentences the redline keeps or pairs itself (so never across an RBI resolution's sections), in paragraphs the
#   redline aligns (or one of them not aligned at all), a paragraph's opening only with an opening, on the same subject, and
#   the best-matched pairs first (round 21: mpc-2024-06-07's paragraph 5 headline sentence was paired with paragraph 2's
#   global one, mpc-2022-12-07's enterprise-survey sentence with a global one, mpc-2026-10-07's global opening with the
#   August CPI sentence, and mpc-2023-06-08's opening and fuel sentences crossed); a sentence the redline left unpaired that is
#   at least PAIR_MIN like one anywhere in the other document is that sentence, moved, so it is never shown as missing;
# - shows a sentence that a reworded one was split into (or merged from) with it, so "doesn't have this sentence" is kept
#   for a sentence the other document has no counterpart for (round 21: mpc-2023-06-08's "It will take further monetary
#   actions …");
# - ranks first the changes the target's v1 output cites (its stored citations, `v1`), and last a change of a word or two
#   that changes no meaning (`_minor`: "These decisions are" to "This decision is", mpc-2025-04-09);
# - quotes an RBI paragraph without its number ("15. Two members …" as "Two members …"), and labels a list item as one
#   ("continue with the neutral monetary policy stance …", mpc-2025-04-09) instead of quoting it as a sentence.
# Fallbacks of v24 or earlier keep their rules.
DIFF_COUNTERPART_FROM = 25
# From v26 (#445, round 23; Anand's decision of 17 Nov 2026, 07:06 IST): v25's text, and one of v25's rules in
# `_counterparts` (`kinds`), from round 22's pre-check:
# - a sentence joins another's change (split in two, or two merged) only when it is of that change's kind (`_kind`, on both
#   sides' text): round 22's mpc-2022-09-30 joined "Dr. Ashima Goyal voted to increase the repo rate by 35 basis points." (a
#   dissent) to the change of the vote on the policy repo rate (a decision), and the fallback's one decision change per output
#   then left the dissent out;
# Fallbacks of v25 or earlier keep their rules.
DIFF_KIND_JOIN_FROM = 26
# Also from v26 (#445, round 23; round 22's verdict, 17 Nov 2026, 08:26 IST: mpc-2022-09-30 and mpc-2020-08-06 failed rule (c)),
# two rules that read the target's v1 output's citations by bullet (`fallback_bullets`' `v1_pairs`):
# - v1's counterparts: where one v1 bullet cites a sentence the previous document alone has and a sentence the new one alone
#   has, and each is a change of its own here, on one topic (the redline's), they are one change, before and after, so neither
#   side is shown without the other (mpc-2020-08-06: the May guidance "the policy space … needs to be used now rather than
#   later" with August's "Accordingly, the MPC decides to stay on hold …", and the inflation and growth outlooks). Its kind is
#   that of the sentences v1 cites;
# - v1's own changes of the decision's kind: a change v1 cites in a bullet other than the shown decision's, and that isn't a
#   restatement of it (a rate figure the decision doesn't give, or a vote), is shown, not left out as a second decision: a
#   vote with the dissents (mpc-2022-09-30: the vote on the policy repo rate, from "unanimously voted" to five members, beside
#   Dr. Goyal's 35 basis points), anything else as the stance (the guidance above).
DIFF_V1_PAIRS_FROM = 26
# From v27 (#445, round 24; Anand's decision of 16 Nov 2026, 22:50 IST, rounds 23 and 24 open; round 23's verdict, 17 Nov 2026,
# 09:30 IST: mpc-2024-08-08 and mpc-2022-02-10 failed rule (c)): v26's text, and every change the target's v1 output cites is
# shown (`_pack`'s `v1_of`): the v1-cited changes came first in v25 and v26, but within the packing's limits (5 bullets, 2
# changes of one kind to a bullet), so when they needed more room than that, the last of them were left out (mpc-2024-08-08:
# the core inflation reading, from 3.2 per cent in April to 3.1 per cent in May-June, which v1 (576) has; mpc-2022-02-10: the
# first advance estimate of 9.2 per cent for 2021-22, and the global PMI). Now a v1-cited change that finds no room of its
# kind goes to another bullet with room (the one with the fewest changes, then one of its kind, then the shortest), then to a
# bullet holding up to FALLBACK_V1_PER_BULLET changes; the changes v1 doesn't cite take only the room left. And a sentence
# only one document has goes beside the other document's side of a change that the same v1 bullet cites with it (`_pack`'s
# `mate`), so the previous "projection for real GDP growth is retained at 9.5 per cent in 2021-22" and the new "first advance
# estimates … placed India's real gross domestic product (GDP) growth at 9.2 per cent for 2021-22" (paired here with the
# previous sentence on the NSO's data) are read together, as v1 (301) gives them. Fallbacks of v26 keep their rules.
DIFF_V1_ALL_FROM = 27
FALLBACK_V1_PER_BULLET = 4             # from v27: changes a bullet may carry so that every change v1 cites is shown
# From v28 (#445, round 25; Anand's decision of 17 Nov 2026, about 14:10 IST, after round 24's verdict: rounds 25 and 26 open,
# and round 25 fixes the pattern of rounds 22 to 24, not only round 24's two failures). All six failures in those rounds were
# RBI targets, five of them rule (c): the new output dropped a change v1 reported, on the model's route (round 24:
# mpc-2021-02-05's 3702 left out the removed "This constrains monetary policy …" and "The outlook for inflation has turned
# adverse …", and quoted the previous resolution's words on the policy rate for a change of the stance) and on the code-built
# one (rounds 22 and 23). So whenever the decision, the vote or its split, the stance or forward guidance, or the committee's
# growth or inflation projections differ between the two documents, and for every change the target's v1 output cites, an
# output reports the change, with both sides where both exist (`must_changes`, read as the fallback reads the redline):
# - the request lists those changes after the changed sentences (`must_text`), and v28's prompt asks for each of them;
# - a reply whose bullets all pass is still rejected, and retried with the reason, when no bullet cites a listed change on
#   each side it has (the decision: one of the sentences stating it), or quotes a side of a change of the stance that names
#   the stance without its words on the stance (`must_problems`), before the second model is asked;
# - the fallback built by code shows every listed change as v27 shows v1's, and a sentence one document alone has beside the
#   other document's sentence alone of its kind (`fallback_bullets`' `must`);
# - a quotation of a predicate ("unchanged at 5.25 per cent") set after a preposition fails the grammar-fit check
#   (`fit_problem`'s `predicate`; round 24: mpc-2026-10-07's 3710, "holding the policy repo rate at "unchanged at 5.25 per
#   cent"").
# All are tightenings; fallbacks and replies of v27 or earlier keep their rules.
DIFF_MUST_FROM = 28
# From v29 (#445, round 26; Anand's decision of 17 Nov 2026, about 14:10 IST: round 26 is round 25's version plus only what its
# verdict names). Round 25's verdict (17 Nov 2026, 18:19 IST) failed mpc-2020-03-27's code-built 3747 on rule (c): the third of
# the new resolution's three decisions, "the reverse repo rate under the LAF stands reduced by 90 basis points to 4.0 per
# cent", which v1 (346) reports, is missing, and so are the new open market purchases of ₹ 10,000 and ₹ 15,000 crore (v1
# bullet 4). Both were among the changes v1 cites; they found no room, because v28's own changes of the stance and the
# projections (one of them a false positive: the fiscal deficit "placed at 3.8 per cent of GDP") ranked with v1's and filled
# the five bullets first, and a change of the corridor could join the decision's bullet only up to two changes. So:
# - a sentence or list item one document alone has that states a corridor rate (the reverse repo, the SDF, the MSF, the Bank
#   Rate) joins the side next to it of the one change whose other side states that rate and whose own side doesn't
#   (`_corridor_mates`):
#   the new "further, … the reverse repo rate … stands reduced … to 4.0 per cent" joins the new "accordingly, the marginal
#   standing facility (MSF) rate and the Bank Rate stand reduced …", set against the previous "Consequently, the reverse repo
#   rate … remains unchanged at 4.90 per cent and the … (MSF) rate and the Bank Rate at 5.40 per cent";
# - the fallback places the changes v1 cites (after the decision) before the changes only v28's list adds (`_pack`'s
#   `v1_cited`), so v28's own take only the room left, as the changes v1 doesn't cite did in v27;
# - a sentence giving a ratio to GDP (the fiscal deficit, the current account) is no growth or inflation projection of the
#   committee's, and is left out of that reading;
# - on the model's route, each item of a list of decisions inside a change to report is cited (`must_problems`), and the
#   request shows each sentence of a change up to MUST_SHOW_WORDS words, as it says (v28 cut a side's sentences together).
# All are tightenings; fallbacks and replies of v28 or earlier keep their rules.
DIFF_ROOM_FROM = 29


def diff_json(version: str) -> bool:
    """Whether a statement_diff_summary prompt version takes the json route (`DIFF_JSON_FROM`)."""
    return int(version.lstrip("v")) >= DIFF_JSON_FROM
_MARK = re.compile(r"^[ \t]*(?:[-*•–—]|\d{1,2}[.)])[ \t]+")


@dataclass(frozen=True)
class Doc:
    """A published document as the summaries see it."""
    document_id: int
    doc_type: str
    label: str                        # core.doc_type.label: 'FOMC statement'
    title: str                        # core.document.title: 'FOMC statement, 28 Oct 2026'
    bank: str                         # core.entity.short_name: 'Fed', 'RBI'
    day: date | None                  # the release date in the bank's time zone
    text: str                         # the canonical text (the chunks, concatenated)
    event_id: int | None = None       # its meeting (the stance is the meeting's: core.ai_output.event_id)
    event_day: date | None = None     # the meeting's decision date (a projection's vintage)
    raw_file_id: int | None = None    # the page it was read from (the stance cross-check re-parses it)
    source_url: str | None = None


@dataclass(frozen=True)
class Cited:
    document_id: int
    start: int
    end: int
    how: str                          # 'offsets' | 'search'


@dataclass
class Bullet:
    text: str
    citations: list[Cited] = field(default_factory=list)
    problem: str | None = None        # why it's dropped
    section: str | None = None        # a call summary's list ('management' | 'analysts'); None for module 1's


@dataclass
class Checked:
    task: str
    bullets: list[Bullet]
    problems: list[str]               # reply-level: the count, no bullets
    mapping: Counter                  # citations by how they mapped: offsets, search, failed, wrong_place, ...
    copies: Counter = field(default_factory=Counter)   # statement_diff_summary: copied blocks 'removed' or 'quoted' (#445)
    verify: dict | None = None        # statement_diff_summary from v17: the second model's check (request, cost, answer)

    @property
    def kept(self) -> list[Bullet]:
        return [b for b in self.bullets if b.problem is None]

    @property
    def dropped(self) -> list[Bullet]:
        return [b for b in self.bullets if b.problem is not None]

    @property
    def ok(self) -> bool:
        return not self.problems and not self.dropped

    def reasons(self) -> list[str]:
        return self.problems + [f"bullet {i + 1}: {b.problem}" for i, b in enumerate(self.bullets) if b.problem]

    def note(self) -> str:
        """The retry's note about this reply (`retry_note`)."""
        return retry_note(self)


def _when(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "date not recorded"


# ---------------------------------------------------------------------------------------------- requests
def _note(note: str | None) -> str | None:
    """The retry's note: sent as its own last block, after the unchanged instruction, so the retry can read the first
    attempt's cached prefix."""
    if not note:
        return None
    return ("Your previous answer to this request couldn't be published: " + note
            + "\nAnswer again, following every rule. Cite the passage that states each point, and write numbers "
              "only as the cited passage writes them.")


note_block = _note        # the retry's note block, shared with ft/ai/call_summary.py (the second user, #174)


def doc_summary_request(doc: Doc, prompt: Prompt, *, note: str | None = None) -> Request:
    lo, hi = BULLETS["doc_summary"]
    src = Source(doc.document_id, doc.text, doc.title, context=f"{doc.bank} · {doc.label} · released {_when(doc.day)}")
    instruction = f"Summarise the {doc.label} above ({doc.title}) in {lo} to {hi} bullets, following the rules."
    return Request("doc_summary", prompt, (src,), instruction, note=_note(note))


def changed_spans(segments: dict) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(spans in the old text, spans in the new text) of the sentences the redline marks changed, removed or added."""
    old, new = [], []
    for p in segments.get("pairs", []):
        if p.get("kind") in ("changed", "removed"):
            old += [tuple(x) for x in p.get("old", [])]
        if p.get("kind") in ("changed", "added"):
            new += [tuple(x) for x in p.get("new", [])]
    return old, new


def sentence_spans(segments: dict) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(spans in the old text, spans in the new text) of every sentence the redline holds, changed or not, in text order
    (the neighbour check from v16, `neighbour_problem`)."""
    old: set[tuple[int, int]] = set()
    new: set[tuple[int, int]] = set()
    for p in segments.get("pairs", []):
        old.update(tuple(x) for x in p.get("old", []))
        new.update(tuple(x) for x in p.get("new", []))
    return sorted(old), sorted(new)


def redline_text(segments: dict, old_text: str, new_text: str) -> str:
    """The changed sentences as plain text for the instruction (it's guidance, not a citable document)."""
    out = []
    for p in segments.get("pairs", []):
        o = " ".join(old_text[s:e] for s, e in p.get("old", []))
        n = " ".join(new_text[s:e] for s, e in p.get("new", []))
        if p.get("kind") == "changed":
            out.append(f'- Revised. Previous: "{o}" New: "{n}"')
        elif p.get("kind") == "added":
            out.append(f'- Added: "{n}"')
        elif p.get("kind") == "removed":
            out.append(f'- Removed: "{o}"')
    return "\n".join(out)


def diff_summary_request(new: Doc, old: Doc, segments: dict, prompt: Prompt, *, note: str | None = None,
                         must: list | None = None) -> Request:
    """The request for a diff summary; from v28 (`DIFF_MUST_FROM`), `must` (`must_changes`) is listed after the changed
    sentences (`must_text`)."""
    lo, hi = BULLETS["statement_diff_summary"]
    sources = (Source(old.document_id, old.text, f"Previous {old.label}, {_when(old.day)}",
                      context=f"Document 1: the previous {old.label} ({old.bank}, released {_when(old.day)})"),
               Source(new.document_id, new.text, f"New {new.label}, {_when(new.day)}",
                      context=f"Document 2: the new {new.label} ({new.bank}, released {_when(new.day)})"))
    structured = diff_json(prompt.version)
    ask = (f"In {lo} to {hi} bullets, explain what changed, as the schema's JSON, following the rules." if structured
           else f"In {lo} to {hi} bullets, explain what changed, following the rules.")
    v = int(prompt.version.lstrip("v") or 0)
    listed = must_text(must, old.text, new.text, each=v >= DIFF_ROOM_FROM) if must and v >= DIFF_MUST_FROM else ""
    instruction = (f"Document 1 is the previous {old.label} ({_when(old.day)}); document 2 is the new one "
                   f"({_when(new.day)}). These sentences changed between them:\n\n"
                   f"{redline_text(segments, old.text, new.text)}\n\n" + (f"{listed}\n\n" if listed else "") + ask)
    if structured:
        return Request("statement_diff_summary", prompt, sources, instruction, model=DIFF_MODEL_OF.get(prompt.version),
                       effort=DIFF_EFFORT_OF.get(prompt.version), route="json",
                       schema=load_schema("statement_diff_summary", prompt.version), note=_json_note(note))
    return Request("statement_diff_summary", prompt, sources, instruction, model=DIFF_MODEL_OF.get(prompt.version),
                   effort=DIFF_EFFORT_OF.get(prompt.version), note=_note(note))


def _json_note(note: str | None) -> str | None:
    """The json route's retry note (v11 on): as `_note`, in the route's own words."""
    if not note:
        return None
    return ("Your previous answer to this request couldn't be published: " + note
            + "\nAnswer again as the schema's JSON, following every rule. Copy each quote exactly from one changed sentence of "
              "the document it names, put every quotation of the text among the bullet's quotes, and write numbers only as "
              "the quoted sentences write them.")


# ---------------------------------------------------------------------------------------------- checking a reply
_LEAD = re.compile(r"^\s*(?:(?:[-*\u2022\u2013\u2014]|\d{1,2}[.)])[ \t]+)?")


_HEADING_FRAME = re.compile(r"^[\s#*_>]+|[\s*_:]+$")


@dataclass
class Scanned:
    bullets: list[tuple[int | None, str, list]]   # (the heading it sits under, its text, the API citations credited to it)
    stray: list[str]                              # the non-bullet lines after the first bullet (or heading)
    headings: list[int]                           # the headings found, as indexes into the given ones, in reply order


def _heading(line: str, headings: tuple[str, ...]) -> int | None:
    """The index of the heading a line is (case and markdown framing aside: '## What we said:', '**What we said**')."""
    flat = _HEADING_FRAME.sub("", line).strip().lower()
    return next((i for i, h in enumerate(headings) if flat == h.lower()), None)


def scan(parts: list[TextPart], headings: tuple[str, ...] = ()) -> Scanned:
    """The reply's bullets, each with the text blocks' citations credited to it. A text block's citations go to the bullet
    its text starts in (after any whitespace and list marker), so one block's citation never verifies two bullets. With
    `headings`, a line that is one of them is a heading, not a stray line, and the bullets after it sit under it."""
    text, spans = "", []
    for p in parts:
        lead = _LEAD.match(p.text)
        spans.append((len(text) + (lead.end() if lead else 0), p.citations))
        text += p.text
    bullets: list[list] = []                      # [region start, text start, text end, the heading it sits under]
    stray: list[str] = []
    seen: list[int] = []
    under: int | None = None
    pos = 0
    for line in text.split("\n"):
        start, end = pos, pos + len(line)
        pos = end + 1
        if headings and (h := _heading(line, headings)) is not None:
            seen.append(h)
            under = h
            continue
        m = _MARK.match(line)
        if m:
            bullets.append([start, start + m.end(), end, under])
        elif line.strip() and (bullets or seen):
            stray.append(line.strip())
    out = []
    for i, (region, s, e, h) in enumerate(bullets):
        nxt = bullets[i + 1][0] if i + 1 < len(bullets) else len(text) + 1
        cites = [c for at, cs in spans if region <= at < nxt for c in cs]
        body = " ".join(text[s:e].split())
        if body:
            out.append((h, body, cites))
    return Scanned(out, stray, seen)


def split_bullets(parts: list[TextPart]) -> tuple[list[tuple[str, list]], list[str]]:
    """(the bullets as (text, the API citations credited to it), the non-bullet lines after the first bullet)."""
    found = scan(parts)
    return [(body, cites) for _, body, cites in found.bullets], found.stray


# A figure as written: digits with whatever they're joined to by . , / - (and letters: "Q2", "FY27", "10-year"), with
# a sign only when it stands alone ("-0.25", not the hyphen of "COVID-19"). Dashes and the minus sign read as "-".
_FIGURE = re.compile(r"(?<![\w.])([-+]?)([A-Za-z]*\d\w*(?:[.,/-]\w+)*)")
_PLAIN = re.compile(r"^\d{1,3}(?:,\d{2,3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$")
_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-"})


def figures(text: str) -> list[tuple[str, str]]:
    """Every figure in `text` as (sign, canonical form): plain numbers by value ("5.50" and "5.5" agree; Indian and
    Western grouping read whole), everything else as written, lower-cased ("4-1/4", "q2", "fy2026-27")."""
    out = []
    for sign, tok in _FIGURE.findall(text.translate(_DASHES)):
        if _PLAIN.match(tok):
            whole, _, frac = tok.replace(",", "").partition(".")
            tok = (whole.lstrip("0") or "0") + ("." + frac.rstrip("0") if frac.rstrip("0") else "")
        out.append((sign, tok.lower()))
    return out


def unsupported_figures(bullet: str, passages: list[str]) -> list[str]:
    """The bullet's figures that aren't in its cited passages. An unsigned figure may match either sign; a signed one
    needs the same sign (Review 01, F4's rule for numbers)."""
    have = {f for p in passages for f in figures(p)}
    unsigned = {t for _, t in have}
    bad = []
    for sign, tok in figures(bullet):
        if (sign and (sign, tok) not in have) or (not sign and tok not in unsigned):
            bad.append(sign + tok)
    return list(dict.fromkeys(bad))


def _overlaps(s: int, e: int, spans: list[tuple[int, int]]) -> bool:
    return any(s < b and e > a for a, b in spans)


# The stable phrases `check` writes into a reason, by the accuracy page's category (ft/quality/reasons.py reads this table;
# tests run `check` on crafted bad replies and assert each phrase still lands where it says). Regexes, case-insensitive.
# Change a message above and this table together.
REASON_PHRASES: dict[str, tuple[str, ...]] = {
    "citation": (r"no verified citation", r"quoted words not in its cited passages",
                 r"isn't in the (?:previous|new) document's changed sentences", r"a claim that something stayed the same",
                 r"a claim about the previous document with no quote from it",
                 r"a claim resting on a sentence next to its quotes"),
    "numbers": (r"numbers not in its cited passages",),
    "format": (r"text outside the bullets", r"no bullets \(each bullet", r"\d+ bullets, not \d+ to \d+",
               r"an unclosed quotation mark", r"repeats its own words", r"doesn't end as a sentence ends",
               r"the reply isn't the schema's JSON", r"isn't the schema's object", r"\d+ quotes, not 1 to \d+",
               r"copies a statement's words without quotation marks", r"two quotations side by side",
               r"a quotation cut in the middle of", r"a quotation that ends on ",
               r"a quotation that doesn't fit the grammar of its sentence", r"the statement's next words after a quotation",
               r"a run of a statement's words outside quotation marks"),
}
REASON_PHRASES["citation"] += (r"claims words were added that the previous statement already had",
                               r"a claim resting on the sentence a quoted one refers back to",
                               r"a claim about who voted for the action", r"a second model's check found",
                               r"the second model's check gave no answer")


def verify_bullet(body: str, cites: list, sources: tuple[Source, ...], mapping: Counter, *,
                  changed: dict[int, list[tuple[int, int]]] | None = None,
                  full_text: dict[int, str] | None = None, quotes: bool = False, repeats: bool = False,
                  ends: bool = False) -> Bullet:
    """One bullet against our copies of the documents: each citation mapped into them (`citations.map_api_citation`),
    then the bullet kept only with a verified citation and only figures from its cited passages. `mapping` counts how
    the citations mapped. `changed`: for statement_diff_summary, each document's changed spans. `quotes`: also only
    quotations from its cited passages (`quote_problem`; statement_diff_summary from v7 on). `repeats`: and no stretch of
    its own words twice (`repeat_problem`; from v8 on). `ends`: and it ends as a sentence ends (`end_problem`; v10 on)."""
    b = Bullet(body)
    seen = set()
    passages = []
    for c in cites:
        if c.kind != "char_location" or c.document_index is None or not 0 <= c.document_index < len(sources):
            mapping["not_a_passage"] += 1
            continue
        if c.start is None or c.end is None or c.end <= c.start or not c.cited_text.strip():
            mapping["empty"] += 1                        # an empty range quotes nothing
            continue
        src = sources[c.document_index]
        doc_text = (full_text or {}).get(src.document_id, src.text if src.seg_start == 0 else None)
        if doc_text is None:
            mapping["failed"] += 1
            continue
        span = map_api_citation(doc_text, src.seg_start, c.start, c.end, c.cited_text)
        if not span.verified:
            mapping["failed"] += 1
            continue
        if changed is not None and not _overlaps(span.start, span.end, changed.get(src.document_id, [])):
            mapping["unchanged_text"] += 1               # the diff summary cites only what changed
            continue
        mapping[span.how] += 1
        key = (src.document_id, span.start, span.end)
        if key not in seen:
            seen.add(key)
            b.citations.append(Cited(src.document_id, span.start, span.end, span.how))
            passages.append(doc_text[span.start:span.end])
    if not b.citations:
        b.problem = ("no verified citation on a changed sentence" if changed is not None and cites
                     else "no verified citation")
    elif extra := unsupported_figures(body, passages):
        b.problem = "numbers not in its cited passages: " + ", ".join(extra)
    elif quotes and (q := quote_problem(body, passages)):
        b.problem = q
    elif repeats and (r := repeat_problem(body)):
        b.problem = r
    elif ends and (e := end_problem(body)):
        b.problem = e
    return b


# ---------------------------------------------------------------------------------------------- quotations (statement_diff_summary)
# #445, round 6 (Anand's decision of 12 Nov 2026, 22:36 IST, after the judge failed round 5): from prompt v7 on
# (`ai_batch.QUOTES_FROM`), a bullet's quotations come only from its own cited passages. The citation check proves each
# citation exists, not that the words a bullet quotes come from them, and the judge's rule (b) fails were exactly that:
# words quoted from the previous statement in a bullet that cited only the new one. A tightening (the card allows it):
# - Each stretch between a pair of double quotation marks (`match_form`: curly ones straightened) is found in one of the
#   bullet's cited passages, or in its passages joined in order (a quoted copy of two cited sentences, `settle_copies`),
#   case aside. Punctuation the sentence puts just inside the closing mark (".", ",", ";", ":", "!", "?") doesn't count.
#   An elision ("...", "…") or a bracketed insertion ("reinvest[ed]") splits it: the pieces are found in order in one
#   haystack. Its ends are word-bounded where they are words ("solid" isn't found in "solidly").
# - An odd number of double quotation marks fails the bullet: what is quoted can't be told.
# A bullet that fails is dropped, so the reply is retried once with a note, then rejected (the published output stays).
_QUOTED = re.compile(r'"([^"]*)"')
_ELISION = re.compile(r"\s*(?:\.\.\.|…|\[[^\]]*\])\s*")
_INNER_END = ".,;:!?"


def _quote_found(q: str, hay: list[str]) -> bool:
    q = q.strip().rstrip(_INNER_END).strip().casefold()
    pieces = [x for x in _ELISION.split(q) if _WORD.search(x)]
    if not pieces:
        return True                                      # a quoted mark or elision alone quotes no words
    body = r".*?".join(re.escape(x) for x in pieces)
    head = r"(?<!\w)" if re.match(r"\w", q) else ""
    tail = r"(?!\w)" if re.search(r"\w$", q) else ""
    t = re.compile(head + body + tail)
    return any(t.search(h) for h in hay)


# #445, round 7 (from prompt v8 on, `ai_batch.REPEATS_FROM`; a tightening): a bullet that repeats a stretch of
# REPEAT_MIN_WORDS or more of its own words fails. Round 6's retry for one target (dev's 2492) wrote its first sentence
# twice. Words as `_WORD` reads them, case aside, in `match_form`; quoted or not.
REPEAT_MIN_WORDS = 12


def repeat_problem(body: str) -> str | None:
    """Why a bullet repeats itself (see above), or None."""
    words = _WORD.findall(match_form(body).casefold())
    seen: set[tuple[str, ...]] = set()
    for i in range(len(words) - REPEAT_MIN_WORDS + 1):
        gram = tuple(words[i:i + REPEAT_MIN_WORDS])
        if gram in seen:
            return f"repeats its own words: \"{' '.join(gram)}\""
        seen.add(gram)
    return None


# #445, round 9 (from prompt v10 on, `ai_batch.ENDS_FROM`; a tightening): a bullet ends as a sentence ends: a full stop, a
# question or exclamation mark, then at most closing quotation marks or brackets. Round 8 (v9, Sonnet) left words written
# only to hold a citation after a bullet's last sentence (dev's 2510: "…per cent". Previous new").
_ENDS = re.compile("[.!?][\"'\u201d\u2019)\\]]*$")


def end_problem(body: str) -> str | None:
    """Why a bullet doesn't end as a sentence ends (see above), or None."""
    return None if _ENDS.search(body.rstrip()) else "doesn't end as a sentence ends"


def quote_problem(body: str, passages: list[str]) -> str | None:
    """Why a bullet's quotations fail (see above), or None."""
    t = match_form(body)
    if t.count('"') % 2:
        return "an unclosed quotation mark"
    hay = [match_form(h).casefold() for h in [*passages, " ".join(passages)]]
    bad = [q for q in _QUOTED.findall(t) if not _quote_found(q, hay)]
    if bad:
        return "quoted words not in its cited passages: " + ", ".join(f'"{q.strip()[:80]}"' for q in bad)
    return None


# ---------------------------------------------------------------------------------------------- copies (statement_diff_summary)
# #445 (R05-2; Anand's option 1, 12 Nov 2026). On the citations route the model's cited text blocks are often word-for-word
# copies of the sentences they cite, and `scan` joins them into the bullet: "The assessment of economic growth changed from
# "solid" to "strong". Economic activity is expanding at a solid pace. ... Economic activity is expanding at a strong pace."
# Prompts v2 and v3 didn't stop it (two dev samples), so for this task only, from prompt v4 on, before the checks:
# - A copy is a cited text block whose text is found word for word, in `match_form`, in its own cited text (one citation's,
#   or its citations' joined in order), a closing punctuation mark aside ("…in several parts of the world." from "…world,
#   especially …"), with at least COPY_MIN_WORDS words. Its list marker, the whitespace around it and a pair of
#   parentheses or quotation marks around it don't count; it is one line. A block whose pieces, joined by " / ", " ... "
#   or "; ", are each such a copy holds one copy per piece. A block with any words of the model's own isn't a copy and is
#   never touched.
# - Copies joined only by separators share one fate. They come out of the bullet's text, with the separators, when the
#   text around them reads without them. Before: the bullet's start, a sentence's end, or a word that doesn't need what
#   follows (not a colon, nor "in", "as", "from", "reads" and the like: `_NEEDS_MORE`). After: the bullet's end,
#   punctuation, or a new sentence. Copies in their own parentheses go with them ("…to "been little changed" (<copy> /
#   <copy>)."), and so does a copy in its own quotation marks ("…retained at neutral." "<copy>""). A full stop that ended
#   a removed copy stays where the sentence would otherwise have none. Their citations stay on the bullet: the page shows
#   each cited sentence through its chip.
# - Any other copy is part of the model's own sentence ("…described as "moderate" in <copy>"): it goes in quotation marks
#   (one pair per piece), or keeps its own. So does every copy of a bullet that would otherwise keep fewer than
#   OWN_MIN_WORDS words, or none outside quotation marks (no sentence of the model's own: "Revised:", or nothing).
# - A copy inside the model's quotation marks with other words is left as it is.
# - From prompt v8 on (#445, round 7; `strict`): a copy comes out only when it stands as a whole sentence of its own: the
#   text before it is the bullet's start or ends a sentence, and after it comes the bullet's end or a new sentence (a full
#   stop of the model's own may close it first). Copies in their own parentheses still go. Every other copy goes in
#   quotation marks. Why: round 6 (v7, Sonnet 5.5) wrote its cited blocks as fragments inside its own sentences ("…says the
#   Committee <copy>."), and the looser test above took 11 of them out, leaving 8 broken sentences in 6 of 10 outputs.
# Our code only removes words copied from the source (with the separators, parentheses and quotation marks that held them),
# or puts them in quotation marks; it never adds a word. The checks then run on the final text: the figures check can only
# get stricter, since a copy's figures are in its cited text.
COPY_MIN_WORDS = 5                # the judge's measure counts copied stretches from five words (ft/eval/diff_quotes.py)
OWN_MIN_WORDS = 3                 # fewer words left, or none outside quotation marks, isn't a sentence of the model's own
_OPEN, _CLOSE = "\x01", "\x02"    # around a copy between `mark_copies` and `settle_copies`; never stored
_GAP, _GAP_STOP = "\x03", "\x04"  # where a removed copy was (`_GAP_STOP`: it ended with a full stop); never stored
_COPY = re.compile("\x01([^\x01\x02]*)\x02")
_WORD = re.compile(r"[^\W_]+")
_PIECES = re.compile(r"\s*;\s+|\s+(?:/|\||\.\.\.|…)\s+")   # between the pieces of a joined copy
_SEP = r"(?:\s|\.\.\.|…|[;|/]|(?<=\s)[-–—](?=\s|$))"
_SEP_TAIL = re.compile(_SEP + "+$")
_SEP_HEAD = re.compile(r"^(?:\s|\.\.\.|…|[;|/]|[-–—](?=\s))+")
_SENTENCE_END = re.compile("(?:^|[.!?][\"'”’)\\]]*)$")
_CLOSING_QUOTE = "\"'”’"
_LAST_WORD = re.compile(r"([^\W_][\w'’-]*)$")
# A word that leaves the sentence open when the copy after it comes out ("…described as "moderate" in <copy>").
_NEEDS_MORE = frozenset({"a", "an", "the", "in", "into", "as", "from", "to", "that", "which", "with", "by", "of",
    "for", "on", "at", "and", "or", "but", "nor", "than", "like", "including", "namely", "while", "whereas", "is",
    "are", "was", "were", "be", "been", "being", "become", "becomes", "became", "read", "reads", "reading", "say",
    "says", "said", "saying", "state", "states", "stated", "stating", "note", "notes", "noted", "noting", "add",
    "adds", "adding", "replace", "replaces", "replaced", "replacing", "now", "then", "where", "when"})


def _found(text: str, hay: list[str]) -> bool:
    if len(_WORD.findall(text)) < COPY_MIN_WORDS:
        return False
    words = match_form(text).rstrip(".,;:!? ")          # a full stop the model put after a shortened copy isn't a word
    t = re.compile(r"(?<!\w)" + re.escape(words) + r"(?!\w)")
    return any(t.search(h) for h in hay)


def copy_spans(core: str, cited: list[str]) -> list[tuple[int, int]]:
    """Where `core` (a cited block's text) copies its cited text: all of it, or each of its joined pieces; else nothing."""
    hay = [match_form(h) for h in [*cited, " ".join(cited)]]
    if _found(core, hay):
        return [(0, len(core))]
    spans, pos = [], 0
    for m in [*_PIECES.finditer(core), None]:
        a, b = pos, (m.start() if m else len(core))
        if not _found(core[a:b], hay):
            return []
        spans.append((a, b))
        pos = m.end() if m else b
    return spans if len(spans) > 1 else []


_PAIRS = ((("(",), r"\)[.,;:!?]?$"), (('"', "\u201c"), r"[\"\u201d][.,;:!?]?$"))


def mark_copies(parts: list[TextPart]) -> list[TextPart]:
    """The reply's text blocks with each copy of its own cited text marked for `settle_copies` (see above)."""
    if any(c in p.text for p in parts for c in (_OPEN, _CLOSE, _GAP, _GAP_STOP)):
        return parts                                     # never: our markers would be ambiguous, so nothing is touched
    out = []
    for p in parts:
        cited = [c.cited_text for c in p.citations if c.kind == "char_location" and c.cited_text.strip()]
        lead = _LEAD.match(p.text)
        s = lead.end() if lead else 0
        e = len(p.text.rstrip())
        for opens, closes in _PAIRS * 2:                 # parentheses or quotation marks around a copy stay outside it
            if p.text[s:s + 1] in opens and (m := re.search(closes, p.text[s + 1:e])):
                s, e = s + 1, s + 1 + m.start()
                s, e = s + len(p.text[s:e]) - len(p.text[s:e].lstrip()), s + len(p.text[s:e].rstrip())
        core = p.text[s:e]
        spans = copy_spans(core, cited) if cited and core and "\n" not in core else []
        if spans:
            text, pos = p.text[:s], 0
            for a, b in spans:
                text += core[pos:a] + _OPEN + core[a:b] + _CLOSE
                pos = b
            p = TextPart(text + core[pos:] + p.text[e:], p.citations)
        out.append(p)
    return out


def _own_words(text: str) -> int:
    """The words of `text` outside double quotation marks."""
    t, n, inside = match_form(text), 0, False
    for piece in re.split('(")', t):
        if piece == '"':
            inside = not inside
        elif not inside:
            n += len(_WORD.findall(piece))
    return n


def unmark(text: str) -> str:
    return text.replace(_OPEN, "").replace(_CLOSE, "")


def _start_ok(before: str, strict: bool = False) -> bool:
    """Whether the bullet's text before a copy reads on without it (see above). `strict` (v8 on): only the bullet's start
    or a sentence's end."""
    b = _SEP_TAIL.sub("", before).rstrip()
    if not b or _SENTENCE_END.search(b):
        return True
    if strict:
        return False
    if b[-1] in _CLOSING_QUOTE:
        return True
    b = b.rstrip(",").rstrip()
    m = _LAST_WORD.search(b)
    return bool(m) and m.group(1).lower() not in _NEEDS_MORE


def _end_ok(copy: str, after: str, strict: bool = False) -> bool:
    """Whether the bullet's text after a copy reads on without it (see above). `strict` (v8 on): only the bullet's end or
    a new sentence, after the copy's own full stop or one of the model's."""
    a = _SEP_HEAD.sub("", after)
    if strict:
        ended = bool(_SENTENCE_END.search(copy.rstrip()))
        if a[:1] in (".", "!", "?") and not ended:
            a, ended = a[1:].lstrip(), True
        return not a or (ended and (a[0].isupper() or a[0] in "\"\u201c"))
    return (not a or a[0] in ".,;:!?)"
            or (bool(_SENTENCE_END.search(copy.rstrip())) and (a[0].isupper() or a[0] in "\"“")))


def _close_gaps(t: str) -> str:
    """The text with each removed copy's gap closed: its separators and its own parentheses go with it, a chain of gaps is
    one gap, and a full stop that ended the copy stays where the sentence would otherwise have none."""
    gap = f"[{_GAP}{_GAP_STOP}]"
    t = re.sub(_SEP + "*(" + gap + ")" + r"(?:\s|\.\.\.|…|[;|/]|[-–—](?=\s))*", r"\1", t)
    t = re.sub(gap + "{2,}", lambda m: m.group(0)[-1], t)
    t = re.sub(r"\(\s*" + gap + r"\s*\)", _GAP, t)      # a parenthesis's full stop never ends the sentence around it
    while (m := re.search(gap, t)) is not None:
        x, y, stop = t[:m.start()].rstrip(), t[m.end():].lstrip(), m.group(0) == _GAP_STOP
        if y and y[0] in ".!?" and _SENTENCE_END.search(x):
            t = x + y[1:]                                # the model's full stop after a removed copy: the sentence has one
        elif y and y[0] in ".,;:!?)":
            t = (x[:-1] if y[0] != "," and x.endswith(",") else x) + y
        elif not y:
            x = x.rstrip(",:;").rstrip()
            t = x + ("." if stop and not _SENTENCE_END.search(x) else "")
        else:
            if stop and not _SENTENCE_END.search(x) and (y[0].isupper() or y[0] in '"\u201c'):
                x = x.rstrip(",:;").rstrip() + "."           # the copy's full stop, before a new sentence only
            t = x + " " + y
    return t


def settle_copies(body: str, tally: Counter, strict: bool = False) -> str:
    """A bullet's final text from its marked copies (see above); `tally` counts them 'removed' and 'quoted'. `strict`: from
    prompt v8 on, only a copy that stands as a whole sentence comes out."""
    if _OPEN not in body:
        return body
    tokens: list[str] = []                               # own text and copies, alternating: own, copy, own, ..., own
    pos = 0
    for m in _COPY.finditer(body):
        tokens += [unmark(body[pos:m.start()]), m.group(1)]
        pos = m.end()
    tokens.append(unmark(body[pos:]))
    copies = range(1, len(tokens), 2)
    marks = {i: match_form("".join(tokens[:i])).count('"') for i in copies}
    quoted = {i for i in copies if marks[i] % 2 and tokens[i - 1].rstrip()[-1:] in '"\u201c'
              and tokens[i + 1].lstrip()[:1] in '"\u201d' and '"' not in match_form(tokens[i])}
    inside = {i for i in copies if i not in quoted and (marks[i] % 2 or '"' in match_form(tokens[i]))}

    def bare(i: int, side: int) -> str:
        """The own text on one side of copy `i` (-1 before, +1 after) without the quotation marks of the copies it lies
        between."""
        t = tokens[i + side]
        if (side < 0 and i in quoted) or (side > 0 and i + 2 in quoted):
            t = t.rstrip()[:-1]
        if (side < 0 and i - 2 in quoted) or (side > 0 and i in quoted):
            t = t.lstrip()[1:]
        return t

    acts: dict[int, str] = {}                            # a copy's index → 'remove' | 'quote'; else it stays as it is
    chains: list[list[int]] = []                         # copies joined by separators only (" / ", " ... ", "; ") share a fate
    for i in copies:
        if i in inside:
            continue
        if chains and chains[-1][-1] == i - 2 and not _SEP_TAIL.sub("", bare(i, -1)):
            chains[-1].append(i)
        else:
            chains.append([i])
    for chain in chains:
        first, last = chain[0], chain[-1]
        before, after = "".join(tokens[:first - 1]) + bare(first, -1), bare(last, +1)
        if not quoted & set(chain) and before.rstrip().endswith("(") and after.lstrip().startswith(")"):
            ok = True                                    # copies in their own parentheses go with them
        else:
            ok = _start_ok(before, strict) and _end_ok(tokens[last], after, strict)
        for k in chain:
            if ok:
                acts[k] = "remove"
            elif k not in quoted:
                acts[k] = "quote"                        # a copy already in its own marks stays as it is

    def build(remove: bool) -> str:
        out = list(tokens)
        for k in sorted(acts):
            if acts[k] == "remove" and remove:
                out[k] = _GAP_STOP if _SENTENCE_END.search(tokens[k].rstrip()) else _GAP
                if k in quoted:                          # its own quotation marks go with it
                    out[k - 1], out[k + 1] = out[k - 1].rstrip()[:-1], out[k + 1].lstrip()[1:]
            elif k not in quoted:
                out[k] = f'"{tokens[k]}"'
        return " ".join(_close_gaps("".join(out)).split())

    removed = sum(1 for a in acts.values() if a == "remove")
    text = build(remove=True)
    if removed and (len(_WORD.findall(text)) < OWN_MIN_WORDS or not _own_words(text)):
        text, removed = build(remove=False), 0           # no sentence of the model's own would be left: quote them all
    quoted_now = sum(1 for k, a in acts.items() if k not in quoted and (a == "quote" or not removed))
    for key, n in (("removed", removed), ("quoted", quoted_now)):
        if n:
            tally[key] += n
    return text


def check(task: str, reply: Reply, sources: tuple[Source, ...], *,
          changed: dict[int, list[tuple[int, int]]] | None = None, full_text: dict[int, str] | None = None,
          drop_copies: bool = False, check_quotes: bool = False, strict_copies: bool = False,
          check_repeats: bool = False, check_ends: bool = False) -> Checked:
    """Check a reply against our copies of the documents it was given (`sources`; `full_text` by document id when a
    source is a segment). `changed`: for statement_diff_summary, each document's changed spans. `drop_copies`: for
    statement_diff_summary from prompt v4 on (`ai_batch.COPIES_FROM`), the copies rule above; `check_quotes`: from v7 on
    (`ai_batch.QUOTES_FROM`), the quotations rule above; `strict_copies` and `check_repeats`: from v8 on
    (`ai_batch.STRICT_COPIES_FROM`, `REPEATS_FROM`), the copies rule's strict form and the repeats rule; `check_ends`:
    from v10 on (`ENDS_FROM`), the ends rule. All five are ignored for other tasks."""
    lo, hi = BULLETS[task]
    mapping: Counter = Counter()
    copies: Counter = Counter()
    parts = reply.parts()
    diff = drop_copies and task == "statement_diff_summary"
    found, stray = split_bullets(mark_copies(parts) if diff else parts)
    if diff:
        found = [(settle_copies(body, copies, strict=strict_copies), cites) for body, cites in found]
        stray = [unmark(x) for x in stray]
    quotes = check_quotes and task == "statement_diff_summary"
    repeats = check_repeats and task == "statement_diff_summary"
    ends = check_ends and task == "statement_diff_summary"
    bullets = [verify_bullet(body, cites, sources, mapping, changed=changed, full_text=full_text, quotes=quotes,
                             repeats=repeats, ends=ends)
               for body, cites in found]
    problems = []
    if stray:
        problems.append("text outside the bullets: " + " / ".join(f'"{x[:120]}"' for x in stray))
    if not bullets:
        problems.append("no bullets (each bullet is a line starting with '- ')")
    elif not lo <= len(bullets) <= hi:
        problems.append(f"{len(bullets)} bullets, not {lo} to {hi}")
    return Checked(task, bullets, problems, mapping, copies)


# ---------------------------------------------------------------------------------------------- the json route (v11 on)
_JSON_DOC = {"previous": 0, "new": 1}     # a quote's document → its index in the request's sources (document 1, document 2)


def _flat(text: str) -> tuple[str, list[int]]:
    """`text` as `match_form` reads it (whitespace runs one space, curly quotes straight, dashes one hyphen), with each
    character's index in `text`."""
    out: list[str] = []
    back: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            if not out or out[-1] == " ":
                continue
            ch = " "
        out.append(ch)
        back.append(i)
    return "".join(out).translate(_STRAIGHT), back


def _find_in_changed(text: str, quote: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The changed sentences (`spans`, in `text`) that an exact copy of `quote` lies in or overlaps; [] if none. Matched as
    `match_form` reads both, case kept; failing that, case aside (a quote that opens with a capital the sentence lacks)."""
    q = match_form(quote).strip()
    if not q:
        return []
    flat, back = _flat(text)
    found: list[tuple[int, int]] = []
    for hay, needle in ((flat, q), (flat.lower(), q.lower())):
        if len(hay) != len(flat) or len(needle) != len(q):
            continue
        at = hay.find(needle)
        while at >= 0:
            start, end = back[at], back[at + len(needle) - 1] + 1
            found += [(s, e) for s, e in spans if s < end and start < e and (s, e) not in found]
            at = hay.find(needle, at + 1)
        if found:
            break
    return sorted(found)


def check_diff_json(answer, sources: tuple[Source, ...], changed: dict[int, list[tuple[int, int]]], *,
                    version: str | None = None, sentences: dict[int, list[tuple[int, int]]] | None = None) -> Checked:
    """Check a v11-on statement_diff_summary reply (`answer`: the reply's JSON) against our copies of the two documents
    (`sources`: document 1, document 2) and their changed sentences (`changed`, by document id). Each bullet's quotes are
    found in its document's changed sentences, which become its citations; then the bullet gets the citations route's
    checks from v10: only figures and quotations from those sentences, no repeats, and it ends as a sentence ends. The
    same `Checked` as `check`'s, so a published output and its citations read the same on either route. `version`: the
    prompt version that asked for it; from v12 (`DIFF_STRICT_FROM`) a bullet also gets the unquoted-copy and side-by-side
    checks, from v13 (`DIFF_WHOLE_FROM`) the same-claim and cut-quotation checks, from v14 (`DIFF_SIDES_FROM`) the wider
    same-claim check and the previous-document check, from v15 (`DIFF_FP_FIX_FROM`) those checks without two false
    positives, from v16 (`DIFF_FIT_FROM`) the grammar-fit and run-on checks, and the neighbour check where `sentences` (each
    document's sentences, from the redline: `sentence_spans`) is given, from v17 (`DIFF_VERIFY_FROM`) v17's checks, and from v20
    (`DIFF_OPEN_END_FROM`) the cut-quotation check on a quotation the bullet goes on from, and from v28 (`DIFF_MUST_FROM`) the
    grammar-fit check on a predicate after a preposition (the changes it must report are checked by the task:
    `must_problems`); an earlier version, or none, is checked as it ran (none: as v11 was)."""
    task, mapping = "statement_diff_summary", Counter()
    number = int(version.lstrip("v")) if version is not None else 0
    strict, whole, sides = number >= DIFF_STRICT_FROM, number >= DIFF_WHOLE_FROM, number >= DIFF_SIDES_FROM
    fixed, fit, runs = number >= DIFF_FP_FIX_FROM, number >= DIFF_FIT_FROM, number >= DIFF_VERIFY_FROM
    open_end, predicate = number >= DIFF_OPEN_END_FROM, number >= DIFF_MUST_FROM
    rows = answer.get("bullets") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        return Checked(task, [], ["the reply isn't the schema's JSON ({bullets: [...]})"], mapping)
    copies: Counter = Counter()
    bullets = [_diff_json_bullet(raw, sources, changed, mapping, strict=strict, whole=whole, sides=sides, fixed=fixed,
                                 fit=fit, sentences=sentences, runs=runs, copies=copies, open_end=open_end,
                                 predicate=predicate)
               for raw in rows]
    problems = []
    lo, hi = BULLETS[task]
    if not lo <= len(bullets) <= hi:
        problems.append(f"{len(bullets)} bullets, not {lo} to {hi}")
    return Checked(task, bullets, problems, mapping, copies)


def _diff_json_bullet(raw, sources: tuple[Source, ...], changed: dict[int, list[tuple[int, int]]], mapping: Counter, *,
                      strict: bool = False, whole: bool = False, sides: bool = False, fixed: bool = False,
                      fit: bool = False, sentences: dict[int, list[tuple[int, int]]] | None = None, runs: bool = False,
                      copies: Counter | None = None, open_end: bool = False, predicate: bool = False) -> Bullet:
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not isinstance(raw.get("quotes"), list):
        b = Bullet("")
        b.problem = "isn't the schema's object"
        return b
    text = _LEAD.sub("", " ".join(raw["text"].split()), count=1)
    b = Bullet(text)
    quotes = raw["quotes"]
    if not text or not all(isinstance(q, dict) and q.get("document") in _JSON_DOC and isinstance(q.get("text"), str)
                           for q in quotes):
        b.problem = "isn't the schema's object"
        return b
    if not 1 <= len(quotes) <= DIFF_MAX_QUOTES:
        b.problem = f"{len(quotes)} quotes, not 1 to {DIFF_MAX_QUOTES}"
        return b
    passages: list[str] = []
    for q in quotes:
        src = sources[_JSON_DOC[q["document"]]]
        hits = _find_in_changed(src.text, q["text"], changed.get(src.document_id, []))
        mapping["search" if hits else "failed"] += 1
        if not hits:
            short = " ".join(q["text"].split())[:80]
            b.problem = f"a quote (\"{short}\") isn't in the {q['document']} document's changed sentences"
            return b
        for s, e in hits:
            cited = Cited(src.document_id, s, e, "search")
            if cited not in b.citations:
                b.citations.append(cited)
                passages.append(src.text[s:e])
    if runs:                                    # v17: a run of a statement's words joins a quotation, or becomes one
        text, n = settle_runs(text, passages, [src.text for src in sources])
        b.text = text
        if copies is not None and n:
            copies["quoted"] += n
    if extra := unsupported_figures(text, passages):
        b.problem = "numbers not in its cited passages: " + ", ".join(extra)
    else:
        b.problem = quote_problem(text, passages) or repeat_problem(text) or end_problem(text)
    if b.problem is None and strict:
        b.problem = side_by_side_problem(text) or unquoted_copy_problem(text, [src.text for src in sources])
    if b.problem is None and whole:
        b.problem = same_problem(text, again=sides) or cut_quote_problem(text, passages, subject_ok=fixed, open_end=open_end)
    if b.problem is None and sides and not any(q["document"] == "previous" for q in quotes):
        b.problem = previous_problem(text, whole_text=fixed)
    if b.problem is None and fit:
        b.problem = fit_problem(text, predicate=predicate) or runs_on_problem(text, passages)
        if b.problem is None and sentences is not None:
            texts = {src.document_id: src.text for src in sources}
            b.problem = neighbour_problem(text, [(c.document_id, c.start, c.end) for c in b.citations], texts, sentences,
                                          changed, refers_back=runs)
    if b.problem is None and runs:
        cited = [(c.document_id, c.start, c.end) for c in b.citations]
        texts = {src.document_id: src.text for src in sources}
        b.problem = (long_run_problem(text, [src.text for src in sources])
                     or added_problem(text, cited, texts, sources[0].document_id, changed, sentences)
                     or vote_for_problem(text, passages))
    return b


_NAME_WORDS = {"and", "dr", "prof", "shri", "smt", "mr", "ms"}     # with capitalised words and initials: a run of names


def unquoted_copy_problem(text: str, documents: list[str], min_words: int = UNQUOTED_MIN_WORDS) -> str | None:
    """Why a bullet copies a statement's words outside quotation marks (see `DIFF_STRICT_FROM`), or None. Words as `_WORD`
    reads them in `match_form`, case aside; a quotation splits the text, so no run crosses one."""
    t = match_form(text)
    if t.count('"') % 2:
        return None                                       # `quote_problem` has already failed it
    grams: set[tuple[str, ...]] = set()
    for d in documents:
        words = _WORD.findall(match_form(d).casefold())
        grams.update(tuple(words[i:i + min_words]) for i in range(len(words) - min_words + 1))
    for piece in _QUOTED.split(t)[::2]:                    # the text outside quotation marks
        raw = _WORD.findall(piece)
        low = [w.casefold() for w in raw]
        for i in range(len(low) - min_words + 1):
            run = range(i, i + min_words)
            plain = sum(not (raw[j][0].isupper() or low[j] in _NAME_WORDS) for j in run)
            if tuple(low[i:i + min_words]) in grams and plain >= COPY_PLAIN_WORDS:
                return f"copies a statement's words without quotation marks: \"{' '.join(raw[i:i + min_words])}\""
    return None


def side_by_side_problem(text: str) -> str | None:
    """Why a bullet sets two quotations side by side (nothing of its own but spaces or punctuation between them), or None."""
    t = match_form(text)
    if t.count('"') % 2:
        return None
    marks = [m.span() for m in re.finditer(r'"[^"]*"', t)]
    for (s1, e1), (s2, e2) in pairwise(marks):
        if not _WORD.search(t[e1:s2]):
            return f"two quotations side by side: {t[s1:e1][:60]} {t[s2:e2][:60]}"
    return None


# `same_problem` (v13 on): the bullet's own words saying that something stayed the same. Matched outside quotation marks, in
# `match_form`, case aside; a document's own words in quotation marks ("unchanged at 5.50 per cent") never count, and
# neither does a decision described in the model's words ("moved from holding the rate", "changed … to no change").
_SAME = re.compile(
    r"(?:\bnot|n't)\s+(?:among|one\s+of|part\s+of)\s+the\s+changes\b"
    r"|\b(?:is|are|was|were|remains?|remained|stays?|stayed)\s+(?:still\s+)?(?:unchanged|the\s+same|kept|retained)\b"
    r"|\b(?:remains?|remained|stays?|stayed)\s+(?:still\s+)?at\b"
    r"|\b(?:did|does|do|has|have|had)\s+not\s+change\b|\b(?:didn't|doesn't|don't|hasn't|haven't|hadn't)\s+change\b"
    r"|\b(?:is|are|was|were)\s+not\s+changed\b|\bno\s+change\s+(?:to|in)\s+(?:the\s+)?wording\b",
    re.IGNORECASE)
# From v14 (`DIFF_SIDES_FROM`) `same_problem` also matches these: a claim that something happened again or as before.
_AGAIN = re.compile(r"\bagain\b|\bas\s+(?:before|previously|it\s+did\s+before)\b|\bas\s+in\s+the\s+(?:previous|earlier)\b",
                    re.IGNORECASE)
# `previous_problem` (v14 on): the bullet's own words describing the previous document. "before" counts only where it ends a
# clause ("… to 6.25 per cent" before;), not as a conjunction ("… before moderating").
_ABOUT_PREVIOUS = re.compile(
    r"\b(?:previous(?:ly)?|earlier|formerly|had\s+been|used\s+to|no\s+longer|instead\s+of|rather\s+than"
    r"|replac(?:es|ed|ing)|removed|dropped|deleted|omitted"
    r"|(?:moved|changed|went|shifted|switched|fell|rose|lowered|raised|revised|cut|increased|reduced|narrowed|widened)"
    r"\s+(?:\w+\s+){0,2}?from)\b"
    r"|\bbefore(?=\s*(?:[.;,:)]|$))",
    re.IGNORECASE)
# `cut_quote_problem` (v13 on): a quotation ending on one of these words leaves its phrase open.
_OPEN_END = frozenset({"a", "an", "the", "of", "to", "in", "into", "for", "by", "with", "on", "at", "from", "as", "than",
                       "and", "or", "but", "nor", "that", "which", "its", "their", "is", "are", "was", "were", "be", "been",
                       "will", "would", "shall", "should", "can", "could", "may", "might", "has", "have", "had"})
# From v20 (`DIFF_OPEN_END_FROM`): a quotation right after one of these names the words it quotes ("dropped the words "the extent
# and timing of" from …", 2578), so its open end isn't a cut phrase. Measured on dev's 322 stored v11 to v19 bullets with
# citations: v20's check fails 3 more (2544, 3394 and 3449, each a quotation stopping mid-phrase before the bullet's words).
_NAMES_WORDS = re.compile(r"\b(?:words?|phrase|phrasing|wording|expression)\s*$", re.IGNORECASE)
# From v15 (`DIFF_FP_FIX_FROM`): a quotation opening its sentence may stop right before one of these, its sentence's verb.
_VERB_NEXT = frozenset({"is", "are", "was", "were", "has", "have", "had", "will", "would", "shall", "should", "can", "could",
                        "may", "might", "must", "do", "does", "did"})
_STARTS_SENTENCE = re.compile("(?:^|[.!?][\"'”’)\\]]*\\s)\\s*$")


def _outside_quotes(text: str) -> list[str] | None:
    """A bullet's own words: its text outside quotation marks, in `match_form`, piece by piece; None if its marks don't pair
    (`quote_problem` has already failed it)."""
    t = match_form(text)
    return None if t.count('"') % 2 else _QUOTED.split(t)[::2]


def same_problem(text: str, *, again: bool = False) -> str | None:
    """Why a bullet's own words say that something stayed the same (see `DIFF_WHOLE_FROM`), or None. With `again` (v14 on,
    `DIFF_SIDES_FROM`), "again" and "as before" count too."""
    patterns = (_SAME, _AGAIN) if again else (_SAME,)
    for piece in _outside_quotes(text) or []:
        for pattern in patterns:
            if m := pattern.search(piece):
                return (f"a claim that something stayed the same, which no quote from a changed sentence can show: "
                        f'"{m.group(0)}"')
    return None


def previous_problem(text: str, *, whole_text: bool = False) -> str | None:
    """Why a bullet's own words describe the previous document, for a bullet with no quote from it (see `DIFF_SIDES_FROM`),
    or None. With `whole_text` (v15 on, `DIFF_FP_FIX_FROM`) the own words are searched with a quotation mark where each
    quotation was, so "before" ahead of a quotation isn't clause-final; v14 searched each piece on its own."""
    pieces = _outside_quotes(text) or []
    for hay in (['"'.join(pieces)] if whole_text else pieces):
        if m := _ABOUT_PREVIOUS.search(hay):
            return ("a claim about the previous document with no quote from it (quote the previous document, or leave "
                    f'the claim out): "{m.group(0)}"')
    return None


def cut_quote_problem(text: str, passages: list[str], *, subject_ok: bool = False, open_end: bool = False) -> str | None:
    """Why a bullet ends a quotation in the middle of a phrase (see `DIFF_WHOLE_FROM`), or None. Only a quotation the bullet
    doesn't go on from: after its closing mark comes punctuation or the bullet's end, not a word. It is found in the cited
    passages (each a whole changed sentence) as `quote_problem` finds it, case aside; it fails only when every place it is
    found is cut: the passage goes on with a word, and the quotation either starts where the passage's sentence starts or
    ends on a word such as "the" or "to" (`_OPEN_END`). With `subject_ok` (v15 on, `DIFF_FP_FIX_FROM`), a quotation that
    starts its sentence and stops right before the sentence's verb (`_VERB_NEXT`) isn't cut. With `open_end` (v20 on,
    `DIFF_OPEN_END_FROM`), a quotation the bullet goes on from also fails when it ends on such a word and the passage goes
    on with a word wherever it is found."""
    t = match_form(text)
    if t.count('"') % 2:
        return None
    hays = [match_form(p) for p in passages]
    for m in re.finditer(r'"([^"]*)"', t):
        goes_on = bool(_WORD.match(t[m.end():].lstrip()[:1] or " "))
        if goes_on and not open_end:
            continue                                       # the bullet goes on from it in its own words
        q = m.group(1).strip().rstrip(_INNER_END).strip()
        if not _WORD.search(q):
            continue
        last = _WORD.findall(q)[-1].casefold()
        if goes_on and (last not in _OPEN_END or _NAMES_WORDS.search(t[:m.start()])):
            continue                                       # v20: only on an open end, and not where it names words it quotes
        head = r"(?<!\w)" if re.match(r"\w", q) else ""
        tail = r"(?!\w)" if re.search(r"\w$", q) else ""
        cut = []
        for h in hays:
            same_length = len(h.lower()) == len(h) and len(q.lower()) == len(q)
            hay, needle = (h.lower(), q.lower()) if same_length else (h, q)
            for x in re.finditer(head + re.escape(needle) + tail, hay):
                rest = h[x.end():].lstrip()
                starts = bool(_STARTS_SENTENCE.search(h[:x.start()]))
                if starts and subject_ok and (nxt := _WORD.match(rest)) and nxt.group(0).casefold() in _VERB_NEXT:
                    starts = False                         # the quoted words are the sentence's whole subject
                cut.append(bool(_WORD.match(rest[:1] or " ")) and (starts or last in _OPEN_END))
        if cut and all(cut):
            shown = q if len(q) <= 80 else "…" + q[-79:]
            if goes_on:
                return (f'a quotation that ends on "{last}", in the middle of a phrase, and goes on in the bullet\'s own words: '
                        f'"{shown}" (quote the phrase to its end, or say it in your own words)')
            if last in _OPEN_END:
                return f'a quotation that ends on "{last}", in the middle of a phrase: "{shown}"'
            return f'a quotation cut in the middle of a sentence it starts: "{shown}" stops before its end'
    return None


# ---------------------------------------------------------------------------------------------- v16's checks (`DIFF_FIT_FROM`)
# `fit_problem`: the bullet's own word right before a quotation's opening mark (nothing but spaces between) and the quotation's
# first words, as closed word classes, case aside, in `match_form`. A pairing that English can't hold fails:
# - the same word on both sides of the mark ("stated at "at least $70 billion per month"");
# - a preposition, then a verb in its base form ("…holding the target range at "maintain the target range …"", round 14's
#   2578); a verb that is also a noun counts only when a determiner follows it ("at "lower the target range"");
# - a preposition, then a finite auxiliary or another preposition ("of "will be"", "at "in light of …"");
# - a verb that needs a noun as its object, then a preposition or an auxiliary ("citing "in light of the shift in the balance
#   of risks"", 2578), and "cite" or "describe", then "that" or the like;
# - an article or a possessive, then a determiner, a preposition, an auxiliary or a base-form verb ("the "in light of …"");
# - "has", "have", "had", "having" or "as", then a past form that isn't a participle ("having "rose"");
# - an auxiliary, then a finite auxiliary ("the Committee being "is continuing" that policy", round 13's 2561), or a modal,
#   then a past form ("will "remained"").
# And after the closing mark: the quotation's last word repeated as the bullet's next word.
# "from", "to" and "between" aren't prepositions here: "changed from "continue with the neutral stance" to "change the stance
# …"" names the words it quotes, and the judges passed it (2581, 2583). Neither is "than" before a verb ("rather than "raise
# the target range"" is English).
_FIT_PREP = frozenset({"about", "above", "across", "against", "along", "amid", "amidst", "among", "around", "at",
                       "behind", "below", "beneath", "beside", "besides", "beyond", "by", "despite", "during", "for",
                       "in", "inside", "into", "near", "of", "off", "on", "onto", "outside", "over", "past", "per",
                       "through", "throughout", "toward", "towards", "under", "underneath", "upon", "versus", "via",
                       "with", "within", "without"})
_FIT_CLAUSE_PREP = frozenset({"as", "after", "before", "since", "until", "like", "unlike", "except"})   # may also open a clause
_FIT_VERB = frozenset({"achieve", "adjust", "anticipate", "assess", "begin", "conclude", "consider", "continue",
                       "decide", "ensure", "expect", "keep", "maintain", "mitigate", "raise", "reduce", "reinvest",
                       "remain", "retain", "revive", "seek", "sustain", "undertake", "withdraw"})
_FIT_VERB_DET = frozenset({"lower", "cut", "hold", "increase", "support", "return", "extend", "take", "slow", "end",
                           "leave"})   # nouns too
_FIT_FINITE = frozenset({"is", "are", "was", "were", "has", "have", "had", "will", "would", "shall", "should", "can",
                         "could", "may", "might", "must", "do", "does", "did"})
_FIT_MODAL = frozenset({"will", "would", "shall", "should", "can", "could", "may", "might", "must"})
_FIT_AUX = _FIT_FINITE | {"be", "been", "being"}
_FIT_DET = frozenset({"the", "a", "an", "its", "their", "our"})   # not "this", "her": pronouns too ("asked her "to …"")
_FIT_QPREP = frozenset({"in", "of", "for", "on", "with", "by", "at", "from", "into", "onto", "upon", "to"})
# Verbs that need a noun as their object ("cited" too: "…the risks cited "in the statement"" is unlikely in a bullet); not
# "list", "reference" or "mention", which are nouns as often ("a reference "to the shift"").
_FIT_OBJECT = frozenset({"cite", "cites", "cited", "citing", "describe", "describes", "describing", "invoke", "invokes",
                         "invoked", "invoking", "attributing", "listing", "mentioning", "highlighting", "flagging",
                         "referencing"})
_FIT_OBJECT_THAT = frozenset({"cite", "cites", "cited", "citing", "describe", "describes", "describing"})   # not "that" either
_FIT_PAST = frozenset({"rose", "fell", "grew", "began", "took", "went", "came", "gave", "saw", "became", "ran", "wrote",
                       "chose", "drew", "shrank", "sank", "spoke", "froze", "broke"})
_FIT_THAT = frozenset({"that", "which", "because", "although", "though", "whereas", "while", "if", "whether"})
_LAST_OWN_WORD = re.compile(r"([^\W_][\w'’-]*)\s*$")
_FIRST_OWN_WORD = re.compile(r"\s*([^\W_][\w'’-]*)")


# From v28 (`DIFF_MUST_FROM`): a predicate (a past participle or one of these words, then a preposition) can't follow a
# preposition: "…holding the policy repo rate at "unchanged at 5.25 per cent"" (mpc-2026-10-07's 3710, round 24).
_FIT_PREDICATE = frozenset({"steady", "stable", "intact", "constant", "benign", "unaltered"})


def _misfit(left: str, q1: str, q2: str, predicate: bool = False) -> bool:
    """Whether the bullet's word `left`, right before a quotation opening with `q1` `q2` (all case-folded), can't fit.
    `predicate` (from v28): a predicate after a preposition doesn't either."""
    prep = left in _FIT_PREP or left in _FIT_CLAUSE_PREP
    if predicate and left in _FIT_PREP and q2 in _FIT_QPREP and (q1 in _FIT_PREDICATE or (q1.endswith("ed") and len(q1) > 4)):
        return True
    return (left == q1
            or (prep and (q1 in _FIT_VERB or (q1 in _FIT_VERB_DET and q2 in _FIT_DET)))
            or (left in _FIT_PREP and (q1 in _FIT_FINITE or q1 in _FIT_QPREP))
            or (left in _FIT_OBJECT and (q1 in _FIT_QPREP or q1 in _FIT_AUX))
            or (left in _FIT_OBJECT_THAT and q1 in _FIT_THAT)
            or (left in _FIT_DET and (q1 in _FIT_DET or q1 in _FIT_QPREP or q1 in _FIT_AUX or q1 in _FIT_VERB))
            or (left in {"has", "have", "had", "having", "as"} and q1 in _FIT_PAST)
            or (left in _FIT_AUX and q1 in _FIT_FINITE)
            or (left in _FIT_MODAL and (q1 in _FIT_PAST or q1.endswith("ed"))))


def fit_problem(text: str, *, predicate: bool = False) -> str | None:
    """Why a bullet sets a quotation where its grammar can't fit (see above), or None. `predicate`: from v28 (`_misfit`)."""
    t = match_form(text)
    if t.count('"') % 2:
        return None                                       # `quote_problem` has already failed it
    for m in re.finditer(r'"([^"]*)"', t):
        q = m.group(1).strip()
        qw = [w.casefold() for w in _WORD.findall(q)]
        if not qw:
            continue
        shown = q if len(q) <= 60 else q[:59] + "…"
        if (left := _LAST_OWN_WORD.search(t[:m.start()])) and _misfit(left.group(1).casefold(), qw[0],
                                                                      qw[1] if len(qw) > 1 else "", predicate):
            return (f'a quotation that doesn\'t fit the grammar of its sentence: {left.group(1)} "{shown}" (quote words that '
                    "fit where they stand, or word the sentence differently)")
        if (right := _FIRST_OWN_WORD.match(t[m.end():])) and right.group(1).casefold() == qw[-1]:
            return (f'a quotation that doesn\'t fit the grammar of its sentence: "{shown}" {right.group(1)} (quote words that '
                    "fit where they stand, or word the sentence differently)")
    return None


# `runs_on_problem`: a quotation found in a cited passage, after whose closing mark the bullet's own words (up to the next
# full stop, semicolon, colon, question or exclamation mark, quotation or bracket) start with RUNS_ON_MIN_WORDS or more of
# the words that follow it in that passage, at least RUNS_ON_PLAIN_WORDS of them not capitalised: the quotation closes
# partway through a clause and the rest of the clause is copied without quotation marks. Three words ("…decided to "raise"
# the target range"), which the judges passed (2546, 2579), don't count.
RUNS_ON_MIN_WORDS = 4
RUNS_ON_PLAIN_WORDS = 3
_CLAUSE_STOP = re.compile(r'[.;:!?"(\[]')


def runs_on_problem(text: str, passages: list[str]) -> str | None:
    """Why a bullet's own words run on from a quotation with the statement's next words (see above), or None."""
    t = match_form(text)
    if t.count('"') % 2:
        return None
    hays = [match_form(p) for p in passages]
    for m in re.finditer(r'"([^"]*)"', t):
        q = m.group(1).strip().rstrip(_INNER_END).strip()
        if not _WORD.search(q):
            continue
        after = t[m.end():]
        stop = _CLAUSE_STOP.search(after)
        own = _WORD.findall(after[:stop.start()] if stop else after)
        if len(own) < RUNS_ON_MIN_WORDS:
            continue
        head = r"(?<!\w)" if re.match(r"\w", q) else ""
        tail = r"(?!\w)" if re.search(r"\w$", q) else ""
        pattern = re.compile(head + re.escape(q) + tail, re.IGNORECASE)
        for h in hays:
            for x in pattern.finditer(h):
                nxt = _WORD.findall(h[x.end():])
                k = 0
                while k < min(len(nxt), len(own)) and nxt[k].casefold() == own[k].casefold():
                    k += 1
                run = own[:k]
                if k >= RUNS_ON_MIN_WORDS and sum(not w[0].isupper() for w in run) >= RUNS_ON_PLAIN_WORDS:
                    shown = q if len(q) <= 40 else "…" + q[-39:]
                    return (f'the statement\'s next words after a quotation, outside quotation marks: "{shown}" is followed '
                            f'by "{" ".join(run)}" in the statement (put them inside the quotation, or say it in your own '
                            "words)")
    return None


# `neighbour_problem`: a word of the bullet's own (outside quotation marks) whose stem is in none of its cited passages but is
# in a sentence next to one of them (the sentence before or after it in the same document, from the redline) that the bullet
# doesn't cite, and in at most NEIGHBOUR_MAX_SENTENCES sentences of that document: the claim rests on that sentence. Stems:
# the first six letters, or the whole of a shorter word, of words of four letters or more ("exception" and "except").
# Words about the documents and their changes, and the topics the prompt names, aren't claims (`_NEIGHBOUR_OWN`), and a word
# common in the document (in more sentences) isn't one sentence's. The reason says to quote the sentence when it changed (a
# quote may come only from a changed sentence), or else to leave the claim out.
NEIGHBOUR_MAX_SENTENCES = 2
_NEIGHBOUR_OWN = frozenset({"the", "and", "but", "nor", "that", "this", "these", "those", "which", "who", "whom",
                            "whose", "what", "when", "where", "while", "with", "from", "into", "onto", "than", "then",
                            "there", "here", "also", "both", "each", "such", "some", "many", "much", "more", "most",
                            "less", "least", "other", "over", "under", "about", "after", "before", "since", "until",
                            "against", "between", "among", "within", "without", "upon", "have", "has", "had", "having",
                            "will", "would", "shall", "should", "could", "might", "must", "been", "being", "were",
                            "does", "did", "they", "them", "their", "its", "it's", "not", "only", "just", "very",
                            "statement", "resolution", "sentence", "document", "wording", "words", "phrase", "passage",
                            "version", "text", "says", "said", "saying", "adds", "added", "adding", "notes", "noted",
                            "noting", "states", "stated", "reads", "change", "changed", "changing", "revised",
                            "revision", "moved", "moves", "shifted", "dropped", "removed", "deleted", "replaced",
                            "replacing", "inserted", "describes", "described", "describing", "description",
                            "assessment", "view", "views", "language", "reference", "refers", "referring", "mention",
                            "mentions", "mentioned", "previous", "previously", "earlier", "later", "new", "newly",
                            "now", "policy", "policies", "decision", "decisions", "stance", "guidance", "projection",
                            "projected", "outlook", "rationale", "hold", "holds", "holding", "held", "lowered", "lower",
                            "raised", "raise", "increase", "increased", "decrease", "decreased", "reduced", "reduce",
                            "eased", "tightened", "rate", "rates", "range", "ranges", "target", "members", "member",
                            "committee", "economy", "economic", "inflation", "labour", "labor", "market", "future",
                            "balance", "sheet", "asset", "assets", "purchase", "purchases", "vote", "votes", "voted",
                            "voting", "drop", "drops"})


def _stem_match(a: str, b: str) -> bool:
    n = min(6, len(a), len(b))
    return len(a) >= 4 and len(b) >= 4 and a[:n] == b[:n]


def _own_word(w: str) -> bool:
    return w in _NEIGHBOUR_OWN or any(_stem_match(w, s) for s in _NEIGHBOUR_OWN if len(s) >= 5)


def neighbour_problem(text: str, cited: list[tuple[int, int, int]], texts: dict[int, str],
                      sentences: dict[int, list[tuple[int, int]]],
                      changed: dict[int, list[tuple[int, int]]], *, refers_back: bool = False) -> str | None:
    """Why a bullet's own words rest on a sentence next to its quotes that it doesn't quote (see above), or None. `cited`:
    the bullet's citations as (document id, start, end); `texts`: each document's text; `sentences` and `changed`: each
    document's sentences and changed sentences (spans into its text). With `refers_back` (v17 on, `DIFF_VERIFY_FROM`), the
    sentence before a cited one that opens by referring back (`_REFERS_BACK`: "Accordingly, they …") counts however common
    its words are in the document: the cited sentence leans on it."""
    t = match_form(text)
    if t.count('"') % 2:
        return None
    own = " ".join(_QUOTED.split(t)[::2])

    def words_of(d: int, s: int, e: int) -> set[str]:
        return {w.casefold() for w in _WORD.findall(match_form(texts[d][s:e]))}

    quoted = {w for d, s, e in cited for w in words_of(d, s, e)}
    spans_cited = set(cited)
    beside: list[tuple[int, int, int]] = []
    leaned_on: set[tuple[int, int, int]] = set()   # the sentence a cited one refers back to (`refers_back`)
    for d, s, e in cited:
        spans = sentences.get(d, [])
        if d not in texts or (s, e) not in spans:
            continue
        i = spans.index((s, e))
        for j in (i - 1, i + 1):
            if 0 <= j < len(spans) and (d, *spans[j]) not in spans_cited and (d, *spans[j]) not in beside:
                beside.append((d, *spans[j]))
        if refers_back and i > 0 and (d, *spans[i - 1]) not in spans_cited and _REFERS_BACK.match(texts[d][s:e]):
            leaned_on.add((d, *spans[i - 1]))
    if not beside:
        return None
    for raw in _WORD.findall(own):
        w = raw.casefold()
        if len(w) < 4 or w.isdigit() or _own_word(w) or any(_stem_match(w, x) for x in quoted):
            continue
        for d, s, e in beside:
            if not any(_stem_match(w, x) for x in words_of(d, s, e)):
                continue
            leaned = (d, s, e) in leaned_on
            if not leaned and sum(any(_stem_match(w, x) for x in words_of(d, a, b))
                                  for a, b in sentences[d]) > NEIGHBOUR_MAX_SENTENCES:
                continue
            sentence = " ".join(texts[d][s:e].split())
            shown = sentence if len(sentence) <= 80 else sentence[:79] + "…"
            fix = ("quote that sentence too, or leave the claim out" if (s, e) in changed.get(d, [])
                   else "that sentence didn't change, so it can't be quoted: leave the claim out")
            if leaned:
                return (f'a claim resting on the sentence a quoted one refers back to: "{raw}" is from "{shown}", not from the '
                        f"quoted sentence ({fix})")
            return f'a claim resting on a sentence next to its quotes that it doesn\'t quote: "{raw}" is from "{shown}" ({fix})'
    return None


# ---------------------------------------------------------------------------------------------- v17's checks (`DIFF_VERIFY_FROM`)
# `_REFERS_BACK`: a sentence that opens by referring back to the one before it: "Accordingly, they …", "These …", "Taking
# into account all these factors, …" (an RBI paragraph number may come first).
_REFERS_BACK = re.compile(
    r"^\W*(?:\d{1,2}\.\s*)?(?:"
    r"(?:(?:accordingly|hence|thus|therefore|consequently|however|moreover|further(?:more)?|in\s+addition|as\s+a\s+result"
    r"|at\s+the\s+same\s+time|nonetheless|nevertheless|overall|on\s+balance)\s*,?\s+)?"
    r"(?:they|these|this|those|it|its|their|such|both|the\s+latter|the\s+former)\b"
    r"|(?:accordingly|hence|thus|therefore|consequently)\b"
    r"|(?:in\s+view\s+of|in\s+light\s+of|against|given|taking\s+into\s+(?:account|consideration))\s+(?:all\s+)?"
    r"(?:this|these|those|the\s+above)\b)",
    re.IGNORECASE)

RUN_MIN_WORDS = 5                     # the judge's pack counts stretches from five words (ft.eval.diff_quotes.MIN_WORDS)
_RUN_WORD = re.compile(r"\w+(?:[-'/.,%]\w+)*")    # ft.eval.diff_quotes' words (a test keeps the two the same)


def _run_words(text: str) -> tuple[str, list[tuple[str, int, int, bool]]]:
    """`text` in `match_form`, and each of its words as ft.eval.diff_quotes reads them: (lower-cased, start, end, inside
    double quotation marks)."""
    t = match_form(text)
    marks = [m.start() for m in re.finditer('"', t)]
    return t, [(m.group(0).lower(), m.start(), m.end(), sum(1 for at in marks if at < m.start()) % 2 == 1)
               for m in _RUN_WORD.finditer(t)]


def _word_line(text: str) -> str:
    return " " + " ".join(w.lower() for w in _RUN_WORD.findall(match_form(text))) + " "


def long_runs(text: str, documents: list[str], min_words: int = RUN_MIN_WORDS) -> list[tuple[int, int]]:
    """The runs of `min_words` or more words of `text` outside quotation marks that are words of one of `documents` in the
    same order, as (start, end) in `match_form(text)`: longest first from the left, never overlapping (the pack's measure,
    `ft.eval.diff_quotes.unquoted_copies`, read against whole documents)."""
    _, words = _run_words(text)
    hays = [_word_line(d) for d in documents]

    def copied(i: int, j: int) -> bool:
        run = " " + " ".join(w[0] for w in words[i:j]) + " "
        return any(run in h for h in hays)

    found, i = [], 0
    while i < len(words):
        if words[i][3]:
            i += 1
            continue
        j = i
        while j < len(words) and not words[j][3] and copied(i, j + 1):
            j += 1
        if j - i >= min_words:
            found.append((words[i][1], words[j - 1][2]))
            i = j
        else:
            i += 1
    return found


def long_run_problem(text: str, documents: list[str]) -> str | None:
    """Why a bullet has a run of a statement's words outside quotation marks (see `DIFF_VERIFY_FROM`), or None."""
    t = match_form(text)
    if t.count('"') % 2:
        return None                                       # `quote_problem` has already failed it
    for s, e in long_runs(text, documents):
        shown = t[s:e] if e - s <= 80 else t[s:s + 79] + "…"
        return (f'a run of a statement\'s words outside quotation marks: "{shown}" (put them inside the quotation, names '
                "and figure labels too, or say it in your own words)")
    return None


def _in_passages(words: str, passages: list[str]) -> bool:
    """Whether `words` (in `match_form`) are a stretch of one of `passages`, case aside, from a word's start to a word's end."""
    q = words.strip()
    if not _WORD.search(q):
        return False
    head = r"(?<!\w)" if re.match(r"\w", q) else ""
    tail = r"(?!\w)" if re.search(r"\w$", q) else ""
    pattern = re.compile(head + re.escape(q.casefold()) + tail)
    return any(pattern.search(match_form(p).casefold()) for p in passages)


def settle_runs(text: str, passages: list[str], documents: list[str]) -> tuple[str, int]:
    """A bullet's text with each run of a statement's words outside quotation marks (`long_runs`) taken into quotation marks
    where it can be, with no word added or removed, and how many were: a run that touches a quotation (nothing but spaces
    between) joins it when the two are one stretch of a cited passage; any other run gets its own marks when it is a stretch
    of a cited passage. A run neither fits stays as it is, and `long_run_problem` fails it. The checks then read the result."""
    n = 0
    for _ in range(2 * DIFF_MAX_QUOTES + 4):
        t = match_form(text)
        if t.count('"') % 2:
            break
        done = False
        for s, e in long_runs(text, documents):
            pre = t[:s].rstrip(" ")
            gap = len(pre)
            if pre.endswith('"') and pre.count('"') % 2 == 0:        # a closing mark right before the run
                opening = pre.rfind('"', 0, gap - 1)
                if _in_passages(t[opening + 1:gap - 1] + t[gap:e], passages):
                    text = text[:gap - 1] + text[gap:e] + text[gap - 1] + text[e:]
                    done = True
            if not done:
                rest = t[e:]
                at = e + len(rest) - len(rest.lstrip(" "))
                if t[at:at + 1] == '"' and t[:at].count('"') % 2 == 0:  # an opening mark right after the run
                    closing = t.find('"', at + 1)
                    if closing > at and _in_passages(t[s:at] + t[at + 1:closing], passages):
                        text = text[:s] + text[at] + text[s:at] + text[at + 1:]
                        done = True
            if not done and _in_passages(t[s:e], passages):
                text = text[:s] + '"' + text[s:e] + '"' + text[e:]
                done = True
            if done:
                n += 1
                break
        if not done:
            break
    return text, n


# `added_problem`: a bullet whose own words say something was added ("adds", "a new sentence", "now says" …) while a stretch it
# quotes shares a distinctive pair of words with one of the previous document's changed sentences that it doesn't quote: the
# previous statement already said it, so it was reworded (mpc-2023-12-08's 3375: "actively disinflationary"). A pair is two
# words of four letters or more, letters only and not function words, side by side, found in at most ADDED_MAX_SENTENCES
# sentences of the previous document.
ADDED_MAX_SENTENCES = 2
_ADDS = re.compile(
    r"\b(?:adds?|added|adding|introduc(?:e|es|ed|ing)|inserts?|inserted|newly|for\s+the\s+first\s+time"
    r"|(?:a|an)\s+(?:new|additional|further)\s+(?:sentence|line|clause|phrase|passage|reference|mention|paragraph|point)"
    r"|now\s+(?:also\s+)?(?:says|states|notes|adds|includes|mentions|refers|describes|expects|sees|cites|warns|stresses))\b",
    re.IGNORECASE)
_FUNCTION = frozenset({"that", "this", "these", "those", "which", "will", "would", "shall", "should", "could", "might", "must",
                       "with", "from", "into", "onto", "than", "then", "there", "here", "also", "been", "being", "were",
                       "have", "has", "having", "their", "they", "them", "what", "when", "where", "while", "over", "under",
                       "about", "after", "before", "since", "until", "upon", "within", "without", "such", "some", "more",
                       "most", "much", "many", "other", "only", "each", "both", "further", "very",
                       "basis", "points", "point", "cent", "percent", "percentage", "bps"})   # units aren't a phrase


_GENERIC = frozenset({"federal", "funds", "repo", "monetary", "economic", "financial", "conditions", "risks"})


def _pairs(text: str) -> set[tuple[str, str]]:
    """`text`'s distinctive pairs of words (see above), lower case only: a capitalised word is a name or opens a sentence.
    A word of the documents' everyday vocabulary (the topics `_NEIGHBOUR_OWN` names, such as "target", "stance", "policy",
    "inflation", and `_GENERIC`) is never one: round 16's second dev run read "target range", "neutral stance" and "policy
    rate" as rewordings."""
    raw = _WORD.findall(match_form(text))
    words = [w.casefold() for w in raw]
    ok = [len(w) >= 4 and w.isalpha() and r[0].islower() and w not in _FUNCTION and w not in _GENERIC and not _own_word(w)
          for w, r in zip(words, raw, strict=True)]
    return {(words[i], words[i + 1]) for i in range(len(words) - 1) if ok[i] and ok[i + 1]}


def added_problem(text: str, cited: list[tuple[int, int, int]], texts: dict[int, str], previous_id: int,
                  changed: dict[int, list[tuple[int, int]]],
                  sentences: dict[int, list[tuple[int, int]]] | None) -> str | None:
    """Why a bullet says words were added that the previous document's changed sentences already had (see above), or None.
    `cited`: the bullet's citations as (document id, start, end); `previous_id`: document 1's id."""
    t = match_form(text)
    if t.count('"') % 2 or previous_id not in texts:
        return None
    own = " ".join(_QUOTED.split(t)[::2])
    if not _ADDS.search(own):
        return None
    quoted = set().union(*(_pairs(q) for q in _QUOTED.findall(t))) if _QUOTED.search(t) else set()
    if not quoted:
        return None
    old = texts[previous_id]
    every = (sentences or {}).get(previous_id) or changed.get(previous_id, [])
    cited_old = {(s, e) for d, s, e in cited if d == previous_id}
    for s, e in changed.get(previous_id, []):
        if (s, e) in cited_old:
            continue
        for pair in sorted(quoted & _pairs(old[s:e])):
            if sum(pair in _pairs(old[a:b]) for a, b in every) > ADDED_MAX_SENTENCES:
                continue
            sentence = " ".join(old[s:e].split())
            shown = sentence if len(sentence) <= 100 else sentence[:99] + "…"
            return (f'claims words were added that the previous statement already had: "{" ".join(pair)}" is in its '
                    f'"{shown}" (say it was reworded, and quote that sentence too)')
    return None


# `vote_for_problem`: own words about who voted for the action ("voting for", "voted in favour", "among those voting") with no
# cited sentence that lists the votes for it (fomc-2025-12-10's 3384: "…is no longer among those voting for the action",
# citing only the voting-against sentences).
_VOTE_FOR = re.compile(r"\bvot(?:e|es|ed|ing)\s+(?:for|in\s+favou?r)\b|\bamong\s+those\s+voting\b", re.IGNORECASE)
_VOTE_FOR_CITED = re.compile(r"\bvot(?:e|es|ed|ing)\s+(?:for|in\s+favou?r)\b", re.IGNORECASE)


def vote_for_problem(text: str, passages: list[str]) -> str | None:
    """Why a bullet's own words say who voted for the action without a cited sentence listing those votes, or None."""
    t = match_form(text)
    if t.count('"') % 2:
        return None
    own = " ".join(_QUOTED.split(t)[::2])
    if (m := _VOTE_FOR.search(own)) and not any(_VOTE_FOR_CITED.search(match_form(p)) for p in passages):
        return (f'a claim about who voted for the action that quotes no sentence listing those votes: "{m.group(0)}" (quote '
                "that sentence too, or leave the claim out)")
    return None


# ---------------------------------------------------------------------------------------------- the second model (v17 on)
def verify_request(req: Request, c: Checked, prompt: Prompt) -> Request:
    """The second model's request about a reply's bullets (`c`, all of which passed the checks above): the two documents as
    the generator had them (`req.sources`), then each bullet with only the sentences it cites, by document. The json route,
    `prompt`'s schema (`verify2.schema.json; verify1.schema.json before`); Haiku 5.5 at effort medium (`VERIFY_*`)."""
    old, new = req.sources[0], req.sources[1]
    lines: list[str] = []
    for i, b in enumerate(c.bullets, 1):
        lines += [f"Bullet {i}: {b.text}", "Its cited sentences:"]
        for x in b.citations:
            src, which = (old, "Document 1 (previous)") if x.document_id == old.document_id else (new, "Document 2 (new)")
            lines.append(f'- {which}: "{" ".join(src.text[x.start:x.end].split())}"')
        lines.append("")
    instruction = ("Document 1 is the previous statement and document 2 is the new one. Check these bullets about what "
                   "changed between them, each shown with the sentences it cites:\n\n" + "\n".join(lines)
                   + f"\nAnswer for each of the {len(c.bullets)} bullets, in order, as the schema's JSON.")
    return Request("statement_diff_summary", prompt, (old, new), instruction, model=VERIFY_MODEL,
                   schema=load_schema("statement_diff_summary", prompt.version), route="json", effort=VERIFY_EFFORT,
                   max_tokens=VERIFY_MAX_TOKENS)


def apply_verdicts(c: Checked, answer) -> None:
    """The second model's answer (`answer`: its JSON) on `c`'s bullets: a bullet it doesn't find supported, whose claims of
    change it doesn't find true, or that it doesn't find reads well (verify2 on), is dropped with its objection; one it gives no verdict for is dropped too. An answer
    that isn't the schema's fails the reply."""
    rows = answer.get("verdicts") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        c.problems.append("not checked by a second model: its answer isn't the schema's JSON")
        return
    by: dict[int, dict] = {}
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("bullet"), int) and not isinstance(r.get("bullet"), bool):
            by.setdefault(r["bullet"], r)
    for i, b in enumerate(c.bullets, 1):
        r = by.get(i)
        if r is None:
            b.problem = "the second model's check gave no answer for it"
        elif not (r.get("supported") is True and r.get("changes_true") is True
                  and r.get("reads_well", True) is True):        # verify1's answers have no reads_well
            why = " ".join(str(r.get("objection") or "").split())
            b.problem = "a second model's check found: " + ((why[:300] + "…") if len(why) > 300 else why or "no reason given")


# ---------------------------------------------------------------------------------------------- the fallback (v17 on)
# A target whose replies are all rejected gets bullets built by code from the redline's changed sentences, each quoted whole
# and labelled previous or new, with no word of a model's, at most five:
# - one change of each of the first FALLBACK_FIRST_KINDS kinds (`_kind`), in this order: the policy decision (the policy rate
#   or the target range with a verb of deciding, such as decided, raise, cut, keep, maintain or unchanged; never a vote, a
#   dissent, the reverse repo rate or a market rate traded against the repo rate), the stance or guidance, a dissent, the
#   inflation projections, the growth projections, inflation, the economy (growth, jobs, activity), the balance sheet, then
#   the corridor rates (reverse repo, SDF, MSF, Bank Rate); within a kind, a change whose new sentence is itself of that kind
#   first, then what the rejected reply's quotes pointed at, in its order, then by position. Then the slots left go to what
#   the rejected reply quoted, in its order, then by kind (votes, then the rest last), then by position. The bullets read in
#   kind order, the decision first (#445, round 17: in round 16's third dev run any sentence holding "repo rate" ranked with
#   the decision, so mpc-2019-02-07's fallback quoted two call-rate sentences and left out the stance change, the dissent and
#   the projections that v1 had; round 18: in round 17's run mpc-2020-02-06's left out its GDP projection, so growth
#   projections are their own kind);
# - up to FALLBACK_RUN_MAX consecutive added sentences (only spaces between them, within one paragraph), or removed ones, are
#   one change, quoted together (round 18: fomc-2023-03-22's three new banking sentences took three of its five bullets);
# - the committee's own vote ("the MPC voted unanimously to increase the policy repo rate …") is the decision, not a vote or a
#   dissent, and "policy rates" (other central banks') is never the decision; the decision is shown once (round 19:
#   mpc-2026-10-07's round 18 fallback led with "higher global policy rates" and left out its rate hike);
# - a change only in dates, a paragraph number or a connective is left out (`_trivial`): dates are month names, years, and day
#   numbers near a month name, so a rate, a projection or a count that changed is never a date;
# - a change shown twice in the redline (an RBI resolution says its decision in two paragraphs) is shown once, by its words,
#   and no sentence is quoted by two bullets;
# - two sentences the redline pairs as one change but that share few words (`_same_topic`: fewer than PAIR_MIN_SHARED of the
#   shorter side's content words, names aside) are shown as two, one the previous document had and one the new one has, never side by
#   side as if one replaced the other; and an added and a removed sentence sharing a distinctive pair of words (`_pairs`, in
#   at most ADDED_MAX_SENTENCES sentences of the previous document), or of the same kind (`_kind`) and topic (`_same_topic`;
#   round 17: mpc-2020-12-04's reworded decision and GDP sentences were shown as two bullets each), are shown as one change,
#   the removed sentence sharing the most content words, so a reworded sentence is never called added.
# From v23 (`DIFF_PACK_FROM`, `_pack`), a bullet carries up to FALLBACK_PER_BULLET changes of one kind, the second opening
# "Also, …", while bullets are scarce; the balance sheet comes before the projections (FALLBACK_PACKED_ORDER); and the
# decision's sentence that gives the rate comes first.
# The citations are the quoted sentences.
_MONTH_WORD = re.compile(r"^(?:january|february|march|april|may|june|july|august|september|october|november|december"
                         r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)$", re.IGNORECASE)
_YEAR_WORD = re.compile(r"^(?:19|20)\d\d(?:-\d\d)?$")
_DAY_WORD = re.compile(r"^\d{1,2}(?:st|nd|rd|th)?$", re.IGNORECASE)
DAY_NEAR_MONTH = 3                    # a day number this many words or fewer from a month name ("4 to 6 October", "May 2 and 4")
PAIR_MIN_SHARED = 0.3
# The fallback's kinds of change, in order (see above). Matched on both sides' words, case aside.
FALLBACK_KINDS = ("decision", "stance", "dissent", "projections", "growth projections", "inflation", "economy", "balance sheet",
                  "corridor", "votes", "other")
FALLBACK_FIRST_KINDS = 9              # decision to corridor: one of each before any second of a kind
FALLBACK_RUN_MAX = 3                  # consecutive added (or removed) sentences shown as one change, at most this many
FALLBACK_PER_BULLET = 2               # from v23 (`DIFF_PACK_FROM`): changes of one kind a bullet may carry
# From v23, the first pass's order and the bullets' (the balance sheet is policy: it comes before the projections and the
# economy's words): fomc-2025-10-29's decision to conclude the reduction of its securities holdings is a policy change v1 has.
FALLBACK_PACKED_ORDER = ("decision", "stance", "dissent", "balance sheet", "projections", "growth projections", "inflation",
                         "economy", "corridor", "votes", "other")
_PARA_NO = re.compile(r"\d{1,2}\.\s")    # an RBI paragraph number opening a sentence: a new paragraph, so no run across it
_K_RATE = re.compile(r"target range|federal funds rate|(?<!reverse )repo rate|policy rate\b", re.IGNORECASE)   # not "policy rates"
# A decision says so: "decided"/"decides", the committee's own vote, or an RBI resolution's list item opening with its verb
# ("decided to: reduce the policy repo rate …"). Round 19: "anticipates that ongoing increases in the target range will be
# appropriate" is guidance, not a second decision.
_K_DECIDE = re.compile(r"\bdecid\w*|(?:^|[.:;]\s+)[\"“]?(?:keep|reduce|increase|raise|lower|cut|maintain|retain|hike)\b",
                       re.IGNORECASE)
_K_MARKET = re.compile(r"\btraded\b|\bon an average\b|\bWACR\b|\bcall rate|\bmoney market|\bovernight|\bfinancial markets",
                       re.IGNORECASE)
_K_VOTE = re.compile(r"\bvot", re.IGNORECASE)
# The committee's own vote states its decision ("the MPC voted unanimously to increase the policy repo rate …", "All members of
# the MPC … unanimously voted for keeping …"; round 18: mpc-2026-10-07's fallback left out its rate hike), not a dissent.
_K_COMMITTEE_VOTE = re.compile(r"\b(?:MPC|Committee)\b(?:\s+\w+){0,2}\s+voted\b|\bunanimously\b|\ball members\b", re.IGNORECASE)
_K_DISSENT = re.compile(r"\bvot\w* against\b|\bdissent|\bpreferred\b|\bwere of the view\b|\bexpressed reservations\b"
                        r"|\bvoted to (?:keep|reduc|rais|increas|lower|cut|hike|maintain|retain|change|leave|hold)"
                        r"|\bvoted for (?:a|an)\b", re.IGNORECASE)
_K_STANCE = re.compile(r"stance|withdrawal of accommodation|appropriate|guided by|prepared to|expects|anticipates"
                       r"|will continue|future|patient|vigil|disinflationary", re.IGNORECASE)
# From v23: "will continue" is guidance only with a policy maker as its subject ("The Committee will continue to monitor …"; not
# "global economic uncertainty will continue to have some bearing on domestic economic activity", mpc-2026-10-07), "future"
# is a word of its own (not "The crude oil futures curve has become downward sloping", mpc-2021-02-05), and the
# RBI's forward guidance is guidance ("rate cuts are off the table in the near term and policy action ahead can only be a rate
# hike or a pause", mpc-2026-10-07, which v1 (58) has and the v22 fallback read as the economy, for "the outlook").
_K_STANCE_PACKED = re.compile(r"stance|withdrawal of accommodation|appropriate|guided by|prepared to|expects|anticipates"
                              r"|\b(?:Committee|MPC|Reserve Bank|Federal Reserve|it)\s+will continue|\bfuture\b|patient|vigil"
                              r"|disinflationary|\boff the table\b|\bpolicy action ahead\b"
                              r"|\brate (?:hike|cut|tightening|easing) cycle\b", re.IGNORECASE)
_K_PROJECTIONS = re.compile(r"projected|projection|path of|revised (?:up|down)", re.IGNORECASE)
_K_GROWTH = re.compile(r"\bgdp\b|\bgva\b|growth", re.IGNORECASE)
_K_INFLATION = re.compile(r"inflation|price pressures|prices", re.IGNORECASE)
_K_ECONOMY = re.compile(r"growth|gdp|employment|labor|labour|unemployment|activity|spending|investment|outlook|uncertain",
                        re.IGNORECASE)
_K_BALANCE = re.compile(r"securities|holdings|purchases|balance sheet|treasury|mortgage", re.IGNORECASE)
# From v23: the central bank's balance sheet, not "the balance sheets of banks and corporates" (mpc-2023-08-10).
_K_BALANCE_PACKED = re.compile(r"securities|holdings|purchases|balance sheet\b|treasury|mortgage", re.IGNORECASE)
_K_CORRIDOR = re.compile(r"reverse repo|standing deposit|\bSDF\b|marginal standing|\bMSF\b|bank rate", re.IGNORECASE)
_K_FIGURE_RATE = re.compile(r"\d(?:[\d./-]*\d)?\s*(?:per\s*cent|percent|bps\b|basis points?|percentage points?)", re.IGNORECASE)


def _kind(text: str, *, packed: bool = False) -> int:
    """The fallback's kind of a change (an index into FALLBACK_KINDS) from its two sides' words. `packed` (from v23,
    `DIFF_PACK_FROM`): a change naming the balance sheet's words is the balance sheet before it is the stance, so "will
    continue reducing its holdings" and "decided to conclude the reduction of its aggregate securities holdings" are one
    kind; and the stance reads `_K_STANCE_PACKED`."""
    if _K_MARKET.search(text):
        return FALLBACK_KINDS.index("other")              # the call rate against the repo rate is no decision
    committee = bool(_K_COMMITTEE_VOTE.search(text))
    if _K_DISSENT.search(text) and not committee:
        return FALLBACK_KINDS.index("dissent")
    if _K_RATE.search(text) and (committee or (_K_DECIDE.search(text) and not _K_VOTE.search(text))):
        return FALLBACK_KINDS.index("decision")
    if packed and _K_BALANCE_PACKED.search(text):
        return FALLBACK_KINDS.index("balance sheet")
    for name, pattern in (("stance", _K_STANCE_PACKED if packed else _K_STANCE), ("projections", _K_PROJECTIONS), ("inflation", _K_INFLATION),
                          ("economy", _K_ECONOMY), ("balance sheet", _K_BALANCE), ("corridor", _K_CORRIDOR), ("votes", _K_VOTE)):
        if pattern.search(text):
            if name == "projections" and _K_GROWTH.search(text) and not _K_INFLATION.search(text):
                return FALLBACK_KINDS.index("growth projections")     # a growth path beside the inflation path
            return FALLBACK_KINDS.index(name)
    return FALLBACK_KINDS.index("other")


def _undated(text: str) -> list[str]:
    """`text`'s words (ft.eval.diff_quotes', so "4.90" is one word), lower-cased, without its dates: month names, years, and a
    day number within DAY_NEAR_MONTH words of a month name."""
    words = [w.lower() for w in _RUN_WORD.findall(match_form(text))]
    out = []
    for i, w in enumerate(words):
        near = any(_MONTH_WORD.match(x) for x in words[max(0, i - DAY_NEAR_MONTH):i + DAY_NEAR_MONTH + 1])
        if _MONTH_WORD.match(w) or _YEAR_WORD.match(w) or (_DAY_WORD.match(w) and near):
            continue
        out.append(w)
    return out


def _dates_only(old_text: str, new_text: str) -> bool:
    return _undated(old_text) == _undated(new_text)


_CONNECTIVE = frozenset({"consequently", "accordingly", "also", "further", "furthermore", "however", "hence", "thus",
                         "moreover", "therefore", "meanwhile", "overall"})


def _gist(text: str) -> list[str]:
    """`text`'s words without dates, connectives ("Consequently, …") or an opening paragraph number ("8. …")."""
    words = _undated(text)
    if words and re.fullmatch(r"\d{1,2}", words[0]):
        words = words[1:]
    return [w for w in words if w not in _CONNECTIVE]


def _trivial(old_text: str, new_text: str) -> bool:
    """A change only in dates, a paragraph number or a connective: no bullet of the fallback's."""
    return _gist(old_text) == _gist(new_text)


def _content(text: str) -> set[str]:
    """A sentence's content words: four letters or more, letters only, not a function word, and not capitalised (names, and
    a sentence's first word, say little about what it is about)."""
    return {w for w in _WORD.findall(match_form(text))
            if len(w) >= 4 and w.isalpha() and w[0].islower() and w not in _FUNCTION}


def _same_topic(old_text: str, new_text: str) -> bool:
    a, b = _content(old_text), _content(new_text)
    return bool(a and b) and len(a & b) >= PAIR_MIN_SHARED * min(len(a), len(b))


def _side(text: str, spans: list[tuple[int, int]], *, bare: bool = False) -> tuple[str, list[tuple[int, int]]]:
    """One side of a change as one quotation: its sentences, joined as the document joins them when only spaces lie between.
    `bare` (from v25): a sentence opening a paragraph is quoted without its RBI paragraph number ("15. Two members …" as "Two
    members …"; its span starts after the number), and it starts a quotation of its own."""
    spans = sorted(spans)
    parts, last, out = [], None, []
    for s, e in spans:
        cut = False
        if bare and _opening(text, s) and (m := _PARA_NUMBER.match(text, s, e)) and m.end() < e:
            s, cut = m.end(), True
        if last is not None and not cut and not text[last:s].strip():
            parts[-1] += " " + " ".join(text[s:e].split())
        else:
            parts.append(" ".join(text[s:e].split()))
        last = e
        out.append((s, e))
    return '" and "'.join(parts), out


# ---- v25's counterparts (`DIFF_COUNTERPART_FROM`; `fallback_bullets`' `counterparts`)
_PARA_NUMBER = re.compile(r"\d{1,2}\.[ \t]+")      # an RBI paragraph number opening a paragraph ("15. Two members …")
_DECISIONS_LEAD = re.compile(r"\bdecided to:$", re.IGNORECASE)
# Words whose change alone changes no meaning (`_minor`), besides a word's other form ("decisions" for "decision").
_MINOR_WORDS = frozenset({"a", "an", "the", "this", "these", "that", "those", "is", "are", "was", "were", "has", "have", "had",
                          "its", "their", "it", "they", "also", "and", "of", "to", "in", "on", "for", "by", "with", "as",
                          "be", "been"})
MINOR_MAX_WORDS = 6                    # a change of at most this many words, all of them `_MINOR_WORDS` or another form
SUBJECT_STEM = 6                       # letters of a word that make its stem (`_subject`), as `_stem_match`
# Whether two sentences are on one subject (`_counterparts`), by their subject words (`_subject`), the share of the smaller
# one's that they have in common (`_shared`) and how many:
# - what each is about first in common (`_heads`, leaving out words in more than SUBJECT_COMMON of the sentences, such as
#   "inflation" in an RBI resolution), unless both open their paragraphs: mpc-2023-06-08's "Fuel inflation
#   remained elevated …" and "In the fuel group, inflation …"; not mpc-2021-06-04's "Firms engaged in manufacturing, services
#   and infrastructure … were optimistic about a pick-up in demand …" and "Urban demand has been dented by the second wave,
#   but … manufacturing and services sectors …";
# - in paragraphs that correspond (the redline aligns them, or their openings are paired): SUBJECT_STRONG of them, at least
#   SUBJECT_SHARED; or SUBJECT_SAME, at least SUBJECT_SHARED, of one kind (`_kind`);
# - elsewhere in one stretch (paragraphs not known to correspond), the same on the words' weights (rarer words weigh more:
#   the log of the two documents' sentences over those having the word), at SUBJECT_FAR_STRONG and SUBJECT_FAR;
# - all of a short sentence's subject words when it has fewer than SUBJECT_SHARED, SUBJECT_ALL of the share (mpc-2026-04-08's
#   "The growth outlook remains favourable." isn't "It is prudent to wait and watch … the evolving growth-inflation
#   outlook.");
# - never two sentences naming different financial years ("2021-22" and "2022-23": mpc-2022-02-10's two inflation
#   projections) or different economies (`_regions`: mpc-2019-02-07's "In most EMEs, bond yields have softened …" and "The
#   10-year yield in the US …"), even moved ones;
# - two paragraphs' openings of one kind: OPENING_SHARED words in common, and the same RBI paragraph number or what each is
#   about first in common (mpc-2026-10-07's paragraph 3, the global outlook, in both).
SUBJECT_SAME = 0.3
SUBJECT_STRONG = 0.5
SUBJECT_SHARED = 3
SUBJECT_ALL = 0.6
SUBJECT_FAR = 0.45
SUBJECT_FAR_STRONG = 0.55
SUBJECT_HEAD = 5
SUBJECT_COMMON = 0.2                  # a word in more than this share of the sentences is common (`_heads` leaves it out),
SUBJECT_COMMON_MIN = 6                # and in this many at least
OPENING_SHARED = 2
_FISCAL_YEAR = re.compile(r"\b(?:19|20)\d\d-\d\d\b")
_SUBJECT_STOP = frozenset({"remain", "contin", "likely", "expect", "recent", "though", "amidst", "around", "across",
                           "toward", "reflec", "owing", "along", "level", "period", "month", "year", "quarte", "week",
                           "still", "well", "alread", "ahead", "going", "forwar", "lookin", "taking", "accoun", "factor",
                           "consid", "noted", "note", "view", "mpc", "commit", "previo", "current", "curren", "durin",
                           "even", "however", "howeve", "overal", "becaus", "includ", "furthe", "togeth",
                           "first", "firstl", "second", "third", "thirdl", "fourth", "fifthl", "sixthl", "sevent",
                           "the", "in", "on", "as", "at", "it", "its", "of", "for", "with", "while", "accord",
                           "reserv", "bank", "rbi", "federa", "fomc", "open", "market", "q1", "q2", "q3", "q4", "dr",
                           "shri", "prof", "smt", "advanc", "emergi", "aes", "emes", "keepin", "four", "five", "three",
                           "seven", "eight", "nine"})


def _opening(text: str, s: int) -> bool:
    """Whether the sentence at `s` opens its paragraph: only spaces between it and the text's start or a line break."""
    before = text[:s].rstrip(" \t")
    return not before or before.endswith("\n")


@lru_cache(maxsize=4096)
def _subject_words(text: str) -> tuple[str, ...]:
    """A sentence's subject words in order (see `_subject`), each once."""
    t = " ".join(text.split())
    t = t[m.end():] if (m := _PARA_NUMBER.match(t)) else t
    first = t.split(" ", 1)[0]
    if not (len(first) > 1 and first.isupper()):    # an opening acronym ("CPI inflation …") stays a name
        t = t[:1].lower() + t[1:]
    out: list[str] = []
    for w in _WORD.findall(match_form(t).replace("-", " ")):
        if not w.isalpha() or _MONTH_WORD.match(w):
            continue
        if w[0].isupper():                             # a name ("Japanese economy", "GVA", "Dr. Goyal"): what it's about
            if len(w) < 2:
                continue
        elif len(w) < 4 or w in _FUNCTION:
            continue
        w = w.lower()
        stem = (w[:-1] if w.endswith("s") and len(w) > 4 else w)[:SUBJECT_STEM]
        if stem not in _SUBJECT_STOP and stem not in out:
            out.append(stem)
    return tuple(out)


def _subject(text: str) -> frozenset[str]:
    """A sentence's subject: its content words (`_content`; hyphenated ones split, and names too) cut to their first
    SUBJECT_STEM letters, without its paragraph number and with its first letter in lower case, so its opening word counts
    ("Fuel inflation …") and a word's other forms match ("increase", "increases"); words that say little about what a
    sentence is about (`_SUBJECT_STOP`) aside."""
    return frozenset(_subject_words(text))


def _heads(a: str, b: str, common: frozenset[str] | set[str] = frozenset()) -> bool:
    """Whether two sentences' first SUBJECT_HEAD subject words, leaving out the `common` ones, share one: what each is
    about first ("Fuel inflation …", "In the fuel group, inflation …")."""
    x, y = [w for w in _subject_words(a) if w not in common], [w for w in _subject_words(b) if w not in common]
    return bool(set(x[:SUBJECT_HEAD]) & set(y[:SUBJECT_HEAD]))


_REGION = re.compile(r"\b(?:US|U\.S\.|United States|UK|EMEs?|AEs?|emerging market|advanced economies|Japan|Japanese|China"
                     r"|Chinese|euro area|Euro area|Germany|German|Brazil|Brazilian|Russia|Russian|South Africa|India|Indian"
                     r"|domestic)\b")
_REGION_OF = {"U.S.": "US", "United States": "US", "EME": "EMEs", "emerging market": "EMEs", "AE": "AEs",
              "advanced economies": "AEs", "Japanese": "Japan", "Chinese": "China", "Euro area": "euro area",
              "German": "Germany", "Brazilian": "Brazil", "Russian": "Russia", "Indian": "India", "domestic": "India"}


def _regions(text: str) -> set[str]:
    """The economies a sentence names ("the US", "EMEs", "Japan", "domestic" as India)."""
    return {_REGION_OF.get(m, m) for m in _REGION.findall(text)}


def _shared(a: str, b: str, weight: dict[str, float] | None = None) -> tuple[float, int]:
    """The share of the smaller subject (`_subject`) that the other sentence's has (by `weight` when given: a word's weight,
    1 for a word it lacks), and how many words they share."""
    x, y = _subject(a), _subject(b)
    if not x or not y:
        return 0.0, 0

    def w(z: set[str]) -> float:
        return sum((weight or {}).get(t, 1.0) for t in z)
    return w(x & y) / min(w(x), w(y)), len(x & y)


def _likeness(a: str, b: str) -> float:
    """The redline's own likeness of two sentences (ft.engines.document.diff: difflib's ratio over their lower-cased words)."""
    x, y = [w.lower() for w in text_words(a)], [w.lower() for w in text_words(b)]
    return SequenceMatcher(None, x, y, autojunk=False).ratio() if x and y else 0.0


def _minor(old_text: str, new_text: str) -> bool:
    """A change of a word or two that changes no meaning ("These decisions are" to "This decision is"): at most
    MINOR_MAX_WORDS words differ, dates and connectives aside (`_gist`), and each is a `_MINOR_WORDS` word or another form of
    a word on the other side. A name, a figure or any other word is never minor."""
    a, b = _gist(old_text), _gist(new_text)
    if not a or not b:
        return False
    gone, came = [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            gone += a[i1:i2]
            came += b[j1:j2]
    if not gone and not came or len(gone) + len(came) > MINOR_MAX_WORDS:
        return False

    def ok(w: str, other: list[str]) -> bool:
        return w in _MINOR_WORDS or any(_stem_match(w, x) for x in other)
    return all(ok(w, came) for w in gone) and all(ok(w, gone) for w in came)


def _list_item(text: str, s: int) -> str | None:
    """'decisions' for an item of an RBI resolution's list of decisions (a lower-case opening after a line ending "decided
    to:", with no paragraph closing in between), 'list' for another list's item, None for a sentence."""
    if not text[s:s + 1].islower() or not _opening(text, s):
        return None
    colon = text.rfind(":", 0, s)
    if colon < 0 or re.search(r"\.[ \t]*\n", text[colon:s]) or not text[colon + 1:s].startswith(("\n", " \n")):
        return None
    line = text[text.rfind("\n", 0, colon) + 1:colon + 1]
    return "decisions" if _DECISIONS_LEAD.search(line) else "list"


@dataclass(eq=False)
class _One:
    """One sentence of a change (`_counterparts`)."""
    span: tuple[int, int]
    para: int                          # its ¶ (block) number in its document
    gap: int                           # the stretch between two sentences the redline keeps or pairs that it lies in
    opening: bool                      # it opens its paragraph
    text: str


def _counterparts(segments: dict, old: Doc, new: Doc, kind_of, *, kinds: bool = False) -> list[list]:
    """v25's changes from the redline, as [old spans, new spans] (see DIFF_COUNTERPART_FROM).
    - The redline's own changed pairs stand. A pair whose sides aren't on one subject (`_same_topic`) is two changes, each
      alone; a pair whose side ends with a colon (a list's lead-in) joins the next pair (its first item).
    - Its unpaired removed and added sentences pair only with their counterparts, best first (`_shared`'s share): the same
      sentence, moved (at least PAIR_MIN alike, anywhere); else a removed and an added sentence in the same stretch between
      two sentences the redline keeps or pairs, or in paragraphs the redline aligns (never in paragraphs it aligns with
      others), an opening only with an opening, on one subject (SUBJECT_SAME and the constants after it). Openings pair
      first, and paragraphs whose openings pair correspond, like the ones the redline aligns.
    - A sentence next to one side of a change, in its paragraph, that the other side's sentences speak to (SUBJECT_SAME of
      its subject words, two of them that the side's own sentences lack) more than any other sentence of the other
      document's corresponding paragraphs, joins that side: a sentence split in two, or two merged.
    - The rest stand alone, consecutive ones in a paragraph as one change (up to FALLBACK_RUN_MAX)."""
    align_old: dict[int, set[int]] = {}
    align_new: dict[int, set[int]] = {}
    changes: list[list[list[_One]]] = []               # [old sentences, new sentences]
    removed: list[_One] = []
    added: list[_One] = []
    gap = 0

    def ones(p: dict, side: str, text: str) -> list[_One]:
        paras = p.get(side + "_para", [])
        return [_One(tuple(x), paras[i] if i < len(paras) else 0, gap, _opening(text, x[0]), text[x[0]:x[1]])
                for i, x in enumerate(p.get(side, []))]

    lead: list | None = None
    every: list[str] = []
    for p in segments.get("pairs", []):
        kind = p.get("kind")
        every += [old.text[s:e] for s, e in p.get("old", [])] + [new.text[s:e] for s, e in p.get("new", [])]
        if kind in ("same", "changed"):
            for a in p.get("old_para", []):
                align_old.setdefault(a, set()).update(p.get("new_para", []))
            for b in p.get("new_para", []):
                align_new.setdefault(b, set()).update(p.get("old_para", []))
            gap += 1
        if kind == "changed":
            o, n = ones(p, "old", old.text), ones(p, "new", new.text)
            if lead is not None:                       # a list's lead-in and its first item: one change
                lead[0] += o
                lead[1] += n
                lead = None
                continue
            change = [o, n]
            changes.append(change)
            if o[-1].text.rstrip().endswith(":") or n[-1].text.rstrip().endswith(":"):
                lead = change
            continue
        lead = None
        if kind == "removed":
            removed += ones(p, "old", old.text)
        elif kind == "added":
            added += ones(p, "new", new.text)
    for change in list(changes):
        ot, nt = " ".join(x.text for x in change[0]), " ".join(x.text for x in change[1])
        if not _same_topic(ot, nt) and not _trivial(ot, nt):
            changes.remove(change)                     # paired by position, not by what they say: two, alone
            removed += change[0]
            added += change[1]
            for x in change[0] + change[1]:
                gap += 1
                x.gap = -gap                           # each its own stretch: no other pairing but a moved sentence
    df: Counter = Counter(w for t in every for w in _subject(t))
    weight = {w: math.log(max(len(every), 2) / n) for w, n in df.items()}
    common = {w for w, n in df.items()                # "inflation" in an RBI resolution
              if n > SUBJECT_COMMON * len(every) and n >= SUBJECT_COMMON_MIN}

    def regions_differ(x: str, y: str) -> bool:
        a, b = _regions(x), _regions(y)
        return bool(a and b and not a & b)

    def correspond(r: _One, a: _One) -> bool | None:
        """True for paragraphs the redline aligns, False for ones it aligns with others, None when not known."""
        if align_old.get(r.para) and align_new.get(a.para):
            return a.para in align_old[r.para]
        return None

    def years_differ(r: _One, a: _One) -> bool:
        yr, ya = set(_FISCAL_YEAR.findall(r.text)), set(_FISCAL_YEAR.findall(a.text))
        return bool(yr and ya and not yr & ya)

    def same_subject(r: _One, a: _One, near: bool) -> bool:
        if years_differ(r, a) or regions_differ(r.text, a.text):
            return False                               # different financial years, or different economies
        x, y = _subject(r.text), _subject(a.text)
        share, count = _shared(r.text, a.text, None if near else weight)
        if not count:
            return False
        if count < SUBJECT_SHARED:                     # a short sentence: all of its subject words
            return (count == min(len(x), len(y)) and share >= SUBJECT_ALL
                    and (r.opening or _heads(r.text, a.text, common)))
        if not (r.opening or _heads(r.text, a.text, common)):
            return False                               # what each is about first differs
        strong, same = (SUBJECT_STRONG, SUBJECT_SAME) if near else (SUBJECT_FAR_STRONG, SUBJECT_FAR)
        return share >= strong or (share >= same and kind_of(r.text) == kind_of(a.text))

    paired: set[int] = set()
    opened: set[tuple[int, int]] = set()              # paragraphs (old, new) whose openings pair

    def match(tier_of) -> None:
        cands = []
        for i, r in enumerate(removed):
            for j, a in enumerate(added):
                if id(r) in paired or id(a) in paired:
                    continue
                t = tier_of(r, a)
                if t is not None:
                    cands.append((t, _shared(r.text, a.text)[0], i, j))
        cands.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
        for _, _, i, j in cands:
            r, a = removed[i], added[j]
            if id(r) in paired or id(a) in paired:
                continue
            paired.update((id(r), id(a)))
            changes.append([[r], [a]])
            if r.opening and a.opening:
                opened.add((r.para, a.para))

    def first_pass(r: _One, a: _One) -> int | None:
        if _likeness(r.text, a.text) >= PAIR_MIN and not years_differ(r, a) and not regions_differ(r.text, a.text):
            return 3                                   # the same sentence, moved (and perhaps reworded)
        known = correspond(r, a)
        if known is False or (known is None and r.gap != a.gap) or r.opening != a.opening:
            return None                                # another section, paragraphs aligned with others, or an
        if r.opening:                                  # opening against a non-opening
            if same_subject(r, a, bool(known)):
                return 2
            number = (m := _PARA_NUMBER.match(r.text)) and (n := _PARA_NUMBER.match(a.text)) and m.group() == n.group()
            if (kind_of(r.text) == kind_of(a.text) and _shared(r.text, a.text)[1] >= OPENING_SHARED
                    and (number or _heads(r.text, a.text, common))
                    and not years_differ(r, a) and not regions_differ(r.text, a.text)):
                return 2                               # two paragraphs' openings of one kind, sharing subject words
            return None
        return 1 if same_subject(r, a, bool(known)) else None

    def second_pass(r: _One, a: _One) -> int | None:
        if r.opening or a.opening or (r.para, a.para) not in opened:
            return None                                # paragraphs whose openings paired correspond
        return 1 if same_subject(r, a, True) else None

    match(first_pass)
    match(second_pass)

    def near(x: _One, y: _One, side: int) -> bool:
        r, a = (x, y) if side == 0 else (y, x)
        return bool(correspond(r, a)) or (r.para, a.para) in opened or (correspond(r, a) is None and r.gap == a.gap)

    # A sentence split in two (or two merged): see the docstring.
    for side, mine, theirs_all, text in ((1, added, removed, new.text), (0, removed, added, old.text)):
        grew = True
        while grew:
            grew = False
            for x in mine:
                if id(x) in paired:
                    continue
                mine_s = _subject(x.text)
                for change in changes:
                    have = change[side]
                    if not mine_s or not have or not change[1 - side] or len(have) >= FALLBACK_RUN_MAX:
                        continue
                    before, after = x.span[1] <= have[0].span[0], x.span[0] >= have[-1].span[1]
                    edge = have[0] if before else have[-1]
                    between = text[x.span[1]:edge.span[0]] if before else text[edge.span[1]:x.span[0]]
                    if not (before or after) or edge.para != x.para or between.strip():
                        continue
                    if before and x.opening and not change[1 - side][0].opening:
                        continue                       # an opening joins only a side whose other side opens too
                    if any(regions_differ(x.text, y.text) for y in change[1 - side]):
                        continue                       # another economy
                    if kinds and kind_of(x.text) != kind_of(" ".join(y.text for y in change[0] + change[1])):
                        continue                       # from v26: another kind (a dissent beside a vote) stays its own
                    theirs = set().union(*(_subject(y.text) for y in change[1 - side]))
                    ours = set().union(*(_subject(y.text) for y in have))
                    if len(mine_s & theirs) / len(mine_s) < SUBJECT_SAME or len((mine_s & theirs) - ours) < 2:
                        continue
                    best = max((len(mine_s & _subject(y.text)) / len(mine_s) for y in theirs_all
                                if y not in change[1 - side] and near(x, y, side)), default=0.0)
                    if best >= len(mine_s & theirs) / len(mine_s):
                        continue                       # another sentence speaks to it as much
                    change[side] = [x] + have if before else have + [x]
                    paired.add(id(x))
                    grew = True
                    break

    out: list[list] = []
    for o, n in changes:
        ot, nt = " ".join(x.text for x in o), " ".join(x.text for x in n)
        if not _trivial(ot, nt):                       # a change only in dates, a paragraph number or a connective
            out.append([[x.span for x in o], [x.span for x in n]])
    out += [[[x.span], []] for x in removed if id(x) not in paired]
    out += [[[], [x.span]] for x in added if id(x) not in paired]
    for side, text in ((1, new.text), (0, old.text)):  # consecutive sentences alone: one change, up to a limit
        alone = sorted((p for p in out if p[side] and not p[1 - side]), key=lambda p: min(s for s, _ in p[side]))
        run: list = []
        for p in alone:
            gap_text = text[max(e for _, e in run[-1][side]):min(s for s, _ in p[side])] if run else "x"
            if (run and len(run[-1][side]) < FALLBACK_RUN_MAX and not gap_text.strip() and "\n" not in gap_text
                    and not _PARA_NO.match(text[min(s for s, _ in p[side]):])):        # one paragraph only
                run[-1][side] = sorted(run[-1][side] + p[side])
                out.remove(p)
            else:
                run.append(p)
    return out


def _v1_bullets(v1: list[tuple[int, ...]]) -> dict[int, list[tuple[int, int, int]]]:
    """The v1 output's citations by its bullet's index (from v26, a citation's fourth item; one without it is left out)."""
    out: dict[int, list[tuple[int, int, int]]] = {}
    for c in v1:
        if len(c) > 3 and isinstance(c[3], int) and c[3] >= 0:
            out.setdefault(c[3], []).append((c[0], c[1], c[2]))
    return out


def _hits(spans, doc_id: int, cites: list[tuple[int, int, int]]) -> bool:
    """Whether one of `cites` (document id, start, end) overlaps one of `spans` in document `doc_id`."""
    return any(d == doc_id and a < e and s < b for s, e in spans for d, a, b in cites)


def _v1_counterparts(segments: dict, old: Doc, new: Doc, changes: list[list], v1: list[tuple[int, ...]],
                     said: dict[int, str]) -> None:
    """From v26 (`DIFF_V1_PAIRS_FROM`): where one v1 bullet cites a sentence of exactly one change that only the previous
    document has and of exactly one that only the new one has, both on one topic (the redline's, where both have one), the two
    are one change, in place in `changes`. `said` gets the merged change's sentences that v1 cites, by its id: its kind."""
    topic: dict[tuple[int, int], str | None] = {}
    for p in segments.get("pairs", []):
        for side, key in ((0, "old"), (1, "new")):
            for s, _ in p.get(key) or []:
                topic[(side, s)] = p.get("topic")
    for _, cites in sorted(_v1_bullets(v1).items()):
        olds = [p for p in changes if p[0] and not p[1] and _hits(p[0], old.document_id, cites)]
        news = [p for p in changes if p[1] and not p[0] and _hits(p[1], new.document_id, cites)]
        if len(olds) != 1 or len(news) != 1:
            continue
        o, n = olds[0], news[0]
        a, b = topic.get((0, min(o[0])[0])), topic.get((1, min(n[1])[0]))
        if a and b and a != b:
            continue                                   # another part of the document
        o[1] = n[1]
        changes[:] = [c for c in changes if c is not n]
        said[id(o)] = " ".join([old.text[s:e] for s, e in o[0] if _hits([(s, e)], old.document_id, cites)]
                               + [new.text[s:e] for s, e in o[1] if _hits([(s, e)], new.document_id, cites)])


@dataclass(eq=False)
class _Changes:
    """The fallback's reading of a redline (`_analyse`): its changes as [old spans, new spans], each one's kind (an index into
    FALLBACK_KINDS) and ranks by id, the first pass's order (`by_kind`) and the later passes' (`after`), and from v25 the
    changes the v1 output cites (`first`), sentences alone (`later`) and minor changes (`last`)."""
    unique: list
    kind: dict
    says: dict
    rate: dict
    preferred: dict
    by_kind: list
    after: list
    first: set
    later: set
    last: set
    v1_said: dict


def _where(p) -> tuple[int, int]:
    return min([s for s, _ in p[1]] or [10 ** 9]), min([s for s, _ in p[0]] or [10 ** 9])


def _words(spans: list[tuple[int, int]], text: str) -> str:
    return " ".join(text[s:e] for s, e in spans)


_INSTRUMENT = re.compile(r"reverse repo|standing deposit facility|\bSDF\b|marginal standing facility|\bMSF\b|bank rate",
                         re.IGNORECASE)
_INSTRUMENT_NAME = {"standing deposit facility": "sdf", "marginal standing facility": "msf"}


def _instruments(text: str) -> set[str]:
    """The corridor rates a text names (`_corridor_mates`), as one name each ("MSF" and "marginal standing facility": msf)."""
    out = set()
    for m in _INSTRUMENT.finditer(text):
        w = " ".join(m.group().lower().split())
        out.add(_INSTRUMENT_NAME.get(w, w))
    return out


def _corridor_mates(changes: list[list], old: Doc, new: Doc) -> None:
    """From v29 (`DIFF_ROOM_FROM`): a change only one document has, naming a corridor rate, joins the side of the one change
    with both sides next to it (only spaces and line breaks between them) whose other side names that rate and whose own side
    doesn't (in place in `changes`): what the rate was
    and what it became, read together (mpc-2020-03-27: the new "further, … the reverse repo rate … stands reduced by 90 basis
    points to 4.0 per cent" joins the new "accordingly, the … (MSF) rate and the Bank Rate stand reduced …", answering the
    previous "Consequently, the reverse repo rate … remains unchanged at 4.90 per cent and the … (MSF) rate … at 5.40 per
    cent")."""
    texts = (old.text, new.text)

    def next_to(a: list, b: list, text: str) -> bool:
        """Only spaces and line breaks between one of `a`'s sentences and one of `b`'s."""
        return any(not text[min(e, x):max(e, x)].strip() for _, e in a for x, _ in b) or \
            any(not text[min(e, x):max(e, x)].strip() for _, e in b for x, _ in a)

    for p in [p for p in changes if bool(p[0]) != bool(p[1])]:
        s = 0 if p[0] else 1
        mine = _instruments(_words(p[s], texts[s]))
        if not mine:
            continue
        hosts = [c for c in changes if c is not p and c[0] and c[1] and next_to(p[s], c[s], texts[s])
                 and mine & _instruments(_words(c[1 - s], texts[1 - s])) - _instruments(_words(c[s], texts[s]))]
        if len(hosts) != 1:
            continue                                   # none, or more than one: it stays alone
        hosts[0][s] = sorted(hosts[0][s] + p[s])
        changes[:] = [c for c in changes if c is not p]


def _analyse(segments: dict, old: Doc, new: Doc, prefer: list[tuple[str, str]], *, packed: bool = False,
             restated: bool = False, counterparts: bool = False, v1: list[tuple[int, ...]] | None = None,
             kinds: bool = False, v1_pairs: bool = False, room: bool = False) -> _Changes:
    """The fallback's changes and their kinds and ranks (`fallback_bullets`, whose arguments these are); from v28 the
    changes every output must report are read from it too (`must_changes`)."""
    def kind_of(text: str) -> int:
        return _kind(text, packed=packed)

    words = _words
    pairs: list[list] = []
    v1_said: dict[int, str] = {}                       # from v26: a change v1's counterparts made: the sentences v1 cites
    if counterparts:
        pairs = _counterparts(segments, old, new, kind_of, kinds=kinds)
        if v1_pairs:
            _v1_counterparts(segments, old, new, pairs, v1 or [], v1_said)
        if room:
            _corridor_mates(pairs, old, new)
    for p in [] if counterparts else segments.get("pairs", []):
        kind = p.get("kind")
        if kind not in ("changed", "added", "removed"):
            continue
        o = [tuple(x) for x in p.get("old", [])] if kind != "added" else []
        n = [tuple(x) for x in p.get("new", [])] if kind != "removed" else []
        if o and n:
            if _trivial(words(o, old.text), words(n, new.text)):
                continue                                   # a change only in dates, a paragraph number or a connective
            if not _same_topic(words(o, old.text), words(n, new.text)):
                pairs += [[o, []], [[], n]]                # paired by position, not by what they say: two
                continue
        if o or n:
            pairs.append([o, n])
    every_old = [(s, e) for p in segments.get("pairs", []) for s, e in (tuple(x) for x in p.get("old", []))]
    removed = [p for p in pairs if p[0] and not p[1]] if not counterparts else []
    for p in [p for p in pairs if p[1] and not p[0] and not counterparts]:  # an added sentence that rewords a removed one
        mine = set().union(*(_pairs(new.text[s:e]) for s, e in p[1]))
        added = words(p[1], new.text)
        best, best_shared = None, -1.0
        for r in removed:
            theirs = set().union(*(_pairs(old.text[s:e]) for s, e in r[0]))
            gone = words(r[0], old.text)
            if (any(sum(x in _pairs(old.text[a:b]) for a, b in every_old) <= ADDED_MAX_SENTENCES for x in mine & theirs)
                    or (_same_topic(gone, added) and kind_of(gone) == kind_of(added))):    # round 17: same kind and topic too
                a, b = _content(gone), _content(added)
                shared = len(a & b) / max(1, min(len(a), len(b)))
                if shared > best_shared:
                    best, best_shared = r, shared
        if best is not None:
            best[1] = p[1]
            pairs.remove(p)
            removed.remove(best)
    for side, text in ((1, new.text), (0, old.text)) if not counterparts else ():   # consecutive alone: one change
        alone = sorted((p for p in pairs if p[side] and not p[1 - side]), key=lambda p: min(s for s, _ in p[side]))
        run: list = []
        for p in alone:
            gap = text[max(e for _, e in run[-1][side]):min(s for s, _ in p[side])] if run else "x"
            if (run and len(run[-1][side]) < FALLBACK_RUN_MAX and not gap.strip() and "\n" not in gap
                    and not _PARA_NO.match(text[min(s for s, _ in p[side]):])):        # one paragraph only
                run[-1][side] = sorted(run[-1][side] + p[side])
                pairs.remove(p)
            else:
                run.append(p)
    seen: set[tuple[str, str]] = set()
    unique = []
    for p in pairs:                                     # a change the redline shows twice, once
        k = (" ".join(_undated(words(p[0], old.text))), " ".join(_undated(words(p[1], new.text))))
        if k not in seen:
            seen.add(k)
            unique.append(p)

    def first_preferred(p) -> int:
        for k, (doc, q) in enumerate(prefer):
            text, spans = (old.text, p[0]) if doc == "previous" else (new.text, p[1])
            if any(_in_passages(match_form(q), [text[s:e]]) for s, e in spans):
                return k
        return len(prefer)

    kind = {id(p): kind_of(v1_said.get(id(p)) or words(p[0], old.text) + " " + words(p[1], new.text)) for p in unique}
    preferred = {id(p): first_preferred(p) for p in unique}
    where = _where

    # Within a kind, a change whose new side is itself of that kind first: the new sentence says it (round 17: mpc-2019-02-07's
    # old "consistent with the stance of calibrated tightening" was paired with a new sentence without the stance, while the
    # added "decided to change the monetary policy stance from calibrated tightening to neutral" was left out).
    says = {id(p): 0 if p[1] and kind_of(words(p[1], new.text)) == kind[id(p)] else 1 for p in unique}
    # From v23, the decision whose new sentence gives the rate first (mpc-2020-03-27: "reduce the policy repo rate … by 75 basis
    # points to 4.40 per cent" before "All members voted for a reduction in the policy repo rate").
    rate = {id(p): 0 if not packed or kind[id(p)] or _K_FIGURE_RATE.search(words(p[1], new.text)) else 1 for p in unique}
    by_kind = sorted(unique, key=lambda p: (kind[id(p)], says[id(p)], rate[id(p)], preferred[id(p)], where(p)))
    if packed and restated:
        # From v24: the decision is shown once, and another change of the decision's that also states the stance is the stance
        # (mpc-2021-02-05, round 20: "the MPC decided to continue with an accommodative stance … till the prospects of a
        # sustained recovery are well secured" restates the rate decision too, so v23 left it out as a second decision, and v1
        # (397) has it).
        decision, stance = FALLBACK_KINDS.index("decision"), FALLBACK_KINDS.index("stance")
        shown = next((p for p in by_kind if kind[id(p)] == decision), None)
        for p in unique:
            text = words(p[0], old.text) + " " + words(p[1], new.text)
            if kind[id(p)] == decision and p is not shown and _K_STANCE_PACKED.search(text):
                kind[id(p)], rate[id(p)] = stance, 0
                says[id(p)] = 0 if p[1] and _K_STANCE_PACKED.search(words(p[1], new.text)) else 1
        by_kind = sorted(unique, key=lambda p: (kind[id(p)], says[id(p)], rate[id(p)], preferred[id(p)], where(p)))
    after = sorted(unique, key=lambda p: (preferred[id(p)], kind[id(p)], where(p)))
    first: set[int] = set()
    later: set[int] = set()
    last: set[int] = set()
    if counterparts:
        # From v25: the changes the target's v1 output cites first, and a minor change last (rule (c), change by change).
        def cited(p) -> bool:
            return any(d == doc and a < e and s < b for doc, spans in ((old.document_id, p[0]), (new.document_id, p[1]))
                       for s, e in spans for d, a, b, *_ in v1 or ())
        first = {id(p) for p in unique if cited(p)}
        last = {id(p) for p in unique if p[0] and p[1] and _minor(words(p[0], old.text), words(p[1], new.text))}
        later = {id(p) for p in unique if not (p[0] and p[1])}       # a sentence alone, after the pairs
        tier = {id(p): 0 if id(p) in first else 3 if id(p) in last else 2 if id(p) in later else 1 for p in unique}
        if v1_pairs:
            # From v26: a change v1 cites in a bullet of its own, of the decision's kind but not the decision shown nor a
            # restatement of it (the same rate figures, and no vote), is shown: a vote with the dissents, else as the stance.
            decision = FALLBACK_KINDS.index("decision")
            order = sorted(unique, key=lambda p: (kind[id(p)], tier[id(p)], says[id(p)], rate[id(p)], preferred[id(p)],
                                                  where(p)))
            shown = next((p for p in order if kind[id(p)] == decision), None)
            bullets_of = _v1_bullets(v1 or [])

            def of(p) -> set[int]:
                return {k for k, cites in bullets_of.items()
                        if _hits(p[0], old.document_id, cites) or _hits(p[1], new.document_id, cites)}

            def figures(p) -> set[str]:
                side = words(p[1], new.text) if p[1] else words(p[0], old.text)
                return {" ".join(x.lower().split()) for x in _K_FIGURE_RATE.findall(side)}

            for p in unique:
                if shown is None or kind[id(p)] != decision or p is shown or not of(p) or of(p) & of(shown):
                    continue
                text = v1_said.get(id(p)) or words(p[0], old.text) + " " + words(p[1], new.text)
                vote = bool(_K_VOTE.search(text))
                if not vote and figures(p) and figures(p) == figures(shown):
                    continue                           # the decision again (an RBI resolution says it twice)
                kind[id(p)] = FALLBACK_KINDS.index("dissent" if vote else "stance")
                says[id(p)], rate[id(p)] = 0, 0
        by_kind = sorted(unique, key=lambda p: (kind[id(p)], tier[id(p)], says[id(p)], rate[id(p)], preferred[id(p)],
                                                where(p)))
        after = sorted(unique, key=lambda p: (tier[id(p)], preferred[id(p)], kind[id(p)], where(p)))
        later -= first
    return _Changes(unique, kind, says, rate, preferred, by_kind, after, first, later, last, v1_said)


def fallback_bullets(segments: dict, old: Doc, new: Doc, prefer: list[tuple[str, str]], hi: int = 5, *,
                     packed: bool = False, restated: bool = False, counterparts: bool = False,
                     v1: list[tuple[int, ...]] | None = None, kinds: bool = False,
                     v1_pairs: bool = False, v1_all: bool = False, must: bool = False,
                     room: bool = False) -> list[Bullet]:
    """The fallback's bullets (see above): `prefer` holds a rejected reply's quotes as (document, text), in its order.
    `packed` (from v23, `DIFF_PACK_FROM`): the balance sheet's words rank before the stance's, and a bullet carries up to
    FALLBACK_PER_BULLET changes of one kind (`_pack`). `restated` (from v24, `DIFF_RESTATED_FROM`; with `packed`): a change
    of the decision's other than the one shown that also states the stance is the stance. `counterparts` (from v25,
    `DIFF_COUNTERPART_FROM`; with `packed` and `restated`): changes paired only with their counterparts (`_counterparts`),
    the changes `v1` cites first (the target's v1 output's citations, as (document id, start, end), from v26 with the v1
    bullet's index as a fourth), a minor change last (`_minor`), quotations without RBI paragraph numbers and list items
    labelled as such. `kinds` (from v26, `DIFF_KIND_JOIN_FROM`; with `counterparts`): `_counterparts`' joining rule of round
    23. `v1_pairs` (from v26, `DIFF_V1_PAIRS_FROM`; with `counterparts`): v1's counterparts are one change, and v1's own
    changes of the decision's kind are shown (`_v1_counterparts`, and below). `v1_all` (from v27, `DIFF_V1_ALL_FROM`; with
    `packed` and `counterparts`): every change v1 cites is shown, a sentence one document alone has beside one of the other's
    that the same v1 bullet cites (`_pack`'s `v1_of` and `side`). `must` (from v28, `DIFF_MUST_FROM`; with `v1_all`): every
    change an output must report is shown too (`must_changes`), whether v1 cites it or not, and a sentence one document alone
    has sits beside the other document's of the same kind that `must_changes` reads as its other side. `room` (from v29,
    `DIFF_ROOM_FROM`; with `must`): a corridor rate's sentence alone joins its other side (`_corridor_mates`), the changes v1
    cites are placed before the ones only `must_changes` adds, and a ratio to GDP is no projection (`_must_of`)."""
    a = _analyse(segments, old, new, prefer, packed=packed, restated=restated, counterparts=counterparts, v1=v1,
                 kinds=kinds, v1_pairs=v1_pairs, room=room)
    unique, kind, preferred = a.unique, a.kind, a.preferred
    first, later, last = set(a.first), set(a.later), set(a.last)
    words, where = _words, _where
    chosen: list = []
    used: set[tuple[int, int, int]] = set()

    def take(p) -> bool:
        nonlocal used
        mine = {(old.document_id, *x) for x in p[0]} | {(new.document_id, *x) for x in p[1]}
        if mine & used or any(c is p for c in chosen):
            return False                               # a sentence another bullet already quotes
        used |= mine
        chosen.append(p)
        return True

    if packed:
        said = {id(p): (" ".join(_undated(words(p[0], old.text))), " ".join(_undated(words(p[1], new.text)))) for p in unique}
        v1_of: dict[int, frozenset[tuple[int, str]]] | None = None
        side: dict[int, str] = {}
        v1_cited = set(a.first) if room else None       # from v29: v1's changes are placed before v28's own
        if counterparts and v1_all:
            # From v27: the v1 bullets citing each change's sides (every change v1 cites is shown), and which document alone
            # has it.
            cited_by = _v1_bullets(v1 or [])
            v1_of = {id(p): frozenset((k, s) for k, cites in cited_by.items()
                                      for s, spans, doc in (("old", p[0], old.document_id), ("new", p[1], new.document_id))
                                      if _hits(spans, doc, cites))
                     for p in unique}
            side = {id(p): "old" if p[0] and not p[1] else "new" if p[1] and not p[0] else "both" for p in unique}
            if must:
                # From v28: the changes every output must report are shown as v1's are, and a lone sentence's other side
                # (`_Must.mate`) sits beside it, as if one v1 bullet cited both (a key below v1's bullet indexes).
                for m in _must_of(a, old, new, v1 or [], room=room):
                    first.add(id(m.change))
                    later.discard(id(m.change))
                    last.discard(id(m.change))
                    if m.mate is not None and id(m.mate) in v1_of:
                        key = -1 - unique.index(m.change)
                        v1_of[id(m.change)] |= {(key, side[id(m.change)])}
                        v1_of[id(m.mate)] |= {(key, side[id(m.mate)])}
        bullets = _pack(a.by_kind, a.after, {id(p): kind[id(p)] for p in unique}, said, take, hi, first=first, later=later,
                        last=last, v1_of=v1_of, side=side, v1_cited=v1_cited)
    else:
        for k in range(FALLBACK_FIRST_KINDS):          # one of each kind first
            for p in a.by_kind:
                if len(chosen) < hi and kind[id(p)] == k and take(p):
                    break
        for p in a.after:
            if len(chosen) == hi:
                break
            if kind[id(p)] == 0 and any(kind[id(c)] == 0 for c in chosen):
                continue                               # the decision once (an RBI resolution says it in two paragraphs)
            take(p)
        bullets = [[p] for p in chosen]

    def rank(p) -> int:
        return FALLBACK_PACKED_ORDER.index(FALLBACK_KINDS[kind[id(p)]]) if packed else kind[id(p)]

    out: list[Bullet] = []
    for changes in sorted(bullets, key=lambda g: (rank(g[0]), preferred[id(g[0])], where(g[0]))):
        b = Bullet("")
        parts = []
        for i, (o, n) in enumerate(changes):
            said = []
            item = (_list_item(old.text, min(o)[0]) if o else None, _list_item(new.text, min(n)[0]) if n else None)
            if o:
                q, spans = _side(old.text, o, bare=counterparts)
                said.append(_said(f"The previous {old.label}", "listed", item[0] if counterparts else None, "said", q))
                b.citations += [Cited(old.document_id, s, e, "redline") for s, e in spans]
            if n:
                q, spans = _side(new.text, n, bare=counterparts)
                said.append(_said(f"The new {new.label}", "lists", item[1] if counterparts else None, "says", q))
                b.citations += [Cited(new.document_id, s, e, "redline") for s, e in spans]
            what = "item" if counterparts and (item[0] or item[1]) else "sentence"
            these = f"these {what}s" if len(o or n) > 1 else f"this {what}"
            if not o:
                said.append(f"The previous {old.label} doesn't have {these}.")
            if not n:
                said.append(f"The new {new.label} doesn't have {these}.")
            text = " ".join(said)
            parts.append(f"Also, {text[0].lower()}{text[1:]}" if i else text)    # a second change of the bullet's kind
        b.text = " ".join(parts)
        out.append(b)
    return out


# ---- v28's changes every output must report (`DIFF_MUST_FROM`; `must_changes`, `must_problems`, `fallback_bullets`' `must`)
# The bank's own projection, not the IMF's or a past one it is compared with ("in line with the projection of (-)7.5 per cent
# set out in the December 2020 resolution", mpc-2021-02-05, is no projection of the committee's).
_K_OWN_PROJECTION = re.compile(r"\b(?:is|are|was|were|has\s+been|have\s+been)\s+(?:now\s+)?(?:projected|revised|retained|placed)\b"
                               r"|\bprojections?\s+(?:for|of)\s+(?:the\s+)?(?:CPI|headline|core|real|GDP|GVA|inflation|growth)\b",
                               re.IGNORECASE)
_K_OTHERS_PROJECTION = re.compile(r"\bIMF\b|International Monetary Fund|\bOECD\b|World Bank", re.IGNORECASE)
_K_UNANIMOUS = re.compile(r"\bunanimous(?:ly)?\b|\ball members\b", re.IGNORECASE)
_STANCE_WORD = re.compile(r"\bstance\b", re.IGNORECASE)
MUST_SHOW_WORDS = 30                   # words of each sentence the request's list of changes to report shows


@dataclass(eq=False)
class _Must:
    change: list                       # one of `_Changes.unique`
    why: str                           # 'decision', 'vote', 'stance', 'projection' or 'v1'
    kind: str                          # its FALLBACK_KINDS name
    mate: list | None = None           # a sentence alone: the other document's sentence alone of its kind that answers it


@dataclass(frozen=True)
class Must:
    """A change every v28-on output must report (`must_changes`): its sentences in each document (spans; one side empty for
    a sentence only one document has), why, and `group` ('decision' for the decision, which any one of the group's changes
    reports: an RBI resolution states it twice)."""
    old: tuple[tuple[int, int], ...]
    new: tuple[tuple[int, int], ...]
    why: str
    kind: str
    group: str | None = None


def _names_only(old_text: str, new_text: str) -> bool:
    """A change only in names, titles or initials, and in punctuation ("Chairman" to "Chair", a member who joined): which
    members vote, not how they voted."""
    a, b = _RUN_WORD.findall(match_form(old_text)), _RUN_WORD.findall(match_form(new_text))
    differ = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            differ += a[i1:i2] + b[j1:j2]
    return bool(a and b) and all(w[:1].isupper() or w.lower() in _NAME_WORDS for w in differ)


_K_RATIO = re.compile(r"\bper\s*cent\s+of\s+(?:the\s+)?(?:GDP|GVA)\b|\bfiscal deficit\b|\bcurrent account\b", re.IGNORECASE)


def _own_projection(t: str) -> bool:
    """A text giving the committee's own growth or inflation projection (v28's reading of `_must_of`)."""
    return bool(_K_OWN_PROJECTION.search(t) and _K_FIGURE_RATE.search(t) and (_K_GROWTH.search(t) or _K_INFLATION.search(t))
                and not _K_OTHERS_PROJECTION.search(t))


def _must_of(a: _Changes, old: Doc, new: Doc, v1: list[tuple[int, ...]], *, room: bool = False) -> list[_Must]:
    """The changes every output must report, from the fallback's reading (`_analyse`, with v26's rules), in its first pass's
    order: the decision once (the first of its kind); every change of the stance or forward guidance, and of the vote's split
    (a dissent, or unanimity on one side only); every change with a sentence giving the committee's own growth or inflation
    projection; and every change the target's v1 output cites. Never a change of a word or two that changes no meaning
    (`_minor`) or only of names (`_names_only`); a change only in dates is no change already. A sentence one document alone
    has, of one of those kinds, gets the other document's sentence alone of its kind as its mate, the one sharing most of its
    words, so its other side is reported with it. From v29 (`room`), a sentence giving a ratio to GDP (the fiscal deficit,
    the current account) is left out of the projection's reading (`DIFF_ROOM_FROM`)."""
    decision, stance, dissent = (FALLBACK_KINDS.index(k) for k in ("decision", "stance", "dissent"))
    cites = [c[:3] for c in v1]
    out: list[_Must] = []
    shown = False
    for p in a.by_kind:
        o, n = _words(p[0], old.text), _words(p[1], new.text)
        if p[0] and p[1] and (_minor(o, n) or _names_only(o, n)):
            continue
        k = a.kind[id(p)]
        sides = [t for t in (o, n) if t]
        why = None
        if k == decision:
            if not shown:
                why, shown = "decision", True
        elif k in (stance, dissent):
            why = "stance" if k == stance else "vote"
        if why is None and p[0] and p[1] and _K_VOTE.search(o) and _K_VOTE.search(n) and (
                bool(_K_UNANIMOUS.search(o)) != bool(_K_UNANIMOUS.search(n))):
            why = "vote"                               # unanimous on one side only (mpc-2022-09-30, round 22)
        if room:                                       # from v29: never a sentence giving a ratio to GDP
            projection = any(_own_projection(" ".join(t[s:e] for s, e in spans if not _K_RATIO.search(t[s:e])))
                             for t, spans in ((old.text, p[0]), (new.text, p[1])))
        else:
            projection = any(_own_projection(t) for t in sides)
        if why is None and projection:
            why = "projection"
        if why is None and k != decision and (_hits(p[0], old.document_id, cites) or _hits(p[1], new.document_id, cites)):
            why = "v1"
        if why is not None:
            out.append(_Must(p, why, FALLBACK_KINDS[k]))
    lone = [m for m in out if m.why != "v1" and bool(m.change[0]) != bool(m.change[1])]
    for m in lone:
        if m.mate is not None:
            continue
        mine = _words(m.change[0], old.text) or _words(m.change[1], new.text)
        best, best_share = None, 0.0
        for x in lone:
            if x is m or x.mate is not None or bool(x.change[0]) == bool(m.change[0]) or x.kind != m.kind:
                continue
            share = _shared(mine, _words(x.change[0], old.text) or _words(x.change[1], new.text))[0]
            if share > best_share:
                best, best_share = x, share
        if best is not None:
            m.mate, best.mate = best.change, m.change
    return out


def must_changes(segments: dict, old: Doc, new: Doc, v1: list[tuple[int, ...]] | None = None, *,
                 room: bool = False) -> list[Must]:
    """From v28 (`DIFF_MUST_FROM`): the changes every output must report (`_must_of`), read as the v28 fallback reads the
    redline; `v1` the target's v1 output's citations as `fallback_bullets` takes them. `room` (from v29, `DIFF_ROOM_FROM`):
    read as the v29 fallback reads it."""
    a = _analyse(segments, old, new, [], packed=True, restated=True, counterparts=True, v1=v1 or [], kinds=True,
                 v1_pairs=True, room=room)
    return [Must(tuple(m.change[0]), tuple(m.change[1]), m.why, m.kind, "decision" if m.why == "decision" else None)
            for m in _must_of(a, old, new, v1 or [], room=room)]


def _shown(text: str, spans, *, each: bool = False) -> str:
    """A change's side as the request lists it: its first MUST_SHOW_WORDS words; from v29 (`each`), each sentence's."""
    if each:
        return " ".join(_shown(text, [x]) for x in sorted(spans))
    words = " ".join(" ".join(text[s:e].split()) for s, e in sorted(spans)).split()
    return " ".join(words[:MUST_SHOW_WORDS]) + (" …" if len(words) > MUST_SHOW_WORDS else "")


def must_text(must: list[Must], old_text: str, new_text: str, *, each: bool = False) -> str:
    """The request's list of the changes to report (v28 on), after the changed sentences; from v29 (`each`), each sentence
    shown up to MUST_SHOW_WORDS words."""
    lines = []
    decision = [m for m in must if m.group == "decision"]
    for m in must:
        if m.group == "decision" and m is not decision[0]:
            continue
        if m.old and m.new:
            line = f'Previous: "{_shown(old_text, m.old, each=each)}" New: "{_shown(new_text, m.new, each=each)}"'
        elif m.old:
            line = f'Removed (only the previous document has it): "{_shown(old_text, m.old, each=each)}"'
        else:
            line = f'Added (only the new document has it): "{_shown(new_text, m.new, each=each)}"'
        lines.append(("- The decision: " if m.group == "decision" else "- ") + line)
    if not lines:
        return ""
    return ("Each of these changes must be reported in a bullet, with what each document says where both are given "
            "(a sentence is shown up to its first " + str(MUST_SHOW_WORDS) + " words):\n" + "\n".join(lines))


def must_problems(c: Checked, must: list[Must], old: Doc, new: Doc, *, room: bool = False) -> list[str]:
    """From v28: why a reply whose bullets all passed still can't be published: a change it must report (`must_changes`) that
    no bullet cites on each side it has (the decision: one of its sentences), or a change of the stance quoted, on a side
    whose sentence names the stance, without the words on the stance (round 24: mpc-2021-02-05's 3702 quoted the previous
    resolution's "maintain status quo on the policy rate" for a change of the stance). From v29 (`room`, `DIFF_ROOM_FROM`):
    also an item of a list of decisions, inside a change it reports, that no bullet cites (round 25: mpc-2020-03-27's
    "further, … the reverse repo rate … stands reduced by 90 basis points to 4.0 per cent")."""
    cites = [(x.document_id, x.start, x.end) for b in c.kept for x in b.citations]

    def covered(m: Must) -> bool:
        return ((not m.old or _hits(m.old, old.document_id, cites))
                and (not m.new or _hits(m.new, new.document_id, cites)))

    out = []
    decision = [m for m in must if m.group == "decision"]
    missing = [m for m in must if m.group is None and not covered(m)]
    if decision and not any(covered(m) for m in decision):
        missing.insert(0, decision[0])
    if room:                                           # from v29: each item of a list of decisions in a reported change
        for m in must:
            if m in missing or m.group == "decision":  # the decision: any one of its statements, as in v28
                continue
            for side, spans, doc in (("previous", m.old, old), ("new", m.new, new)):
                for x in spans:
                    if _list_item(doc.text, x[0]) == "decisions" and not _hits([x], doc.document_id, cites):
                        out.append(f'an item of the {side} document\'s list of decisions is left out: '
                                   f'"{_shown(doc.text, [x])[:120]}"; report it with the change it belongs to')
    for m in missing:
        if m.old and m.new:
            what = (f'the previous document\'s "{_shown(old.text, m.old)[:120]}" and the new one\'s '
                    f'"{_shown(new.text, m.new)[:120]}"')
        elif m.old:
            what = f'the previous document\'s "{_shown(old.text, m.old)[:120]}", which the new one doesn\'t have'
        else:
            what = f'the new document\'s "{_shown(new.text, m.new)[:120]}", which the previous one doesn\'t have'
        out.append(f"a change every answer must report is left out or shown from one side only: {what}")
    for m in must:
        if m.kind != "stance" or not covered(m):
            continue
        for side, spans, doc in (("previous", m.old, old), ("new", m.new, new)):
            text = _words(list(spans), doc.text)
            if not spans or not _STANCE_WORD.search(text):
                continue
            quoted = [q for b in c.kept if _hits(spans, doc.document_id, [(x.document_id, x.start, x.end)
                                                                          for x in b.citations])
                      for q in _QUOTED.findall(match_form(b.text)) if _in_passages(q, [match_form(text)])]
            if not any(_STANCE_WORD.search(q) for q in quoted):
                out.append(f'a change of the stance quotes the {side} document without its words on the stance '
                           f'("{_shown(doc.text, spans)[:120]}"): quote what it says about the stance itself')
    return out


def _said(who: str, lists: str, item: str | None, says: str, q: str) -> str:
    """One side's label (from v25, a list item's is "listed among its decisions" or "listed", not "said")."""
    if item == "decisions":
        return f'{who} {lists} among its decisions: "{q}"'
    if item == "list":
        return f'{who} {lists}: "{q}"'
    return f'{who} {says}: "{q}"'


def _pack(by_kind: list, after: list, kind: dict[int, int], said: dict[int, tuple[str, str]], take, hi: int, *,
          first: set[int] | frozenset = frozenset(), later: set[int] | frozenset = frozenset(),
          last: set[int] | frozenset = frozenset(), v1_of: dict[int, frozenset[tuple[int, str]]] | None = None,
          side: dict[int, str] | None = None, v1_cited: set[int] | None = None) -> list[list]:
    """The packed fallback's choice (v23 on, `fallback_bullets`): bullets as lists of changes, each holding up to
    FALLBACK_PER_BULLET changes of one kind. Here the inflation and growth projections are one kind, and the corridor rates
    join the decision's bullet (they move with it: "Consequently, the reverse repo rate … stands adjusted to …"), as v1 often
    puts them. `by_kind` and `after` are the changes in the first pass's order and the later passes' order, `kind` their
    kinds by id, `said` their two sides' words without dates, and `take` claims a change's sentences (False if another
    bullet quotes one). Three passes: one change of each of the first FALLBACK_FIRST_KINDS kinds of FALLBACK_PACKED_ORDER
    (the balance sheet before the projections), joining the bullet of its
    kind if it has room, else in a bullet of its own while there are fewer than `hi`; then each bullet takes the next
    changes of its kind, in `by_kind`'s order, up to its limit; then the changes left, in `after`'s order, join a bullet of
    their kind with room or open one while slots are left. The decision is shown once, and no change whose previous or new
    words another chosen change already has (an RBI resolution states its stance in two paragraphs). Last, while there are
    fewer than `hi` bullets, a bullet's second change moves to a bullet of its own: packing is for coverage, so a target with
    few changes keeps one change to a bullet.
    From v27 (`DIFF_V1_ALL_FROM`), with `v1_of` (the v1 bullets citing each change's sides, as (bullet, "old" or "new"), by
    id) and `side` ("old", "new" or "both": which document alone has the change): every change in `first` is shown. A sentence
    one document alone has goes first to the bullet holding the other document's side that the same v1 bullet cites with it
    (`mate`), and a change to the bullet holding such a sentence, then where `place` puts it; and when there is no room of
    its kind and no bullet left to open, to the bullet other than the decision's with the fewest changes (then one of its
    kind, then the shortest), below FALLBACK_PER_BULLET changes, then below FALLBACK_V1_PER_BULLET. When bullets are spare,
    a change is never moved away from its mate.
    From v29 (`DIFF_ROOM_FROM`), with `v1_cited` (the changes the v1 output cites, by id): of the changes in `first`, the
    decision and those v1 cites are placed first, then the others (the ones only `must_changes` adds), each in `by_kind`'s
    order."""
    decision, projections = FALLBACK_KINDS.index("decision"), FALLBACK_KINDS.index("projections")
    growth, corridor = FALLBACK_KINDS.index("growth projections"), FALLBACK_KINDS.index("corridor")
    bullets: list[list] = []

    def group(p) -> int:
        return {growth: projections, corridor: decision}.get(kind[id(p)], kind[id(p)])

    def place(p, *, open_new: bool = True) -> bool:
        chosen = [c for g in bullets for c in g]
        if kind[id(p)] == decision and any(kind[id(c)] == decision for c in chosen):
            return False                               # the decision once (an RBI resolution says it in two paragraphs)
        if any(a and a == b for c in chosen for a, b in zip(said[id(c)], said[id(p)], strict=True)):
            return False                               # words another chosen change already quotes
        room = [g for g in bullets if group(g[0]) == group(p) and kind[id(p)] != decision and len(g) < FALLBACK_PER_BULLET]
        if not room and (not open_new or len(bullets) >= hi):
            return False
        if not take(p):
            return False                               # a sentence another bullet already quotes
        if room:
            room[0].append(p)
        else:
            bullets.append([p])
        return True

    def beside(p, c) -> bool:
        """One document alone has `p`, and a v1 bullet that cites it cites `c`'s side in the other document and not its side
        in `p`'s: what `p` became, or what it was (mpc-2022-02-10: the previous "retained at 9.5 per cent in 2021-22" and the
        new "placed India's real gross domestic product (GDP) growth at 9.2 per cent for 2021-22", v1 (301) bullet 4)."""
        if v1_of is None or side is None or side.get(id(p)) not in ("old", "new"):
            return False
        mine, theirs = v1_of.get(id(p), frozenset()), v1_of.get(id(c), frozenset())
        s = side[id(p)]
        o = "new" if s == "old" else "old"
        return any((k, o) in theirs and (k, s) not in theirs for k, x in mine if x == s)

    def mate(p, c) -> bool:
        """From v27: `p` and `c` are two sides of what one v1 bullet compares (`beside`, either way round)."""
        return beside(p, c) or beside(c, p)

    def size(g) -> int:
        return sum(len(w) for c in g for w in said[id(c)])

    def put(p, g) -> bool:
        """From v27: `p` into bullet `g`, past `place`'s kinds and limits (the decision once and no words twice still hold)."""
        chosen = [c for b in bullets for c in b]
        if kind[id(p)] == decision and any(kind[id(c)] == decision for c in chosen):
            return False
        if any(a and a == b for c in chosen for a, b in zip(said[id(c)], said[id(p)], strict=True)):
            return False
        if not take(p):
            return False
        g.append(p)
        return True

    def place_v1(p) -> None:
        """From v27: a change v1 cites is shown (see above)."""
        if any(put(p, g) for g in bullets if len(g) < FALLBACK_V1_PER_BULLET and any(mate(p, c) for c in g)):
            return                                     # beside its other side, as v1 gives them
        if place(p):
            return
        others = [g for g in bullets if group(g[0]) != decision]
        for cap in (FALLBACK_PER_BULLET, FALLBACK_V1_PER_BULLET):
            for g in sorted((g for g in others if len(g) < cap), key=lambda g: (len(g), group(g[0]) != group(p), size(g))):
                if put(p, g):
                    return

    firsts = [p for p in by_kind if id(p) in first]
    if v1_cited is not None:                           # from v29: the decision and v1's before v28's own
        firsts.sort(key=lambda p: 0 if kind[id(p)] == decision or id(p) in v1_cited else 1)
    for p in firsts:                                   # from v25: the changes the v1 output cites first
        if id(p) in first:
            if v1_of is None:
                place(p)
            else:
                place_v1(p)
    for name in FALLBACK_PACKED_ORDER[:FALLBACK_FIRST_KINDS]:     # one of each kind first
        for p in by_kind:
            if kind[id(p)] == FALLBACK_KINDS.index(name) and id(p) not in later | last and place(p):
                break
    for p in by_kind:                                  # then each bullet's next changes of its kind
        if id(p) not in later | last:
            place(p, open_new=False)
    for p in after:                                    # then the slots left (from v25: pairs, a sentence alone, a minor
        if id(p) not in later | last:                  # change)
            place(p)
    for p in after:
        if id(p) in later:
            place(p)
    for p in after:
        if id(p) in last:
            place(p)
    for g in list(bullets):                            # bullets to spare: a second change gets a bullet of its own
        while len(g) > 1 and len(bullets) < hi:
            free = [c for c in g if not any(mate(c, d) for d in g if d is not c)]   # from v27: never away from its mate
            if not free:
                break
            c = free[-1]
            g[:] = [d for d in g if d is not c]
            bullets.append([c])
    return bullets


def retry_note(c: Checked) -> str:
    """What the retry is told about the first reply (the bullets it dropped, the count)."""
    lines = []
    for i, b in enumerate(c.bullets):
        if b.problem:
            lines.append(f'bullet {i + 1} ("{b.text[:160]}") had {b.problem}')
    return "; ".join(c.problems + lines) + "."


def content(c: Checked, *, attempt: int, reply: Reply) -> tuple[dict, str, list[tuple[str, Cited]]]:
    """(core.ai_output.content, .body, the citations as (claim_path, span)) for a published or rejected output.
    A published output holds only verified bullets (it's published only when none was dropped); a rejected one also
    lists what was dropped and why, for the record (rejected outputs are never visible)."""
    kept = c.kept
    cites: list[tuple[str, Cited]] = []
    bullets = []
    for i, b in enumerate(kept):
        nos = []
        for x in b.citations:
            cites.append((f"/bullets/{i}", x))
            nos.append(len(cites))
        bullets.append({"text": b.text, "citations": nos, **({"list": b.section} if b.section else {})})
    doc = {"bullets": bullets, "attempt": attempt, "stop_reason": reply.stop_reason,
           "citation_mapping": dict(sorted(c.mapping.items()))}
    if +c.copies:
        doc["copies"] = dict(sorted((+c.copies).items()))  # the diff summary's copied blocks our code removed or quoted (#445)
    if c.verify is not None:
        doc["verify"] = c.verify                           # the second model's check of the bullets (v17 on, #445)
    if not c.ok:
        doc["problems"] = c.reasons()
        doc["dropped"] = [{"text": b.text, "problem": b.problem} for b in c.dropped]
    return doc, "\n".join(b.text for b in kept), cites


# ---------------------------------------------------------------------------------------------- copied text (call summaries)
# Used by ft/ai/call_summary.py only (2B-11c, #301): module 1's checks above never call these, so `doc_summary` and
# `statement_diff_summary` are checked exactly as before. Matching is character for character after `match_form`: whitespace
# collapsed, curly quotes straight, dashes one hyphen, case kept. Every mapped character is one character, so an index in the
# matched form of a text whose whitespace is already single is an index in the text itself.
_STRAIGHT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
                           "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"'}) | _DASHES


def match_form(text: str) -> str:
    """`text` as it is compared for copying (see above)."""
    return " ".join(text.translate(_STRAIGHT).split())


def copied_runs(text: str, haystack: str, longer_than: int) -> list[tuple[int, int]]:
    """The stretches of `text` that are copied: every run of more than `longer_than` characters found verbatim in `haystack`
    (both in `match_form`), as (start, end) with overlapping or touching runs merged, in order."""
    n, j, found = len(text), 0, []
    for i in range(n):
        if n - i <= longer_than:
            break
        j = max(j, i)
        if j - i <= longer_than and text[i:i + longer_than + 1] not in haystack:
            continue                                   # no run longer than the limit starts here
        while j < n and text[i:j + 1] in haystack:     # j only moves right: a run from i + 1 ends no sooner than one from i
            j += 1
        if j - i > longer_than:
            found.append((i, j))
    merged: list[list[int]] = []
    for a, b in found:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def longest_copied(text: str, haystack: str) -> int:
    """The length of the longest stretch of `text` found verbatim in `haystack` (both in `match_form`)."""
    best = 0
    for i in range(len(text)):
        if len(text) - i <= best:
            break
        while i + best < len(text) and text[i:i + best + 1] in haystack:
            best += 1
    return best
