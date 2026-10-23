"""policy.ai_batch, part 1, through the runner and the watch loop against the test database as job_writer (Prompt 01
B7; Architecture 03 §1.1-§1.5; Spec 01 §3.2-§3.3). The Messages API and Voyage are fakes (tests/ai_fakes.py); the
Anthropic SDK itself runs. Meetings are synthetic: 2045 for the decision-day path with policy.fed_documents, 2046-2048
for the rest; the task sees only each test's meetings (`scope`)."""
from datetime import UTC, date, datetime, timedelta
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

from ft.ai.client import Prompt, halfvec_literal
from ft.engines.document.chunk import chunk_text
from ft.engines.document.diff import diff_texts
from ft.engines.document.store import DocumentRef, save_chunks, save_diff, save_version
from ft.jobs import due as due_rules
from ft.jobs import fetch
from ft.jobs.runner import JobRun, connect, run
from ft.jobs.tasks import ai_batch
from ft.jobs.tasks.ai_batch import AiBatch, context_line
from ft.jobs.watch import watch
from ft.policy import fed_calendar, fed_statement

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
NY = ZoneInfo("America/New_York")
KEY = "test.b7_ai_batch"
FED_KEY = "test.b5_fed_documents"


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
        for k, wf in ((KEY, "{daily,watch}"), (FED_KEY, "{daily,watch}")):
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


def task(slugs, fake: FakeClaude, voyage: FakeVoyage | None = None) -> AiBatch:
    class T(AiBatch):
        key = KEY
    t = T()
    t.scope = frozenset(slugs)
    t.workflows, t.rules = ("daily", "watch"), {"daily": "every 20 hours", "watch": "after_decision_document"}
    t.claude = claude(fake)
    from ft.ai.client import Embedder
    t.embedder = Embedder(client=voyage or FakeVoyage())
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
        assert first["status"] == "failed" and "NoReply" in first["error"] and outputs(new) == []
        assert requests(new) == [("doc_summary", "errored", "BadRequestError 400", False)]
        job.run_pass([t], record_skips=False)                              # the next poll
        assert last_run()["status"] == "success"
        assert job.finish() == 0                                            # the last outcome counts
    assert [(g[0], g[1]) for g in outputs(new)] == [("doc_summary", "published"), ("statement_diff_summary", "published")]


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
def test_with_real_time_off_and_on_other_days_it_waits_for_the_batch_path_and_only_embeds():
    (s0, s1), old, new = two_statements(date(2047, 4, 30), date(2047, 6, 18))
    unpublished_base = statement(date(2047, 6, 19), meeting(date(2047, 6, 19))[1], FOMC_A, publish=False)
    fake, voyage = FakeClaude(["ok"]), FakeVoyage()
    # The real-time switch off: a decision day goes to the batch path too (B7 part 2), and only embeds.
    x("update ops.setting set value = 'false' where key = 'ai_realtime_on_decision_days'")
    try:
        watch_once(task([s0, s1], fake, voyage), decision_day(date(2047, 6, 18)))
    finally:
        x("update ops.setting set value = 'true' where key = 'ai_realtime_on_decision_days'")
    bp = last_run()["checks"]["batch_path"]
    assert fake.requests == [] and not bp["passed"] and "2 requests wait" in bp["detail"]
    assert q("select count(*) from core.chunk_embedding where document_id = %s", new)[0][0] > 0
    # A daily run: every published document in scope waits for the batch path; their chunks are embedded.
    code = run(JOB, "daily", [task([s0, s1], fake, voyage)], only=KEY, force=True,
               clock=lambda: datetime(2047, 6, 19, 12, tzinfo=UTC))
    r = last_run()
    assert code == 0 and fake.requests == [] and outputs(new) == []
    bp = r["checks"]["batch_path"]
    assert not bp["passed"] and "3 requests wait" in bp["detail"] and "statement_diff_summary 1" in bp["detail"]
    chunks = q("select count(*) from core.chunk where document_id in (%s, %s)", old, new)[0][0]
    assert q("select count(*) from core.chunk_embedding where document_id in (%s, %s)", old, new)[0][0] == chunks
    assert q("select count(*) from core.chunk_embedding where document_id = %s", unpublished_base)[0][0] == 0


@pytest.mark.db
def test_no_diff_summary_while_the_base_is_unpublished_and_a_voyage_error_doesnt_fail_the_run():
    s0, e0 = meeting(date(2047, 7, 30))
    s1, e1 = meeting(date(2047, 9, 17))
    old = statement(date(2047, 7, 30), e0, FOMC_A, publish=False)
    new = statement(date(2047, 9, 17), e1, FOMC_B, base=old)
    from voyageai import error as verr
    fake = FakeClaude(["ok"])
    voyage = FakeVoyage(fail=[verr.AuthenticationError("bad key")])
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
