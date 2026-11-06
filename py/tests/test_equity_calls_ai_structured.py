"""equity.calls_ai with the call summary on the structured route (prompt v6; 2B-11c, #301; Anand's decision of 6 Nov 2026) through the
runner and the Batch API path against the test database as job_writer. The reply is the schema's JSON (bullets with their own
text and the quotes that state their point); the code finds each quote exactly once in the stored transcript and stores that span
as the citation. The route's checks themselves are in test_ai_call_summary_structured.py (no database); here: the request as it is
sent, what is stored, the retry, a rejection that leaves the earlier version published, the supersession, the copied-text rule on a
fresh summary and the independent read-back. The helpers are test_equity_calls_ai_task.py's (whose own tests pin the summary to
v5, the citations route: its `citations_route` fixture is deliberately not imported here)."""
import json
from decimal import Decimal

import psycopg
import pytest
from ai_fakes import FakeClaude, schema_of
from call_fixtures import GOOD_ANALYSTS, GOOD_MANAGEMENT
from test_ai_call_summary_structured import answer as structured
from test_equity_calls_ai_task import (
    CAPEX_T,
    CLEAN,
    JOB,
    KEY,
    KNOWN_ANALYSTS,
    LEAD,
    MEERA,
    REV_S,
    THIRD,
    Ticker,
    alert,
    answers,
    as_anon,
    citations_of,
    go,
    gx,
    last_run,
    legacy,
    make_call,
    none_rejected,
    outputs,
    published_summary,
    q,
    requests,
    task,
    wipe,
)

from ft.ai import call_summary as cs
from ft.ai.client import load_prompt

PROMPT = load_prompt("call_summary", "v6")
REV = GOOD_MANAGEMENT[0][1][0]
STAMPED_V5 = {"version": "v5", "stamp": cs.ECHO_RULE}          # a published v5 summary that the copied-text re-check leaves alone


@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '2B-11c test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


@pytest.fixture(scope="module", autouse=True)
def clean():
    wipe()
    yield
    wipe()


def steps(*replies):
    """FakeClaude's `answer` for the summary: the replies in order, the last repeating."""
    left = list(replies)
    return lambda body: left.pop(0) if len(left) > 1 else left[0]


def both(summary, guidance):
    """FakeClaude's `answer` when the extraction rides the batch too: each request gets the answer for its schema."""
    return lambda body: summary(body) if "bullets" in schema_of(body)["properties"] else guidance(body)


def bad_quote():
    """A reply whose first management bullet quotes a passage the transcript doesn't hold (a number changed)."""
    wrong = [("Revenue grew.", [REV.replace("grew", "rose")])] + GOOD_MANAGEMENT[1:]
    return structured(management=wrong)


def summary_of(doc_id: int):
    return q("""select id, status, prompt_version, content from core.ai_output where document_id = %s and task = 'call_summary'
                 order by id""", doc_id)


@pytest.mark.db
def test_a_stored_call_gets_a_summary_on_the_structured_route_and_each_quote_is_stored_as_the_span_it_was_found_at():
    c = make_call()
    good = structured()
    fake, tick = FakeClaude(["ok"], answer=steps(good), ends_after=2), Ticker()
    assert go(task([c["slug"]], fake, tick)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 1 and tick.sleeps == [30.0, 60.0]
    ((_, b),) = fake.batches.items()
    (sent,) = b["requests"]
    params = sent["params"]
    assert params["system"].startswith(PROMPT.text[:60]) and "cache_control" not in params
    assert params["output_config"]["effort"] == "low" and params["output_config"]["format"]["type"] == "json_schema"
    assert list(params["output_config"]["format"]["schema"]["properties"]) == ["bullets"]
    block, asked = params["messages"][0]["content"][:2]
    assert block["type"] == "document" and "citations" not in block                       # no Citations API on this route
    assert "[management · Ravi Rao]\nRavi Rao: Thank you." in block["source"]["data"] and "[moderator]" in block["source"]["data"]
    assert "- Management: Ravi Rao; Meera Iyer" in asked["text"] and "- Analysts: Anil Shah; Pooja Menon" in asked["text"]
    # one published output, the model's own sentences, and none of its copies of the quotes
    (o,) = outputs(c["doc"])
    assert o[:3] == ("published", "", 5) and o[4] == "v6" and o[3] > 0
    ((_, _, _, content),) = summary_of(c["doc"])
    assert [x["list"] for x in content["bullets"]] == ["management"] * 3 + ["analysts"] * 2
    assert [x["text"] for x in content["bullets"]] == [t for t, _ in GOOD_MANAGEMENT + GOOD_ANALYSTS]
    assert content["echo_rule"] == cs.ECHO_RULE and content["citation_mapping"] == {"search": 7}
    assert [x["citations"] for x in content["bullets"]] == [[1], [2], [3], [4, 5], [6, 7]]
    assert "quotes" not in json.dumps(content)
    # seven citations, each a verified span of the stored transcript that reads as the quote it was found for
    quotes = [qt for x in good["bullets"] for qt in x["quotes"]]
    cites = q("""select claim_path, kind, verified, char_start, char_end, char_end <= (select char_count from core.document
                                                                                      where id = %s)
                   from core.ai_citation where ai_output_id = (select id from core.ai_output where document_id = %s
                                                                  and task = 'call_summary')
                  order by citation_no""", c["doc"], c["doc"])
    assert len(cites) == 7 and all(k == "document_span" and v and inside for _, k, v, _, _, inside in cites)
    assert [c["built"].text[a:z] for _, _, _, a, z, _ in cites] == quotes
    assert [p for p, *_ in cites] == ["/bullets/0", "/bullets/1", "/bullets/2", "/bullets/3", "/bullets/3", "/bullets/4",
                                      "/bullets/4"]
    assert requests(c["doc"]) == [("succeeded", "", True, True)]
    assert r["cost"] == o[3].quantize(Decimal("0.0001"))
    ck = r["checks"]
    assert all(ck[n]["passed"] for n in ("outputs_stored", "citation_roles", "calls_too_long", "ai_available", "batches",
                                         "ai_spend", "echo_clean")) and none_rejected(r)
    assert "1 published summaries looked at" in ck["echo_clean"]["detail"]
    assert as_anon("select count(*) from api.ai_outputs where document_id = %s", c["doc"]) == [(0,)]     # held back by default
    assert go(task([c["slug"]], fake)) == 0 and fake.create_calls == 1 and last_run()["rows"] == 0        # nothing more to ask


@pytest.mark.db
def test_the_extraction_rides_the_same_batch_while_the_summary_takes_the_structured_route():
    c = make_call(fy_end=3)
    fake = FakeClaude(["ok"], answer=both(steps(structured()), answers([CAPEX_T], [REV_S])))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 1
    ((_, batch),) = fake.batches.items()
    assert sorted(next(iter(schema_of(x["params"])["properties"])) for x in batch["requests"]) == ["bullets", "targets"]
    assert [g[0] for g in outputs(c["doc"])] == ["published"] and gx(c["doc"])[0][:3] == ("published", "", "v1")
    assert none_rejected(r) and r["checks"]["echo_clean"]["passed"]


@pytest.mark.db
def test_a_call_with_one_unreadable_speaker_gets_the_third_list_on_the_structured_route():
    c = make_call(role_of={8: "unknown"})                                    # Pooja Menon's turn
    fake = FakeClaude(["ok"], answer=steps(structured(analysts=KNOWN_ANALYSTS, unknown=THIRD)))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    asked = next(iter(fake.batches.values()))["requests"][0]["params"]["messages"][0]["content"][1]["text"]
    assert "- Role couldn't be read: Pooja Menon" in asked and '"unknown" (up to 4 bullets)' in asked
    ((_, status, version, content),) = summary_of(c["doc"])
    assert (status, version) == ("published", "v6")
    assert [b["list"] for b in content["bullets"]] == ["management"] * 3 + ["analysts"] * 2 + ["unknown"]
    assert r["checks"]["citation_roles"]["passed"] and none_rejected(r)


@pytest.mark.db
def test_a_quote_the_transcript_does_not_hold_is_retried_with_a_note_and_then_published():
    c = make_call()
    fake = FakeClaude(["ok"], answer=steps(bad_quote(), structured()))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 2
    note = list(fake.batches.values())[1]["requests"][0]["params"]["messages"][0]["content"][-1]["text"]
    assert note.startswith("Your previous answer") and "Answer again as the schema's JSON" in note
    assert 'bullet 1 ("Revenue grew.")' in note and 'a quote ("Revenue from operations rose' in note
    got = outputs(c["doc"])
    assert [g[0] for g in got] == ["rejected", "published"]
    assert got[0][1].startswith('retried: bullet 1: a quote ("Revenue from operations rose')
    assert "isn't in the transcript exactly once" in got[0][1]
    assert [x[0] for x in requests(c["doc"])] == ["rejected", "succeeded"]
    assert none_rejected(r) and alert(f"ai_rejected:call_summary:{c['doc']}") == []


@pytest.mark.db
def test_a_summary_rejected_twice_leaves_the_published_v5_one_in_place_and_is_never_resent():
    c = make_call()
    old = legacy(c, CLEAN, **STAMPED_V5)
    fake = FakeClaude(["ok"], answer=steps(bad_quote()))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 2
    assert [(g[0], g[4]) for g in outputs(c["doc"])] == [("published", "v5"), ("rejected", "v6"), ("rejected", "v6")]
    assert [x[0] for x in requests(c["doc"])] == ["rejected", "rejected"]
    assert [p[0] for p in published_summary(c["doc"])] == [old]                     # the visitor-facing summary is unchanged
    assert not r["checks"]["citation_integrity"]["passed"]                          # the rejections are counted and named here
    assert "isn't in the transcript exactly once" in r["checks"]["citation_integrity"]["detail"]
    assert alert(f"ai_rejected:call_summary:{c['doc']}") == []                      # no alert while a summary is still published
    assert go(task([c["slug"]], fake)) == 0                                         # an unchanged input is never sent twice
    assert fake.create_calls == 2 and len(outputs(c["doc"])) == 3


@pytest.mark.db
def test_a_v6_summary_supersedes_the_v5_one_and_keeps_its_row_and_citations():
    c = make_call()
    old = legacy(c, CLEAN, **STAMPED_V5)
    before = citations_of(old)
    fake = FakeClaude(["ok"], answer=steps(structured()))
    assert go(task([c["slug"]], fake)) == 0
    assert last_run()["status"] == "success"
    assert [(g[0], g[4]) for g in outputs(c["doc"])] == [("superseded", "v5"), ("published", "v6")]
    ((new, *_),) = published_summary(c["doc"])
    assert new != old and citations_of(old) == before and len(before) == 6        # the old rows stay (store once, never lose)
    assert len(citations_of(new)) == 7 and all(v for *_, v in citations_of(new))
    assert go(task([c["slug"]], fake)) == 0 and fake.create_calls == 1            # v6 is published: nothing more is asked


@pytest.mark.db
def test_a_copied_end_is_cut_before_a_summary_is_stored_and_its_citation_is_kept():
    c = make_call()
    copied = [(f"{LEAD} {MEERA}", [MEERA])] + GOOD_MANAGEMENT[1:]
    fake = FakeClaude(["ok"], answer=steps(structured(management=copied)))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    ((out_id, content, body),) = published_summary(c["doc"])
    assert content["bullets"][0]["text"] == LEAD and content["echo_rule"] == cs.ECHO_RULE
    assert "Demand in the export market" not in body and MEERA[:40] not in body
    ((a, z),) = q("""select char_start, char_end from core.ai_citation where ai_output_id = %s and claim_path = '/bullets/0'""",
                  out_id)
    assert c["built"].text[a:z] == MEERA                                            # the evidence is still the whole passage
    assert r["checks"]["echo_clean"]["passed"] and none_rejected(r)


@pytest.mark.db
def test_the_run_fails_if_a_published_summary_ever_holds_copied_text_that_the_reply_check_let_through(monkeypatch):
    """Simulates a bug in the copied-text rule: the summaries published in this run are read back, and the blocking
    `echo_clean` check fails the run, so the last good data stays."""
    monkeypatch.setattr(cs, "apply_echo", lambda body, passages, transcript: cs.Echo(body))
    c = make_call()
    copied = [(f"{LEAD} {MEERA}", [MEERA])] + GOOD_MANAGEMENT[1:]
    fake = FakeClaude(["ok"], answer=steps(structured(management=copied)))
    assert go(task([c["slug"]], fake)) != 0 or last_run()["status"] != "success"
    r = last_run()
    assert r["status"] == "checks_failed" and not r["checks"]["echo_clean"]["passed"]
    assert "bullet 1 holds a verbatim run of" in r["checks"]["echo_clean"]["detail"]
