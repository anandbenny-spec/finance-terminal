"""equity.call_text (job 2B-09) through the runner on the test database: the calls are written by equity.call_transcripts from
the saved replies and PDFs in data/fixtures/equity/calls (the same mock transport as test_equity_call_fetch.py; these tests
skip without the private fixtures), then this task cuts turns, chunks and publishes them."""
import itertools
from datetime import timedelta
from types import SimpleNamespace

import boto3
import httpx
import psycopg
import pytest
from moto.server import ThreadedMotoServer
from test_equity_call_fetch import (
    ADMIN,
    BUCKET,
    ENDPOINT,
    HUL_FILE,
    JOB,
    NOW,
    PORT,
    Web,
    go,
    make_company,
    needs_fixtures,
    nse_rows,
    q,
    storage,
    wipe,
)

from ft.equity.calls import discovery as dc
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


def go_text(expect="success", now=NOW):
    t = Text()
    t.workflows = ("weekly", "backfill")
    web = Web()
    http = HttpClient(transport=httpx.MockTransport(web), sleep=lambda s: None, clock=lambda: now)
    code = run(JOB, "backfill", [t], only=KEY2, force=True, http=http, storage=storage, clock=lambda: now)
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


class OldHul(Web):
    """HUL's saved reply cut to its Q1 FY27 transcript row, re-dated to 3 Feb 2025 (season Q3 FY25): a history call."""

    def handle(self, request, chrome):
        if request.url.path == dc.NSE_ANN_PATH:
            row = next(r for r in nse_rows("HINDUNILVR") if str(r.get("attchmntFile", "")).endswith(HUL_FILE))
            stamp = "03-Feb-2025 17:13:19"
            return httpx.Response(200, json=[{**row, "exchdisstime": stamp, "an_dt": stamp, "sort_date": stamp, "seq_id": "999000001"}])
        return super().handle(request, chrome)


@pytest.mark.db
@needs_fixtures
def test_a_call_of_a_history_season_gets_turns_roles_chunks_and_publication_like_a_2b_lite_call(s3):
    make_company("HINDUNILVR", "Hindustan Unilever Limited")
    go(OldHul())                                            # a backfill with no --since reads from history_from
    x("update core.entity set is_published = true where slug like 'zf-%'")
    assert q("""select c.season_key, e.status from equity.earnings_call c join core.event e on e.id = c.event_id
                 join core.entity en on en.id = c.entity_id where en.slug = %s""", HUL) == [("q3-fy25", "stored")]
    code, tr = go_text()
    assert code == 0 and tr[1] > 0
    c = checks(tr)
    assert c["text_matches"]["passed"] and c["chunks_reproduce"]["passed"] and c["turns_contiguous"]["passed"] and c["chunks_stored"]["passed"]
    _, _, published, doc, _, share, _ = call(HUL)
    assert published is True and 0 <= share <= 1
    assert q("select is_published, chunked_at is not null from core.document where id = %s", doc)[0] == (True, True)
    assert q("select count(*) from equity.call_turn where document_id = %s", doc)[0][0] > 30
    assert q("select count(*) from core.chunk where document_id = %s", doc)[0][0] > 0
    assert {r[0] for r in q("select distinct role from equity.call_turn where document_id = %s", doc)} >= {"moderator", "management", "analyst"}
    assert {r[0] for r in q("select distinct section from equity.call_turn where document_id = %s", doc)} <= {"opening", "qa", "closing"}


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
def test_a_call_set_aside_and_restored_is_published_again_without_being_rewritten(s3):
    setup_hul()
    go_text()
    doc = call(HUL)[3]
    chunks = q("select count(*) from core.chunk where document_id = %s", doc)[0][0]
    x("""update core.event set is_published = false where event_type = 'earnings_call'
            and entity_id = (select id from core.entity where slug = %s)""", HUL)               # what the demotion of a general meeting does
    x("update core.document set is_published = false where id = %s", doc)
    assert call(HUL)[2] is False
    code, tr = go_text()
    assert code == 0 and tr[1] == 2                                                         # the document and the event, nothing else
    assert call(HUL)[2] is True and q("select is_published from core.document where id = %s", doc) == [(True,)]
    assert q("select count(*) from core.chunk where document_id = %s", doc)[0][0] == chunks
    code, tr = go_text()
    assert code == 0 and tr[1] == 0


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


@pytest.mark.db
@needs_fixtures
def test_a_published_call_whose_stored_roles_differ_from_the_rules_gets_them_rewritten_and_its_chunks_kept(s3):
    setup_hul()
    go_text()
    doc = call(HUL)[3]
    want = q("select turn_no, role from equity.call_turn where document_id = %s order by turn_no", doc)
    chunks = q("select chunk_no, char_start, char_end, section, speaker from core.chunk where document_id = %s order by chunk_no", doc)
    x("update equity.call_turn set role = 'unknown' where document_id = %s", doc)
    x("update equity.earnings_call set unknown_role_share = 1 where transcript_document_id = %s", doc)
    # A new role rule changes the analysis version (2B-09b): a call analysed under the old one is re-derived once.
    x("update equity.earnings_call set analysis_version = 'the rules before the change' where transcript_document_id = %s", doc)
    _, tr = go_text()
    assert tr[1] > 0
    assert q("select turn_no, role from equity.call_turn where document_id = %s order by turn_no", doc) == want
    assert q("select unknown_role_share from equity.earnings_call where transcript_document_id = %s", doc)[0][0] < 1
    assert q("select chunk_no, char_start, char_end, section, speaker from core.chunk where document_id = %s order by chunk_no", doc) == chunks
    _, tr = go_text()
    assert tr[1] == 0                                                         # and now a second run writes nothing


@pytest.mark.db
@needs_fixtures
def test_a_turn_that_opens_inside_a_paragraph_is_cut_into_chunks_inside_one_turn_and_the_text_stays(s3, monkeypatch):
    """#542: a rule change finds an inline generic label in an analysed call. The call is re-derived, the label opens a
    turn at its offset inside a paragraph, the chunks are cut again so each lies inside one turn, and the document's text
    (its length and hash) is unchanged."""
    from ft.equity.calls import turns as rules
    setup_hul()
    go_text()
    doc = call(HUL)[3]
    text_before = q("select char_count, text_sha256 from core.document where id = %s", doc)[0]
    n_turns = q("select count(*) from equity.call_turn where document_id = %s", doc)[0][0]
    cut: list[int] = []

    def one_label(ex, named):                                   # a sentence's end inside a Q&A paragraph, after its label
        for t in named[len(named) // 2:]:
            b = ex.blocks[t.block_start]
            at = ex.text.find(". ", b.start + 40, b.end - 40)
            if at > 0:
                cut[:] = [at + 2]
                return [rules.InlineLabel(at + 2, at + 2, "Management")]
        raise AssertionError("no paragraph to cut")
    monkeypatch.setattr(rules, "inline_labels", one_label)
    x("update equity.earnings_call set analysis_version = 'the rules before the change' where transcript_document_id = %s", doc)
    _, tr = go_text()
    c = checks(tr)
    assert all(c[k]["passed"] for k in ("text_matches", "chunks_reproduce", "turns_contiguous", "chunks_stored"))
    turns = q("select turn_no, char_start, char_end, speaker, role from equity.call_turn where document_id = %s order by turn_no", doc)
    assert len(turns) == n_turns + 1
    new = [t for t in turns if t[1] == cut[0]]
    assert len(new) == 1 and new[0][3:] == ("Management", "management")
    assert all(a[2] == b[1] for a, b in itertools.pairwise(turns))
    chunks = q("select char_start, char_end, speaker from core.chunk where document_id = %s order by chunk_no", doc)
    assert any(s == cut[0] and speaker == "Management" for s, _, speaker in chunks)
    for s, e, _ in chunks:
        assert e <= turns[0][1] or any(t[1] <= s and e <= t[2] for t in turns)        # inside one turn (or before the first)
    assert q("select char_count, text_sha256 from core.document where id = %s", doc)[0] == text_before


# ---------------------------------------------------------------------------------------------- 2B-09b: the analysis version
SLICES = 4


@pytest.fixture
def reads(monkeypatch):
    """The sizes of the stored files `Fetcher.read` returns, in order: one entry per stored PDF read from Storage."""
    from ft.jobs.fetch import Fetcher
    real, seen = Fetcher.read, []

    def counting(self, raw_file_id):
        data = real(self, raw_file_id)
        seen.append(len(data))
        return data
    monkeypatch.setattr(Fetcher, "read", counting)
    return seen


def hul_event_id():
    return q("""select c.event_id from equity.earnings_call c join core.entity en on en.id = c.entity_id where en.slug = %s""", HUL)[0][0]


def stored_version():
    return q("select analysis_version from equity.earnings_call where event_id = %s", hul_event_id())[0][0]


def run_time(inside: bool, week: int = 0):
    """A weekly run's clock in which HUL's call is (or isn't) in the integrity slice, from `week` weeks after NOW."""
    eid = hul_event_id()
    return next(NOW + timedelta(weeks=k) for k in range(week, week + 2 * SLICES)
                if ect.in_slice(eid, (NOW + timedelta(weeks=k)).date(), SLICES) == inside)


@pytest.mark.db
@needs_fixtures
def test_an_analysed_call_is_read_once_then_skipped_until_its_slice_comes_and_nothing_is_written(s3, reads):
    setup_hul()
    assert stored_version() is None
    reads.clear()
    _, tr = go_text(now=run_time(False))                                        # (d)/(b): new, never analysed: read, version written
    assert len(reads) == 1 and tr[1] > 0 and stored_version() == ect.analysis_version()
    detail = checks(tr)["calls_analysed"]["detail"]
    assert "1 calls analysed" in detail and "0 skipped" in detail and "1 stored files read" in detail and "MB" in detail
    assert checks(tr)["analysis_versions"]["passed"]
    tables = ("core.chunk", "equity.call_turn", "core.document", "core.event", "equity.earnings_call")
    snap = [q(f"select count(*) from {t}")[0][0] for t in tables]
    stamp = q("select updated_at from core.event where slug like 'zf-%'")

    reads.clear()
    _, tr = go_text(now=run_time(False))                                        # (a) current version, outside the slice
    assert reads == [] and tr[1] == 0                                           # (g) not read, and a second run writes nothing
    c = checks(tr)
    assert "1 skipped" in c["calls_analysed"]["detail"] and "0 stored files read" in c["calls_analysed"]["detail"]
    assert c["text_matches"]["passed"] and "0 transcripts re-derived" in c["text_matches"]["detail"]
    assert snap == [q(f"select count(*) from {t}")[0][0] for t in tables] and stamp == q("select updated_at from core.event where slug like 'zf-%'")

    reads.clear()
    _, tr = go_text(now=run_time(True))                                         # the integrity slice reads it, still unchanged
    assert len(reads) == 1 and tr[1] == 0 and stored_version() == ect.analysis_version()
    assert "1 in this run's integrity slice" in checks(tr)["text_matches"]["detail"]


@pytest.mark.db
@needs_fixtures
def test_over_four_consecutive_weekly_runs_with_unchanged_rules_a_call_is_read_exactly_once(s3, reads):
    setup_hul()
    go_text()
    reads.clear()
    for k in range(SLICES):
        _, tr = go_text(now=NOW + timedelta(weeks=k))
        assert tr[1] == 0
    assert len(reads) == 1


@pytest.mark.db
@needs_fixtures
def test_a_changed_rule_makes_every_call_read_again_once_and_records_the_new_version(s3, reads, monkeypatch):
    setup_hul()
    go_text(now=run_time(False))
    old = stored_version()
    monkeypatch.setattr(ect, "analysis_version", lambda: "after-a-rule-change")                 # (c)
    reads.clear()
    _, tr = go_text(now=run_time(False))
    assert len(reads) == 1 and tr[1] == 1 and stored_version() == "after-a-rule-change" != old       # unchanged text: the version row
    assert "1 with no or another analysis version" in checks(tr)["text_matches"]["detail"]
    reads.clear()
    _, tr = go_text(now=run_time(False))                                                        # and then skipped again
    assert reads == [] and tr[1] == 0


@pytest.mark.db
@needs_fixtures
def test_a_file_that_no_longer_hashes_to_the_stored_text_fails_the_check_when_it_is_read_and_the_version_stays(s3, monkeypatch):
    setup_hul()
    go_text(now=run_time(False))
    monkeypatch.setattr(ect.pdfs, "call_text", lambda info: SimpleNamespace(extracted=SimpleNamespace(sha256=bytes(32)), labels=[]))
    _, tr = go_text(now=run_time(False))                                                        # skipped: the check can't see it
    assert checks(tr)["text_matches"]["passed"]
    _, tr = go_text(expect="checks_failed", now=run_time(True))                                 # (f) the integrity slice catches it
    assert not checks(tr)["text_matches"]["passed"] and HUL in checks(tr)["text_matches"]["detail"]
    assert stored_version() == ect.analysis_version() and call(HUL)[2] is True                  # nothing was rolled forward


@pytest.mark.db
@needs_fixtures
def test_without_migration_0033_every_call_is_read_nothing_is_recorded_and_a_warning_says_so(s3, reads, monkeypatch):
    setup_hul()
    go_text(now=run_time(False))
    x("update equity.earnings_call set analysis_version = null")
    monkeypatch.setattr(ect, "_has_version_column", lambda db: False)                          # as on a project not migrated yet
    reads.clear()
    _, tr = go_text(now=run_time(False))
    assert len(reads) == 1 and tr[1] == 0 and stored_version() is None
    c = checks(tr)
    assert not c["analysis_versions"]["passed"] and "0033" in c["analysis_versions"]["detail"]
    assert c["text_matches"]["passed"] and "1 read because versions aren't recorded here yet" in c["calls_analysed"]["detail"]
