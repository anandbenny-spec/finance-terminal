"""#445 (R05-2): `statement_diff_summary` v2 (words taken from a statement go in quotation marks) and v3 (only the changed
words, and the cited sentence isn't repeated after them), the versions its targets keep, the re-run's input, and the
judge's measure. No database and no model: the runs are in test_ai_batch_task.py."""
from datetime import date

import pytest
from ai_fakes import cite
from policy_docs import FOMC_A, FOMC_B
from test_ai_summaries import reply

from ft.ai import summaries
from ft.ai.client import load_prompt
from ft.ai.summaries import Doc, check
from ft.engines.document.diff import diff_texts
from ft.eval import diff_quotes
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import PROMPT_VERSION, AiBatch, Rerun, Work, rerun_of

OLD = Doc(1, "fomc_statement", "FOMC statement", "FOMC statement, 16 Sep 2026", "Fed", date(2026, 9, 16), FOMC_A)
NEW = Doc(2, "fomc_statement", "FOMC statement", "FOMC statement, 28 Oct 2026", "Fed", date(2026, 10, 28), FOMC_B)
RULE = ("- When a bullet uses words from a document, put them in double quotation marks, and make each bullet read as a "
        "sentence on its own.")
RULE_V3 = ("- Quote only the words that changed, and keep every word you take from a document, even a whole sentence, "
           "inside the quotation marks. After describing a change, don't repeat the sentence it came from: the citation "
           "already points to it.")


def test_v2_is_v1_plus_the_quotation_mark_rule_and_v1_is_left_as_it_ran():
    v1, v2 = load_prompt("statement_diff_summary", "v1"), load_prompt("statement_diff_summary", "v2")
    assert (v1.version, v2.version) == ("v1", "v2") and "<!--" not in v2.text
    assert "quotation marks" not in v1.text
    one, two = v1.text.splitlines(), v2.text.splitlines()
    added = [line for line in two if line not in one]
    assert len(two) == len(one) + 1 and len(added) == 1 and added[0].startswith(RULE)
    assert [line for line in two if line != added[0]] == one        # nothing else changed, in the same order
    # The other tasks keep their versions (the card's "must not change").
    assert PROMPT_VERSION == {"doc_summary": "v1", "statement_diff_summary": "v13", "stance_extraction": "v2",
                              "projection_extraction": "v1"}
    assert ai_batch.PINNED_TASKS == ("statement_diff_summary",) and ai_batch.RERUN_TASK == "statement_diff_summary"


def test_v3_is_v2_plus_one_rule_and_v2_is_left_as_it_ran():
    """v2's dev sample (#445) quoted the changed words but often repeated the cited sentence after them without marks: v3
    adds one rule after v2's, and changes nothing else."""
    v2, v3 = load_prompt("statement_diff_summary", "v2"), load_prompt("statement_diff_summary", "v3")
    assert v3.version == "v3" and "<!--" not in v3.text
    two, three = v2.text.splitlines(), v3.text.splitlines()
    added = [line for line in three if line not in two]
    assert len(three) == len(two) + 1 and added == [RULE_V3]
    assert [line for line in three if line != RULE_V3] == two
    assert three.index(RULE_V3) == next(i for i, line in enumerate(two) if line.startswith(RULE)) + 1


def test_v4_sends_v3s_instructions_word_for_word():
    """v4 marks the outputs checked with our copies rule (option 1, test_diff_summary_copies.py): the model is asked
    exactly what v3 asked, and only the note at the top of the file differs."""
    v3, v4 = load_prompt("statement_diff_summary", "v3"), load_prompt("statement_diff_summary", "v4")
    assert v4.version == "v4" and v4.text == v3.text and "<!--" not in v4.text
    # v5 and v6 mark the outputs made with the copies rule's revisions (rounds 3 and 4): the same instructions again.
    for version in ("v5", "v6"):
        later = load_prompt("statement_diff_summary", version)
        assert later.version == version and later.text == v3.text and "<!--" not in later.text
    assert PROMPT_VERSION["statement_diff_summary"] == "v13" and ai_batch.COPIES_FROM == 4


def test_quoted_words_pass_the_checks_as_before():
    """The marks sit outside the cited text, so a bullet that quotes its changed sentence keeps its citation and its
    figures (the checks are unchanged)."""
    d = diff_texts(FOMC_A, FOMC_B)
    req = summaries.diff_summary_request(NEW, OLD, d.segments, load_prompt("statement_diff_summary", "v3"))
    old_changed, new_changed = summaries.changed_spans(d.segments)
    (ns, ne), (os_, oe) = new_changed[0], old_changed[0]
    blocks = [{"type": "text", "text": "- The new statement says \""},
              {"type": "text", "text": FOMC_B[ns:ne], "citations": [cite(FOMC_B, ns, ne, 1)]},
              {"type": "text", "text": "\"\n- The previous statement said \""},
              {"type": "text", "text": FOMC_A[os_:oe], "citations": [cite(FOMC_A, os_, oe, 0)]},
              {"type": "text", "text": "\""}]
    c = check("statement_diff_summary", reply(blocks), req.sources, changed={1: old_changed, 2: new_changed})
    assert c.ok, c.reasons()
    assert [b.text for b in c.kept] == [f'The new statement says "{FOMC_B[ns:ne]}"',
                                        f'The previous statement said "{FOMC_A[os_:oe]}"']


@pytest.mark.parametrize("value, workflow, want", [
    (None, "daily", None), ("", "daily", None), ("none", "watch", None), (" none ", "backfill", None),
    ("all", "backfill", Rerun()),
    ("fomc-2026-10-28, mpc-2026-10-07,fomc-2026-10-28", "backfill", Rerun(("fomc-2026-10-28", "mpc-2026-10-07"))),
])
def test_the_rerun_input_reads_none_all_or_meeting_slugs(value, workflow, want):
    env = {} if value is None else {ai_batch.RERUN_ENV: value}
    assert rerun_of(workflow, env) == want


@pytest.mark.parametrize("value, workflow, message", [
    ("all", "daily", "only a backfill"), ("fomc-2026-10-28", "refresh", "only a backfill"),
    ("all", "watch", "only a backfill"), ("FOMC 2026", "backfill", "meeting slugs"), (",", "backfill", "meeting slugs"),
    ("fomc-2026-10-28;drop", "backfill", "meeting slugs"),
])
def test_a_rerun_asked_of_another_workflow_or_with_a_bad_slug_stops_the_run(value, workflow, message):
    with pytest.raises(ValueError, match=message):
        rerun_of(workflow, {ai_batch.RERUN_ENV: value})


class PinDB:
    """Enough of a connection for `AiBatch._pin`: the answered requests' (document, version)."""

    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, args=None):
        self.sql.append(sql)
        rows = self.rows
        return type("Rows", (), {"fetchall": lambda self: rows})()


def test_a_target_keeps_the_version_it_was_answered_with_unless_the_run_reruns_it():
    works = [Work("statement_diff_summary", Doc(i, *list(NEW.__dict__.values())[1:]), OLD, {"pairs": []})
             for i in (10, 11, 12, 13, 14, 15)]
    works.append(Work("doc_summary", NEW))
    db = PinDB([(10, "v1"), (11, "v1"), (11, "v13"), (13, "v1"), (14, "v2"), (15, "v5")])
    t = AiBatch()
    t._rerun_docs = {13}
    t._pin(db, works)
    by = {w.document_id: w for w in works if w.task == "statement_diff_summary"}
    assert by[10].prompt.version == "v1" and by[10].prompt.text == load_prompt("statement_diff_summary", "v1").text
    assert by[11].prompt is None          # answered with v13 already: the current version
    assert by[14].prompt.version == "v2"  # answered with v2 (dev's first sample): keeps v2 until a re-run
    assert by[15].prompt.version == "v5"  # answered with v5 (dev's fourth sample): keeps v5 until a re-run
    assert by[12].prompt is None          # never answered (a new decision): the current version
    assert by[13].prompt is None          # re-run: the current version
    assert works[-1].prompt is None       # another task is never pinned
    # A pinned target's request is v1's, so its stored request is found and nothing is sent again.
    current = load_prompt("statement_diff_summary", PROMPT_VERSION["statement_diff_summary"])
    assert by[10].request(current).prompt.version == "v1" and by[12].request(current).prompt.version == "v13"
    # Stale and errored requests don't pin: the query keeps answered and waiting ones only.
    (sql,) = db.sql
    assert "status in ('pending', 'succeeded')" in sql and "not like 'stale:%%'" in sql


def test_the_measure_counts_copied_words_outside_quotation_marks():
    passage = "Economic activity is expanding at a solid pace. Job gains have remained low."
    v1 = "The description of economic activity changed from Economic activity is expanding at a solid pace. to strong."
    v2 = "The description of economic activity changed from “Economic activity is expanding at a solid pace.”"
    assert diff_quotes.unquoted_copies(v1, [passage]) == ["economic activity is expanding at a solid pace"]
    assert diff_quotes.unquoted_copies(v2, [passage]) == []
    assert diff_quotes.unquoted_copies("It said gains have remained low.", [passage]) == []    # four words: under five
    assert diff_quotes.unquoted_copies("It said gains have remained low.", [passage], min_words=4) == [
        "gains have remained low"]
    two = "It said Job gains have remained low and Economic activity is expanding at a solid pace."
    assert len(diff_quotes.unquoted_copies(two, [passage])) == 2
    out = {"output_id": 650, "bullets": [{"text": v1, "passages": [passage]}, {"text": v2, "passages": [passage]}]}
    assert diff_quotes.count(out) == 1


def test_the_measure_prints_ids_and_counts_only(tmp_path, capsys):
    import json
    f = tmp_path / "out.json"
    f.write_text(json.dumps([{"output_id": 7, "bullets": [{"text": "A B C D E F", "passages": ["A B C D E F"]}]}]))
    assert diff_quotes.main([str(f)]) == 0
    assert json.loads(capsys.readouterr().out) == {"7": 1}
