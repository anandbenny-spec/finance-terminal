"""equity.call_scores through the runner (job 2B-17, #180): a call compared with the previous fiscal quarter's, no row without
a previous call, without a management opening or when either call is role-held, a new speaker, a re-run that writes
nothing, a row removed when its call stops being comparable, and the checks. The rules are tested in
test_equity_call_remarks.py. Synthetic text only."""
import hashlib
import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest
from conftest import dsn

from ft.jobs.runner import run
from ft.jobs.tasks.equity_call_scores import EquityCallScores

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
KEY = "test.equity_call_scores"
D = date
NOW = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)

OLD_BODY = "Alpha beta gamma delta. Epsilon zeta eta theta. Iota kappa lambda mu."
NEW_BODY = "Alpha beta gamma delta. Epsilon zeta eta theta. Nu xi omicron pi rho."


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def x(sql, *a) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql, a or None)


@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '2B-17 test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


def wipe() -> None:
    ids = "(select id from core.entity where slug like 'cs-%')"
    docs = f"(select id from core.document where entity_id in {ids})"
    with psycopg.connect(ADMIN) as c:           # one transaction: the chunk check is deferred to commit, and the document is gone then
        c.execute(f"delete from equity.call_remarks_change where event_id in (select event_id from equity.earnings_call where entity_id in {ids})")
        c.execute(f"delete from equity.call_turn where document_id in {docs}")
        c.execute(f"delete from core.chunk where document_id in {docs}")
        c.execute(f"delete from equity.earnings_call where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'cs-%'")
        c.execute("delete from ops.alert where task_key = %s", (KEY,))


@pytest.fixture(autouse=True)
def clean():
    wipe()
    yield
    wipe()


def company() -> tuple[int, str]:
    slug = f"cs-{uuid.uuid4().hex[:8]}"
    eid = q("""insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
               values ('company', %s, 'Acme Limited', 'Acme', 'IN', 'INR', true) returning id""", slug)[0][0]
    return eid, slug


def call(eid: int, slug: str, day: date, label: str, turns: list[tuple[str, str, str, str]], share=0.0,
         status="stored") -> int:
    """A stored call whose transcript is the (speaker, role, section, body) turns laid out with their labels."""
    season = "q" + label[1] + "-fy" + label[-2:]
    text, spans = "", []
    for speaker, role, section, body in turns:
        block = f"{speaker}: {body}\n"
        spans.append((len(text), len(text) + len(block) - 1, speaker, role, section, len(block.split())))
        text += block
    ev = q("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
              select 'earnings_call', %s, %s, 'call', %s, %s, s.id, true from core.source s where s.key = 'nse'
              returning id""", eid, f"{slug}-call-{season}", day, status)[0][0]
    doc = q("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                          char_count, text_sha256, chunked_at, is_published)
               select 'call_transcript', %s, %s, 'transcript', s.id, %s, 'https://example.invalid/t.pdf', %s, %s, now(), true
                 from core.source s where s.key = 'nse' returning id""",
            eid, ev, f"/test/cs/{slug}-{season}.pdf", len(text), hashlib.sha256(text.encode()).digest())[0][0]
    half = len(text) // 2
    x("""insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
         values (%s, 0, 0, %s, 'Opening', null, %s), (%s, 1, %s, %s, 'Opening', null, %s)""",
      doc, half, text[:half], doc, half, len(text), text[half:])
    for no, (a, b, speaker, role, section, w) in enumerate(spans):
        x("""insert into equity.call_turn (document_id, turn_no, char_start, char_end, speaker, role, section, words)
             values (%s, %s, %s, %s, %s, %s, %s, %s)""", doc, no, a, b, speaker, role, section, w)
    x("""insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source,
                                           call_date, call_date_source, filed_at, exchange, route, transcript_document_id,
                                           unknown_role_share)
         values (%s, %s, %s, %s, %s, 'text', %s, 'text', %s, 'NSE', 'exchange_pdf', %s, %s)""",
      ev, eid, label, day, season, day, datetime(day.year, day.month, day.day, tzinfo=UTC), doc, share)
    return ev


def opening(body: str, speaker="Alice Rao") -> list[tuple[str, str, str, str]]:
    return [("Operator", "moderator", "opening", "Welcome to the call."), (speaker, "management", "opening", body),
            ("Ben Das", "analyst", "qa", "A question about costs.")]


def task(*slugs) -> EquityCallScores:
    class T(EquityCallScores):
        key = KEY
    t = T()
    t.scope = frozenset(slugs)
    t.workflows, t.rules = ("weekly", "refresh", "backfill"), {"weekly": "every 6 days"}
    return t


def go(*slugs) -> tuple:
    code = run(JOB, "refresh", [task(*slugs)], only=KEY, force=True, clock=lambda: NOW)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1",
           KEY)[0]
    assert code == 0, tr
    return tr


def rows(eid: int) -> list[tuple]:
    return q("""select r.event_id, r.base_event_id, r.words_base, r.words_new, r.words_added, r.words_removed, r.similarity,
                       r.new_speakers, r.label, jsonb_array_length(r.pairs), r.rule_version
                  from equity.call_remarks_change r join equity.earnings_call c on c.event_id = r.event_id
                 where c.entity_id = %s order by c.period_end""", eid)


@pytest.mark.db
def test_a_call_is_compared_with_the_previous_quarter_and_a_rerun_writes_nothing():
    eid, slug = company()
    prev = call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY))
    cur = call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    status, written, checks, _ = go(slug)
    assert status == "success" and written == 1
    [(ev, base, wb, wn, wa, wr, sim, speakers, label, n_pairs, version)] = rows(eid)
    assert (ev, base, speakers, label, version) == (cur, prev, [], None, "1")
    assert wb == 14 and wn == 15 and (wa, wr) == (5, 4)
    assert float(sim) == pytest.approx(1 - 9 / 29, abs=1e-4)
    assert n_pairs == 2
    assert "similarity_range" in str(checks) and "pairs_capped" in str(checks)
    assert go(slug)[1] == 0                                     # nothing changed: nothing written


@pytest.mark.db
def test_no_previous_call_means_no_row():
    eid, slug = company()
    call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    call(eid, slug, D(2026, 2, 25), "Q3 FY26", opening(OLD_BODY))        # not the immediately preceding quarter
    go(slug)
    assert rows(eid) == []


@pytest.mark.db
def test_a_call_without_a_management_opening_gets_no_row():
    eid, slug = company()
    call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY))
    call(eid, slug, D(2026, 10, 25), "Q2 FY27", [("Operator", "moderator", "opening", "Welcome."),
                                                 ("Ben Das", "analyst", "qa", "A question.")])
    go(slug)
    assert rows(eid) == []


@pytest.mark.db
def test_a_role_held_call_on_either_side_gets_no_row():
    eid, slug = company()
    call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY), share=0.1001)
    call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    go(slug)
    assert rows(eid) == []
    x("update equity.earnings_call set unknown_role_share = 0.10 where entity_id = %s", eid)   # exactly 0.10 is not held
    go(slug)
    assert len(rows(eid)) == 1


@pytest.mark.db
def test_a_call_whose_event_is_not_stored_is_not_a_previous_call():
    eid, slug = company()
    call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY), status="unreadable")
    call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    go(slug)
    assert rows(eid) == []


@pytest.mark.db
def test_a_new_ceo_is_a_new_speaker_row_and_a_row_goes_when_its_call_is_held():
    eid, slug = company()
    call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY))
    cur = call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(OLD_BODY, speaker="Carl Ito"))
    go(slug)
    [row] = rows(eid)
    assert row[0] == cur and row[7] == ["Carl Ito"] and row[8] == "new_speaker"
    x("update equity.earnings_call set unknown_role_share = 0.5 where event_id = %s", cur)
    assert go(slug)[1] == 1 and rows(eid) == []                 # the stale row is deleted


@pytest.mark.db
def test_the_year_boundary_pairs_q1_with_the_previous_years_q4():
    eid, slug = company()
    call(eid, slug, D(2026, 3, 25), "Q4 FY26", opening(OLD_BODY))
    call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(NEW_BODY))
    go(slug)
    assert len(rows(eid)) == 1
