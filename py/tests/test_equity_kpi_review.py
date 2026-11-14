"""Unit tests for the KPI-table pilot's flag review (DD-05 part 2, #513): the Sonnet check (`ft.equity.kpi.check`), the
transcript quotes (`ft.equity.kpi.quotes`), rule 4's unit and measure kind, and the task's review, caps, answers kept for
the next run, evidence hash and flag write. Hand-written pages and transcripts; the AI through the fake Messages API."""
from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from ft.ai.client import load_prompt, load_schema
from ft.equity.kpi import check as kc
from ft.equity.kpi import dictionary as kdict
from ft.equity.kpi import filters as fl
from ft.equity.kpi import flags as kf
from ft.equity.kpi import quotes as kq
from ft.equity.kpi.documents import period_end


# ----------------------------------------------------------------------------------- helpers
def _doc(label, present, sha=b"\x01"):
    return kf.Doc(None, label, period_end(label), "press_release", "read", present, sha * 32)


def _flag(key="workforce", current_present=None, last_present=None, page=3):
    last = _doc("Q1 FY26", last_present or {key: kf.Reported(Decimal(613069), "people", page, (1,))})
    cur = _doc("Q2 FY26", current_present or {}, b"\x02")
    return kf.Flag(key, kf.MISSING, cur, [last], last, last.present[key])


METRIC = kdict.Metric("workforce", "Workforce", "people", ("workforce", "headcount"), (), ("people",))


# ----------------------------------------------------------------------------------- the check: what Sonnet reads
def test_excerpt_takes_the_lines_around_the_metric_and_the_whole_page_when_short():
    short = "Highlights\nWorkforce strength 6,13,069\n"
    assert kc.excerpt(short, "Workforce strength", Decimal(613069)) == short
    page = "\n".join([f"filler line {i} " + "x" * 60 for i in range(80)] + ["Workforce strength: 6,13,069"]
                     + [f"after {i} " + "y" * 60 for i in range(80)])
    got = kc.excerpt(page, "Workforce strength", Decimal(613069), limit=600)
    assert "Workforce strength: 6,13,069" in got and len(got) <= 600 and "after 0" in got and "filler line 79" in got
    assert kc.excerpt(page, "Nothing like it", None, limit=100) == page[:100]       # no line names it: the page's start


def test_pick_pages_neighbours_first_then_the_metrics_words_then_the_same_page():
    last_present = {"workforce": kf.Reported(Decimal(1), "people", 3, (1,)), "women": kf.Reported(Decimal(2), "%", 3, (2,)),
                    "attrition": kf.Reported(Decimal(3), "%", 3, (3,))}
    cur = {"women": kf.Reported(Decimal(2), "%", 5, (1,)), "attrition": kf.Reported(Decimal(3), "%", 5, (2,))}
    f = _flag(current_present=cur, last_present=last_present)
    pages = {2: kc.Page(2, "headcount was up"), 3: kc.Page(3, "other"), 5: kc.Page(5, "women attrition")}
    assert kc.pick_pages(f, METRIC, pages) == [5, 2]                               # neighbours' page, then the words
    lone = _flag(current_present={"x": kf.Reported(Decimal(1), "%", 9, (1,))})
    assert kc.pick_pages(lone, METRIC, {3: kc.Page(3, "nothing"), 4: kc.Page(4, "nothing")}) == [3]   # same page number
    assert kc.pick_pages(lone, METRIC, {7: kc.Page(7, "a"), 8: kc.Page(8, "b")}) == [7]
    assert kc.pick_pages(lone, METRIC, {}) == []


def test_groups_keep_to_ten_flags_and_four_pages():
    items = [kc.Item(_flag(key=f"k{i:02d}"), METRIC, "x", None, None, [1 + i % 2]) for i in range(12)]
    gs = kc.groups(items)
    assert [len(g) for g in gs] == [10, 2] and all(len({p for it in g for p in it.pages}) <= kc.MAX_PAGES for g in gs)
    wide = [kc.Item(_flag(key=f"w{i}"), METRIC, "x", None, None, [i * 2, i * 2 + 1]) for i in range(4)]
    assert [len(g) for g in kc.groups(wide)] == [2, 2]


def test_the_request_sends_current_pages_then_each_flags_previous_page():
    f = _flag()
    items = [kc.Item(f, METRIC, "Workforce strength", None, kc.Page(3, "Workforce strength 6,13,069"), [3, 4])]
    current = {3: kc.Page(3, "page three"), 4: kc.Page(4, image="aW1n")}
    req = kc.request("tcs", "press_release", items, current, load_prompt(kc.TASK), load_schema(kc.TASK))
    assert [s.title for s in req.sources] == ["Current press release (Q2 FY26), page 3",
                                              "Current press release (Q2 FY26), page 4",
                                              "Flag 1: previous press release (Q1 FY26), page 3"]
    assert req.sources[1].is_image and req.model_id == "claude-sonnet-5-5" and req.spec.route == "json"
    assert "printed as \"Workforce strength\"" in req.instruction and "613069 people" in req.instruction
    no_prev = kc.request("tcs", "press_release", [kc.Item(f, METRIC, "W", None, None, [3])], current,
                         load_prompt(kc.TASK), load_schema(kc.TASK))
    assert len(no_prev.sources) == 1 and "answer previous_value \"unsure\"" in no_prev.instruction


def test_parse_keeps_valid_answers_by_flag_number_only():
    got = kc.parse({"flags": [{"flag": 1, "present": "no", "previous_value": "yes", "reason_in_release": "no"},
                              {"flag": 1, "present": "yes", "previous_value": "yes", "reason_in_release": "no"},
                              {"flag": 2, "present": "maybe", "previous_value": "yes", "reason_in_release": "no"},
                              {"flag": 9, "present": "no", "previous_value": "yes", "reason_in_release": "no"},
                              "junk"]}, 3)
    assert set(got) == {1} and got[1] == kc.Verdict("no", "yes", "no")
    assert kc.parse({}, 2) == {} and kc.parse(None, 2) == {}


def test_a_verdict_drops_the_flag_only_on_yes_and_keeps_the_value_only_on_yes():
    assert kc.Verdict("yes", "yes", "no").outcome == kc.DROP_PRESENT
    assert kc.Verdict("no", "yes", "yes").outcome == kc.DROP_REASON
    assert kc.Verdict("unsure", "unsure", "unsure").outcome == kc.KEEP
    assert kc.Verdict("no", "no", "unsure").outcome == kc.KEEP
    assert kc.Verdict("no", "yes", "no").value_ok and not kc.Verdict("no", "unsure", "no").value_ok


# ----------------------------------------------------------------------------------- rule 4: the unit and the measure kind
def test_rule4_a_share_and_a_growth_rate_are_not_the_same_unit():
    tcs = kdict.load()["tcs"]
    assert tcs.metrics["attrition_ltm"].unit == tcs.metrics["cc_growth_qoq"].unit == "%"
    assert tcs.metrics["attrition_ltm"].kind != tcs.metrics["cc_growth_qoq"].kind
    assert tcs.metrics["workforce"].kind == ("people", ("people",))
    pad = {f"z{i}": kf.Reported(Decimal(1), "x", 3, (1,)) for i in range(9)}
    labels = ["Q2 FY25", "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26"]
    docs = [_doc(lab, {**pad, "attrition_ltm": kf.Reported(Decimal(13), "%", 3, (1,))}) for lab in labels[:4]]
    docs.append(_doc(labels[4], {**pad, "cc_growth_qoq": kf.Reported(Decimal(1), "%", 3, (1,))}))
    kinds = {**{k: ("count", ("count",)) for k in pad}, "attrition_ltm": tcs.metrics["attrition_ltm"].kind,
             "cc_growth_qoq": tcs.metrics["cc_growth_qoq"].kind}
    assert [(f.key, f.kind) for f in kf.grid(docs, kinds, "press_release").flags] == [("attrition_ltm", "missing")]
    same = {**kinds, "cc_growth_qoq": kinds["attrition_ltm"]}                  # the old reading: "%" alone
    assert kf.grid(docs, same, "press_release").flags[0].kind == "renamed_or_redefined"


# ----------------------------------------------------------------------------------- the quotes
TRANSCRIPT = ("Moderator: Welcome to the call.\n"
              "Mr. A, CEO: Our headcount stood steady this quarter. From this quarter we report workforce figures "
              "half-yearly. Deal wins were strong.\n"
              "Mr. B, Analyst: Thank you. Could you give the headcount number for the quarter? Also on deals.\n"
              "Mr. C, Unknown: Our workforce is great.\n")


def _turns(text=TRANSCRIPT):
    out, pos = [], 0
    for line, role in zip(text.splitlines(keepends=True), ("moderator", "management", "analyst", "unknown")):
        out.append(kq.Turn(pos, pos + len(line.rstrip("\n")), role))
        pos += len(line)
    return out


def test_candidates_are_sentences_of_the_role_that_name_the_metric():
    m = kq.candidates(TRANSCRIPT, _turns(), "management", ("workforce", "headcount"))
    assert [c.text for c in m] == ["Mr. A, CEO: Our headcount stood steady this quarter.",
                                   "From this quarter we report workforce figures half-yearly."]
    assert [c.id for c in m] == ["M1", "M2"]
    a = kq.candidates(TRANSCRIPT, _turns(), "analyst", ("workforce", "headcount"))
    assert [c.text for c in a] == ["Could you give the headcount number for the quarter?"] and a[0].id == "A1"
    assert kq.candidates(TRANSCRIPT, _turns(), "unknown", ("workforce",)) == []          # only the two roles' prefixes
    assert kq.candidates(TRANSCRIPT, _turns(), "management", ()) == []
    for c in m + a:
        assert TRANSCRIPT[c.start:c.end] == c.text


def test_a_term_matches_a_words_start_and_a_member_must_be_named_whole():
    text = "We filed 40 patents. The UK grew. Our ukulele unit grew.\n"
    turns = [kq.Turn(0, len(text) - 1, "management")]
    assert [c.text for c in kq.candidates(text, turns, "management", ("patent",))] == ["We filed 40 patents."]
    assert [c.text for c in kq.candidates(text, turns, "management", ("grew",), ("uk",))] == ["The UK grew."]


def test_a_candidate_is_cut_at_a_word_to_400_code_points_and_the_best_named_come_first():
    long = "Headcount " + "word " * 120 + "end."
    turns = [kq.Turn(0, len(long), "management")]
    c = kq.candidates(long, turns, "management", ("headcount",))[0]
    assert len(c.text) <= 400 and long.startswith(c.text) and not c.text.endswith(" ")
    many = " ".join(f"Headcount note {i}." for i in range(40)) + " Headcount and workforce both."
    got = kq.candidates(many, [kq.Turn(0, len(many), "management")], "management", ("headcount", "workforce"))
    assert len(got) == kq.MAX_CANDIDATES and got[-1].text == "Headcount and workforce both."   # kept, in text order


def test_parse_takes_ids_of_the_right_role_only():
    m = kq.candidates(TRANSCRIPT, _turns(), "management", ("workforce", "headcount"))
    a = kq.candidates(TRANSCRIPT, _turns(), "analyst", ("headcount",))
    assert kq.parse({"reason": "M2", "asked": "A1"}, m, a) == (m[1], a[0])
    assert kq.parse({"reason": "A1", "asked": "M1"}, m, a) == (None, None)
    assert kq.parse({"reason": "M9", "asked": None}, m, a) == (None, None)
    assert kq.parse({}, m, a) == (None, None)


def test_verify_rereads_the_quote_from_the_stored_text_with_its_page():
    m = kq.candidates(TRANSCRIPT, _turns(), "management", ("workforce",))
    pages = [(1, 0, 40), (2, 40, len(TRANSCRIPT))]
    q = kq.verify(TRANSCRIPT, m[-1], 77, pages)
    assert q == kq.Quote(77, m[-1].start, m[-1].end, "From this quarter we report workforce figures half-yearly.", 2)
    assert kq.verify(TRANSCRIPT, m[-1], 77, None) is None                        # no page map: no quote
    forged = kq.Candidate("M1", m[-1].start, m[-1].end, "We stopped reporting it because of attrition.")
    assert kq.verify(TRANSCRIPT, forged, 77, pages) is None                        # not the stored words
    assert kq.verify(TRANSCRIPT, None, 77, pages) is None


def test_the_d8_wording_says_found_and_never_a_motive():
    q = kq.Quote(1, 0, 5, "x", 1)
    assert kq.wording(None, None) == ("No reason found in the release or the call", "An analyst asked: no")
    assert kq.wording(q, q) == ("Reason stated: yes", "An analyst asked: yes")
    for line in (kq.REASON_YES, kq.REASON_NONE, kq.ASKED_YES, kq.ASKED_NO):
        assert "given" not in line and "because" not in line.lower()
    assert "found" in kq.REASON_NONE


def test_the_quote_request_lists_the_candidates_and_writes_no_text_of_its_own():
    m = kq.candidates(TRANSCRIPT, _turns(), "management", ("workforce", "headcount"))
    a = kq.candidates(TRANSCRIPT, _turns(), "analyst", ("headcount",))
    req = kq.request("tcs", "Q2 FY26", "Workforce", "missing", m, a, load_prompt(kq.TASK), load_schema(kq.TASK), 77)
    assert req.model_id == "claude-haiku-5-5" and req.sources[0].document_id == 77
    assert req.sources[0].text.splitlines()[0].startswith("M1: ") and "A1: Could you" in req.sources[0].text
    assert set(load_schema(kq.TASK)["properties"]) == {"reason", "asked"}


# ----------------------------------------------------------------------------------- the task's review
UTC = ZoneInfo("UTC")
LABELS = ["Q2 FY25", "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26"]
ROWS = [("Workforce strength", None, "people", "people"), ("LTM IT services attrition rate", "IT services", "percent", "%")] + [
    ("Industry Composition (%)", m, "percent", "%")
    for m in ("BFSI", "Consumer Business", "Manufacturing", "Life Sciences & Healthcare", "Technology & Services",
              "Communication & Media", "Energy, Resources and Utilities", "Regional Markets & Others")]


def _plan(t, label, rows, entity_id=7):
    plan = t.DocPlan(entity_id, "tcs", label, period_end(label), "press_release", "read", source_url="https://x/pr.pdf",
                     filing_key=f"k{label}", exchange="NSE", filed_at=datetime(2026, 1, 1, tzinfo=UTC), pages_used=[3],
                     page_count=9, sha256=bytes([LABELS.index(label)]) * 32, bytes=1)
    plan.values = [t.Value(i + 1, fl.Row(lab, mem, meas, Decimal(100 + i), unit, 3), Decimal(100 + i), fl.KEPT, None)
                   for i, (lab, mem, meas, unit) in enumerate(rows)]
    plan.texts = {3: "\n".join(f"{lab} {mem or ''} {100 + i}" for i, (lab, mem, _m, _u) in enumerate(rows))}
    return plan


def _harvest(t):
    h = t.Harvest(companies=[])
    for label in LABELS:
        if label == "Q4 FY25":
            h.plans.append(t.DocPlan(7, "tcs", label, period_end(label), "press_release", "held", held_reason="x"))
        else:
            h.plans.append(_plan(t, label, ROWS[2:] if label == "Q2 FY26" else ROWS))
    t.EquityKpiTables._flags(None, h)
    return h


def _answer(present: dict[str, str]):
    """A fake Sonnet: each flag's `present` by its metric's name in the instruction; previous value confirmed."""
    def answer(body):
        text = next(b["text"] for b in body["messages"][0]["content"] if b["type"] == "text" and b["text"].startswith("The current"))
        out = []
        for n, name in re.findall(r"Flag (\d+): the metric \"([^\"]+)\"", text):
            out.append({"flag": int(n), "present": present.get(name, "no"), "previous_value": "yes",
                        "reason_in_release": "no"})
        return {"flags": out}
    return answer


def _task(t, monkeypatch, answer, mode="all"):
    import ai_fakes
    fake = ai_fakes.FakeClaude(answer=answer)
    task = t.EquityKpiTables()
    task.claude = ai_fakes.claude(fake)
    monkeypatch.setenv("KPI_CHECK", mode)
    return task, fake


def test_the_review_drops_a_present_flag_and_keeps_the_other_with_its_confirmed_value(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    h = _harvest(t)
    assert {f.key for f in h.grids["tcs"].flags} == {"workforce", "attrition_ltm"}
    task, fake = _task(t, monkeypatch, _answer({"Attrition, LTM, IT services": "yes"}))
    r = task._review(None, h, Decimal(25), Decimal(0))
    assert r.mode == "all" and r.sent == {"kpi_check": 1} and len(fake.requests) == 1
    assert r.outcomes == {kc.DROP_PRESENT: 1, kc.KEEP: 1}
    [row] = r.rows
    assert row.flag.key == "workforce" and row.value == Decimal(100) and row.unit == "people"
    assert row.reason is None and row.asked is None and r.calls == {"none_stored": 1}
    assert r.k6 == {"workforce": "missing, keep", "attrition_ltm": "missing, present"}
    assert r.check_cost > 0 and len(r.answers) == 1
    checks = {c.name: c for c in task._review_checks(r)}
    detail = json.loads(checks["review"].detail)
    assert detail["flags"] == {"keep": 1, "present": 1} and detail["written"] == 1 and checks["review"].passed
    assert json.loads(checks["ai_answers"].detail)["answers"] == r.answers


def test_an_answer_from_the_last_run_is_used_again_and_never_sent(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    h = _harvest(t)
    task, _fake = _task(t, monkeypatch, _answer({}))
    first = task._review(None, h, Decimal(25), Decimal(0))
    monkeypatch.setattr(t.EquityKpiTables, "_memo", staticmethod(lambda db: dict(first.answers)))
    monkeypatch.setattr(t.EquityKpiTables, "_transcript", staticmethod(lambda db, e, label: None))
    task2, fake2 = _task(t, monkeypatch, _answer({"Workforce": "yes"}))
    second = task2._review(object(), _harvest(t), Decimal(25), Decimal(0))
    assert fake2.requests == [] and second.reused == {"kpi_check": 1} and second.check_cost == 0
    assert second.outcomes == first.outcomes == {kc.KEEP: 2}


def test_a_price_run_sends_nothing_writes_no_flag_and_keeps_the_last_answers(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    h = _harvest(t)
    task, fake = _task(t, monkeypatch, _answer({}), mode="price")
    r = task._review(None, h, Decimal(25), Decimal(0))
    assert fake.requests == [] and r.rows == [] and r.outcomes == {kc.UNCHECKED: 2}
    est = r.estimate["kpi_check"]
    assert est["requests"] == 1 and Decimal(0) < est["central"] < est["worst"]
    assert json.loads({c.name: c for c in task._review_checks(r)}["review"].detail)["estimate_usd"]["kpi_check"]["requests"] == 1
    monkeypatch.setenv("KPI_CHECK", "everything")                                  # anything else is a price run
    assert task._review(None, _harvest(t), Decimal(25), Decimal(0)).mode == "price"
    monkeypatch.delenv("KPI_CHECK")
    assert task._review(None, _harvest(t), Decimal(25), Decimal(0)).mode == "price"


def test_the_mode_comes_from_the_review_file_unless_the_environment_names_one(monkeypatch, tmp_path):
    from ft.jobs.tasks import equity_kpi_tables as t
    monkeypatch.delenv("KPI_CHECK", raising=False)
    assert t.review_mode() == "price"                                              # the file as merged: a dry run
    f = tmp_path / "r.yaml"
    monkeypatch.setattr(t, "REVIEW_FILE", f)
    f.write_text("mode: all\n")
    assert t.review_mode() == "all"
    f.write_text("mode: everything\n")
    assert t.review_mode() == "price"
    f.write_text(": not yaml : [\n")
    assert t.review_mode() == "price"
    monkeypatch.setattr(t, "REVIEW_FILE", tmp_path / "missing.yaml")
    assert t.review_mode() == "price"
    monkeypatch.setenv("KPI_CHECK", "all")
    assert t.review_mode() == "all"


def test_the_check_cap_stops_sending_and_the_flags_stand_unchecked_with_their_values_held(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    monkeypatch.setattr(t, "CHECK_LIMIT_USD", Decimal("0.0001"))
    h = _harvest(t)
    task, fake = _task(t, monkeypatch, _answer({"Workforce": "yes"}))
    r = task._review(None, h, Decimal(25), Decimal(0))
    assert fake.requests == [] and r.stopped == {"kpi_check": "check_cap"} and r.outcomes == {kc.UNCHECKED: 2}
    assert len(r.rows) == 2 and all(x.value is None and x.unit is None for x in r.rows) and r.value_held == 2
    assert not {c.name: c for c in task._review_checks(r)}["review"].passed


def test_the_monthly_switch_and_the_run_cap_stop_every_review_request(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    task, fake = _task(t, monkeypatch, _answer({}))
    r = task._review(None, _harvest(t), Decimal(1), Decimal(1))                   # the month is spent
    assert fake.requests == [] and r.stopped == {"all": "budget"}
    h = _harvest(t)
    h.cost = t.RUN_LIMIT_USD                                                       # the extraction took the run's cap
    assert task._review(None, h, Decimal(25), Decimal(0)).stopped == {"all": "run_cap"}


def test_quotes_are_asked_for_the_flags_that_stand_and_verified(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    h = _harvest(t)
    pages = [(1, 0, 40), (2, 40, len(TRANSCRIPT))]
    monkeypatch.setattr(t.EquityKpiTables, "_memo", staticmethod(lambda db: {}))
    monkeypatch.setattr(t.EquityKpiTables, "_transcript",
                        staticmethod(lambda db, e, label: (77, TRANSCRIPT, _turns(), pages, Decimal("0.2"))))

    def answer(body):
        if "reason" in body["output_config"]["format"]["schema"]["properties"]:
            return {"reason": "M2", "asked": "A1"}
        return _answer({"Attrition, LTM, IT services": "yes"})(body)
    task, _fake = _task(t, monkeypatch, answer)
    r = task._review(object(), h, Decimal(25), Decimal(0))
    [row] = r.rows
    assert row.reason.text == "From this quarter we report workforce figures half-yearly." and row.reason.page == 2
    assert row.asked.text == "Could you give the headcount number for the quarter?" and row.asked.document_id == 77
    assert r.sent == {"kpi_check": 1, "kpi_quote": 1} and r.quotes == {"reason_yes": 1, "asked_yes": 1}
    assert r.calls == {"searched": 1, "role_held_known_turns_only": 1} and r.quote_cost > 0


def test_a_document_not_read_this_run_leaves_its_flags_unchecked(monkeypatch):
    from ft.jobs.tasks import equity_kpi_tables as t
    h = _harvest(t)
    h.plan_of[("tcs", "Q2 FY26")].texts = {}                                      # kept as stored: no pages this run
    task, fake = _task(t, monkeypatch, _answer({}))
    r = task._review(None, h, Decimal(25), Decimal(0))
    assert fake.requests == [] and r.outcomes == {kc.UNCHECKED: 2} and len(r.rows) == 2


def test_the_evidence_hash_changes_with_what_the_flag_shows():
    from ft.jobs.tasks import equity_kpi_tables as t
    f = _flag()
    row = t.FlagRow(7, "tcs", f, METRIC, Decimal(613069), "people")
    base = t.evidence(row)
    assert len(base) == 32 and t.evidence(t.FlagRow(7, "tcs", f, METRIC, Decimal(613069), "people")) == base
    assert t.evidence(t.FlagRow(7, "tcs", f, METRIC, None, None)) != base
    assert t.evidence(t.FlagRow(7, "tcs", f, METRIC, Decimal(613069), "people", reason=kq.Quote(1, 2, 9, "x", 1))) != base
    other = _flag()
    other.current.sha256 = b"\x09" * 32
    assert t.evidence(t.FlagRow(7, "tcs", other, METRIC, Decimal(613069), "people")) != base


def test_the_memo_is_read_from_the_last_successful_runs_answers():
    from ft.jobs.tasks import equity_kpi_tables as t

    class Db:
        def __init__(self, checks):
            self.checks = checks

        def execute(self, sql, params):
            assert "status = 'success'" in sql and params == (t.KEY,)
            return type("R", (), {"fetchone": lambda _s: (self.checks,) if self.checks is not None else None})()
    got = [{"name": "review", "detail": "{}"}, {"name": "ai_answers", "detail": json.dumps({"answers": {"ab": {"x": 1}}})}]
    assert t.EquityKpiTables._memo(Db(got)) == {"ab": {"x": 1}}
    assert t.EquityKpiTables._memo(Db(None)) == {}
    assert t.EquityKpiTables._memo(Db([{"name": "ai_answers", "detail": "not json"}])) == {}


def test_the_transcript_is_read_whole_or_not_at_all():
    from ft.jobs.tasks import equity_kpi_tables as t

    class Db:
        def __init__(self, text):
            self.text = text

        def execute(self, sql, params):
            if "from equity.earnings_call" in sql:
                rows = [(77, len(TRANSCRIPT), Decimal("0.01"))]
            elif "from core.chunk" in sql:
                rows = [(self.text,)]
            elif "from equity.call_turn" in sql:
                assert "role in ('management','analyst')" in sql
                rows = [(0, 10, "management")]
            else:
                rows = [(1, 0, len(TRANSCRIPT))]
            return type("R", (), {"fetchone": lambda _s: rows[0], "fetchall": lambda _s: rows})()
    doc, text, turns, pages, share = t.EquityKpiTables._transcript(Db(TRANSCRIPT), 7, "Q2 FY26")
    assert (doc, text, turns, pages) == (77, TRANSCRIPT, [kq.Turn(0, 10, "management")], [(1, 0, len(TRANSCRIPT))])
    assert share == Decimal("0.01")
    assert t.EquityKpiTables._transcript(Db(TRANSCRIPT[:-1]), 7, "Q2 FY26") is None    # not whole: nothing read


def test_keep_pages_holds_text_pages_or_an_image_documents_file():
    from types import SimpleNamespace

    from ft.jobs.tasks import equity_kpi_tables as t
    plan = t.DocPlan(7, "tcs", "Q2 FY26", period_end("Q2 FY26"), "press_release", "read", pages_used=[2, 3])
    t.EquityKpiTables._keep_pages(plan, SimpleNamespace(page_texts=["one", "two", "three"]), b"%PDF")
    assert plan.texts == {2: "two", 3: "three"} and plan.body is None
    img = t.DocPlan(7, "hdfcbank", "Q2 FY26", period_end("Q2 FY26"), "press_release", "read", pages_used=[1, 2],
                    image_page_count=2)
    t.EquityKpiTables._keep_pages(img, SimpleNamespace(page_texts=["", ""]), b"%PDF")
    assert img.body == b"%PDF" and img.texts == {}
    assert t.EquityKpiTables._check_pages(img) == {1: kc.Page(1), 2: kc.Page(2)}


# ----------------------------------------------------------------------------------- the write (database)
@pytest.mark.db
def test_the_flags_are_written_with_their_documents_and_cleared_before_a_document_is_replaced(monkeypatch):
    import psycopg
    from conftest import dsn

    from ft.jobs.tasks import equity_kpi_tables as t
    admin = dsn("postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("delete from equity.kpi_change where entity_id in (select id from core.entity where slug like 'zf-%')")
        c.execute("delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zf-%'))")
        c.execute("delete from equity.kpi_document where entity_id in (select id from core.entity where slug like 'zf-%')")
        c.execute("delete from core.entity where slug like 'zf-%'")
        eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                           values ('company','zf-tcs','ZF TCS','IN','INR',true) returning id""").fetchone()[0]
    h = _harvest(t)
    for p in h.plans:
        p.entity_id = eid
    task, _fake = _task(t, monkeypatch, _answer({"Attrition, LTM, IT services": "yes"}))
    h.review = task._review(None, h, Decimal(25), Decimal(0))
    q = """select dict_key, kind, fiscal_label, metric_label, last_value, last_value_unit, last_value_quarter, reason_stated,
                  analyst_asked, rule_version, cardinality(previous_documents), length(evidence_sha256),
                  current_document_id = (select id from equity.kpi_document d where d.entity_id = c.entity_id
                                          and d.fiscal_label = 'Q2 FY26')
             from equity.kpi_change c where entity_id = %s"""
    with psycopg.connect(admin) as c, c.transaction():
        n = task.load(c, h, task_run_id=_run(c))
        assert c.execute(q, (eid,)).fetchall() == [("workforce", "missing", "Q2 FY26", "Workforce", Decimal(100), "people",
                                                    "Q1 FY26", False, False, 1, 3, 32, True)]
        assert n >= 1
    # a re-run that re-reads (replaces) a flagged document: the flags go first, so the FK never blocks the replace
    h2 = _harvest(t)
    for p in h2.plans:
        p.entity_id = eid
    monkeypatch.setenv("KPI_CHECK", "price")
    h2.review = task._review(None, h2, Decimal(25), Decimal(0))
    with psycopg.connect(admin) as c, c.transaction():
        task.load(c, h2, task_run_id=_run(c))
        assert c.execute(q, (eid,)).fetchall() == []                               # a price run writes none
    # a price run keeps the flags unless it replaces a document they rest on: here it re-reads only the held quarter
    with psycopg.connect(admin) as c, c.transaction():
        h3 = _harvest(t)
        for p in h3.plans:
            p.entity_id, p.reused = eid, p.status == "read"
        h3.review = h.review                                                       # mode all: written again
        task.load(c, h3, task_run_id=_run(c))
        assert len(c.execute(q, (eid,)).fetchall()) == 1
        h3.review = t.Review("price")
        task.load(c, h3, task_run_id=_run(c))                                      # Q4 FY25 (held) replaced: no flag rests on it
        assert len(c.execute(q, (eid,)).fetchall()) == 1
        h3.plans[-1].reused = False                                                # Q2 FY26, the flag's current document
        task.load(c, h3, task_run_id=_run(c))
        assert c.execute(q, (eid,)).fetchall() == []
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("delete from equity.kpi_change where entity_id = %s", (eid,))
        c.execute("delete from equity.kpi_value where document_id in (select id from equity.kpi_document where entity_id = %s)", (eid,))
        c.execute("delete from equity.kpi_document where entity_id = %s", (eid,))
        c.execute("delete from core.entity where id = %s", (eid,))


def _run(c) -> int:
    jr = c.execute("insert into ops.job_run (workflow, trigger, runner) values ('backfill', 'cli', 'local') returning id").fetchone()[0]
    # a seeded task key (the test database's ops.task holds the reference seeds, not tasks.yaml): only the cost is updated
    return c.execute("insert into ops.task_run (job_run_id, task_key) values (%s, 'policy.fed_documents') returning id",
                     (jr,)).fetchone()[0]

