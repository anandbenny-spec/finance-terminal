"""equity.call_scores through the runner (job 2B-17, #180): a call compared with the previous fiscal quarter's, no row without
a previous call, without a management opening or when either call is role-held, a new speaker, a re-run that writes
nothing, a row removed when its call stops being comparable, and the checks. The rules are tested in
test_equity_call_remarks.py. Synthetic text only."""
import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

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
        c.execute(f"delete from equity.call_tone where event_id in (select event_id from equity.earnings_call where entity_id in {ids})")
        c.execute(f"delete from core.ai_output where document_id in {docs}")
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
    assert (ev, base, speakers, label, version) == (cur, prev, [], None, "remarks-3")
    assert wb == 14 and wn == 15 and (wa, wr) == (5, 4)
    assert float(sim) == pytest.approx(1 - 9 / 29, abs=1e-4)
    assert n_pairs == 2
    assert "similarity_range" in str(checks) and "pairs_capped" in str(checks)
    assert go(slug)[1] == 0                                     # nothing changed: nothing written


@pytest.mark.db
def test_stored_pairs_are_verified_citations_into_the_stored_text():   # remarks-3, #414
    eid, slug = company()
    prev = call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY))
    cur = call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    go(slug)
    [(pairs,)] = q("select pairs from equity.call_remarks_change where event_id = %s", cur)
    assert pairs
    docs = {cur: q("select transcript_document_id from equity.earnings_call where event_id = %s", cur)[0][0],
            prev: q("select transcript_document_id from equity.earnings_call where event_id = %s", prev)[0][0]}
    texts = {d: q("select string_agg(body, '' order by chunk_no) from core.chunk where document_id = %s", d)[0][0]
             for d in docs.values()}
    seen = 0
    for pair in pairs:
        for side, doc in (("new", docs[cur]), ("old", docs[prev])):
            s = pair[side]
            if s is None:
                continue
            seen += 1
            assert s["verified"] is True and s["document_id"] == doc and len(s["text"]) <= 400
            assert " ".join(texts[doc][s["start"]:s["end"]].split()) == s["text"]       # re-read from the stored text
            assert "spans" not in s and s["page"] is None                              # no page rows in this fixture
    assert seen >= 2


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


# ---------------------------------------------------------------------------------------------- tone (job 2B-12, #175)
def tone_output(event_id: int, values: list[int], section="qa", status="published") -> int:
    """A published call_tone output for the call's transcript with the passage scores given (one citation, in the text)."""
    import json
    doc = q("select transcript_document_id from equity.earnings_call where event_id = %s", event_id)[0][0]
    content = {"scores": [{"p": i + 1, "score": v, "chunk_no": 0, "section": section, "speaker": "Alice Rao", "words": 45}
                          for i, v in enumerate(values)], "evidence": [], "rule_version": "1"}
    out = q("""insert into core.ai_output (task, document_id, content, model, prompt_key, prompt_version, input_sha256)
               values ('call_tone', %s, %s, 'test', 'call_tone', 'v1', %s) returning id""",
            doc, json.dumps(content), hashlib.sha256(uuid.uuid4().bytes).digest())[0][0]
    x("""insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
         values (%s, 1, '/scores/0', 'document_span', %s, 0, 10, true)""", out, doc)
    if status == "published":
        x("update core.ai_output set status = 'published' where id = %s", out)
    return out


def tones(eid: int) -> list[tuple]:
    return q("""select t.event_id, t.ai_output_id, t.n, t.mean, t.sd, t.opening_n, t.opening_mean, t.answers_n, t.answers_mean,
                       t.prev_event_id, t.delta, t.se_delta, t.change, t.held_reason, t.rule_version
                  from equity.call_tone t join equity.earnings_call c on c.event_id = t.event_id
                 where c.entity_id = %s order by c.period_end""", eid)


@pytest.mark.db
def test_a_tone_row_per_call_with_the_change_against_the_previous_quarter_and_a_rerun_writes_nothing():
    eid, slug = company()
    prev = call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY))
    cur = call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    o1 = tone_output(prev, [0] * 10)
    o2 = tone_output(cur, [1] * 10)
    status, written, checks, _ = go(slug)
    assert status == "success" and written >= 2
    first, second = tones(eid)
    assert first == (prev, o1, 10, Decimal("0.0000"), Decimal("0.0000"), 0, None, 10, Decimal("0.0000"), None, None, None, None,
                     None, "1")
    assert second == (cur, o2, 10, Decimal("1.0000"), Decimal("0.0000"), 0, None, 10, Decimal("1.0000"), prev,
                      Decimal("1.0000"), Decimal("0.0000"), "more_positive", None, "1")
    assert "tone_range" in str(checks) and "tone_scored" in str(checks)
    assert go(slug)[1] == 0                                                 # nothing changed: nothing written


@pytest.mark.db
def test_a_call_with_fewer_than_eight_passages_is_held_and_gives_no_change_label_to_the_next_call():
    eid, slug = company()
    prev = call(eid, slug, D(2026, 7, 25), "Q1 FY27", opening(OLD_BODY))
    cur = call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    tone_output(prev, [1] * 7)
    tone_output(cur, [1] * 9)
    go(slug)
    first, second = tones(eid)
    assert first[2:5] == (7, None, None) and first[13] == "too little management text to score" and first[12] is None
    assert second[3] == Decimal("1.0000") and second[9] == prev and second[10:13] == (None, None, None)


@pytest.mark.db
def test_a_call_without_a_published_tone_output_has_no_row_and_a_row_goes_when_its_output_is_superseded():
    eid, slug = company()
    cur = call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    go(slug)
    assert tones(eid) == []
    out = tone_output(cur, [2] * 8)
    go(slug)
    assert len(tones(eid)) == 1
    x("update core.ai_output set status = 'superseded', status_reason = 'test' where id = %s", out)
    assert go(slug)[1] == 1 and tones(eid) == []


@pytest.mark.db
def test_the_openings_and_the_answers_have_their_own_means_and_a_draft_output_is_not_read():
    eid, slug = company()
    cur = call(eid, slug, D(2026, 10, 25), "Q2 FY27", opening(NEW_BODY))
    import json
    out = tone_output(cur, [2, 2, 2, 2], section="opening")
    content = q("select content from core.ai_output where id = %s", out)[0][0]
    more = [{**content["scores"][0], "p": 5 + i, "section": "qa", "score": 0} for i in range(4)]
    # a published output is frozen, so build the whole content first: supersede this one and publish a complete one
    x("update core.ai_output set status = 'superseded', status_reason = 'test' where id = %s", out)
    doc = q("select transcript_document_id from equity.earnings_call where event_id = %s", cur)[0][0]
    out2 = q("""insert into core.ai_output (task, document_id, content, model, prompt_key, prompt_version, input_sha256)
                values ('call_tone', %s, %s, 'test', 'call_tone', 'v1', %s) returning id""",
             doc, json.dumps({"scores": content["scores"] + more, "evidence": []}), hashlib.sha256(b"x").digest())[0][0]
    x("""insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start, char_end, verified)
         values (%s, 1, '/scores/0', 'document_span', %s, 0, 10, true)""", out2, doc)
    go(slug)                                                                  # still a draft: no row
    assert tones(eid) == []
    x("update core.ai_output set status = 'published' where id = %s", out2)
    go(slug)
    [row] = tones(eid)
    assert row[2:4] == (8, Decimal("1.0000")) and row[5:9] == (4, Decimal("2.0000"), 4, Decimal("0.0000"))
