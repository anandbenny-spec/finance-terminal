"""#445, round 6 (Anand's decision of 12 Nov 2026, 22:36 IST, after the judge failed round 5): `statement_diff_summary` v7 is
v6's text plus a paragraph on what to cover and four rules, it runs on Sonnet 5.5, and from it every quotation in a bullet
must be found in that bullet's own cited passages (`summaries.quote_problem`, a tightening). A target pinned to v1 to v6 is
asked and checked exactly as before. Round 7: v8 sends v7's text on Sonnet, and from it only a copy that stands as a whole
sentence comes out (the rest are quoted) and a bullet that repeats 12 or more of its own words fails. No database and no
model: the runs are in test_ai_batch_task.py."""
import pytest
from ai_fakes import cite
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import NEW, OLD, reply
from test_diff_summary_copies import FRONT_PAGE

from ft.ai import summaries
from ft.ai.client import HAIKU, SONNET, load_prompt
from ft.ai.summaries import check, end_problem, quote_problem, repeat_problem
from ft.engines.document.diff import diff_texts
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION, Work
from ft.quality import reasons

D = diff_texts(FOMC_A, FOMC_B)
OLD_CHANGED, NEW_CHANGED = summaries.changed_spans(D.segments)
CHANGED = {1: OLD_CHANGED, 2: NEW_CHANGED}
A_RISK, A_RATE = (318, 374), (451, 580)                 # changed in the old statement (test_diff_summary_copies.py)
B_RISKS, B_RATE = (326, 456), (458, 654)                # changed in the new one
V7 = load_prompt("statement_diff_summary", "v7")
REQ = summaries.diff_summary_request(NEW, OLD, D.segments, V7)
COVER = ("Before you write, go through the list of changes. Don't leave out a change in the policy decision or the policy "
         "rates, in the stance or the guidance about future policy, in the projections, or a member's dissent, and count a "
         "sentence that was added or removed as a change too, described by its key words. You have at most five bullets: "
         "put two closely related changes in one bullet rather than leave one of these out. A change only in which members "
         "vote, with no change in how they voted, matters least.")
RULES = [
    ("- Every word you put in quotation marks must come from a passage that the same bullet cites. So when a bullet says "
     "what the previous statement said (wording that was removed, or the earlier side of a change from one wording to "
     "another), it cites that sentence in document 1, and it cites the new wording in document 2."),
    ("- Write each bullet as one or two complete sentences of your own, with the quotations inside them and joined to them "
     "by your own words: never two quotations side by side, and never a quotation standing on its own after a full stop, "
     "a colon or a semicolon."),
    ("- Name members in plain words (\"Lisa D. Cook and Beth M. Hammack\"), never with a list's semicolons. Don't use \"...\" "
     "to shorten a quotation, and don't change or add words inside quotation marks: quote a shorter stretch instead."),
    ("- Say each change once."),
]


def old(span):
    s, e = span
    return cite(FOMC_A, s, e, 0)


def new(span):
    s, e = span
    return cite(FOMC_B, s, e, 1)


def bullet(text, *cites, first=False):
    return [{"type": "text", "text": ("" if first else "\n") + "- "}, {"type": "text", "text": text, "citations": list(cites)}]


DECISION = bullet('The decision changed from "maintain" to "lower" the target range.', old(A_RATE), new(B_RATE))


def diff_check(blocks, check_quotes=True):
    return check("statement_diff_summary", reply(blocks), REQ.sources, changed=CHANGED, drop_copies=True,
                 check_quotes=check_quotes)


def own(text):
    return {"type": "text", "text": text}


def copy_of(doc, idx, span, *, stop=True):
    """A cited block copying its cited sentence word for word (`stop=False`: without its full stop)."""
    s, e = span
    t = doc[s:e] if stop else doc[s:e].rstrip(".")
    return {"type": "text", "text": t, "citations": [cite(doc, s, e, idx)]}


def at(version, blocks):
    w = Work("statement_diff_summary", NEW, OLD, D.segments)
    return w.check(w.request(load_prompt("statement_diff_summary", version)), reply(blocks))


# ---------------------------------------------------------------------------------------------- the prompt and the model
def test_v7_is_v6_plus_a_paragraph_on_coverage_and_four_rules_and_v6_is_left_as_it_ran():
    v6 = load_prompt("statement_diff_summary", "v6")
    assert V7.version == "v7" and "<!--" not in V7.text
    six, seven = v6.text.splitlines(), V7.text.splitlines()
    assert [line for line in seven if line not in six] == [COVER, *RULES]
    i = seven.index(COVER)
    assert seven[i - 1] == seven[i + 1] == ""          # its own paragraph
    assert [x for x in seven[:i] + seven[i + 2:] if x not in RULES] == six    # every v6 line kept, in the same order
    assert seven.index(COVER) == next(i for i, x in enumerate(seven) if x.startswith("Write bullets on the changes")) + 2
    rule = next(i for i, x in enumerate(seven) if x.startswith("- Every bullet must cite the changed sentences"))
    assert seven[rule + 1:rule + 1 + len(RULES)] == RULES
    assert ai_batch.QUOTES_FROM == 7 and ai_batch.COPIES_FROM == 4


def test_v7_runs_on_sonnet_with_the_tasks_settings_and_every_earlier_version_on_haiku():
    assert REQ.model_id == SONNET and summaries.DIFF_MODEL_OF == {"v7": SONNET, "v8": SONNET, "v9": SONNET, "v10": SONNET}
    assert (REQ.spec.route, REQ.spec.effort, REQ.spec.max_tokens) == ("citations", "low", 8_000)
    for version in ("v1", "v2", "v3", "v4", "v5", "v6"):
        req = summaries.diff_summary_request(NEW, OLD, D.segments, load_prompt("statement_diff_summary", version))
        assert req.model_id == HAIKU, version
    # doc_summary is untouched
    assert summaries.doc_summary_request(NEW, load_prompt("doc_summary", "v1")).model_id == HAIKU
    # A pinned target's request names its own version's model (`Work.request` with a pinned prompt).
    w = Work("statement_diff_summary", NEW, OLD, D.segments, prompt=load_prompt("statement_diff_summary", "v6"))
    assert w.request(V7).model_id == HAIKU and Work("statement_diff_summary", NEW, OLD, D.segments).request(
        V7).model_id == SONNET


# ---------------------------------------------------------------------------------------------- quote_problem
PASSAGE = "Recent indicators suggest that economic activity has been expanding at a solid pace."
SECOND = "Job gains have remained low, and the unemployment rate has been little changed in recent months."


@pytest.mark.parametrize("text", [
    'Growth was described as "expanding at a solid pace".',
    'Growth was described as "Expanding at a solid pace."',                 # case aside; punctuation inside the mark
    "Growth was described as “expanding at a solid pace,” with jobs “remained low”.",
    'The statement said activity "has been expanding ... solid pace".',      # an elision: the pieces in order
    'The statement said "the unemployment rate has been little change[d]".',  # a bracketed insertion
    'The statement read "solid pace. Job gains have remained low".',         # across the two passages, joined in order
    "No quotation at all.",
])
def test_quotations_from_the_cited_passages_pass(text):
    assert quote_problem(text, [PASSAGE, SECOND]) is None


@pytest.mark.parametrize("text, want", [
    ('Growth changed to "strong".', '"strong"'),
    ('It said "expanding at a solid p".', '"expanding at a solid p"'),         # whole words: "p" isn't "pace"
    ('It said "solid pace ... remained high".', '"solid pace ... remained high"'),
    ('It said "low, and the unemployment" then "pace. Recent".', '"pace. Recent"'),   # the joined passages, in order only
])
def test_quotations_not_in_the_cited_passages_fail_and_are_named(text, want):
    got = quote_problem(text, [PASSAGE, SECOND])
    assert got is not None and got.startswith("quoted words not in its cited passages: ") and want in got


def test_an_unclosed_quotation_mark_fails():
    assert quote_problem('Growth was described as "expanding at a solid pace.', [PASSAGE]) == "an unclosed quotation mark"


# ---------------------------------------------------------------------------------------------- in the checks
def test_a_bullet_quoting_the_previous_statement_without_citing_it_is_dropped_from_v7_on():
    """Round 5's rule (b) fails (dev's 2474, bullets 2 and 4; 2481, bullet 3): words quoted from the previous statement in
    a bullet that cites only the new one."""
    uncited = bullet('The statement dropped "Uncertainty about the economic outlook remains elevated" and now says the '
                     'Committee "judges that downside risks to employment have risen".', new(B_RISKS), first=True)
    c = diff_check(uncited + DECISION)
    assert not c.ok and [b.problem for b in c.bullets][1] is None
    assert c.bullets[0].problem == ('quoted words not in its cited passages: '
                                    '"Uncertainty about the economic outlook remains elevated"')
    assert "quoted words not in its cited passages" in c.note()          # the retry is told which words
    # The reason lands in the accuracy page's citation category.
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("citation", True)
    # Citing the previous statement's sentence too, the same bullet passes.
    cited = bullet(uncited[1]["text"], new(B_RISKS), old(A_RISK), first=True)
    assert diff_check(cited + DECISION).ok
    # A v6 reply (pinned) is checked as before: no quotations check.
    assert diff_check(uncited + DECISION, check_quotes=False).ok


def test_an_unclosed_quotation_mark_is_a_format_reason():
    c = diff_check(bullet('The decision changed from "maintain to "lower" the target range.', old(A_RATE), new(B_RATE),
                          first=True) + DECISION)
    assert c.bullets[0].problem == "an unclosed quotation mark"
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("format", True)


def test_work_checks_quotations_from_v7_only():
    uncited = bullet('The statement dropped "Uncertainty about the economic outlook remains elevated".', new(B_RISKS),
                     first=True)
    w = Work("statement_diff_summary", NEW, OLD, D.segments)
    for version, ok in (("v4", True), ("v5", True), ("v6", True), ("v7", False)):
        req = w.request(load_prompt("statement_diff_summary", version))
        assert w.check(req, reply(uncited + DECISION)).ok is ok, version


def test_the_copies_rule_still_applies_at_v7_and_its_quoted_copies_pass():
    """The front page's shape (test_diff_summary_copies.py): its copies come out at v7 as at v6. Its second bullet quotes
    "maintain" from the previous statement but cites only the new one, so v7 drops it; citing the previous statement's
    sentence too, v7 keeps the same bullets as v6."""
    w = Work("statement_diff_summary", NEW, OLD, D.segments)
    v6, v7 = (w.request(load_prompt("statement_diff_summary", v)) for v in ("v6", "v7"))
    six = w.check(v6, reply(FRONT_PAGE))
    assert six.ok, six.reasons()
    seven = w.check(v7, reply(FRONT_PAGE))
    assert seven.reasons() == ['bullet 2: quoted words not in its cited passages: "maintain"']
    fixed = [*FRONT_PAGE[:-1], {**FRONT_PAGE[-1], "citations": [old(A_RATE), new(B_RATE)]}]
    seven = w.check(v7, reply(fixed))
    assert seven.ok, seven.reasons()
    assert ([b.text for b in seven.kept], dict(seven.copies)) == ([b.text for b in six.kept], dict(six.copies))
    # A copy the rule puts in quotation marks (no sentence of the model's own left) is in its cited passage.
    c = diff_check([{"type": "text", "text": "- Revised: "},
                    {"type": "text", "text": FOMC_B[slice(*B_RATE)], "citations": [new(B_RATE)]}, *DECISION])
    assert c.ok, c.reasons()
    assert c.kept[0].text == f'Revised: "{FOMC_B[slice(*B_RATE)]}"' and c.copies["quoted"] == 1


# ---------------------------------------------------------------------------------------------- v8 (round 7)
A_GROWTH, B_GROWTH = (0, 73), (0, 81)
DECISION_COPIES = [own('\n- The decision changed from "maintain" to "lower". '), copy_of(FOMC_A, 0, A_RATE), own(" "),
                   copy_of(FOMC_B, 1, B_RATE)]


def test_v8_sends_v7s_text_on_sonnet():
    v8 = load_prompt("statement_diff_summary", "v8")
    assert v8.text == V7.text and "<!--" not in v8.text
    assert summaries.diff_summary_request(NEW, OLD, D.segments, v8).model_id == SONNET
    assert (ai_batch.QUOTES_FROM, ai_batch.STRICT_COPIES_FROM, ai_batch.REPEATS_FROM) == (7, 8, 8)


def test_from_v8_a_copy_inside_the_models_sentence_is_quoted_not_cut():
    """Regression (round 6, v7 on Sonnet: dev's 2491, 2485, 2490, 2484 and 2486): Sonnet wrote its cited blocks as
    fragments inside its own sentences, and the looser rule took them out, leaving "…says the Committee."."""
    fragment = FOMC_B[slice(*B_RISKS)][len("The Committee "):].rstrip(".")
    blocks = [own("- The new wording says the Committee "),
              {"type": "text", "text": fragment, "citations": [new(B_RISKS)]}, own("."), *DECISION_COPIES]
    v7 = at("v7", blocks)
    assert v7.ok and v7.kept[0].text == "The new wording says the Committee."              # the round 6 bug, pinned
    v8 = at("v8", blocks)
    assert v8.ok, v8.reasons()
    assert v8.kept[0].text == f'The new wording says the Committee "{fragment}".'
    assert v8.kept[1].text == 'The decision changed from "maintain" to "lower".'           # whole sentences still go
    assert dict(v8.copies) == {"quoted": 1, "removed": 2}
    # Followed by a comma, the sentence goes on: the copy is quoted too.
    blocks = [own("- The previous statement said growth "), copy_of(FOMC_A, 0, A_GROWTH, stop=False),
              own(', while the new one says it "has moderated further".'), *DECISION_COPIES]
    blocks[1] = {**blocks[1], "text": FOMC_A[slice(*A_GROWTH)].rstrip(".")[len("Recent indicators suggest that growth "):]}
    c = at("v8", [*blocks[:2], {**blocks[2], "citations": [new(B_GROWTH)]}, *blocks[3:]])
    assert c.ok, c.reasons()
    assert c.kept[0].text.startswith('The previous statement said growth "of economic activity has moderated", while')


def test_from_v8_whole_sentence_copies_still_come_out_as_before():
    blocks = [own('- Growth changed from "has moderated" to "has moderated further". '), copy_of(FOMC_A, 0, A_GROWTH),
              own(" ... "), copy_of(FOMC_B, 1, B_GROWTH), *DECISION_COPIES]
    v7, v8 = at("v7", blocks), at("v8", blocks)
    assert v8.ok and [b.text for b in v8.kept] == [b.text for b in v7.kept] == [
        'Growth changed from "has moderated" to "has moderated further".',
        'The decision changed from "maintain" to "lower".']


@pytest.mark.parametrize("version", ["v4", "v6", "v7", "v8"])
def test_a_removed_copy_closed_by_the_models_own_full_stop_leaves_one_full_stop(version):
    """Regression (round 7's trial): "…further". <copy without its stop>." read "…further".." once the copy came out."""
    blocks = [own('- Growth changed from "has moderated" to "has moderated further". '),
              copy_of(FOMC_B, 1, B_GROWTH, stop=False), own("."), *DECISION_COPIES]
    c = at(version, blocks)
    assert c.ok, c.reasons()
    assert c.kept[0].text == 'Growth changed from "has moderated" to "has moderated further".'


SENTENCE = "The Committee decided to lower the target range for the federal funds rate this time."


def test_repeat_problem_finds_twelve_of_its_own_words_twice():
    assert repeat_problem(f"{SENTENCE} {SENTENCE}").startswith("repeats its own words: ")
    assert repeat_problem(SENTENCE) is None
    eleven = "one two three four five six seven eight nine ten eleven"
    assert repeat_problem(f"{eleven}, then {eleven}.") is None
    names = "Dr. Ashima Goyal and Prof. Jayanth R. Varma voted"                 # round 6's 2490: 9 words twice
    assert repeat_problem(f'"{names} to reduce the policy repo rate", and "{names} for a change in stance"') is None


def test_from_v8_a_bullet_that_repeats_itself_is_dropped():
    """Round 6's retry for dev's 2492 wrote its first sentence twice."""
    text = ('The decision changed from "maintain" to "lower" the target range for the federal funds rate this time. '
            'The decision changed from "maintain" to "lower" the target range for the federal funds rate this time.')
    blocks = bullet(text, old(A_RATE), new(B_RATE), first=True) + DECISION
    assert at("v7", blocks).ok
    c = at("v8", blocks)
    assert c.bullets[0].problem.startswith("repeats its own words: ") and c.bullets[1].problem is None
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("format", True)


# ---------------------------------------------------------------------------------------------- v9 (round 8)
RULES_V9 = [
    "- Keep each bullet under 60 words.",
    ("- Each quotation must fit the grammar of your own sentence. If the statement's words don't fit, quote fewer of them "
     "or word your sentence differently (write that inflation \"rose\", not that it was described as having \"rose\")."),
    "- Don't give a bullet to a change only in a date, a title or which members attended.",
]


def test_v9_is_v8_plus_three_rules_after_say_each_change_once():
    """Round 7 (v8 on dev): a quotation bent into the sentence's grammar ("described as having "rose to 7.0 per cent"",
    dev's 2497), a five-sentence bullet about a date (2502), the same change stated twice (2497)."""
    v8, v9 = load_prompt("statement_diff_summary", "v8"), load_prompt("statement_diff_summary", "v9")
    assert v9.version == "v9" and "<!--" not in v9.text
    eight, nine = v8.text.splitlines(), v9.text.splitlines()
    assert [x for x in nine if x not in eight] == RULES_V9
    assert [x for x in nine if x not in RULES_V9] == eight                    # every v8 line kept, in the same order
    assert nine[nine.index("- Say each change once.") + 1:][:3] == RULES_V9
    assert summaries.diff_summary_request(NEW, OLD, D.segments, v9).model_id == SONNET
    # The strict copies rule and the repeats check hold for v9 too.
    text = f"{SENTENCE} {SENTENCE}"
    assert at("v9", bullet(text, old(A_RATE), new(B_RATE), first=True) + DECISION).bullets[0].problem.startswith(
        "repeats its own words: ")


# ---------------------------------------------------------------------------------------------- v10 (round 9)
CARRIER_RULE = ("- Put each citation on the words of yours that use the cited passage. Never write a sentence, a phrase or a "
                "label only to hold a citation, such as a sentence saying that the earlier wording was replaced by the new "
                "wording.")


def test_v10_is_v9_plus_the_carrier_rule_on_sonnet_at_effort_medium_and_is_current():
    """Round 8 (v9 on dev): Sonnet wrote words only to hold a citation ("Previous new", dev's 2510; "The earlier figure was
    replaced by the new figure.", 2508)."""
    v9, v10 = load_prompt("statement_diff_summary", "v9"), load_prompt("statement_diff_summary", "v10")
    nine, ten = v9.text.splitlines(), v10.text.splitlines()
    assert [x for x in ten if x not in nine] == [CARRIER_RULE]
    assert [x for x in ten if x != CARRIER_RULE] == nine
    assert ten[ten.index(RULES_V9[-1]) + 1] == CARRIER_RULE
    req = summaries.diff_summary_request(NEW, OLD, D.segments, v10)
    assert (req.model_id, req.spec.effort, req.spec.route) == (SONNET, "medium", "citations")
    assert summaries.diff_summary_request(NEW, OLD, D.segments, v9).spec.effort == "low"     # v9 ran at the task's
    assert summaries.DIFF_EFFORT_OF == {"v10": "medium"} and ai_batch.ENDS_FROM == 10
    assert PROMPT_VERSION["statement_diff_summary"] == "v10"


@pytest.mark.parametrize("text, ok", [
    ("The decision changed.", True),
    ('The decision changed from "maintain" to "lower".', True),
    ('The statement now says "Inflation remains somewhat elevated."', True),
    ("Growth slowed (in the third quarter.)", True),
    ("Did the decision change?", True),
    ('The decision changed from "maintain" to "lower". Previous new', False),     # round 8's 2510
    ("The decision changed", False),
    ("The decision changed:", False),
])
def test_end_problem(text, ok):
    assert (end_problem(text) is None) is ok


def test_from_v10_a_bullet_with_words_after_its_last_sentence_is_dropped():
    blocks = bullet('The decision changed from "maintain" to "lower" the target range.', old(A_RATE), new(B_RATE),
                    first=True) + [{"type": "text", "text": " Previous", "citations": [old(A_RATE)]},
                                   {"type": "text", "text": " new", "citations": [new(B_RATE)]}] + DECISION
    assert at("v9", blocks).ok
    c = at("v10", blocks)
    assert c.bullets[0].problem == "doesn't end as a sentence ends" and c.bullets[1].problem is None
    assert reasons.categorise("rejected", "; ".join(c.reasons())) == ("format", True)
