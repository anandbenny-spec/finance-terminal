"""Choosing Pass 2's labelling items (Prompt 01 E1; Architecture 03 §1.9): balanced by bank and phase, spread over
time, never an anchor, deterministic; the candidates query against the test database as job_writer; the
eval-items workflow reaches production only when chosen."""
import itertools
import json
from collections import Counter
from datetime import date, datetime, timedelta

import psycopg
import pytest
from conftest import dsn
from test_workflows import resolve, secrets

from ft.eval import labelling as lab
from ft.eval.labelling import ANCHORS, Decision, MemberStatement, NotEnough, choose, phase, spread, stance_sentences

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
REPO = lab.SETS_DIR.parents[3]


def history(bank: str, prefix: str, start: date, n: int, pattern=(-25, 0, 0, 25)) -> list[Decision]:
    return [Decision(bank, f"{prefix}-{(start + timedelta(days=45 * i)).isoformat()}", start + timedelta(days=45 * i),
                     pattern[i % len(pattern)]) for i in range(n)]


DECISIONS = history("Fed", "fomc", date(2019, 1, 2), 60) + history("RBI", "mpc", date(2019, 1, 3), 60)
STATEMENTS = [MemberStatement(d.slug, f"Member {k}", 900 + k) for d in DECISIONS if d.bank == "RBI" for k in range(6)]


def test_phase_and_spread():
    assert [phase(b) for b in (-75, 0, 40)] == ["easing", "holding", "tightening"]
    assert spread(list(range(10)), 5) == [1, 3, 5, 7, 9]
    assert spread([1, 2], 2) == [1, 2]
    with pytest.raises(NotEnough):
        spread([1], 2)


def test_the_sets_are_balanced_by_bank_and_phase():
    sets = choose(DECISIONS, STATEMENTS)
    assert {k: len(v) for k, v in sets.items()} == {"policy_hawk_dove": 30, "policy_stance": 20, "policy_member_hawk_dove": 20}
    hd = Counter((i["bank"], i["phase"]) for i in sets["policy_hawk_dove"])
    assert hd == {(b, p): 5 for b in ("Fed", "RBI") for p in lab.PHASES}
    st = Counter((i["bank"], i["phase"]) for i in sets["policy_stance"])
    assert st == {(b, p): n for b in ("Fed", "RBI") for p, n in lab.STANCE.items()}
    mb = Counter(i["phase"] for i in sets["policy_member_hawk_dove"])
    assert mb == lab.MEMBER and all(i["bank"] == "RBI" and i["doc_type"] == "mpc_minutes" for i in sets["policy_member_hawk_dove"])


def test_stance_items_are_other_meetings_than_hawk_dove_and_no_item_is_an_anchor():
    anchored = DECISIONS + [Decision("Fed", s, date.fromisoformat(s[5:]), -100) for s in ANCHORS if s.startswith("fomc")]
    sets = choose(anchored, STATEMENTS)
    hd = {i["meeting"] for i in sets["policy_hawk_dove"]}
    assert not hd & {i["meeting"] for i in sets["policy_stance"]}
    assert not ANCHORS & {i["meeting"] for items in sets.values() for i in items}


def test_items_spread_over_the_whole_period():
    sets = choose(DECISIONS, STATEMENTS)
    years = {i["decision_date"][:4] for i in sets["policy_hawk_dove"] if i["bank"] == "Fed"}
    assert len(years) >= 5


def test_members_are_spread_one_statement_per_meeting():
    sets = choose(DECISIONS, STATEMENTS)
    items = sets["policy_member_hawk_dove"]
    assert len({i["meeting"] for i in items}) == 20
    per = Counter(i["speaker"] for i in items)
    assert set(per) == {f"Member {k}" for k in range(6)} and max(per.values()) - min(per.values()) <= 1
    assert all(i["id"] == f"policy_member_hawk_dove:{i['meeting']}:{lab._slug(i['speaker'])}" for i in items)


def test_short_statements_and_old_meetings_are_not_candidates():
    old = [Decision("RBI", "mpc-2018-12-05", date(2018, 12, 5), 0)]
    short = [MemberStatement(d.slug, "Member 9", 100) for d in DECISIONS if d.bank == "RBI"]
    sets = choose(DECISIONS + old, STATEMENTS + short)
    assert "Member 9" not in {i["speaker"] for i in sets["policy_member_hawk_dove"]}
    assert "mpc-2018-12-05" not in {i["meeting"] for items in sets.values() for i in items}


def test_the_rule_is_deterministic_and_refuses_an_unbalanced_set():
    assert choose(DECISIONS, STATEMENTS) == choose(list(reversed(DECISIONS)), list(reversed(STATEMENTS)))
    no_hikes = [d for d in DECISIONS if not (d.bank == "RBI" and d.change_bps > 0)]
    with pytest.raises(NotEnough, match="RBI, tightening"):
        choose(no_hikes, STATEMENTS)


def test_stance_sentences():
    text = ("The MPC decided to keep the policy repo rate unchanged. The MPC also decided to remain focused on "
            "withdrawal of accommodation. Its stance is neutral.\nThe stance was retained by 5 to 1.")
    assert stance_sentences(text) == ["Its stance is neutral.", "The stance was retained by 5 to 1."]
    assert stance_sentences("No such word.") == []


def test_annotations_escape_newlines_and_percent():
    assert lab.annotation("t", "a\n50%") == "::notice title=t::a%0A50%25"
    assert lab.chunks("abcde", 2) == ["ab", "cd", "e"]


def test_the_items_file_and_its_web_copy_are_identical_and_parse():
    py = (lab.ITEMS_FILE).read_text()
    assert (REPO / "web" / "lib" / "labelling" / "items.json").read_text() == py
    data = json.loads(py)
    assert set(data["sets"]) == {"policy_hawk_dove", "policy_stance", "policy_member_hawk_dove"}
    assert data["rule_version"] == lab.RULE_VERSION


def test_the_guide_names_every_anchor():
    guide = (lab.SETS_DIR / "GUIDE.md").read_text()
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    months = {f"{m:02d}": n for m, n in zip(range(1, 13), names, strict=True)}
    for slug in ANCHORS:
        y, m, d = slug.split("-")[1:]
        assert f"{int(d)} {months[m]} {y}" in guide, slug


def test_main_needs_a_database_url():
    with pytest.raises(SystemExit):
        lab.main([], env={})


@pytest.mark.parametrize("inputs", [{"target": "dev"}, {}, {"target": ""}])
@pytest.mark.parametrize("dev_secrets", [True, False])
def test_the_workflow_reaches_production_only_when_chosen(inputs, dev_secrets):
    job_env, steps = resolve("eval-items", "workflow_dispatch", inputs, secrets(dev_secrets))
    step = next(e for e in steps if "JOB_DATABASE_URL" in e)
    assert job_env["FT_TARGET"] == "dev"
    assert step["JOB_DATABASE_URL"] == ("DEV:JOB_DATABASE_URL" if dev_secrets else "")
    assert not [v for e in [job_env, *steps] for v in e.values() if "PROD:" in v]
    job_env, steps = resolve("eval-items", "workflow_dispatch", {"target": "production"}, secrets(dev_secrets))
    assert next(e for e in steps if "JOB_DATABASE_URL" in e)["JOB_DATABASE_URL"] == "PROD:JOB_DATABASE_URL"
    assert set(next(e for e in steps if "JOB_DATABASE_URL" in e)) == {"JOB_DATABASE_URL"}


# ---------------------------------------------------------------------------------------------- the database

_n = itertools.count(1)


@pytest.mark.db
def test_load_reads_published_decisions_and_member_statements_as_job_writer():
    p = f"ev{next(_n)}x{datetime.now().strftime('%H%M%S%f')}"
    fed, rbi = f"{p}-fed", f"{p}-rbi"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for slug, mkt, cur in ((fed, "US", "USD"), (rbi, "IN", "INR")):
            c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                         values ('central_bank', %s, %s, %s, %s, true)""", (slug, slug, mkt, cur))

        def meeting(bank, slug, day, bps, *, status="held", decision=True, published=True, minutes=None):
            e = c.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, is_published)
                             select 'policy_meeting', id, %s, %s, %s, %s, true from core.entity where slug = %s
                             returning id""", (slug, slug, day, status, bank)).fetchone()[0]
            c.execute("insert into policy.meeting (event_id, kind, is_rate_decision) values (%s, %s, %s)",
                      (e, "scheduled" if decision else "special", decision))
            src = "fed" if bank == fed else "rbi"
            dt = "fomc_statement" if bank == fed else "mpc_resolution"

            def doc(doc_type, text, chunks):
                d = c.execute("""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key,
                                   source_url, text_sha256, char_count, is_published, extracted_at, chunked_at)
                                 select %s, b.id, %s, %s, s.id, %s, 'https://example.org', sha256(%s::bytea), %s, %s,
                                        now(), now()
                                   from core.entity b, core.source s where b.slug = %s and s.key = %s returning id""",
                              (doc_type, e, slug, f"{p}/{slug}/{doc_type}", text.encode(), len(text), published, bank,
                               src)).fetchone()[0]
                at = 0
                for i, (body, speaker) in enumerate(chunks):
                    c.execute("""insert into core.chunk (document_id, chunk_no, char_start, char_end, speaker, body)
                                 values (%s, %s, %s, %s, %s, %s)""", (d, i, at, at + len(body), speaker, body))
                    at += len(body)
                return d

            text = "The MPC decided to keep the policy repo rate unchanged. The stance is neutral."
            d = doc(dt, text, [(text, None)])
            c.execute("""insert into policy.rate_setting (meeting_event_id, instrument, value_pct, change_bps,
                         document_id, char_start, char_end) values (%s, %s, 5.5, %s, %s, 0, 5)""",
                      (e, "fed_target_upper" if bank == fed else "repo", bps, d))
            if minutes:
                doc("mpc_minutes", "".join(b for b, _ in minutes), minutes)
            return d

        statement = meeting(rbi, f"{p}-mpc-2024-08-08", "2024-08-08", 0,
                            minutes=[("Opening. ", None), ("x" * 700, "Dr. A"), ("y" * 300, "Dr. B")])
        meeting(fed, f"{p}-fomc-2024-09-18", "2024-09-18", -50)
        meeting(fed, f"{p}-fomc-2018-12-19", "2018-12-19", 25)                    # a baseline: before 2019
        meeting(fed, f"{p}-fomc-2025-03-19", "2025-03-19", 0, published=False)    # not published
        meeting(rbi, f"{p}-mpc-2022-11-03", "2022-11-03", 0, decision=False)      # special meeting no. 39
        meeting(fed, f"{p}-fomc-2026-12-09", "2026-12-09", 0, status="scheduled")

    with psycopg.connect(JOB, autocommit=True) as conn:
        conn.execute("set default_transaction_read_only = on")
        decisions, stmts, docs = lab.load(conn, fed=fed, rbi=rbi)
        assert [(d.bank, d.slug.removeprefix(p + "-"), d.change_bps) for d in decisions] == [
            ("RBI", "mpc-2024-08-08", 0), ("Fed", "fomc-2024-09-18", -50)]
        assert sorted((s.speaker, s.chars) for s in stmts) == [("Dr. A", 700), ("Dr. B", 300)]
        assert docs[f"{p}-mpc-2024-08-08"] == statement
        assert stance_sentences(lab.document_text(conn, statement)) == ["The stance is neutral."]
