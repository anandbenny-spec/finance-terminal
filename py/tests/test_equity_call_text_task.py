"""equity.call_text (job 2B-09) through the runner on the test database: the calls are written by equity.call_transcripts from
the saved replies and PDFs in data/fixtures/equity/calls (the same mock transport as test_equity_call_fetch.py; these tests
skip without the private fixtures), then this task cuts turns, chunks and publishes them."""
import itertools

import boto3
import httpx
import psycopg
import pytest
from moto.server import ThreadedMotoServer
from test_equity_call_fetch import (
    ADMIN,
    BUCKET,
    ENDPOINT,
    JOB,
    NOW,
    PORT,
    Web,
    go,
    make_company,
    needs_fixtures,
    q,
    storage,
    wipe,
)

from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import equity_call_text as ect

KEY2 = "test.equity_call_text"
HUL = "zf-hindunilvr"


class Text(ect.EquityCallText):
    key = KEY2


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe_all()
    yield
    wipe_all()


def wipe_all():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zf-%')"
        c.execute(f"delete from equity.call_turn where document_id in (select id from core.document where entity_id in {ids})")
    wipe()


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        for key in (KEY2, "test.equity_call_fetch"):
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'call text test', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (key,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


def go_text(expect="success"):
    t = Text()
    t.workflows = ("weekly", "backfill")
    web = Web()
    http = HttpClient(transport=httpx.MockTransport(web), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "backfill", [t], only=KEY2, force=True, http=http, storage=storage, clock=lambda: NOW)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1", KEY2)[0]
    assert tr[0] == expect, ("RUN " + tr[0] + " " + (tr[3] or "")[-500:] + " " + str([c for c in tr[2] if not c["passed"]])).replace("\n", " | ")
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def x(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql, a or None)


def as_anon(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("set role anon")
        return c.execute(sql, a).fetchall()


def call(slug):
    return q("""select e.slug, e.status, e.is_published, c.transcript_document_id, c.words, c.unknown_role_share, c.status_reason
                  from equity.earnings_call c join core.event e on e.id = c.event_id
                  join core.entity en on en.id = c.entity_id where en.slug = %s""", slug)[0]


def setup_hul():
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    go(Web())
    x("update core.entity set is_published = true where slug like 'zf-%'")


@pytest.mark.db
@needs_fixtures
def test_a_stored_call_gets_turns_and_chunks_and_is_published_with_its_text_private(s3):
    setup_hul()
    c0 = call(HUL)
    assert c0[1] == "stored" and c0[2] is False and c0[5] is None
    code, tr = go_text()
    assert code == 0 and tr[1] > 0
    c = checks(tr)
    assert c["text_matches"]["passed"] and c["chunks_reproduce"]["passed"] and c["turns_contiguous"]["passed"] and c["chunks_stored"]["passed"]
    _, _, published, doc, words, share, _ = call(HUL)
    assert published is True and 0 <= share <= 1 and words == c0[4]                  # words stay as 2B-07 wrote them
    assert q("select is_published, chunked_at is not null from core.document where id = %s", doc)[0] == (True, True)
    turns = q("select turn_no, char_start, char_end, speaker, role, section, words from equity.call_turn where document_id = %s order by turn_no", doc)
    n_chars = q("select char_count from core.document where id = %s", doc)[0][0]
    assert len(turns) > 30 and turns[-1][2] == n_chars
    assert all(a[2] == b[1] for a, b in itertools.pairwise(turns)) and all(t[2] > t[1] for t in turns)
    assert {t[4] for t in turns} >= {"moderator", "management", "analyst"} and {t[5] for t in turns} <= {"opening", "qa", "closing"}
    chunks = q("select char_start, char_end, speaker, section from core.chunk where document_id = %s order by chunk_no", doc)
    assert chunks and chunks[0][0] == 0 and chunks[-1][1] == n_chars
    for s, e, speaker, section in chunks:                                   # no chunk crosses a turn
        owner = [t for t in turns if t[1] <= s < t[2]]
        if not owner:                                                       # the text before the first label
            assert e <= turns[0][1] and speaker is None and section is None
            continue
        assert e <= owner[0][2] and speaker == owner[0][3]
        assert section == {"opening": "Opening remarks", "qa": "Q&A", "closing": "Closing remarks"}[owner[0][5]]
    assert q("select count(*) from core.chunk where document_id = %s and body is not null", doc)[0][0] == len(chunks)
    # the text stays private: anon reads the document row and its page rows, never a chunk or a turn
    assert as_anon("select count(*) from api.documents where id = %s", doc)[0][0] == 1
    assert as_anon("select count(*) from equity.transcript_page where document_id = %s", doc)[0][0] > 0
    assert as_anon("select count(*) from api.document_chunks where document_id = %s", doc)[0][0] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_anon("select count(*) from equity.call_turn")
    assert as_anon("select count(*) from equity.earnings_call where entity_id = (select id from core.entity where slug = %s)", HUL)[0][0] == 1
    assert checks(tr)["calls_analysed"]["passed"] and "1 calls analysed" in checks(tr)["calls_analysed"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_second_run_writes_nothing(s3):
    setup_hul()
    go_text()
    tables = ("core.chunk", "equity.call_turn", "core.document", "core.event", "equity.earnings_call")
    snap = [q(f"select count(*) from {t}")[0][0] for t in tables]
    stamp = q("select updated_at from core.event where slug like 'zf-%'")
    code, tr = go_text()
    assert code == 0 and tr[1] == 0
    assert snap == [q(f"select count(*) from {t}")[0][0] for t in tables] and stamp == q("select updated_at from core.event where slug like 'zf-%'")


@pytest.mark.db
@needs_fixtures
def test_a_revised_version_of_the_transcript_gets_no_turns_or_chunks(s3):
    setup_hul()
    doc = call(HUL)[3]
    rev = q("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url, raw_file_id,
                                          version_no, supersedes_id, text_sha256, char_count, extractor_version)
               select doc_type, entity_id, event_id, title, source_id, external_key, source_url, raw_file_id, 2, id, text_sha256,
                      char_count, extractor_version from core.document where id = %s returning id""", doc)[0][0]
    go_text()
    assert q("select count(*) from core.chunk where document_id = %s", rev)[0][0] == 0
    assert q("select count(*) from equity.call_turn where document_id = %s", rev)[0][0] == 0
    assert q("select is_published, chunked_at is null from core.document where id = %s", rev)[0] == (False, True)
    assert q("select count(*) from core.chunk where document_id = %s", doc)[0][0] > 0


@pytest.mark.db
@needs_fixtures
def test_a_call_held_on_its_quarter_is_not_analysed_or_published_and_is_named(s3):
    setup_hul()
    x("update equity.earnings_call set status_reason = 'quarter_conflict' where entity_id = (select id from core.entity where slug = %s)", HUL)
    _, tr = go_text()
    doc = call(HUL)[3]
    assert call(HUL)[2] is False
    assert q("select count(*) from core.chunk where document_id = %s", doc)[0][0] == 0
    assert q("select count(*) from equity.call_turn where document_id = %s", doc)[0][0] == 0
    assert not checks(tr)["quarter_conflicts"]["passed"] and HUL in checks(tr)["quarter_conflicts"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_a_call_on_the_company_website_has_its_event_published_with_no_turns(s3):
    make_company("MARUTI", "Maruti Suzuki India Limited")
    go(Web())
    assert call("zf-maruti")[1] == "on_company_website" and call("zf-maruti")[2] is False
    go_text()
    assert call("zf-maruti")[2] is True
    assert q("select count(*) from equity.call_turn")[0][0] == 0


@pytest.mark.db
@needs_fixtures
def test_chunks_that_cross_a_turn_fail_the_run_and_nothing_is_published(s3, monkeypatch):
    setup_hul()
    from ft.engines.document.chunk import chunk_text as real
    monkeypatch.setattr(ect, "chunk_text", lambda ex, **kw: real(ex, target=10**6, max_chars=10**7, min_tail=0))
    _, tr = go_text(expect="checks_failed")
    assert not checks(tr)["chunks_reproduce"]["passed"]
    doc = call(HUL)[3]
    assert call(HUL)[2] is False and q("select count(*) from core.chunk where document_id = %s", doc)[0][0] == 0
    assert q("select count(*) from equity.call_turn where document_id = %s", doc)[0][0] == 0
