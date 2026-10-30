"""policy.ai_batch through the runner and the watch loop against the test database as job_writer (Prompt 01 B7;
Architecture 03 §1.1-§1.5; Spec 01 §3.2-§3.3): part 1's real time on decision days, and part 2's Batch API path for
daily, refresh and backfill runs. The Messages and Message Batches APIs and Voyage are fakes (tests/ai_fakes.py); the
Anthropic SDK itself runs. The batch poll's clock is a fake too (`Ticker`). Meetings are synthetic: 2045 for the
decision-day path with policy.fed_documents, 2046-2048 for part 1's tests, 2049-2051 for the batch path; the task sees
only each test's meetings (`scope`), and only the batches holding their requests."""
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest
from ai_fakes import FakeClaude, FakeVoyage, claude
from conftest import dsn
from policy_docs import FOMC_A, FOMC_B, fed_rss
from test_document_chunks import plain
from test_fed_documents_task import (  # noqa: F401 - s3 and roster are fixtures
    Clock,
    Sites,
    calendar,
    fred,
    named,
    roster,
    s3,
    storage,
    url,
)
from test_fed_documents_task import meeting as fed_meeting
from test_fed_documents_task import task as fed_task

from ft.ai.client import Prompt, VoyageAPIError, batch_custom_id, halfvec_literal
from ft.engines.document.chunk import chunk_text
from ft.engines.document.diff import diff_texts
from ft.engines.document.store import DocumentRef, save_chunks, save_diff, save_version
from ft.jobs import due as due_rules
from ft.jobs import fetch
from ft.jobs.runner import Check, JobRun, Task, connect, run
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import AiBatch, context_line
from ft.jobs.watch import watch
from ft.policy import fed_calendar, fed_statement

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NY = ZoneInfo("America/New_York")
KEY = "test.b7_ai_batch"
FED_KEY = "test.b5_fed_documents"
DATA_KEY = "test.s2_data"


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a).fetchall()


def x(sql, *a) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql, a)


def as_anon(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("set role anon")
        return c.execute(sql, a).fetchall()


@pytest.fixture(scope="module", autouse=True)
def ai_task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        for k, wf in ((KEY, "{daily,watch}"), (FED_KEY, "{daily,watch}"), (DATA_KEY, "{daily}")):
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'B7 test task', %s, '20 hours', '3 days', false)
                         on conflict (key) do nothing""", (k, wf))


def meeting(d: date) -> tuple[str, int]:
    """A held, published FOMC meeting on `d` (14:00 New York)."""
    s = f"fomc-{d.isoformat()}"
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at, status,
                                             source_id, source_url, is_published)
                     select 'policy_meeting', b.id, %s, 'FOMC meeting', %s, %s, %s, 'held', src.id, %s, true
                       from core.entity b, core.source src where b.slug = 'fed' and src.key = 'fed'
                     on conflict (event_type, slug) do nothing""",
                  (s, d - timedelta(days=1), d, datetime(d.year, d.month, d.day, 14, tzinfo=NY), fed_calendar.CALENDAR))
        eid = c.execute("select id from core.event where event_type = 'policy_meeting' and slug = %s", (s,)).fetchone()[0]
        c.execute("""insert into policy.meeting (event_id, kind, is_rate_decision) values (%s, 'scheduled', true)
                     on conflict do nothing""", (eid,))
    return s, eid


def statement(d: date, eid: int, text: str, *, base: int | None = None, publish: bool = True) -> int:
    """A stored FOMC statement (its chunks, and its diff against `base`), published unless told otherwise."""
    doc = plain(text.split("\n\n"))
    fed, src = q("select (select id from core.entity where slug = 'fed'), (select id from core.source where key = 'fed')")[0]
    key = f"/test/b7/monetary{d:%Y%m%d}a.htm"
    with connect(JOB) as c, c.transaction():
        saved = save_version(c, DocumentRef("fomc_statement", fed, src, key, "https://www.federalreserve.gov" + key,
                                            f"FOMC statement, {d.day} {d:%b %Y}", eid, None,
                                            datetime(d.year, d.month, d.day, 14, tzinfo=NY)), doc)
        save_chunks(c, saved.document_id, doc.text, chunk_text(doc, root="Statement"))
        if base is not None:
            old = c.execute("select string_agg(body, '' order by chunk_no) from core.chunk where document_id = %s",
                            (base,)).fetchone()[0]
            save_diff(c, saved.document_id, base, diff_texts(old, doc.text))
        if publish:
            c.execute("update core.document set is_published = true where id = %s", (saved.document_id,))
    return saved.document_id


class Ticker:
    """The batch poll's clock: sleeping moves it on at once."""

    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def task(slugs, fake: FakeClaude, voyage: FakeVoyage | None = None, tick: Ticker | None = None) -> AiBatch:
    class T(AiBatch):
        key = KEY
    t = T()
    t.scope = frozenset(slugs)
    t.workflows, t.rules = ("daily", "watch"), {"daily": "every 20 hours", "watch": "after_decision_document"}
    t.claude = claude(fake)
    from ft.ai.client import Embedder
    t.embedder = Embedder(client=voyage or FakeVoyage())
    tick = tick or Ticker()
    t.sleep, t.monotonic = tick.sleep, tick.monotonic
    return t


def last_run(key=KEY):
    r = q("""select id, status, rows_written, checks, coalesce(error, ''), ai_cost_usd from ops.task_run
              where task_key = %s order by id desc limit 1""", key)[0]
    return {"id": r[0], "status": r[1], "rows": r[2], "checks": {c["name"]: c for c in r[3] or []}, "error": r[4],
            "cost": r[5]}


def outputs(doc_id: int):
    return q("""select task, status, coalesce(status_reason, ''), jsonb_array_length(content -> 'bullets'), cost_usd,
                       prompt_version, model
                  from core.ai_output where document_id = %s order by id""", doc_id)


def requests(doc_id: int):
    return q("""select ai_task, status, coalesce(reject_reason, ''), ai_output_id is not null
                  from ops.ai_request where document_id = %s order by created_at, custom_id""", doc_id)


def decision_day(d: date, minutes: int = 3) -> datetime:
    return datetime(d.year, d.month, d.day, 14, minutes, tzinfo=NY)


def watch_once(t: AiBatch, now: datetime):
    clock = Clock(now)
    with JobRun(JOB, "watch", clock=clock) as job:
        polls = watch(job, [t], sleep=clock.sleep)
        code = job.finish()
    return polls, code


def again(t: AiBatch, d: date, now: datetime) -> int:
    """Run the task once more as if it were due on the decision day (the rule itself says it isn't any more)."""
    def due(db, workflow, now=None):
        m = due_rules.decision_meeting(db, due_rules.BANKS["fomc_decision_day"], d)
        return due_rules.Due(True, "test: run again", meeting=m, until=m.closes_at)
    t.due = due
    with JobRun(JOB, "watch", clock=lambda: now) as job:
        job.run_pass([t])
        return job.finish()


def two_statements(prev_day: date, day: date):
    s0, e0 = meeting(prev_day)
    s1, e1 = meeting(day)
    old = statement(prev_day, e0, FOMC_A)
    new = statement(day, e1, FOMC_B, base=old)
    return (s0, s1), old, new


# ---------------------------------------------------------------------------------------------- decision day
@pytest.mark.db
def test_decision_day_publishes_cited_summaries_and_embeddings_in_real_time():
    (s0, s1), old, new = two_statements(date(2046, 1, 24), date(2046, 3, 14))
    fake, voyage = FakeClaude(["ok"]), FakeVoyage()
    polls, code = watch_once(task([s0, s1], fake, voyage), decision_day(date(2046, 3, 14)))
    assert (polls, code) == (1, 0)
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert r["checks"]["outputs_stored"]["passed"] and r["checks"]["citation_integrity"]["passed"]
    assert r["checks"]["embeddings"]["passed"] and "batch_path" not in r["checks"]   # real time: no batch check

    # Only the decision document goes in real time: its summary and what changed; the previous statement waits.
    assert [b["messages"][0]["content"][0]["title"] for b in fake.requests] == [
        "FOMC statement, 14 Mar 2046", "Previous FOMC statement, 24 Jan 2046"]
    got = outputs(new)
    assert [(g[0], g[1], g[3], g[5], g[6]) for g in got] == [
        ("doc_summary", "published", 3, "v1", "claude-haiku-5-5"),
        ("statement_diff_summary", "published", 3, "v1", "claude-haiku-5-5")]
    assert all(g[4] > 0 for g in got) and r["cost"] > 0                      # every call's cost; the run's total
    assert outputs(old) == [] and [x[:2] for x in requests(new)] == [
        ("doc_summary", "succeeded"), ("statement_diff_summary", "succeeded")]

    # Visitors see the summaries, labelled, with every citation's quote rebuilt from the stored text.
    vis = as_anon("select task, display_label, body from api.ai_outputs where document_id = %s order by task", new)
    assert [(v[0], v[1]) for v in vis] == [("doc_summary", "AI summary"), ("statement_diff_summary", "AI summary")]
    cites = as_anon("""select c.document_id, c.quote, c.char_start, c.char_end from api.ai_citations c
                         join api.ai_outputs o on o.id = c.ai_output_id where o.document_id = %s""", new)
    texts = {old: FOMC_A, new: FOMC_B}
    assert cites and all(texts[d][s:e] == quote for d, quote, s, e in cites)
    assert {d for d, *_ in cites} == {old, new}                             # the diff cites both documents

    # The new statement's chunks are embedded (voyage-4, 512), each with its context line.
    emb = q("""select count(*), min(extensions.vector_dims(embedding::extensions.vector)), min(model) from core.chunk_embedding
                where document_id = %s""", new)[0]
    chunks = q("select count(*) from core.chunk where document_id = %s", new)[0][0]
    assert emb == (chunks, 512, "voyage-4@512")
    assert voyage.calls[0]["texts"][0].startswith("Fed · FOMC statement · 14 Mar 2046 · Statement\n\n")

    # Retrieval reads the stored vectors (sql_reader, the command bar's login): a question embedded like the
    # statement's chunk finds it.
    sent = voyage.calls[0]["texts"]
    question = FakeVoyage().embed([sent[0]]).embeddings[0]
    with psycopg.connect(dsn("sql_reader"), autocommit=True) as c:
        hits = c.execute("""select document_id, chunk_no from semantic.search_chunks(%s::extensions.halfvec(512),
                                   'downside risks to employment', '{fed}', '{fomc_statement}', '2046-03-14', '2046-03-14', 3)""",
                         (halfvec_literal(question),)).fetchall()
    assert hits[0] == (new, 0)

    # Run again in real time: nothing is sent (never twice) and nothing is written.
    n = fake.n
    assert again(task([s0, s1], fake), date(2046, 3, 14), decision_day(date(2046, 3, 14), 9)) == 0
    assert fake.n == n and last_run()["rows"] == 0 and last_run()["status"] == "success"


@pytest.mark.db
def test_a_bullet_with_a_number_not_in_its_passage_is_retried_once_then_published():
    (s0, s1), _, new = two_statements(date(2046, 4, 25), date(2046, 6, 13))
    fake = FakeClaude(["bad_number", "ok", "ok"])
    _, code = watch_once(task([s0, s1], fake), decision_day(date(2046, 6, 13)))
    assert code == 0
    retry = fake.requests[1]["messages"][0]["content"][-1]["text"]
    assert "couldn't be published" in retry and "numbers not in its cited passages: 25" in retry
    got = outputs(new)
    assert [(g[0], g[1]) for g in got] == [("doc_summary", "rejected"), ("doc_summary", "published"),
                                           ("statement_diff_summary", "published")]
    assert got[0][2].startswith("retried: bullet 1: numbers not in its cited passages: 25")
    assert [x[:2] for x in requests(new)] == [("doc_summary", "rejected"), ("doc_summary", "succeeded"),
                                              ("statement_diff_summary", "succeeded")]
    assert last_run()["checks"]["citation_integrity"]["passed"]           # the target was published


@pytest.mark.db
def test_a_reply_that_fails_twice_is_rejected_with_an_alert_and_never_resent():
    (s0, s1), _, new = two_statements(date(2046, 7, 25), date(2046, 9, 19))
    fake = FakeClaude(["uncited", "uncited", "ok"])
    _, code = watch_once(task([s0, s1], fake), decision_day(date(2046, 9, 19)))
    assert code == 0
    r = last_run()
    ci = r["checks"]["citation_integrity"]
    assert r["status"] == "success" and not ci["passed"] and not ci["blocking"] and "rejected: bullet 1" not in ci["detail"]
    got = outputs(new)
    assert [(g[0], g[1]) for g in got] == [("doc_summary", "rejected"), ("doc_summary", "rejected"),
                                           ("statement_diff_summary", "published")]
    assert got[1][2].startswith("rejected: bullet 1: no verified citation")
    assert as_anon("select count(*) from api.ai_outputs where document_id = %s and task = 'doc_summary'", new) == [(0,)]
    assert q("select severity from ops.alert where dedupe_key = %s and resolved_at is null",
             f"ai_rejected:doc_summary:{new}") == [("warn",)]
    n = fake.n
    again(task([s0, s1], fake), date(2046, 9, 19), decision_day(date(2046, 9, 19), 20))
    assert fake.n == n                                                       # a rejected input isn't sent again


@pytest.mark.db
def test_an_api_error_fails_the_poll_and_the_next_poll_sends_again():
    (s0, s1), _, new = two_statements(date(2046, 10, 31), date(2046, 12, 12))
    fake = FakeClaude(["400", "ok"])
    t = task([s0, s1], fake)
    with JobRun(JOB, "watch", clock=Clock(decision_day(date(2046, 12, 12)))) as job:
        job.run_pass([t])
        first = last_run()
        assert first["status"] == "failed" and "Unfinished" in first["error"] and outputs(new) == []
        assert requests(new) == [("doc_summary", "errored", "BadRequestError 400", False)]
        job.run_pass([t], record_skips=False)                              # the next poll
        assert last_run()["status"] == "success"
        assert job.finish() == 0                                            # the last outcome counts
    assert [(g[0], g[1]) for g in outputs(new)] == [("doc_summary", "published"), ("statement_diff_summary", "published")]


@pytest.mark.db
def test_a_retry_that_errors_is_sent_again_from_its_stored_note_and_the_first_reply_isnt():
    """Review of B7, 23 Oct: the first reply is stored ("retried: …", with the retry's note); the retry fails with a
    400; the poll fails; the next poll sends only the retry, from the stored note."""
    (s0, s1), _, new = two_statements(date(2048, 1, 26), date(2048, 3, 15))
    fake = FakeClaude(["uncited", "400", "ok"])
    t = task([s0, s1], fake)
    with JobRun(JOB, "watch", clock=Clock(decision_day(date(2048, 3, 15)))) as job:
        job.run_pass([t])
        assert last_run()["status"] == "failed" and fake.n == 2
        assert [x[:2] for x in requests(new)] == [("doc_summary", "rejected"), ("doc_summary", "errored")]
        job.run_pass([t], record_skips=False)
        assert last_run()["status"] == "success" and job.finish() == 0
    retries = [b["messages"][0]["content"][-1]["text"] for b in fake.requests[1:3]]
    assert retries[0] == retries[1] and retries[0].startswith("Your previous answer")   # the same retry, twice
    assert fake.n == 4                                                   # a1, a2 (400), a2, the diff summary
    assert [(g[0], g[1]) for g in outputs(new)] == [("doc_summary", "rejected"), ("doc_summary", "published"),
                                                    ("statement_diff_summary", "published")]


@pytest.mark.db
def test_a_target_left_unfinished_fails_the_poll_and_the_finished_one_isnt_sent_again():
    (s0, s1), _, new = two_statements(date(2048, 4, 26), date(2048, 6, 14))
    fake = FakeClaude(["ok", "400", "ok"])
    t = task([s0, s1], fake)
    with JobRun(JOB, "watch", clock=Clock(decision_day(date(2048, 6, 14)))) as job:
        job.run_pass([t])
        assert last_run()["status"] == "failed"
        assert [(g[0], g[1]) for g in outputs(new)] == [("doc_summary", "published")]    # stored as it came
        job.run_pass([t], record_skips=False)
        assert last_run()["status"] == "success"
    assert fake.n == 3 and [(g[0], g[1]) for g in outputs(new)] == [("doc_summary", "published"),
                                                                    ("statement_diff_summary", "published")]


@pytest.mark.db
def test_a_daily_run_after_the_statement_doesnt_stand_in_for_the_decision_day_step():
    """after_decision_document counts only watch runs, and a batch run inside a meeting's watch window leaves that
    meeting's decision documents to the watch (in a batch they'd wait up to an hour, and the watch never sends a
    request twice): the daily run batches only the previous statement's summary, and the watch then summarises the
    new statement in real time."""
    (s0, s1), old, new = two_statements(date(2048, 7, 26), date(2048, 9, 20))
    fake = FakeClaude(["ok"])
    run(JOB, "daily", [task([s0, s1], fake)], only=KEY, force=True, clock=lambda: datetime(2048, 9, 20, 18, tzinfo=UTC))
    r = last_run()                                                           # 14:00 in New York: the window is open
    assert [len(b["requests"]) for b in fake.batches.values()] == [1] and outputs(new) == []
    assert f'"held_for_watch": [{new}]' in r["checks"]["batches"]["detail"]
    assert [(g[0], g[1]) for g in outputs(old)] == [("doc_summary", "published")]
    rt = FakeClaude(["ok"])
    _, code = watch_once(task([s0, s1], rt), decision_day(date(2048, 9, 20), 10))
    assert code == 0 and last_run()["status"] == "success" and rt.n == 2
    assert sorted((g[0], g[1]) for g in outputs(new)) == [("doc_summary", "published"),
                                                          ("statement_diff_summary", "published")]


@pytest.mark.db
def test_the_monthly_budget_stops_requests_with_an_alert():
    (s0, s1), _, new = two_statements(date(2047, 1, 30), date(2047, 3, 20))
    fake = FakeClaude(["ok"])
    x("update ops.setting set value = '0' where key = 'ai_monthly_budget_usd'")
    try:
        _, code = watch_once(task([s0, s1], fake), decision_day(date(2047, 3, 20)))
    finally:
        x("update ops.setting set value = '15' where key = 'ai_monthly_budget_usd'")
    r = last_run()
    assert code == 0 and fake.requests == [] and outputs(new) == [] and not r["checks"]["ai_spend"]["passed"]
    assert '"stopped": "budget"' in r["checks"]["ai_spend"]["detail"]
    month = datetime.now(UTC).strftime("%Y-%m")
    assert q("select severity from ops.alert where dedupe_key = %s and resolved_at is null", f"ai_budget:{month}") == [
        ("error",)]
    x("update ops.alert set resolved_at = now() where dedupe_key = %s", f"ai_budget:{month}")


@pytest.mark.db
def test_with_real_time_off_a_decision_day_goes_to_a_batch_without_polling_and_a_daily_run_collects_it():
    (s0, s1), old, new = two_statements(date(2047, 4, 30), date(2047, 6, 18))
    unpublished_base = statement(date(2047, 6, 19), meeting(date(2047, 6, 19))[1], FOMC_A, publish=False)
    fake, voyage, tick = FakeClaude(["ok"]), FakeVoyage(), Ticker()
    # The real-time switch off: the decision day's documents go to a batch, submitted without waiting (the watch's
    # polls are short); their chunks are embedded.
    x("update ops.setting set value = 'false' where key = 'ai_realtime_on_decision_days'")
    try:
        watch_once(task([s0, s1], fake, voyage, tick), decision_day(date(2047, 6, 18)))
    finally:
        x("update ops.setting set value = 'true' where key = 'ai_realtime_on_decision_days'")
    r = last_run()
    sent = [b["requests"] for b in fake.batches.values()]
    assert r["status"] == "success" and tick.sleeps == [] and fake.create_calls == 1 and len(sent[0]) == 2
    assert r["checks"]["batches"]["passed"] and outputs(new) == []          # open, not yet read
    assert q("select count(*) from core.chunk_embedding where document_id = %s", new)[0][0] > 0
    # A daily run: collects that batch, and sends the previous statement's summary in a new one.
    code = run(JOB, "daily", [task([s0, s1], fake, voyage)], only=KEY, force=True,
               clock=lambda: datetime(2047, 6, 19, 12, tzinfo=UTC))
    r = last_run()
    assert code == 0 and r["status"] == "success" and fake.create_calls == 2
    assert sorted((g[0], g[1]) for g in outputs(new)) == [("doc_summary", "published"),
                                                          ("statement_diff_summary", "published")]
    assert [(g[0], g[1]) for g in outputs(old)] == [("doc_summary", "published")]
    chunks = q("select count(*) from core.chunk where document_id in (%s, %s)", old, new)[0][0]
    assert q("select count(*) from core.chunk_embedding where document_id in (%s, %s)", old, new)[0][0] == chunks
    assert q("select count(*) from core.chunk_embedding where document_id = %s", unpublished_base)[0][0] == 0


@pytest.mark.db
def test_no_diff_summary_while_the_base_is_unpublished_and_a_voyage_error_doesnt_fail_the_run():
    s0, e0 = meeting(date(2047, 7, 30))
    s1, e1 = meeting(date(2047, 9, 17))
    old = statement(date(2047, 7, 30), e0, FOMC_A, publish=False)
    new = statement(date(2047, 9, 17), e1, FOMC_B, base=old)
    from ft.ai.client import VoyageAPIError
    fake = FakeClaude(["ok"])
    voyage = FakeVoyage(fail=[VoyageAPIError(401, "bad key")])
    _, code = watch_once(task([s0, s1], fake, voyage), decision_day(date(2047, 9, 17)))
    r = last_run()
    assert code == 0 and [(g[0], g[1]) for g in outputs(new)] == [("doc_summary", "published")]
    emb = r["checks"]["embeddings"]
    assert not emb["passed"] and "AuthenticationError" in emb["detail"] and r["status"] == "success"


@pytest.mark.db
def test_a_new_prompt_version_supersedes_the_published_output(monkeypatch):
    (s0, s1), _, new = two_statements(date(2047, 10, 29), date(2047, 12, 10))
    fake = FakeClaude(["ok"])
    watch_once(task([s0, s1], fake), decision_day(date(2047, 12, 10)))
    real = ai_batch.load_prompt

    def v2(name, version):
        p = real(name, version)
        return Prompt(p.task, "v2", p.text + "\nOne more rule.\n")
    monkeypatch.setattr(ai_batch, "load_prompt", v2)
    again(task([s0, s1], fake), date(2047, 12, 10), decision_day(date(2047, 12, 10), 30))
    got = [(g[0], g[1], g[5]) for g in outputs(new)]
    assert got == [("doc_summary", "superseded", "v1"), ("statement_diff_summary", "superseded", "v1"),
                   ("doc_summary", "published", "v2"), ("statement_diff_summary", "published", "v2")]
    assert as_anon("select count(*) from api.ai_outputs where document_id = %s", new) == [(2,)]


# ---------------------------------------------------------------------------------------------- the Batch API (B7.2)
def daily(t: AiBatch, when: datetime) -> int:
    return run(JOB, "daily", [t], only=KEY, force=True, clock=lambda: when)


def one_statement(d: date) -> tuple[str, int]:
    s, e = meeting(d)
    return s, statement(d, e, FOMC_A)


def batch_requests(doc_ids: list[int]):
    return q("""select ai_task, status, coalesce(reject_reason, ''), ai_batch_id is not null
                  from ops.ai_request where document_id = any(%s) order by created_at, custom_id""", doc_ids)


@pytest.mark.db
def test_a_daily_run_sends_one_batch_polls_it_and_publishes_in_the_same_run():
    (s0, s1), old, new = two_statements(date(2049, 1, 26), date(2049, 3, 15))
    fake, tick = FakeClaude(["ok"], ends_after=2), Ticker()
    assert daily(task([s0, s1], fake, tick=tick), datetime(2049, 3, 16, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 1 and tick.sleeps == [30.0, 60.0]         # read once still processing, then ended
    ((bid, b),) = fake.batches.items()
    sent = b["requests"]
    stored = [c for (c,) in q("select custom_id from ops.ai_request where document_id = any(%s)", [old, new])]
    assert len(stored) == 3 and sorted(s["custom_id"] for s in sent) == sorted(batch_custom_id(c) for c in stored)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", s["custom_id"]) for s in sent) and all(":" in c for c in stored)
    assert not any("cache_control" in s["params"] for s in sent)          # batch requests are uncached
    assert sorted((g[0], g[1]) for g in outputs(new)) == [("doc_summary", "published"),
                                                          ("statement_diff_summary", "published")]
    assert [(g[0], g[1]) for g in outputs(old)] == [("doc_summary", "published")]
    # Priced at the batch rate (half the real-time price of the same tokens); the run's spend and the batch's agree.
    rows = q("select input_tokens, output_tokens, cost_usd from core.ai_output where document_id = any(%s)", [old, new])
    for tin, tout, cost in rows:
        assert cost == ((tin * Decimal("0.10") + tout * Decimal("0.50")) / 1_000_000 / 2).quantize(Decimal("0.000001"))
    total = sum(c for *_, c in rows)
    assert r["cost"] == total.quantize(Decimal("0.0001"))
    (row,) = q("""select status, request_count, succeeded, errored, expired, cost_usd, task_run_id, collected_at
                    from ops.ai_batch where provider_batch_id = %s""", bid)
    assert row[:5] == ("collected", 3, 3, 0, 0) and abs(row[5] - total) < Decimal("0.0001") and row[6] == r["id"]
    assert row[7] is not None
    assert [x[1:] for x in batch_requests([old, new])] == [("succeeded", "", True)] * 3
    checks = r["checks"]
    assert checks["batches"]["passed"] and '"turnaround_s"' in checks["batches"]["detail"]
    assert checks["citation_integrity"]["passed"] and checks["outputs_stored"]["passed"]
    assert as_anon("select count(*) from api.ai_outputs where document_id = any(%s)", [old, new]) == [(3,)]
    # Run again: nothing is sent, nothing is written.
    assert daily(task([s0, s1], fake), datetime(2049, 3, 17, 12, tzinfo=UTC)) == 0
    assert fake.create_calls == 1 and last_run()["rows"] == 0 and last_run()["checks"]["batches"]["passed"]


@pytest.mark.db
def test_a_batch_still_open_when_the_poll_ends_is_collected_by_the_next_run():
    s, doc = one_statement(date(2049, 4, 30))
    fake, tick = FakeClaude(["ok"], ends_after=None), Ticker()
    assert daily(task([s], fake, tick=tick), datetime(2049, 5, 1, 12, tzinfo=UTC)) == 0
    first = last_run()
    assert first["status"] == "success" and first["checks"]["batches"]["passed"] and outputs(doc) == []
    assert len(tick.sleeps) == 15 and sum(tick.sleeps) <= ai_batch.BATCH_POLL_SECONDS   # 30 s, then 60 s, to 15 min
    assert batch_requests([doc]) == [("doc_summary", "pending", "", True)]
    fake.ends_after = 1
    assert daily(task([s], fake), datetime(2049, 5, 2, 12, tzinfo=UTC)) == 0
    second = last_run()
    assert fake.create_calls == 1 and [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]
    assert second["cost"] > 0 and not first["cost"]                       # spend goes on the run that collected it
    assert q("select status, task_run_id from ops.ai_batch where provider_batch_id = %s",
             next(iter(fake.batches))) == [("collected", first["id"])]   # sent by the first run


@pytest.mark.db
def test_a_reply_rejected_in_a_batch_is_retried_in_a_second_batch_in_the_same_run():
    s, doc = one_statement(date(2049, 6, 18))
    fake, tick = FakeClaude(["uncited", "ok"]), Ticker()
    assert daily(task([s], fake, tick=tick), datetime(2049, 6, 19, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 2 and tick.sleeps == [30.0, 60.0]
    retry = list(fake.batches.values())[1]["requests"][0]["params"]
    assert retry["messages"][0]["content"][-1]["text"].startswith("Your previous answer")
    got = outputs(doc)
    assert [(g[0], g[1]) for g in got] == [("doc_summary", "rejected"), ("doc_summary", "published")]
    assert got[0][2].startswith("retried: bullet 1: no verified citation")
    assert [x[1] for x in batch_requests([doc])] == ["rejected", "succeeded"]
    assert r["checks"]["citation_integrity"]["passed"] and r["checks"]["batches"]["passed"]


@pytest.mark.db
def test_errored_and_expired_results_are_sent_again_by_the_next_run_never_twice_in_one():
    (s0, s1), old, new = two_statements(date(2049, 7, 30), date(2049, 9, 17))
    fake = FakeClaude(["400", "expired", "ok"])        # in request order: old's summary, new's summary, the diff
    assert daily(task([s0, s1], fake), datetime(2049, 9, 18, 12, tzinfo=UTC)) == 0
    r = last_run()
    b = r["checks"]["batches"]
    assert r["status"] == "success" and fake.create_calls == 1 and not b["passed"] and not b["blocking"]
    assert '"errored": 1' in b["detail"] and '"expired": 1' in b["detail"] and '"left_for_next_run": 2' in b["detail"]
    assert batch_requests([old]) == [("doc_summary", "errored", "batch invalid_request_error", True)]
    assert sorted(batch_requests([new])) == [("doc_summary", "expired", "batch expired", True),
                                             ("statement_diff_summary", "succeeded", "", True)]
    assert outputs(old) == [] and [(g[0], g[1]) for g in outputs(new)] == [("statement_diff_summary", "published")]
    # The next run sends those two again, in one new batch; nothing is billed twice.
    assert daily(task([s0, s1], fake), datetime(2049, 9, 19, 12, tzinfo=UTC)) == 0
    assert fake.create_calls == 2 and len(list(fake.batches.values())[1]["requests"]) == 2
    assert last_run()["checks"]["batches"]["passed"]
    assert [(g[0], g[1]) for g in outputs(old)] == [("doc_summary", "published")]
    assert sorted((g[0], g[1]) for g in outputs(new)) == [("doc_summary", "published"),
                                                          ("statement_diff_summary", "published")]


@pytest.mark.db
def test_a_refused_batch_leaves_its_requests_for_the_next_run_and_the_embeddings_still_load():
    s, doc = one_statement(date(2049, 10, 29))
    fake = FakeClaude(["ok"], creates=["400", "ok"])
    assert daily(task([s], fake), datetime(2049, 10, 30, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 1 and not r["checks"]["batches"]["passed"]
    assert "create: BadRequestError (400)" in r["checks"]["batches"]["detail"]
    assert batch_requests([doc]) == [("doc_summary", "errored", "batch BadRequestError 400", False)]
    assert r["checks"]["embeddings"]["passed"]
    assert q("select count(*) from core.chunk_embedding where document_id = %s", doc)[0][0] > 0
    assert daily(task([s], fake), datetime(2049, 10, 31, 12, tzinfo=UTC)) == 0
    assert fake.create_calls == 2 and [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]


@pytest.mark.db
def test_the_run_estimate_and_the_budget_stop_a_batch_before_it_is_sent(monkeypatch):
    s, doc = one_statement(date(2050, 1, 26))
    fake = FakeClaude(["ok"])
    monkeypatch.setattr(ai_batch, "RUN_LIMIT_USD", Decimal("0.0001"))
    assert daily(task([s], fake), datetime(2050, 1, 27, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert fake.create_calls == 0 and '"stopped": "run_estimate"' in r["checks"]["ai_spend"]["detail"]
    assert q("select severity from ops.alert where dedupe_key = 'ai_run_estimate' and resolved_at is null") == [
        ("error",)]
    monkeypatch.undo()
    x("update ops.setting set value = '0' where key = 'ai_monthly_budget_usd'")
    try:
        daily(task([s], fake), datetime(2050, 1, 28, 12, tzinfo=UTC))
    finally:
        x("update ops.setting set value = '15' where key = 'ai_monthly_budget_usd'")
    r = last_run()
    assert fake.create_calls == 0 and '"stopped": "budget"' in r["checks"]["ai_spend"]["detail"]
    assert q("select count(*) from ops.alert where dedupe_key = 'ai_run_estimate' and resolved_at is null") == [(0,)]
    month = datetime.now(UTC).strftime("%Y-%m")
    assert q("select severity from ops.alert where dedupe_key = %s and resolved_at is null", f"ai_budget:{month}") == [
        ("error",)]
    x("update ops.alert set resolved_at = now() where dedupe_key = %s", f"ai_budget:{month}")
    assert batch_requests([doc]) == []                                     # nothing was claimed either


@pytest.mark.db
def test_a_result_whose_input_changed_is_stored_stale_with_its_cost(monkeypatch):
    s, doc = one_statement(date(2050, 3, 16))
    fake = FakeClaude(["ok"], ends_after=None)
    daily(task([s], fake), datetime(2050, 3, 17, 12, tzinfo=UTC))
    real = ai_batch.load_prompt

    def v2(name, version):
        p = real(name, version)
        return Prompt(p.task, "v2", p.text + "\nOne more rule.\n")
    monkeypatch.setattr(ai_batch, "load_prompt", v2)
    fake.ends_after = 1
    assert daily(task([s], fake), datetime(2050, 3, 18, 12, tzinfo=UTC)) == 0
    r = last_run()
    got = outputs(doc)
    assert [(g[0], g[1], g[5]) for g in got] == [("doc_summary", "rejected", "v1"), ("doc_summary", "published", "v2")]
    assert got[0][2].startswith("stale:") and got[0][4] > 0                # billed, so its cost is kept
    assert r["cost"] == (got[0][4] + got[1][4]).quantize(Decimal("0.0001"))
    assert '"stale": 1' in r["checks"]["batches"]["detail"] and r["checks"]["batches"]["passed"]
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"ai_rejected:doc_summary:{doc}") == [(0,)]                    # stale isn't a citation rejection
    # The v1 prompt again: the stale request's input is a target's again, so it's sent again (its reply was never
    # checked), and its output supersedes v2's.
    monkeypatch.undo()
    assert daily(task([s], fake), datetime(2050, 3, 19, 12, tzinfo=UTC)) == 0
    assert fake.create_calls == 3 and [(g[1], g[5]) for g in outputs(doc)] == [
        ("rejected", "v1"), ("superseded", "v2"), ("published", "v1")]


@pytest.mark.db
def test_a_batch_the_api_doesnt_know_isnt_given_up_until_its_results_would_have_expired():
    """A 404 within 29 days might be passing (or the key's workspace changed): the batch's replies may have been
    billed, so its requests aren't sent again; the check fails. After 29 days the results are gone anyway."""
    s, doc = one_statement(date(2050, 4, 27))
    fake = FakeClaude(["ok"], ends_after=None)
    daily(task([s], fake), datetime(2050, 4, 28, 12, tzinfo=UTC))
    lost = next(iter(fake.batches))
    del fake.batches[lost]
    fake.ends_after = 1
    assert daily(task([s], fake), datetime(2050, 4, 29, 12, tzinfo=UTC)) == 0
    b = last_run()["checks"]["batches"]
    assert not b["passed"] and b["detail"].count("NotFoundError (404)") == 1     # once, however often it was read
    assert q("select status from ops.ai_batch where provider_batch_id = %s", lost) == [("submitted",)]
    assert fake.create_calls == 1 and batch_requests([doc]) == [("doc_summary", "pending", "", True)]
    x("update ops.ai_batch set submitted_at = submitted_at - interval '30 days' where provider_batch_id = %s", lost)
    assert daily(task([s], fake), datetime(2050, 4, 30, 12, tzinfo=UTC)) == 0
    assert q("select status from ops.ai_batch where provider_batch_id = %s", lost) == [("failed",)]
    assert fake.create_calls == 2 and [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]


@pytest.mark.db
def test_an_archived_batch_is_given_up_and_its_requests_sent_again():
    s, doc = one_statement(date(2051, 4, 26))
    fake = FakeClaude(["ok"], ends_after=None)
    daily(task([s], fake), datetime(2051, 4, 27, 12, tzinfo=UTC))
    archived = next(iter(fake.batches))
    fake.archive(archived)
    fake.ends_after = 1
    assert daily(task([s], fake), datetime(2051, 4, 28, 12, tzinfo=UTC)) == 0
    assert q("select status from ops.ai_batch where provider_batch_id = %s", archived) == [("failed",)]
    assert fake.create_calls == 2 and [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]


@pytest.mark.db
@pytest.mark.parametrize("cut", ["mid", "clean"])
def test_a_results_read_that_breaks_off_or_ends_short_is_finished_by_the_next_read(cut):
    d0, d1 = (date(2050, 6, 15), date(2050, 7, 27)) if cut == "mid" else (date(2050, 6, 16), date(2050, 7, 28))
    (s0, s1), old, new = two_statements(d0, d1)
    fake, tick = FakeClaude(["ok"], truncate=1, cut=cut), Ticker()
    assert daily(task([s0, s1], fake, tick=tick), datetime(2050, 7, 29, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert fake.create_calls == 1 and tick.sleeps == [30.0, 60.0]
    assert q("select count(*) from core.ai_output where document_id = any(%s)", [old, new]) == [(3,)]   # one each
    assert [x[1] for x in batch_requests([old, new])] == ["succeeded"] * 3
    assert r["checks"]["batches"]["passed"]                                # the broken read was finished


@pytest.mark.db
def test_a_request_that_errored_in_a_half_read_batch_waits_for_the_rest_of_the_read():
    """An errored result read before the read breaks off isn't sent again until its batch has been read to the end
    (so the rest of the read still maps every result), and not twice in one run."""
    (s0, s1), _, new = two_statements(date(2051, 1, 25), date(2051, 3, 15))
    fake = FakeClaude(["ok", "ok", "400", "ok"], truncate=1, cut="clean")   # the diff errors; its result is read first
    assert daily(task([s0, s1], fake), datetime(2051, 3, 16, 12, tzinfo=UTC)) == 0
    detail = last_run()["checks"]["batches"]["detail"]
    assert fake.create_calls == 1 and '"unknown"' not in detail and '"left_for_next_run": 1' in detail
    assert sorted(batch_requests([new])) == [("doc_summary", "succeeded", "", True),
                                             ("statement_diff_summary", "errored", "batch invalid_request_error", True)]
    assert daily(task([s0, s1], fake), datetime(2051, 3, 17, 12, tzinfo=UTC)) == 0
    assert fake.create_calls == 2 and sorted((g[0], g[1]) for g in outputs(new)) == [
        ("doc_summary", "published"), ("statement_diff_summary", "published")]


@pytest.mark.db
def test_the_worst_case_of_what_waits_in_open_batches_counts_against_the_budget():
    days = [date(2050, 9, 1) + timedelta(days=7 * i) for i in range(5)]
    slugs, docs = [], []
    for i, d in enumerate(days):
        s, e = meeting(d)
        slugs.append(s)
        docs.append(statement(d, e, FOMC_A, publish=i < 4))
    fake = FakeClaude(["ok"], ends_after=None)
    daily(task(slugs, fake), datetime(2050, 10, 2, 12, tzinfo=UTC))
    assert [len(b["requests"]) for b in fake.batches.values()] == [4]      # still processing
    x("update core.document set is_published = true where id = %s", docs[4])
    month = q("select coalesce(sum(ai_cost_usd), 0) from ops.task_run where started_at >= date_trunc('month', now())")
    # Room for the new request on its own (its worst case is about $0.0021), not with the four in the open batch.
    x("update ops.setting set value = %s where key = 'ai_monthly_budget_usd'", str(month[0][0] + Decimal("0.004")))
    try:
        daily(task(slugs, fake), datetime(2050, 10, 3, 12, tzinfo=UTC))
    finally:
        x("update ops.setting set value = '15' where key = 'ai_monthly_budget_usd'")
    spend = json.loads(last_run()["checks"]["ai_spend"]["detail"])
    assert fake.create_calls == 1 and spend["stopped"] == "budget"
    assert Decimal(spend["in_batches_worst_usd"]) >= Decimal("0.008")
    x("update ops.alert set resolved_at = now() where dedupe_key = %s and resolved_at is null",
      f"ai_budget:{datetime.now(UTC):%Y-%m}")
    fake.ends_after = 1
    assert daily(task(slugs, fake), datetime(2050, 10, 4, 12, tzinfo=UTC)) == 0
    assert fake.create_calls == 2 and [g[1] for d in docs for g in outputs(d)] == ["published"] * 5


@pytest.mark.db
def test_a_batch_whose_create_timed_out_is_found_and_adopted_not_sent_twice():
    s, doc = one_statement(date(2050, 10, 26))
    fake = FakeClaude(["ok"], creates=["lost", "ok"])
    assert daily(task([s], fake), datetime(2050, 10, 27, 12, tzinfo=UTC)) == 0
    b = last_run()["checks"]["batches"]
    assert fake.create_calls == 1 and [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]
    assert b["passed"] and '"adopted": ["msgbatch_' in b["detail"]
    assert batch_requests([doc]) == [("doc_summary", "succeeded", "", True)]


@pytest.mark.db
def test_a_create_that_timed_out_before_making_a_batch_is_sent_again_by_the_next_run():
    s, doc = one_statement(date(2050, 11, 30))
    fake = FakeClaude(["ok"], creates=["lost_before", "ok"])
    assert daily(task([s], fake), datetime(2050, 12, 1, 12, tzinfo=UTC)) == 0
    b = last_run()["checks"]["batches"]
    assert not b["passed"] and "create: APITimeoutError" in b["detail"] and fake.batches == {}
    assert batch_requests([doc]) == [("doc_summary", "errored", "batch APITimeoutError", False)]
    assert daily(task([s], fake), datetime(2050, 12, 2, 12, tzinfo=UTC)) == 0
    assert fake.create_calls == 2 and [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]


@pytest.mark.db
def test_another_tasks_batches_are_left_to_it():
    s, doc = one_statement(date(2051, 6, 14))
    jr = q("insert into ops.job_run (workflow, trigger, runner) values ('daily', 'cli', 'local') returning id")[0][0]
    tr = q("insert into ops.task_run (job_run_id, task_key) values (%s, %s) returning id", jr, FED_KEY)[0][0]
    other = q("""insert into ops.ai_batch (provider_batch_id, task_run_id, request_count)
                 values ('msgbatch_another_task_' || md5(random()::text), %s, 1) returning id""", tr)[0][0]
    x("""insert into ops.ai_request (custom_id, ai_batch_id, ai_task, document_id, input_sha256, model, prompt_version)
         values (%s, %s, 'doc_summary', %s, sha256('another task'::bytea), 'claude-haiku-5-5', 'v1')""",
      f"doc_summary:doc-{doc}:feedfacefeedface", other, doc)
    fake = FakeClaude(["ok"])
    assert daily(task([s], fake), datetime(2051, 6, 15, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert r["checks"]["batches"]["passed"] and q("select status from ops.ai_batch where id = %s", other) == [
        ("submitted",)]                                                    # never read (the fake would answer 404)
    assert json.loads(r["checks"]["ai_spend"]["detail"])["in_batches_worst_usd"] == "0.0000"
    assert [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]


@pytest.mark.db
def test_a_reply_the_database_refuses_is_stored_bare_with_its_cost_and_retried():
    s, doc = one_statement(date(2051, 7, 26))
    fake = FakeClaude(["nul", "ok"])
    assert daily(task([s], fake), datetime(2051, 7, 27, 12, tzinfo=UTC)) == 0
    r = last_run()
    got = outputs(doc)
    assert [(g[0], g[1]) for g in got] == [("doc_summary", "rejected"), ("doc_summary", "published")]
    assert got[0][2].startswith("retried: the reply couldn't be stored") and got[0][4] > 0
    assert fake.create_calls == 2 and r["cost"] == (got[0][4] + got[1][4]).quantize(Decimal("0.0001"))
    assert '"unstorable": 1' in r["checks"]["batches"]["detail"] and not r["checks"]["batches"]["passed"]


@pytest.mark.db
def test_a_result_another_run_stored_meanwhile_isnt_stored_again(monkeypatch):
    s, doc = one_statement(date(2051, 9, 20))
    fake = FakeClaude(["ok"])
    real = AiBatch._entry

    def entry(self, db, cid, doc_id, prompts, index):
        got = real(self, db, cid, doc_id, prompts, index)
        x("update ops.ai_request set status = 'succeeded' where custom_id = %s", cid)   # as another run would
        return got
    monkeypatch.setattr(AiBatch, "_entry", entry)
    assert daily(task([s], fake), datetime(2051, 9, 21, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert fake.create_calls == 1 and outputs(doc) == [] and not r["cost"]


def test_batches_are_split_under_the_request_and_size_limits():
    from ft.ai.client import Request, Source
    p = Prompt("doc_summary", "v1", "Summarise.")
    items = [(None, Request("doc_summary", p, (Source(i, "x" * 1000, "t"),), "Go."), f"c{i}", Decimal(0))
             for i in range(5)]
    assert [len(c) for c in ai_batch._chunks(items, max_requests=2)] == [2, 2, 1]
    assert [len(c) for c in ai_batch._chunks(items, max_bytes=2_500)] == [1, 1, 1, 1, 1]
    assert [len(c) for c in ai_batch._chunks(items)] == [5]


def test_a_batch_requests_worst_case_is_uncached_input_at_the_batch_price():
    from ft.ai.client import Request, Source
    r = Request("doc_summary", Prompt("doc_summary", "v1", "Summarise."), (Source(1, "x" * 3000, "t"),), "Go.")
    t = AiBatch()
    tokens, out = Decimal(r.estimated_tokens()), Decimal(r.spec.max_tokens)
    assert t._worst_case(r, batch=True) == (tokens * Decimal("0.10") + out * Decimal("0.50")) / 1_000_000 / 2
    assert t._worst_case(r) == (tokens * Decimal("0.125") + out * Decimal("0.50")) / 1_000_000   # a 5-minute write


def test_context_line():
    assert context_line("RBI", "MPC minutes", date(2026, 10, 1), "Minutes > Statement by Dr. Nagesh Kumar",
                        "Dr. Nagesh Kumar") == "RBI · MPC minutes · 1 Oct 2026 · Minutes > Statement by Dr. Nagesh Kumar"
    assert context_line("Fed", "FOMC statement", None, None, None) == "Fed · FOMC statement"


# ---------------------------------------------------------------------------------------------- the 28 Oct path
@pytest.mark.db
def test_decision_day_path_statement_then_summary_in_the_same_watch(s3, roster):  # noqa: F811
    """What D1 runs on 28 Oct: the watch polls; once policy.fed_documents stores and publishes the statement,
    policy.ai_batch (after_decision_document) summarises it in real time in the same poll, and the loop ends."""
    before, day = date(2045, 1, 25), date(2045, 3, 15)
    slugs = [fed_meeting(d) for d in (before, day)]
    fred(before - timedelta(days=1), "2", "2.25")
    pages = {fed_calendar.CALENDAR: calendar(before),
             url(before): named(before, "decided to maintain the target range for the federal funds rate at 2 to 2-1/4 "
                                "percent"),
             fed_statement.RSS: fed_rss(("Minutes of the Federal Open Market Committee, January 24-25, 2045",
                                         "https://www.federalreserve.gov/newsevents/pressreleases/monetary20450215a.htm",
                                         "Wed, 15 Feb 2045 19:00:00 GMT"))}
    sites = Sites(pages)
    http = fetch.HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    code = run(JOB, "backfill", [fed_task(roster, slugs, date(2045, 3, 1))], only=FED_KEY, force=True, http=http,
               storage=storage, clock=lambda: datetime(2045, 3, 1, tzinfo=UTC))
    assert code == 0

    fake, voyage = FakeClaude(["ok"]), FakeVoyage()
    docs, ai = fed_task(roster, slugs, date(2045, 3, 1)), task(slugs, fake, voyage)
    ai.run_after = (FED_KEY,)
    clock = Clock(datetime(2045, 3, 15, 14, 1, tzinfo=NY))
    http = fetch.HttpClient(transport=httpx.MockTransport(sites), sleep=lambda s: None)
    with JobRun(JOB, "watch", http=http, storage=storage, clock=clock) as job:
        def sleep(seconds):                    # the statement appears after the first poll
            pages[url(day)] = named(day, "decided to lower the target range for the federal funds rate by 1/4 "
                                    "percentage point to 1-3/4 to 2 percent")
            pages[fed_calendar.CALENDAR] = calendar(before, day)
            clock.sleep(seconds)
        polls = watch(job, [docs, ai], sleep=sleep)
        assert job.finish() == 0
    assert polls == 2 and fake.n == 2
    runs = q("""select task_key, status from ops.task_run where job_run_id = %s order by id""", job.id)
    assert runs == [(FED_KEY, "success"), (KEY, "skipped_not_due"), (FED_KEY, "success"), (KEY, "success")]
    doc = q("""select d.id from core.document d join core.event e on e.id = d.event_id
                where e.slug = %s and d.doc_type = 'fomc_statement' and d.is_published""", slugs[1])[0][0]
    assert [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published"), ("statement_diff_summary", "published")]
    assert q("select count(*) from core.chunk_embedding where document_id = %s", doc)[0][0] > 0


# ---------------------------------------------------------------------------------------------- S2 (#197): no AI
def month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def paused_alert():
    return q("select severity, message from ops.alert where dedupe_key = %s and resolved_at is null",
             f"ai_unavailable:{month()}")


def resolve_paused() -> None:
    x("update ops.alert set resolved_at = now() where dedupe_key = %s and resolved_at is null",
      f"ai_unavailable:{month()}")


def without_key(t: AiBatch) -> AiBatch:
    t.claude = None                                         # built from ANTHROPIC_API_KEY on first use
    return t


@pytest.mark.db
def test_with_no_anthropic_key_a_decision_day_skips_sends_nothing_and_the_run_is_green(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (s0, s1), _, new = two_statements(date(2052, 1, 23), date(2052, 3, 12))
    fake, voyage = FakeClaude(["ok"]), FakeVoyage()
    _, code = watch_once(without_key(task([s0, s1], fake, voyage)), decision_day(date(2052, 3, 12)))
    r = last_run()
    assert code == 0 and r["status"] == "success", r["error"]
    assert fake.requests == [] and outputs(new) == [] and requests(new) == []      # nothing sent, no ops.ai_request row
    ok = r["checks"]["ai_available"]
    assert not ok["passed"] and "skipped: AI unavailable (no_key)" in ok["detail"]
    assert r["checks"]["embeddings"]["passed"]                                      # Voyage has its own key
    (sev, msg) = paused_alert()[0]
    assert sev == "warn" and "no API key" in msg and "published outputs" in msg
    resolve_paused()


@pytest.mark.db
def test_with_no_anthropic_key_a_daily_run_skips_and_a_later_run_with_the_key_publishes(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s, doc = one_statement(date(2052, 6, 18))
    fake = FakeClaude(["ok"])
    assert daily(without_key(task([s], fake)), datetime(2052, 6, 19, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 0 and requests(doc) == [] and outputs(doc) == []
    assert not r["checks"]["ai_available"]["passed"] and len(paused_alert()) == 1
    assert daily(task([s], fake), datetime(2052, 6, 20, 12, tzinfo=UTC)) == 0       # the key is back
    assert [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]
    assert last_run()["checks"]["ai_available"]["passed"] and paused_alert() == []  # the alert resolves itself


@pytest.mark.db
def test_a_billing_error_mid_run_stops_further_requests_keeps_the_stored_reply_and_the_run_is_green():
    (s0, s1), _, new = two_statements(date(2052, 8, 14), date(2052, 10, 2))
    fake = FakeClaude(["ok", "402"])
    _, code = watch_once(task([s0, s1], fake), decision_day(date(2052, 10, 2)))
    r = last_run()
    assert code == 0 and r["status"] == "success", r["error"]
    assert len(fake.requests) == 2                                                  # the second one was refused: no more
    assert [(g[0], g[1]) for g in outputs(new)] == [("doc_summary", "published")]   # the first reply stands
    assert [x[:3] for x in requests(new)] == [("doc_summary", "succeeded", ""),
                                             ("statement_diff_summary", "errored", "unavailable billing")]
    assert not r["checks"]["ai_available"]["passed"] and "billing" in r["checks"]["ai_available"]["detail"]
    assert "no API credits left" in paused_alert()[0][1]
    resolve_paused()


@pytest.mark.db
def test_a_refused_key_in_a_batch_create_skips_and_the_next_run_sends_the_requests():
    s, doc = one_statement(date(2052, 11, 20))
    fake = FakeClaude(["ok"], creates=["401", "ok"])
    assert daily(task([s], fake), datetime(2052, 11, 21, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 1 and outputs(doc) == []
    assert batch_requests([doc]) == [("doc_summary", "errored", "batch unavailable auth", False)]
    assert not r["checks"]["ai_available"]["passed"] and "auth" in r["checks"]["ai_available"]["detail"]
    resolve_paused()
    assert daily(task([s], fake), datetime(2052, 11, 22, 12, tzinfo=UTC)) == 0
    assert [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]


@pytest.mark.db
def test_a_refused_voyage_key_skips_the_embeddings_and_the_summaries_still_publish():
    s, doc = one_statement(date(2053, 2, 12))

    fake = FakeClaude(["ok"])
    assert daily(task([s], fake, FakeVoyage(fail=[VoyageAPIError(401, "invalid key")])), datetime(2053, 2, 13, 12, tzinfo=UTC)) == 0
    r = last_run()
    assert r["status"] == "success" and [(g[0], g[1]) for g in outputs(doc)] == [("doc_summary", "published")]
    assert not r["checks"]["embeddings"]["passed"] and "skipped: AI unavailable (auth)" in r["checks"]["embeddings"]["detail"]
    assert not r["checks"]["ai_available"]["passed"]
    assert q("select count(*) from core.chunk_embedding where document_id = %s", doc)[0][0] == 0
    resolve_paused()


class DataTask(Task):
    key = DATA_KEY
    workflows = ("daily",)
    loaded = 0

    def load(self, tx, fetched, tr):
        DataTask.loaded += 1
        return 1

    def checks(self, tx, fetched, tr):
        return [Check("loaded", True, "")]


@pytest.mark.db
def test_a_run_with_a_data_task_and_the_ai_task_without_a_key_loads_the_data_and_is_green(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s, doc = one_statement(date(2053, 5, 21))
    DataTask.loaded = 0
    code = run(JOB, "daily", [DataTask(), without_key(task([s], FakeClaude(["ok"])))], force=True,
               clock=lambda: datetime(2053, 5, 22, 12, tzinfo=UTC))
    assert code == 0 and DataTask.loaded == 1
    assert last_run(DATA_KEY)["status"] == "success" and last_run()["status"] == "success"
    assert outputs(doc) == [] and not last_run()["checks"]["ai_available"]["passed"]
    resolve_paused()
