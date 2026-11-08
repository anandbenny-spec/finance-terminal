"""equity.calls_ai with the tone of the call's management passages (`call_tone`; 2B-12, #175) through the runner and the Batch API
path against the test database as job_writer. Tone is asked for only by a run limited to it (`CALLS_AI_TASK=call_tone`); a default
run never asks. The reply is the schema's JSON (a score for every management passage of 40 words or more, and up to three quotes);
the code checks the scores and finds each quote exactly once in its passage. The checks themselves are in test_ai_call_tone.py (no
database); here: the request as it is sent, what is stored, the retry and rejection, role-held calls, and that a default run leaves
tone alone. The helpers are test_equity_calls_ai_task.py's."""
import json
import re

import psycopg
import pytest
from ai_fakes import FakeClaude, documents_of
from call_fixtures import GOOD_ANALYSTS, GOOD_MANAGEMENT
from conftest import dsn
from test_equity_calls_ai_task import (
    JOB,
    KEY,
    alert,
    as_anon,
    go,
    last_run,
    make_call,
    q,
    replies,
    task,
    wipe,
)

from ft.ai import call_tone as ct
from ft.jobs.tasks import equity_calls_ai as eca

FILL = " ".join(f"detail{i}" for i in range(36))
UP = "we are raising our outlook for the full year"
DOWN = "demand in the export market has fallen sharply"
LONG = [
    ("Moderator", "moderator", "Moderator: Good day and welcome to the Acme Limited Q2 FY27 earnings conference call."),
    ("Ravi Rao", "management", f"Ravi Rao: Thank you. Demand has been strong across our segments and {UP}. {FILL}"),
    ("Moderator", "moderator", "Moderator: The first question is from Anil Shah of Orbit Securities."),
    ("Anil Shah", "analyst", "Anil Shah: Can you explain the margin outlook for the second half?"),
    ("Meera Iyer", "management", f"Meera Iyer: In the export market {DOWN} and margins are under pressure. {FILL}"),
    ("Moderator", "moderator", "Moderator: Thank you. I now hand the conference back to Mr. Rao for closing comments."),
    ("Ravi Rao", "management", "Ravi Rao: Thank you all for joining us."),
]


@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '2B-12 test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


@pytest.fixture(scope="module", autouse=True)
def clean():
    wipe()
    yield
    wipe()


def rechunk(c: dict) -> dict:
    """One chunk per speaker turn, as the production chunker cuts a transcript. (The test chunker needs a speaker-label shape these
    synthetic lines don't have and cuts the whole call as one chunk; re-chunking is allowed, the text stays the same.)"""
    text = c["built"].text
    with psycopg.connect(dsn("postgres")) as conn:
        conn.execute("delete from core.chunk where document_id = %s", (c["doc"],))
        for no, t in enumerate(c["built"].turns):
            conn.execute("""insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
                            values (%s, %s, %s, %s, null, %s, %s)""",
                         (c["doc"], no, t.char_start, t.char_end, t.speaker, text[t.char_start:t.char_end]))
    return c


def tone_answer(scores, evidence):
    def fn(body):
        docs, _ = documents_of(body)
        n = len(re.findall(r"^\[\d+\] \(", docs[0][1], flags=re.MULTILINE))
        assert n == len(scores), (n, docs[0][1])
        return {"scores": [{"p": i + 1, "score": s} for i, s in enumerate(scores)],
                "evidence": [{"p": p, "quote": qt} for p, qt in evidence]}
    return fn


def tone_outputs(doc_id):
    return q("""select status, coalesce(status_reason, ''), prompt_version, content from core.ai_output
                 where document_id = %s and task = 'call_tone' order by id""", doc_id)


def tone_requests(doc_id):
    return q("""select status, coalesce(reject_reason, ''), ai_output_id is not null from ops.ai_request
                 where document_id = %s and ai_task = 'call_tone' order by created_at, custom_id""", doc_id)


@pytest.mark.db
def test_a_run_limited_to_tone_asks_for_the_management_passages_only_and_stores_scores_with_verified_citations(monkeypatch):
    monkeypatch.setenv(eca.ONLY_ENV, ct.TASK)
    c = rechunk(make_call(lines=LONG, fy_end=3))
    fake = FakeClaude(["ok"], reply=replies(), answer=tone_answer([2, -1], [(1, UP), (2, DOWN)]))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert q("select ai_task from ops.ai_request where document_id = %s", c["doc"]) == [("call_tone",)]
    ((_, batch),) = fake.batches.items()
    (req,) = batch["requests"]
    sent = req["params"]
    assert sent["output_config"]["effort"] == "medium" and sent["output_config"]["format"]["type"] == "json_schema"
    block = sent["messages"][0]["content"][0]
    assert "citations" not in block
    listing = block["source"]["data"]
    assert listing.startswith("[1] (opening remarks, Ravi Rao)") and "\n\n[2] (answer, Meera Iyer)" in listing
    assert "Anil Shah" not in listing and "Moderator" not in listing and "Thank you all for joining" not in listing
    ((status, reason, version, content),) = tone_outputs(c["doc"])
    assert (status, reason, version) == ("published", "", "v1")
    assert [s["score"] for s in content["scores"]] == [2, -1] and [s["section"] for s in content["scores"]] == ["opening", "qa"]
    assert [e["quote"] for e in content["evidence"]] == [UP, DOWN] and content["dropped"] == {}
    assert FILL not in json.dumps(content)                                  # scores and quotes only, no passage text
    cites = q("""select claim_path, kind, verified, char_start, char_end from core.ai_citation
                  where ai_output_id = (select id from core.ai_output where document_id = %s and task = 'call_tone')
                  order by citation_no""", c["doc"])
    text = c["built"].text
    assert [(p, k, v) for p, k, v, _, _ in cites] == [("/evidence/0", "document_span", True), ("/evidence/1", "document_span", True)]
    assert [text[a:b] for _, _, _, a, b in cites] == [UP, DOWN]
    assert tone_requests(c["doc"]) == [("succeeded", "", True)]
    ck = r["checks"]
    assert ck["tone_stored"]["passed"] and ck["tone_stored"]["detail"].startswith("1 stored")
    assert "1 published" in ck["call_tone"]["detail"]
    assert as_anon("select count(*) from api.ai_outputs where document_id = %s", c["doc"]) == [(0,)]       # hidden: nothing shows it
    assert go(task([c["slug"]], fake)) == 0                                  # published: never asked again
    assert fake.create_calls == 1


@pytest.mark.db
def test_a_default_run_never_asks_for_tone(monkeypatch):
    monkeypatch.delenv(eca.ONLY_ENV, raising=False)
    c = make_call(lines=LONG, fy_end=3)
    fake = FakeClaude(["ok"], reply=replies(management=GOOD_MANAGEMENT, analysts=GOOD_ANALYSTS),
                      answer=lambda body: {"targets": [], "stated_figures": []})
    assert go(task([c["slug"]], fake)) == 0
    assert "call_tone" not in {t for (t,) in q("select ai_task from ops.ai_request where document_id = %s", c["doc"])}
    assert tone_outputs(c["doc"]) == [] and tone_requests(c["doc"]) == []


@pytest.mark.db
def test_a_role_held_call_is_not_asked_for_tone(monkeypatch):
    monkeypatch.setenv(eca.ONLY_ENV, ct.TASK)
    c = rechunk(make_call(lines=LONG, fy_end=3, role_of={4: "unknown"}))
    fake = FakeClaude(["ok"], reply=replies(), answer=tone_answer([0], []))
    assert go(task([c["slug"]], fake)) == 0
    assert q("select count(*) from ops.ai_request where document_id = %s", c["doc"]) == [(0,)]
    assert fake.create_calls == 0


@pytest.mark.db
def test_a_neutral_call_cites_its_first_passage_so_it_can_be_published(monkeypatch):
    monkeypatch.setenv(eca.ONLY_ENV, ct.TASK)
    c = rechunk(make_call(lines=LONG, fy_end=3))
    fake = FakeClaude(["ok"], reply=replies(), answer=tone_answer([0, 0], []))
    assert go(task([c["slug"]], fake)) == 0
    ((status, _, _, content),) = tone_outputs(c["doc"])
    assert status == "published" and content["fallback_citation"] is True and content["evidence"] == []
    ((path, ok),) = q("""select claim_path, verified from core.ai_citation
                          where ai_output_id = (select id from core.ai_output where document_id = %s and task = 'call_tone')""",
                      c["doc"])
    assert (path, ok) == ("/scores/0", True)


@pytest.mark.db
def test_a_score_out_of_range_is_retried_with_a_note_then_rejected_with_an_alert_and_never_resent(monkeypatch):
    monkeypatch.setenv(eca.ONLY_ENV, ct.TASK)
    c = rechunk(make_call(lines=LONG, fy_end=3))
    fake = FakeClaude(["ok"], reply=replies(), answer=tone_answer([3, 0], []))
    assert go(task([c["slug"]], fake)) == 0
    outs = tone_outputs(c["doc"])
    assert [o[0] for o in outs] == ["rejected", "rejected"] and "-2 to +2" in outs[-1][1]
    assert alert(f"ai_rejected:call_tone:{c['doc']}")
    assert fake.create_calls == 2
    assert fake.create_calls == 2 and last_run()["checks"]["tone_stored"]["passed"]
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 2 and len(tone_outputs(c["doc"])) == 2       # an unchanged input is never sent twice
