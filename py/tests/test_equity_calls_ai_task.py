"""equity.calls_ai (Spec 02B §5.6, §13.3; job 2B-11, #174) through the runner and the Batch API path against the test database as
job_writer: the cited summary of each stored call, its role rules, the retry, the limits and S2's degrade path. The Messages and
Message Batches APIs are fakes (tests/ai_fakes.py); the Anthropic SDK itself runs. The calls are synthetic (tests/call_fixtures.py):
a company, a `stored` earnings-call event, a published transcript document with its chunks and `equity.call_turn` rows. Each test
makes its own calls (unique slugs) and the task sees only those (`scope`).

The guidance extraction (2B-13, #176) rides the same batch for a call whose company row holds a fiscal-year end month: the tests at
the end give their calls one (`fy_end=3`), and the older ones, which don't, are summarised only (the extraction names such a call
in `call_guidance` and skips it)."""
import hashlib
import json
import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise

import psycopg
import pytest
from ai_fakes import FakeClaude, claude
from call_fixtures import (
    GOOD_ANALYSTS,
    GOOD_MANAGEMENT,
    LINES,
    build,
    lists,
)
from conftest import dsn
from test_ai_guidance_extraction import S, T

from ft.ai import call_summary as cs
from ft.ai import guidance_extraction as ge
from ft.ai import summaries
from ft.ai.batch import batch_custom_id
from ft.engines.document.chunk import chunk_text
from ft.engines.document.store import DocumentRef, save_chunks, save_version
from ft.jobs import runner
from ft.jobs.runner import connect, run
from ft.jobs.tasks import equity_calls_ai as eca
from ft.jobs.tasks.equity_calls_ai import EquityCallsAi

JOB, ADMIN = dsn("job_writer"), dsn("postgres")
KEY = "test.equity_calls_ai"
QUESTION = "Can you explain the margin outlook for the second half?"
POOJA = "What is the plan for the dividend and the debt repayment?"
# when Pooja Menon's turn is unreadable: the analysts' bullets rest on Anil Shah's question and the answers to it
KNOWN_ANALYSTS = [GOOD_ANALYSTS[0], ("Freight costs were also discussed.", ["Freight costs are also lower than last year."])]
THIRD = [("A participant asked about the plan for the dividend and the debt repayment.", [POOJA])]
ALL_THIRD = [(f"A participant said {s}", [s]) for s in (
    "Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27.", "EBITDA margin was 21.3 per cent.",
    "Our order book stands at 9,200 crore.")]


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


def x(sql, *a) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql, a or None)


def as_anon(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("set role anon")
        return c.execute(sql, a).fetchall()


@pytest.fixture(scope="module", autouse=True)
def task_row():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', '2B-11 test task', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))


def wipe() -> None:
    """Everything these tests made (companies slugged 'cai-%' and what hangs off them): other tests count companies, events and
    alerts. Published outputs can't be updated, but an admin can delete them (their citations go with them)."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'cai-%')"
        docs = f"(select id from core.document where entity_id in {ids})"
        c.execute(f"delete from ops.ai_request where document_id in {docs}")
        c.execute(f"delete from equity.transcript_page where document_id in {docs}")
        c.execute(f"delete from equity.stated_figure where entity_id in {ids}")
        c.execute(f"delete from equity.guidance where entity_id in {ids}")
        c.execute(f"delete from core.ai_output where document_id in {docs}")
        c.execute(f"delete from equity.call_turn where document_id in {docs}")
        c.execute(f"delete from equity.earnings_call where entity_id in {ids}")
        c.execute(f"update core.document set supersedes_id = null where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'cai-%'")
        c.execute("delete from core.source where key like 'cai\\_%' and key like '%suppressed'")
        c.execute("delete from ops.alert where dedupe_key like 'ai_rejected:call_summary:%' "
                  "or dedupe_key like 'ai_rejected:guidance_extraction:%' or dedupe_key like 'ai_rejected:call_tone:%' "
                  "or dedupe_key like 'ai_unavailable:calls:%'")


@pytest.fixture(scope="module", autouse=True)
def clean():
    wipe()
    yield
    wipe()


@pytest.fixture(autouse=True)
def citations_route(monkeypatch):
    """The tests in this file drive the summary on the citations route (prompts v1-v5, which the fake answers with cited text
    blocks). The current prompt is v6, the structured route (#301): its tests are in test_equity_calls_ai_structured.py."""
    monkeypatch.setattr(eca, "PROMPT_VERSION", {**eca.PROMPT_VERSION, cs.TASK: "v5"})


class Ticker:
    """The batch poll's clock: sleeping moves it on at once."""

    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def source(key: str, *, suppressed: bool = False) -> int:
    x("""insert into core.source (key, name, attribution, is_suppressed) values (%s, %s, %s, %s)
         on conflict (key) do nothing""", key, key, f"Source: {key}", suppressed)
    return q("select id from core.source where key = %s", key)[0][0]


def make_call(*, lines=LINES, role_of=None, filed: date = date(2026, 10, 20), src_key: str = "nse",
              held: bool = False, publish_doc: bool = True, status: str = "stored", with_doc: bool = True,
              fy_end: int | None = None) -> dict:
    """A stored call: company, event, transcript document (published, chunked), its turns and the earnings_call row.
    `fy_end`: the company's fiscal-year end month (an `equity.company` row), which the guidance extraction needs."""
    tag = uuid.uuid4().hex[:8]
    lines = [(sp, (role_of or {}).get(i, role), t) for i, (sp, role, t) in enumerate(lines)]
    b = build(lines)
    src = source(src_key, suppressed=src_key.endswith("_suppressed"))
    eid = q("""insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
               values ('company', %s, 'Acme Limited', 'Acme', 'IN', 'INR', true) returning id""", f"cai-{tag}")[0][0]
    if fy_end is not None:
        x("insert into equity.company (entity_id, fy_end_month) values (%s, %s)", eid, fy_end)
    slug = f"cai-{tag}-call-q2-fy27"
    ev = q("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
              values ('earnings_call', %s, %s, 'Acme Q2 FY27 call', %s, %s, %s, true) returning id""",
           eid, slug, filed, status, src)[0][0]
    doc = None
    if with_doc:
        with connect(JOB) as c, c.transaction():
            saved = save_version(c, DocumentRef("call_transcript", eid, src, f"/test/cai/{tag}.pdf",
                                                f"https://example.invalid/cai/{tag}.pdf", "Acme Q2 FY27 transcript", ev, None,
                                                datetime(filed.year, filed.month, filed.day, 12, tzinfo=UTC)), b.extracted)
            doc = saved.document_id
            save_chunks(c, doc, b.extracted.text, chunk_text(b.extracted, root="Transcript"))
            if publish_doc:
                c.execute("update core.document set is_published = true where id = %s", (doc,))
        with psycopg.connect(JOB, autocommit=True) as c:
            for t in b.turns:
                c.execute("""insert into equity.call_turn (document_id, turn_no, char_start, char_end, speaker, role, section,
                                                          words)
                             values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                          (doc, t.turn_no, t.char_start, t.char_end, t.speaker, t.role, t.section,
                           len(b.text[t.char_start:t.char_end].split())))
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into equity.earnings_call (event_id, entity_id, fiscal_label, period_end, season_key, quarter_source,
                          call_date, call_date_source, filed_at, exchange, route, transcript_document_id, words, status_reason,
                          unknown_role_share)
                     values (%s, %s, 'Q2 FY27', '2026-09-30', 'q2-fy27', 'text', %s, 'text', %s, 'NSE', %s, %s, %s, %s, %s)""",
                  (ev, eid, filed, datetime(filed.year, filed.month, filed.day, 12, tzinfo=UTC),
                   "exchange_pdf" if with_doc else "none", doc, len(b.text.split()), "quarter_conflict" if held else None,
                   round(b.unknown_share, 4)))
    return {"slug": slug, "doc": doc, "event": ev, "built": b, "entity": eid, "src": src}


def replies(*, management=GOOD_MANAGEMENT, analysts=GOOD_ANALYSTS, unknown=()):
    """FakeClaude's `reply`: a good reply with the lists given, or (mode 'role_bad') one whose first management bullet cites an
    analyst's question."""
    def fn(docs, mode):
        title, text = docs[0]
        if mode == "role_bad":
            bad = [(f"Management was asked: {QUESTION}", [QUESTION])] + list(management[1:])
            return lists(text, management=bad, analysts=analysts, unknown=unknown, title=title)
        return lists(text, management=management, analysts=analysts, unknown=unknown, title=title)
    return fn


def task(slugs, fake: FakeClaude, tick: Ticker | None = None) -> EquityCallsAi:
    class T(EquityCallsAi):
        key = KEY
    t = T()
    t.scope = frozenset(slugs)
    t.workflows, t.rules = ("weekly", "backfill"), {"weekly": "every 6 days"}
    t.claude = claude(fake)
    tick = tick or Ticker()
    t.sleep, t.monotonic = tick.sleep, tick.monotonic
    return t


def go(t, when=datetime(2026, 11, 4, 12, tzinfo=UTC), workflow="backfill", **job) -> int:
    return run(JOB, workflow, [t], only=KEY, force=True, clock=lambda: when, **job)


def last_run():
    r = q("""select id, status, rows_written, checks, coalesce(error, ''), ai_cost_usd from ops.task_run
              where task_key = %s order by id desc limit 1""", KEY)[0]
    return {"id": r[0], "status": r[1], "rows": r[2], "checks": {c["name"]: c for c in r[3] or []}, "error": r[4],
            "cost": r[5]}


def outputs(doc_id: int):
    return q("""select status, coalesce(status_reason, ''), jsonb_array_length(content -> 'bullets'), cost_usd, prompt_version
                  from core.ai_output where document_id = %s and task = 'call_summary' order by id""", doc_id)


def requests(doc_id: int):
    return q("""select status, coalesce(reject_reason, ''), ai_output_id is not null, ai_batch_id is not null
                  from ops.ai_request where document_id = %s and ai_task = 'call_summary' order by created_at, custom_id""",
             doc_id)


def none_rejected(r) -> bool:
    """The run rejected nothing (the check's month-to-date share also counts every earlier test's rejections)."""
    return json.loads(r["checks"]["citation_integrity"]["detail"])["rejected"] == []


def alert(key: str):
    return q("select severity, message from ops.alert where dedupe_key = %s and resolved_at is null", key)


def month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def paused():
    return alert(f"ai_unavailable:calls:{month()}")


def resolve(key: str) -> None:
    x("update ops.alert set resolved_at = now() where dedupe_key = %s and resolved_at is null", key)


@pytest.mark.db
def test_a_stored_call_gets_a_cited_two_list_summary_through_the_batch_api():
    c = make_call()
    fake, tick = FakeClaude(["ok"], reply=replies(), ends_after=2), Ticker()
    assert go(task([c["slug"]], fake, tick)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 1 and tick.sleeps == [30.0, 60.0]              # read once still processing, then ended
    ((bid, b),) = fake.batches.items()
    (sent,) = b["requests"]
    stored = q("select custom_id from ops.ai_request where document_id = %s", c["doc"])
    assert stored == [(f"call_summary:doc-{c['doc']}:" + sent["custom_id"].rsplit("_", 1)[1],)]
    assert sent["custom_id"] == batch_custom_id(stored[0][0]) and "cache_control" not in sent["params"]
    params = sent["params"]
    assert params["system"].startswith("You summarise one earnings call") and params["output_config"] == {"effort": "low"}
    doc_block = params["messages"][0]["content"][0]
    assert doc_block["source"]["data"] == c["built"].text and doc_block["citations"] == {"enabled": True}
    (o,) = outputs(c["doc"])
    assert o[:3] == ("published", "", 5) and o[4] == eca.PROMPT_VERSION[cs.TASK]
    asked = params["messages"][0]["content"][1]["text"]
    assert "- Management: Ravi Rao; Meera Iyer" in asked and "- Analysts: Anil Shah; Pooja Menon" in asked
    content, = q("select content from core.ai_output where document_id = %s and task = 'call_summary'", c["doc"])[0]
    assert [b["list"] for b in content["bullets"]] == ["management"] * 3 + ["analysts"] * 2
    cites = q("""select claim_path, kind, verified, char_end <= (select char_count from core.document where id = %s)
                   from core.ai_citation where ai_output_id = (select id from core.ai_output where document_id = %s
                                                                  and task = 'call_summary')
                  order by citation_no""", c["doc"], c["doc"])
    assert len(cites) == 7 and all(k == "document_span" and v and inside for _, k, v, inside in cites)
    assert q("select event_id, entity_id, scope from core.ai_output where document_id = %s and task = 'call_summary'",
             c["doc"]) == [(None, None, "")]
    assert requests(c["doc"]) == [("succeeded", "", True, True)]
    assert r["cost"] == o[3].quantize(Decimal("0.0001")) and o[3] > 0
    (row,) = q("select status, request_count, succeeded, task_run_id from ops.ai_batch where provider_batch_id = %s", bid)
    assert row == ("collected", 1, 1, r["id"])
    ck = r["checks"]
    assert all(ck[n]["passed"] for n in ("outputs_stored", "citation_roles", "calls_too_long", "ai_available", "batches",
                                         "ai_spend")) and none_rejected(r)
    assert "1 calls lacked a published summary" in ck["call_summaries"]["detail"] and "1 published" in ck["call_summaries"]["detail"]
    # held back by default: the field policy is hidden, so visitors see no summary
    assert as_anon("select count(*) from api.ai_outputs where document_id = %s", c["doc"]) == [(0,)]
    # run again: the call has a summary, so nothing is asked and nothing is written
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 1 and last_run()["rows"] == 0 and "0 calls lacked" in last_run()["checks"]["call_summaries"]["detail"]


@pytest.mark.db
def test_a_management_bullet_that_cites_an_analyst_is_retried_with_a_note_and_then_published():
    c = make_call()
    fake = FakeClaude(["role_bad", "ok"], reply=replies())
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 2
    retry = list(fake.batches.values())[1]["requests"][0]["params"]
    note = retry["messages"][0]["content"][-1]["text"]
    assert note.startswith("Your previous answer") and "a management bullet cites a turn whose role is analyst" in note
    got = outputs(c["doc"])
    assert [g[0] for g in got] == ["rejected", "published"]
    assert got[0][1].startswith("retried: bullet 1: a management bullet cites a turn whose role is analyst")
    assert [x[0] for x in requests(c["doc"])] == ["rejected", "succeeded"]
    assert none_rejected(r) and r["checks"]["citation_roles"]["passed"]
    assert alert(f"ai_rejected:call_summary:{c['doc']}") == []


@pytest.mark.db
def test_a_reply_that_fails_the_role_rules_twice_is_rejected_with_an_alert_and_never_resent():
    c = make_call()
    fake = FakeClaude(["role_bad"], reply=replies())
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 2
    got = outputs(c["doc"])
    assert [g[0] for g in got] == ["rejected", "rejected"]
    assert got[0][1].startswith("retried:") and got[1][1].startswith("rejected: bullet 1: a management bullet cites")
    assert [x[0] for x in requests(c["doc"])] == ["rejected", "rejected"]
    assert not r["checks"]["citation_integrity"]["passed"]
    ((sev, msg),) = alert(f"ai_rejected:call_summary:{c['doc']}")
    assert sev == "warn" and "whose role is analyst" in msg
    # a later run sends nothing for it: an unchanged input is never sent twice
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 2 and len(outputs(c["doc"])) == 2 and len(alert(f"ai_rejected:call_summary:{c['doc']}")) == 1
    assert as_anon("select count(*) from api.ai_outputs where document_id = %s", c["doc"]) == [(0,)]


@pytest.mark.db
def test_a_call_with_one_unreadable_speaker_gets_a_third_list_not_attributed_to_management():
    c = make_call(role_of={8: "unknown"})                                    # Pooja Menon's turn: about 8% of the words
    fake = FakeClaude(["ok"], reply=replies(analysts=KNOWN_ANALYSTS, unknown=THIRD))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    asked = next(iter(fake.batches.values()))["requests"][0]["params"]["messages"][0]["content"][1]["text"]
    assert "- Role couldn't be read: Pooja Menon" in asked and "Said by participants whose role couldn't be read" in asked
    assert [g[0] for g in outputs(c["doc"])] == ["published"]
    content, = q("select content from core.ai_output where document_id = %s and task = 'call_summary'", c["doc"])[0]
    assert [b["list"] for b in content["bullets"]] == ["management"] * 3 + ["analysts"] * 2 + ["unknown"]
    assert r["checks"]["citation_roles"]["passed"] and none_rejected(r)


@pytest.mark.db
def test_a_role_held_call_with_no_readable_roles_gets_only_the_third_list_and_the_usual_lists_are_rejected():
    everyone = {i: "unknown" for i in range(len(LINES))}
    ok, usual = make_call(role_of=everyone), make_call(role_of=everyone)
    fake = FakeClaude(["ok"], reply=replies(management=(), analysts=(), unknown=ALL_THIRD))
    assert go(task([ok["slug"]], fake)) == 0
    assert last_run()["status"] == "success"
    asked = next(iter(fake.batches.values()))["requests"][0]["params"]["messages"][0]["content"][1]["text"]
    assert "roles are only partly read" in asked and "- Role couldn't be read: Moderator; Ravi Rao" in asked
    content, = q("select content from core.ai_output where document_id = %s and task = 'call_summary'", ok["doc"])[0]
    assert [b["list"] for b in content["bullets"]] == ["unknown"] * 3
    assert [g[0] for g in outputs(ok["doc"])] == ["published"]
    # the usual two lists on a call nobody's role is read for: nothing can be attributed, so it is rejected, never guessed
    bad = FakeClaude(["ok"], reply=replies())
    assert go(task([usual["slug"]], bad)) == 0
    assert [g[0] for g in outputs(usual["doc"])] == ["rejected", "rejected"]
    assert "whose role is unknown" in outputs(usual["doc"])[1][1]
    assert alert(f"ai_rejected:call_summary:{usual['doc']}") and not alert(f"ai_rejected:call_summary:{ok['doc']}")


@pytest.mark.db
def test_a_new_prompt_version_asks_again_for_the_calls_summarised_under_the_old_one(monkeypatch):
    c = make_call()
    fake = FakeClaude(["ok"], reply=replies())
    monkeypatch.setattr(eca, "PROMPT_VERSION", {cs.TASK: "v1"})
    assert go(task([c["slug"]], fake)) == 0
    assert [(g[0], g[4]) for g in outputs(c["doc"])] == [("published", "v1")]
    assert go(task([c["slug"]], fake)) == 0 and fake.create_calls == 1               # the same version: not asked again
    monkeypatch.setattr(eca, "PROMPT_VERSION", {cs.TASK: "v2"})
    assert go(task([c["slug"]], fake)) == 0
    assert [(g[0], g[4]) for g in outputs(c["doc"])] == [("superseded", "v1"), ("published", "v2")]
    assert go(task([c["slug"]], fake)) == 0 and fake.create_calls == 2                # v2 is published: nothing more


@pytest.mark.db
def test_a_batch_still_open_when_the_poll_ends_is_collected_by_the_next_run_without_resending():
    c = make_call()
    fake, tick = FakeClaude(["ok"], reply=replies(), ends_after=None), Ticker()
    assert go(task([c["slug"]], fake, tick)) == 0
    first = last_run()
    assert first["status"] == "success" and outputs(c["doc"]) == [] and len(tick.sleeps) == 15
    assert requests(c["doc"]) == [("pending", "", False, True)]
    fake.ends_after = 1
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 1 and [g[0] for g in outputs(c["doc"])] == ["published"]
    assert last_run()["cost"] > 0 and not first["cost"]                    # the spend goes on the run that collected it


@pytest.mark.db
def test_a_transcript_over_the_length_limit_is_skipped_with_a_warning_naming_it():
    long = LINES[:2] + [("Ravi Rao", "management", "Ravi Rao: " + "We continue to invest in capacity. " * 6_500)] + LINES[2:]
    big, small = make_call(lines=long), make_call()
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task([big["slug"], small["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 1
    assert len(next(iter(fake.batches.values()))["requests"]) == 1                # only the short one was sent
    assert requests(big["doc"]) == [] and outputs(big["doc"]) == []
    assert [g[0] for g in outputs(small["doc"])] == ["published"]
    ck = r["checks"]["calls_too_long"]
    assert not ck["passed"] and big["slug"] in ck["detail"] and "over 60,000" in ck["detail"] and small["slug"] not in ck["detail"]
    assert cs.MAX_ESTIMATED_TOKENS == 60_000


@pytest.mark.db
def test_only_stored_published_calls_with_an_unsuppressed_source_are_asked_for():
    ok = make_call()
    held = make_call(held=True)
    unpublished = make_call(publish_doc=False)
    suppressed = make_call(src_key="cai_suppressed")
    on_site = make_call(status="on_company_website", with_doc=False)
    slugs = [c["slug"] for c in (ok, held, unpublished, suppressed, on_site)]
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task(slugs, fake)) == 0
    assert last_run()["status"] == "success" and len(next(iter(fake.batches.values()))["requests"]) == 1
    assert [g[0] for g in outputs(ok["doc"])] == ["published"]
    for c in (held, unpublished, suppressed):
        assert requests(c["doc"]) == [] and outputs(c["doc"]) == []


@pytest.mark.db
def test_a_backfills_since_and_until_limit_it_to_calls_filed_in_the_range():
    early, mid, late = (make_call(filed=date(2026, 9, d)) for d in (8, 15, 22))
    slugs = [c["slug"] for c in (early, mid, late)]
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task(slugs, fake), since=date(2026, 9, 10), until=date(2026, 9, 20)) == 0
    assert [len(outputs(c["doc"])) for c in (early, mid, late)] == [0, 1, 0]
    assert go(task(slugs, fake)) == 0                                          # no range: the rest
    assert [len(outputs(c["doc"])) for c in (early, mid, late)] == [1, 1, 1]


@pytest.mark.db
def test_with_no_anthropic_key_the_run_skips_sends_nothing_keeps_every_output_and_is_green(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    done, waiting = make_call(filed=date(2026, 8, 10)), make_call(filed=date(2026, 8, 11))
    slugs = [done["slug"], waiting["slug"]]
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task([done["slug"]], fake)) == 0                                 # a summary exists already
    assert [g[0] for g in outputs(done["doc"])] == ["published"]
    before = fake.create_calls
    t = task(slugs, fake)
    t.claude = None                                                            # built from ANTHROPIC_API_KEY on first use
    assert go(t) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == before and requests(waiting["doc"]) == [] and outputs(waiting["doc"]) == []
    assert [g[0] for g in outputs(done["doc"])] == ["published"]               # what was published stays
    ck = r["checks"]["ai_available"]
    assert not ck["passed"] and "skipped: AI unavailable (no_key)" in ck["detail"] and waiting["slug"] not in ck["detail"]
    assert json.loads(ck["detail"])["skipped_targets"] == [f"call_summary:doc-{waiting['doc']}"]
    assert "no API key" in paused()[0][1]
    assert go(task(slugs, fake)) == 0                                          # the key is back
    assert [g[0] for g in outputs(waiting["doc"])] == ["published"]
    assert last_run()["checks"]["ai_available"]["passed"] and paused() == []   # the alert resolves itself


@pytest.mark.db
def test_a_billing_error_mid_run_keeps_the_stored_replies_skips_the_rest_and_the_run_is_green():
    first, second = make_call(filed=date(2026, 8, 20)), make_call(filed=date(2026, 8, 21))
    fake = FakeClaude(["ok", "role_bad"], reply=replies(), creates=["ok", "402"])
    assert go(task([first["slug"], second["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 2                                              # the retry's batch was refused: no credit
    assert [g[0] for g in outputs(first["doc"])] == ["published"]              # the stored replies stand
    assert [g[0] for g in outputs(second["doc"])] == ["rejected"] and outputs(second["doc"])[0][1].startswith("retried:")
    assert [x[:2] for x in requests(second["doc"])] == [("rejected", outputs(second["doc"])[0][1]),
                                                      ("errored", "batch unavailable billing")]
    ck = r["checks"]["ai_available"]
    assert not ck["passed"] and "billing" in ck["detail"] and "no API credits left" in paused()[0][1]
    resolve(f"ai_unavailable:calls:{month()}")
    assert alert(f"ai_rejected:call_summary:{second['doc']}") == []            # not finally rejected: the retry waits


@pytest.mark.db
def test_a_result_for_a_call_that_stopped_being_eligible_is_stored_stale_with_its_cost():
    c = make_call(src_key="cai_later_suppressed")
    x("update core.source set is_suppressed = false where key = 'cai_later_suppressed'")
    fake = FakeClaude(["ok"], reply=replies(), ends_after=None)
    assert go(task([c["slug"]], fake)) == 0
    assert requests(c["doc"]) == [("pending", "", False, True)]
    x("update core.source set is_suppressed = true where key = 'cai_later_suppressed'")      # the source is switched off
    fake.ends_after = 1
    try:
        assert go(task([c["slug"]], fake)) == 0
        r = last_run()
        assert r["status"] == "success", r["error"]
        assert outputs(c["doc"])[0][0] == "rejected" and outputs(c["doc"])[0][1].startswith("stale:")
        assert outputs(c["doc"])[0][3] > 0 and r["cost"] > 0                   # billed, stored bare, never published
        assert as_anon("select count(*) from api.ai_outputs where document_id = %s", c["doc"]) == [(0,)]
    finally:
        x("update core.source set is_suppressed = false where key = 'cai_later_suppressed'")


@pytest.mark.db
def test_the_independent_role_check_fails_the_run_if_a_bad_bullet_ever_got_through(monkeypatch):
    """Simulates a bug in the reply check (no role rules): the stored citations are checked again against the turns in the
    database, and the blocking `citation_roles` check fails the run."""
    def no_roles(self, req, reply):
        mapping: Counter = Counter()
        found = summaries.scan(reply.parts(), cs.HEADINGS)
        bullets = []
        for h, body, cites in found.bullets:
            b = summaries.verify_bullet(body, cites, req.sources, mapping)
            b.section = cs.LISTS[h]
            bullets.append(b)
        return summaries.Checked(cs.TASK, bullets, [], mapping)
    monkeypatch.setattr(eca.Work, "check", no_roles)
    c = make_call()
    assert go(task([c["slug"]], FakeClaude(["role_bad"], reply=replies()))) != 0 or last_run()["status"] != "success"
    r = last_run()
    assert r["status"] == "checks_failed"
    ck = r["checks"]["citation_roles"]
    assert not ck["passed"] and "a management bullet cites a turn whose role is analyst" in ck["detail"]


@pytest.mark.db
def test_due_goes_through_the_production_guard():
    class Fresh(EquityCallsAi):
        key = f"test.equity_calls_ai_{uuid.uuid4().hex[:8]}"           # never run on this project
    fresh = Fresh()
    fresh.workflows, fresh.rules = ("weekly", "backfill"), {"weekly": "every 6 days"}
    db = runner.Control(JOB)
    scheduled = fresh.due(db, "weekly")
    assert not scheduled.due and "first recorded run" in scheduled.reason     # a scheduled run waits for the first one
    assert fresh.due(db, "backfill").due                                      # a backfill is how the first run happens
    assert fresh.requires == "equity.earnings_call"
    # once it has succeeded, the task's own rule decides (this module's KEY has run above)
    ran = task([], FakeClaude()).due(db, "weekly")
    assert not ran.due and ran.reason.startswith("last success") and "every 6 days" in ran.reason


# ---------------------------------------------------------------------------------------------- the extraction (2B-13, #176)
REV = "Revenue from operations grew 12.5 per cent to 4,500 crore in Q2 FY27."
MARGIN = "EBITDA margin was 21.3 per cent."
CAPEX = "We plan capital expenditure of 800 crore this year."
OUTLOOK = "We expect margins to stay near 21 per cent because input costs have eased."
REPAY = "We will repay 300 crore of debt this year and maintain the dividend payout."
CAPEX_T = T(CAPEX, metric="capex", metric_text="capital expenditure", value=800, unit="inr", growth_basis="level")
OUTLOOK_T = T(OUTLOOK, metric="ebitda_margin", metric_text="EBITDA margin", value=21, growth_basis="level",
              period_kind="half_year", part=2, period_text="the second half")
ASKED_T = T(QUESTION, metric="ebitda_margin", metric_text="EBITDA margin", kind="direction", value=None, unit=None,
            direction="increase")                                                  # in Anil Shah's turn: an analyst's
REPAY_T = T(REPAY, metric="other", metric_text="debt repayment", value=300, unit="inr", growth_basis="level")
REV_S = S(REV)
MARGIN_S = S(MARGIN, metric="ebitda_margin", metric_text="EBITDA margin", value=21.3, unit="pct")
# a participant whose role couldn't be read, and says something in the first person (about 5% of the call's words)
PARTICIPANT = "We will invest 500 crore in the new plant in FY28."
WITH_PARTICIPANT = LINES[:10] + [("Participant X", "unknown", f"Participant X: {PARTICIPANT}")] + LINES[10:]
THIRD_PARTICIPANT = [(f"A participant said {PARTICIPANT}", [PARTICIPANT])]           # the summary's third list
PARTICIPANT_T = T(PARTICIPANT, metric="capex", metric_text="capital expenditure", value=500, unit="inr", fiscal_year=2028,
                  growth_basis="level")


def answers(targets=(), stated=()):
    """FakeClaude's `answer`: the extraction's JSON, whatever the transcript."""
    return lambda body: {"targets": list(targets), "stated_figures": list(stated)}


def gx(doc_id: int):
    """The extraction's outputs of a call: status, reason, prompt version, targets and stated figures in the content."""
    return q("""select status, coalesce(status_reason, ''), prompt_version, jsonb_array_length(content -> 'targets'),
                       jsonb_array_length(content -> 'stated_figures')
                  from core.ai_output where document_id = %s and task = 'guidance_extraction' order by id""", doc_id)


def grequests(doc_id: int):
    return q("""select status, coalesce(reject_reason, ''), ai_output_id is not null
                  from ops.ai_request where document_id = %s and ai_task = 'guidance_extraction' order by created_at, custom_id""",
             doc_id)


def grows(doc_id: int):
    return q("""select speaker_role, metric, kind, value, unit, target_start, target_end, duplicate_of is not null,
                       coalesce(not_scored_reason, ''), vintage_date, citation_no
                  from equity.guidance where document_id = %s order by id""", doc_id)


def srows(doc_id: int):
    return q("""select metric, value, unit, period_kind, period_start, period_end, duplicate_of is not null, citation_no
                  from equity.stated_figure where document_id = %s order by id""", doc_id)


def shown(event_id: int):
    return q("select guidance_targets from equity.earnings_call where event_id = %s", event_id)[0][0]


def guidance_prompt_v2(monkeypatch) -> None:
    """A second version of the extraction's prompt (only v1 exists): the same text under another version, so the input hash and
    the version both change."""
    real = eca.load_prompt
    monkeypatch.setattr(eca, "PROMPT_VERSION", {**eca.PROMPT_VERSION, ge.TASK: "v2"})
    monkeypatch.setattr(eca, "load_prompt", lambda t, v: replace(real(t, "v1"), version="v2") if (t, v) == (ge.TASK, "v2")
                        else real(t, v))


@pytest.mark.db
def test_the_extraction_rides_the_summary_batch_and_stores_rows_with_verified_citations():
    c = make_call(fy_end=3)
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T, OUTLOOK_T, ASKED_T], [REV_S, MARGIN_S]))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 1
    ((_, batch),) = fake.batches.items()
    stored = q("select custom_id, ai_task from ops.ai_request where document_id = %s order by ai_task", c["doc"])
    assert [t for _, t in stored] == ["call_summary", "guidance_extraction"]
    assert stored[1][0].startswith(f"guidance_extraction:doc-{c['doc']}:")
    assert sorted(x["custom_id"] for x in batch["requests"]) == sorted(batch_custom_id(cid) for cid, _ in stored)
    sent = next(x for x in batch["requests"] if x["custom_id"] == batch_custom_id(stored[1][0]))["params"]
    assert sent["output_config"]["effort"] == "medium" and sent["output_config"]["format"]["type"] == "json_schema"
    block = sent["messages"][0]["content"][0]
    assert "citations" not in block and block["source"]["data"].startswith("[moderator]\nModerator: Good day")
    assert "[management · Ravi Rao]\nRavi Rao:" in block["source"]["data"] and "[analyst · Anil Shah]" in block["source"]["data"]
    assert "fiscal year ends in March" in sent["messages"][0]["content"][1]["text"]
    # the output: published, with the kept items in its content and nothing of the analyst's turn
    assert gx(c["doc"]) == [("published", "", "v1", 2, 2)]
    content, = q("select content from core.ai_output where document_id = %s and task = 'guidance_extraction'", c["doc"])[0]
    assert content["dropped"] == {"analyst_turn": 1} and content["attempt"] == 1 and "no_targets" not in content
    assert QUESTION not in json.dumps(content) and all(len(x["quote"]) <= 400 for x in content["targets"] + content["stated_figures"])
    assert [x["quote"] for x in content["targets"]] == [CAPEX, OUTLOOK] and [x["quote"] for x in content["stated_figures"]] == [REV, MARGIN]
    # four citations, all verified spans inside the published document, in the order the rows number them
    cites = q("""select claim_path, kind, verified, char_end <= (select char_count from core.document where id = %s),
                        citation_no
                   from core.ai_citation where ai_output_id = (select id from core.ai_output where document_id = %s
                                                                  and task = 'guidance_extraction')
                  order by citation_no""", c["doc"], c["doc"])
    assert [(p, k, v, i, n) for p, k, v, i, n in cites] == [
        ("/targets/0", "document_span", True, True, 1), ("/targets/1", "document_span", True, True, 2),
        ("/stated_figures/0", "document_span", True, True, 3), ("/stated_figures/1", "document_span", True, True, 4)]
    # the rows: management's, dated through the March year end, vintage the call's date, no forecast series
    assert grows(c["doc"]) == [
        ("management", "capex", "point", Decimal(800), "inr", date(2026, 4, 1), date(2027, 3, 31), False, "", date(2026, 10, 20), 1),
        ("management", "ebitda_margin", "point", Decimal(21), "pct", date(2026, 10, 1), date(2027, 3, 31), False, "",
         date(2026, 10, 20), 2)]
    assert srows(c["doc"]) == [
        ("revenue", Decimal(4500), "inr", "quarter", date(2026, 7, 1), date(2026, 9, 30), False, 3),
        ("ebitda_margin", Decimal("21.3"), "pct", "quarter", date(2026, 7, 1), date(2026, 9, 30), False, 4)]
    assert q("select count(*) from equity.guidance where document_id = %s and forecast_series_id is not null", c["doc"]) == [(0,)]
    assert shown(c["event"]) == 2
    assert grequests(c["doc"]) == [("succeeded", "", True)]
    # the summary is published beside it, and neither is visible to visitors (held back until their gates pass)
    assert [g[0] for g in outputs(c["doc"])] == ["published"]
    assert as_anon("select count(*) from api.ai_outputs where document_id = %s", c["doc"]) == [(0,)]
    ck = r["checks"]
    assert all(ck[n]["passed"] for n in ("outputs_stored", "citation_roles", "guidance_stored", "guidance_items", "calls_too_long",
                                         "ai_available", "batches", "ai_spend")) and none_rejected(r)
    assert ck["guidance_stored"]["detail"].startswith("1 stored") and ck["guidance_items"]["detail"].startswith("1 stored")
    assert "1 calls lacked a published summary" in ck["call_summaries"]["detail"]
    assert "1 calls lacked their extraction at the start; this run: 1 published" in ck["call_guidance"]["detail"]
    for key in ("analyst_turn", "moderator_turn", "across_turns", "content_test"):          # Spec 02B.1 C3: always reported
        assert ck[f"guidance_dropped_{key}"]["passed"] and not ck[f"guidance_dropped_{key}"]["blocking"]
    assert ck["guidance_dropped_analyst_turn"]["detail"].startswith("1 items dropped")
    assert ck["guidance_dropped_content_test"]["detail"].startswith("0 items dropped")
    assert "guidance_dropped_not_found" not in ck                                          # the rest only when something dropped
    assert r["cost"] > 0
    # run again: both outputs exist, so nothing is asked and nothing is written
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 1 and last_run()["rows"] == 0
    assert "0 calls lacked their extraction" in last_run()["checks"]["call_guidance"]["detail"]


# ---------------------------------------------------------------------------- one task only (2B-13b, #302)
# `CALLS_AI_TASK` limits what a run asks for; unset (every other run) it is `both`, which every test above relies on.

@pytest.mark.db
def test_the_task_limit_is_both_unless_one_task_is_named_and_a_bad_value_stops_the_run(monkeypatch):
    monkeypatch.delenv(eca.ONLY_ENV, raising=False)
    assert eca.only_task() is None and eca.only_task({}) is None
    assert eca.only_task({eca.ONLY_ENV: ""}) is None and eca.only_task({eca.ONLY_ENV: "both"}) is None
    assert eca.only_task({eca.ONLY_ENV: " guidance_extraction "}) == ge.TASK
    assert eca.only_task({eca.ONLY_ENV: "call_summary"}) == cs.TASK
    with pytest.raises(ValueError, match="CALLS_AI_TASK must be both, call_summary or guidance_extraction"):
        eca.only_task({eca.ONLY_ENV: "summaries"})


@pytest.mark.db
def test_a_run_limited_to_the_extraction_asks_for_no_summary_and_leaves_stored_summaries_unchecked(monkeypatch):
    monkeypatch.setenv(eca.ONLY_ENV, ge.TASK)
    bare, kept = make_call(fy_end=3), make_call(fy_end=3)           # no summary yet / a stored summary with a copied end
    old = legacy(kept, COPIED)
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T, OUTLOOK_T, ASKED_T], [REV_S, MARGIN_S]))
    assert go(task([bare["slug"], kept["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    ((_, batch),) = fake.batches.items()
    assert len(batch["requests"]) == 2
    for c in (bare, kept):
        assert q("select ai_task from ops.ai_request where document_id = %s", c["doc"]) == [("guidance_extraction",)]
        assert gx(c["doc"]) == [("published", "", "v1", 2, 2)]
    assert outputs(bare["doc"]) == []                                # a default run would have asked for one
    assert [o[0] for o in outputs(kept["doc"])] == ["published"]     # and would have cut its copied end: not re-checked
    assert q("select content ->> 'echo_rule' from core.ai_output where id = %s", old) == [(None,)]
    assert "0 calls lacked a published summary" in r["checks"]["call_summaries"]["detail"]
    assert "2 calls lacked their extraction at the start; this run: 2 published" in r["checks"]["call_guidance"]["detail"]
    assert echo_detail(r)["looked"] == 0


@pytest.mark.db
def test_a_run_limited_to_the_summary_asks_for_no_extraction(monkeypatch):
    monkeypatch.setenv(eca.ONLY_ENV, cs.TASK)
    c = make_call(fy_end=3)
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T, OUTLOOK_T, ASKED_T], [REV_S, MARGIN_S]))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert q("select ai_task from ops.ai_request where document_id = %s", c["doc"]) == [("call_summary",)]
    assert [o[0] for o in outputs(c["doc"])] == ["published"] and gx(c["doc"]) == []
    assert "0 calls lacked their extraction at the start" in r["checks"]["call_guidance"]["detail"]


@pytest.mark.db
def test_a_call_where_nothing_is_found_keeps_a_no_targets_draft_counts_zero_and_is_not_asked_again():
    empty, dropped = make_call(fy_end=3), make_call(fy_end=3)
    fake = FakeClaude(["ok"], reply=replies(), answer=answers())
    assert go(task([empty["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert gx(empty["doc"]) == [("draft", "no_targets", "v1", 0, 0)]
    content, = q("select content from core.ai_output where document_id = %s and task = 'guidance_extraction'", empty["doc"])[0]
    assert content["no_targets"] is True and content["dropped"] == {}
    assert grows(empty["doc"]) == [] and srows(empty["doc"]) == [] and shown(empty["event"]) == 0
    assert q("select count(*) from core.ai_citation where ai_output_id = (select id from core.ai_output where document_id = %s "
             "and task = 'guidance_extraction')", empty["doc"]) == [(0,)]
    assert grequests(empty["doc"]) == [("succeeded", "", True)]
    ck = r["checks"]
    assert ck["guidance_stored"]["passed"] and "1 calls lacked their extraction at the start; this run: 1 no_targets" in ck["call_guidance"]["detail"]
    assert alert(f"ai_rejected:guidance_extraction:{empty['doc']}") == [] and none_rejected(r)
    assert go(task([empty["slug"]], fake)) == 0 and fake.create_calls == 1               # "none found" is an answer: not asked again
    # everything the model found was an analyst's: counted, and the call is "none found" too
    only_analyst = FakeClaude(["ok"], reply=replies(), answer=answers([ASKED_T]))
    assert go(task([dropped["slug"]], only_analyst)) == 0
    assert gx(dropped["doc"]) == [("draft", "no_targets", "v1", 0, 0)]
    content, = q("select content from core.ai_output where document_id = %s and task = 'guidance_extraction'", dropped["doc"])[0]
    assert content["dropped"] == {"analyst_turn": 1} and shown(dropped["event"]) == 0 and QUESTION not in json.dumps(content)
    assert last_run()["checks"]["guidance_dropped_analyst_turn"]["detail"].startswith("1 items dropped")


@pytest.mark.db
def test_a_first_person_target_in_a_turn_nobody_could_read_goes_to_the_separate_list_and_is_never_counted_as_management_s():
    c = make_call(fy_end=3, lines=WITH_PARTICIPANT, role_of={})
    fake = FakeClaude(["ok"], reply=replies(unknown=THIRD_PARTICIPANT), answer=answers([CAPEX_T, PARTICIPANT_T, REPAY_T]))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert gx(c["doc"]) == [("published", "", "v1", 3, 0)]
    assert [(g[0], g[1], g[3]) for g in grows(c["doc"])] == [("management", "capex", Decimal(800)),
                                                             ("management", "other", Decimal(300)),
                                                             ("unknown", "capex", Decimal(500))]
    assert grows(c["doc"])[1][8] == "no_filed_figure"
    assert shown(c["event"]) == 2                                              # management's two; the separate list isn't counted
    assert r["checks"]["guidance_items"]["passed"] and r["checks"]["guidance_stored"]["passed"]
    assert [g[0] for g in outputs(c["doc"])] == ["published"]                           # the summary is published beside it


@pytest.mark.db
def test_with_the_switch_off_the_separate_list_is_dropped_and_counted_and_management_s_targets_stand():
    c = make_call(fy_end=3, lines=WITH_PARTICIPANT)
    off = task([c["slug"]], FakeClaude(["ok"], reply=replies(unknown=THIRD_PARTICIPANT), answer=answers([CAPEX_T, PARTICIPANT_T])))
    off.metrics = replace(ge.load_metrics(), unattributed_targets=False)
    assert go(off) == 0
    assert last_run()["status"] == "success"
    assert [g[0] for g in grows(c["doc"])] == ["management"] and shown(c["event"]) == 1
    content, = q("select content from core.ai_output where document_id = %s and task = 'guidance_extraction'", c["doc"])[0]
    assert content["dropped"] == {"unknown_turn_off": 1} and content["unattributed_targets"] is False
    assert last_run()["checks"]["guidance_dropped_unknown_turn_off"]["detail"].startswith("1 items dropped")


@pytest.mark.db
def test_a_duplicate_keeps_its_row_and_its_citation_and_is_not_counted():
    again = "We plan capital expenditure of 500 crore in FY28 as well."
    lines = WITH_PARTICIPANT[:10] + [("Participant X", "unknown", f"Participant X: {PARTICIPANT} {again}")] + WITH_PARTICIPANT[11:]
    d = make_call(fy_end=3, lines=lines)
    dup = T(again, metric="capex", metric_text="capital expenditure", value=500, unit="inr", fiscal_year=2028, growth_basis="level")
    assert go(task([d["slug"]], FakeClaude(["ok"], reply=replies(unknown=THIRD_PARTICIPANT), answer=answers([PARTICIPANT_T, dup])))) == 0
    rows = grows(d["doc"])
    assert [(g[0], g[7], g[8]) for g in rows] == [("unknown", False, ""), ("unknown", True, "duplicate")]
    assert [g[10] for g in rows] == [1, 2] and shown(d["event"]) == 0
    # a management statement of the same target comes first whatever the transcript's order: the separate list's is the duplicate
    mgmt = "We plan capital expenditure of 500 crore in FY28 too."
    lines = WITH_PARTICIPANT[:12] + [("Ravi Rao", "management", f"Ravi Rao: {mgmt}")]
    e = make_call(fy_end=3, lines=lines)
    both = [PARTICIPANT_T, T(mgmt, metric="capex", metric_text="capital expenditure", value=500, unit="inr", fiscal_year=2028,
                             growth_basis="level")]
    assert go(task([e["slug"]], FakeClaude(["ok"], reply=replies(unknown=THIRD_PARTICIPANT), answer=answers(both)))) == 0
    assert [(g[0], g[7]) for g in grows(e["doc"])] == [("management", False), ("unknown", True)] and shown(e["event"]) == 1


@pytest.mark.db
def test_a_reply_that_isnt_json_is_retried_with_a_note_then_rejected_with_an_alert_and_never_resent():
    c = make_call(fy_end=3)
    fake = FakeClaude(["not_json"], reply=replies())
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success" and fake.create_calls == 2
    retry = list(fake.batches.values())[1]["requests"]
    assert len(retry) == 1                                                             # only the extraction is asked again
    asked = retry[0]["params"]["messages"][0]["content"]
    assert asked[-1]["text"].startswith("Your previous answer") and "the reply isn't the schema's JSON" in asked[-1]["text"]
    got = gx(c["doc"])
    assert [g[0] for g in got] == ["rejected", "rejected"]
    assert got[0][1].startswith("retried: the reply isn't the schema's JSON") and got[1][1].startswith("rejected: the reply isn't")
    assert [g[0] for g in grequests(c["doc"])] == ["rejected", "rejected"]
    assert grows(c["doc"]) == [] and srows(c["doc"]) == []
    assert [g[0] for g in outputs(c["doc"])] == ["published"]                            # the summary is unaffected
    ((sev, msg),) = alert(f"ai_rejected:guidance_extraction:{c['doc']}")
    assert sev == "warn" and "the reply isn't the schema's JSON" in msg and alert(f"ai_rejected:call_summary:{c['doc']}") == []
    assert not r["checks"]["citation_integrity"]["passed"]
    # an unchanged input is never sent twice
    assert go(task([c["slug"]], fake)) == 0
    assert fake.create_calls == 2 and len(gx(c["doc"])) == 2 and len(alert(f"ai_rejected:guidance_extraction:{c['doc']}")) == 1


@pytest.mark.db
def test_a_new_prompt_version_asks_again_supersedes_the_old_output_resolves_the_alert_and_keeps_the_old_rows(monkeypatch):
    c = make_call(fy_end=3)
    broken = FakeClaude(["not_json"], reply=replies())
    assert go(task([c["slug"]], broken)) == 0
    assert alert(f"ai_rejected:guidance_extraction:{c['doc']}")
    guidance_prompt_v2(monkeypatch)
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T], [REV_S]))
    assert go(task([c["slug"]], fake)) == 0
    assert [(g[0], g[2]) for g in gx(c["doc"])][-1] == ("published", "v2")
    assert alert(f"ai_rejected:guidance_extraction:{c['doc']}") == []                  # published: the alert resolves itself
    assert [g[0] for g in outputs(c["doc"])] == ["published"] and fake.create_calls == 1   # the summary wasn't asked for again
    # a third version finds more: the second is superseded, its rows stay (every observation is stored once fetched)
    monkeypatch.setattr(eca, "PROMPT_VERSION", {**eca.PROMPT_VERSION, ge.TASK: "v3"})
    real = eca.load_prompt
    monkeypatch.setattr(eca, "load_prompt", lambda t, v: replace(real(t, "v1"), version="v3") if (t, v) == (ge.TASK, "v3")
                        else real(t, v))
    more = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T, OUTLOOK_T], [REV_S, MARGIN_S]))
    assert go(task([c["slug"]], more)) == 0
    assert [g[0] for g in gx(c["doc"])][-2:] == ["superseded", "published"] and gx(c["doc"])[-1][2] == "v3"
    assert len(grows(c["doc"])) == 3 and len(srows(c["doc"])) == 3 and shown(c["event"]) == 2
    published = q("select id from core.ai_output where document_id = %s and task = 'guidance_extraction' and status = 'published'",
                  c["doc"])
    assert len(published) == 1
    assert q("select count(*) from equity.guidance where ai_output_id = %s", published[0][0]) == [(2,)]


@pytest.mark.db
def test_a_call_with_no_fiscal_year_end_month_is_summarised_but_not_extracted_and_named():
    c = make_call()                                                                    # no equity.company row
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T]))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success" and len(next(iter(fake.batches.values()))["requests"]) == 1
    assert [g[0] for g in outputs(c["doc"])] == ["published"] and gx(c["doc"]) == [] and grequests(c["doc"]) == []
    assert c["slug"] in r["checks"]["call_guidance"]["detail"] and "no fiscal-year end month" in r["checks"]["call_guidance"]["detail"]
    assert r["checks"]["guidance_stored"]["passed"] and shown(c["event"]) is None


@pytest.mark.db
def test_a_transcript_over_the_length_limit_is_named_once_for_both_tasks():
    long = LINES[:2] + [("Ravi Rao", "management", "Ravi Rao: " + "We continue to invest in capacity. " * 6_500)] + LINES[2:]
    big = make_call(lines=long, fy_end=3)
    fake = FakeClaude(["ok"], reply=replies(), answer=answers())
    assert go(task([big["slug"]], fake)) == 0
    r = last_run()
    assert fake.create_calls == 0 and gx(big["doc"]) == [] and outputs(big["doc"]) == []
    ck = r["checks"]["calls_too_long"]
    assert not ck["passed"] and ck["detail"].count(big["slug"]) == 1


@pytest.mark.db
def test_with_no_anthropic_key_neither_task_is_sent_and_both_are_named_as_skipped(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = make_call(fy_end=3)
    t = task([c["slug"]], FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T])))
    t.claude = None
    assert go(t) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert gx(c["doc"]) == [] and outputs(c["doc"]) == [] and grequests(c["doc"]) == []
    ck = r["checks"]["ai_available"]
    assert not ck["passed"] and sorted(json.loads(ck["detail"])["skipped_targets"]) == [
        f"call_summary:doc-{c['doc']}", f"guidance_extraction:doc-{c['doc']}"]
    assert "call summaries and extracted figures are skipped" in paused()[0][1]
    assert go(task([c["slug"]], FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T])))) == 0   # the key is back
    assert gx(c["doc"])[0][0] == "published" and paused() == []


@pytest.mark.db
def test_the_independent_check_fails_the_run_if_a_stored_item_lies_in_a_turn_its_list_doesnt_allow(monkeypatch):
    """Simulates a bug in the reply check (the role rules gone, so any turn counts as management's): the stored rows are read
    again against the turns in the database, and the blocking `guidance_items` check fails the run."""
    def lenient(raw, call, c, metrics, *, target):
        span = ge.locate_quote(call.text, ge.normalize_inline(raw["quote"]))
        turn = next(t for t in call.turns if t.char_start <= span.start < t.char_end)
        return ge.Where(span.start, span.end, replace(turn, role="management"), call.text[span.start:span.end])
    monkeypatch.setattr(ge, "_place", lenient)
    c = make_call(fy_end=3)
    asked = T(QUESTION, metric="ebitda_margin", metric_text="EBITDA margin", kind="direction", value=None, unit=None,
              direction="increase")
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([asked]))
    assert go(task([c["slug"]], fake)) != 0 or last_run()["status"] != "success"
    r = last_run()
    assert r["status"] == "checks_failed"
    ck = r["checks"]["guidance_items"]
    assert not ck["passed"] and "its quote isn't inside one turn whose role is management" in ck["detail"]


@pytest.mark.db
def test_the_independent_check_fails_the_run_if_a_figure_isnt_in_its_stored_quote(monkeypatch):
    """Simulates a bug in the reply check's figure rule (it accepts the first figure it is shown): the stored value the quote
    doesn't hold is caught when the stored rows are read again, and the blocking `guidance_items` check fails the run."""
    real, seen = ge.figures_ok, []

    def lenient_once(*a, **k):
        seen.append(1)
        return True if len(seen) == 1 else real(*a, **k)
    monkeypatch.setattr(ge, "figures_ok", lenient_once)
    c = make_call(fy_end=3)
    wrong = {**CAPEX_T, "value": 900}                                            # the quote says 800
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([wrong]))
    assert go(task([c["slug"]], fake)) != 0 or last_run()["status"] != "success"
    r = last_run()
    assert r["status"] == "checks_failed" and "a figure isn't in its quote" in r["checks"]["guidance_items"]["detail"]


def add_pages(c: dict, cuts: list[int]) -> None:
    """`equity.transcript_page` rows for the call's document: its text split at the offsets given (page 1, 2, ...)."""
    edges = [0, *cuts, len(c["built"].text)]
    for n, (a, b) in enumerate(pairwise(edges), start=1):
        x("insert into equity.transcript_page (document_id, pdf_page, char_start, char_end) values (%s, %s, %s, %s)",
          c["doc"], n, a, b)


@pytest.mark.db
def test_every_stored_item_starts_inside_a_page_row_of_its_document_when_it_has_them_and_is_stored_when_it_has_none():
    text = build().text
    paged, unpaged = make_call(fy_end=3), make_call(fy_end=3)
    add_pages(paged, [text.index("Anil Shah:"), text.index("Pooja Menon:")])               # three pages, cut between turns
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([CAPEX_T, OUTLOOK_T], [REV_S, MARGIN_S]))
    assert go(task([paged["slug"], unpaged["slug"]], fake)) == 0
    assert last_run()["status"] == "success"
    for c in (paged, unpaged):
        assert gx(c["doc"]) == [("published", "", "v1", 2, 2)] and len(grows(c["doc"])) == 2 and len(srows(c["doc"])) == 2
    for table in ("guidance", "stated_figure"):
        got = q(f"""select i.char_start, p.pdf_page from equity.{table} i
                      left join equity.transcript_page p on p.document_id = i.document_id
                                                         and i.char_start >= p.char_start and i.char_start < p.char_end
                     where i.document_id = %s order by i.id""", paged["doc"])
        assert len(got) == 2 and all(page is not None for _, page in got), (table, got)             # every item has its page
    pages = {t: sorted({p for _, p in q(f"""select i.char_start, p.pdf_page from equity.{t} i
                                              join equity.transcript_page p on p.document_id = i.document_id
                                               and i.char_start >= p.char_start and i.char_start < p.char_end
                                             where i.document_id = %s""", paged["doc"])}) for t in ("guidance", "stated_figure")}
    assert pages == {"guidance": [1, 2], "stated_figure": [1]}                                       # capex on page 1, the outlook on 2; both stated figures on 1
    assert q("select count(*) from equity.transcript_page where document_id = %s", unpaged["doc"]) == [(0,)]
    assert q("select count(*) from core.forecast f join core.series s on s.id = f.series_id "
             "where s.entity_id = any(%s)", [paged["entity"], unpaged["entity"]]) == [(0,)]       # no forecast row, ever


@pytest.mark.db
def test_a_call_with_only_stated_figures_is_published_with_them_and_counts_zero_targets():
    c = make_call(fy_end=3)
    fake = FakeClaude(["ok"], reply=replies(), answer=answers([], [REV_S, MARGIN_S]))
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert gx(c["doc"]) == [("published", "", "v1", 0, 2)] and grows(c["doc"]) == [] and len(srows(c["doc"])) == 2
    assert shown(c["event"]) == 0
    assert [p for (p,) in q("select claim_path from core.ai_citation where ai_output_id = (select id from core.ai_output "
                            "where document_id = %s and task = 'guidance_extraction') order by citation_no", c["doc"])] == [
        "/stated_figures/0", "/stated_figures/1"]
    assert r["checks"]["guidance_stored"]["passed"] and r["checks"]["guidance_items"]["passed"]
    assert "1 published" in r["checks"]["call_guidance"]["detail"]


# ---------------------------------------------------------------------------- the copied-text re-check (2B-11c, #301): no AI, no request
# Summaries stored before the rule hold a copied end; an admin writes them here (the task can no longer store one). Each test
# makes its own calls.
MEERA = ("Our order book stands at 9,200 crore. Demand in the export market remains soft because of weak freight rates. "
         "We plan capital expenditure of 800 crore this year.")                         # one management turn's text, label left off
LEAD = "The order book is 9,200 crore and capital expenditure of 800 crore is planned this year."
CLEAN = [(t, "management", p) for t, p in GOOD_MANAGEMENT[:2]] + [(t, "analysts", p) for t, p in GOOD_ANALYSTS]
COPIED = [(f"{LEAD} {MEERA}", "management", [MEERA]), *CLEAN]                          # five bullets, the first copying MEERA


def legacy(c, bullets, *, version="v5", stamp=None, task_key="call_summary") -> int:
    """A published output as an earlier run stored it: `bullets` as (text, list, [the passages it cites]), each citation verified
    at its passage's place in the transcript. `stamp`: the copied-text rule it was checked under, if any."""
    text, cites, items = c["built"].text, [], []
    for i, (claim, kind, passages) in enumerate(bullets):
        nos = []
        for passage in passages:
            a = text.index(passage)
            cites.append((f"/bullets/{i}", a, a + len(passage)))
            nos.append(len(cites))
        items.append({"text": claim, "citations": nos, "list": kind})
    content = {"bullets": items, "attempt": 1, "stop_reason": "end_turn", "citation_mapping": {"offsets": len(cites)},
               "message_id": "msg_legacy", **({"echo_rule": stamp} if stamp else {})}
    sha = hashlib.sha256(uuid.uuid4().bytes).digest()
    with psycopg.connect(ADMIN) as conn, conn.transaction():
        out = conn.execute(
            """insert into core.ai_output (task, document_id, content, body, status, model, prompt_key, prompt_version,
                                           input_sha256, input_tokens, output_tokens, cost_usd)
               values (%s, %s, %s, %s, 'draft', 'claude-haiku-5-5', %s, %s, %s, 4000, 300, 0.00235) returning id""",
            (task_key, c["doc"], json.dumps(content, ensure_ascii=False), "\n".join(i["text"] for i in items), task_key, version,
             sha)).fetchone()[0]
        for i, (path, a, b) in enumerate(cites):
            conn.execute("""insert into core.ai_citation (ai_output_id, citation_no, claim_path, kind, document_id, char_start,
                                                          char_end, verified)
                            values (%s, %s, %s, 'document_span', %s, %s, %s, true)""", (out, i + 1, path, c["doc"], a, b))
        conn.execute("update core.ai_output set status = 'published' where id = %s", (out,))
    return out


def published_summary(doc_id: int):
    return q("""select id, content, body from core.ai_output where document_id = %s and task = 'call_summary'
                   and status = 'published'""", doc_id)


def citations_of(out_id: int):
    return q("""select citation_no, claim_path, char_start, char_end, verified from core.ai_citation where ai_output_id = %s
                 order by citation_no""", out_id)


def echo_detail(r) -> dict:
    return json.loads(r["checks"]["echo_recheck"]["detail"])


@pytest.mark.db
def test_the_recheck_supersedes_a_stored_summary_with_its_copied_end_cut_keeping_the_old_row_its_citations_and_no_cost():
    c = make_call()
    old = legacy(c, COPIED)
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task([c["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert fake.create_calls == 0 and r["rows"] == 1 and not r["cost"]                 # nothing was asked; one output written
    was, now = outputs(c["doc"])
    assert was[0] == "superseded" and was[1].startswith("superseded by output") and f"re-check {cs.ECHO_RULE}" in was[1]
    assert was[2] == 5 and was[3] == Decimal("0.00235")                                # the old row stays as it was
    assert now == ("published", "", 5, 0, "v5")                                        # same prompt version, no cost
    ((new, content, body),) = published_summary(c["doc"])
    was_content = q("select content from core.ai_output where id = %s", old)[0][0]
    assert content["bullets"][0] == {**was_content["bullets"][0], "text": LEAD}        # cut where the copied run started
    assert content["bullets"][1:] == was_content["bullets"][1:]                        # the clean ones as they were
    assert content["recheck"] == content["echo_rule"] == cs.ECHO_RULE and content["echo_cut"] == [0]
    assert content["message_id"] == "msg_legacy" and body == "\n".join(b["text"] for b in content["bullets"])
    assert "Demand in the export market" not in body and MEERA[:40] not in body
    assert citations_of(new) == citations_of(old) and len(citations_of(old)) == 7      # the same citations, copied over
    assert r["checks"]["echo_clean"]["passed"] and r["checks"]["echo_recheck"]["passed"]
    d = echo_detail(r)
    assert (d["looked"], d["unchanged"], d["superseded"], d["rejected"]) == (1, 0, 1, 0)
    assert d["bullets_cut"] == {"management": 1} and d["bullets_failed"] == {} and d["lost_publication"] == []
    assert d["published_left"] == 1 and d["longest_verbatim_run_left"] <= cs.MAX_ECHO_CHARS
    # a second run finds the stamp and writes nothing
    assert go(task([c["slug"]], fake)) == 0
    assert len(outputs(c["doc"])) == 2 and last_run()["rows"] == 0 and echo_detail(last_run())["looked"] == 0


@pytest.mark.db
def test_the_recheck_replaces_a_summary_whose_bullet_copies_elsewhere_or_is_too_short_with_a_rejection_and_names_the_call():
    start, short, fine = make_call(), make_call(), make_call()
    legacy(start, [(f"{MEERA} {LEAD}", "management", [MEERA]), *CLEAN])                # copied text at the start
    legacy(short, [(f"Capex is planned. {MEERA}", "management", [MEERA]), *CLEAN])    # under six words once the end is cut
    legacy(fine, CLEAN)
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task([start["slug"], short["slug"], fine["slug"]], fake)) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    for c, why in ((start, "copies transcript text"), (short, "words are its own once the copied end is cut")):
        rows = outputs(c["doc"])
        assert [o[0] for o in rows] == ["superseded", "rejected"] and rows[1][2] == 0 and rows[1][3] == 0
        assert rows[1][1].startswith(f"rejected: re-check {cs.ECHO_RULE}: bullet 1: ") and why in rows[1][1]
        assert q("select count(*) from core.ai_output where document_id = %s and status = 'published'", c["doc"]) == [(0,)]
        assert q("""select count(*) from core.ai_citation where ai_output_id = (select max(id) from core.ai_output
                                                                               where document_id = %s)""", c["doc"]) == [(0,)]
    assert [o[0] for o in outputs(fine["doc"])] == ["published"]                       # nothing changed: nothing written
    d = echo_detail(r)
    assert (d["looked"], d["unchanged"], d["superseded"], d["rejected"]) == (3, 1, 0, 2)
    assert d["bullets_failed"] == {"management": 2} and sum(d["failures_by_reason"].values()) == 2
    assert d["lost_publication"] == ["Acme Limited Q2 FY27"] * 2 and d["published_left"] == 1
    assert r["checks"]["echo_clean"]["passed"] and not r["checks"]["echo_recheck"]["passed"]     # a warning names what was lost
    assert len(alert(f"check:{KEY}:echo_recheck")) == 1
    resolve(f"check:{KEY}:echo_recheck")


@pytest.mark.db
def test_the_recheck_leaves_an_older_prompt_version_a_stamped_output_and_other_tasks_alone_and_runs_with_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    older, stamped, other, clean = make_call(), make_call(), make_call(), make_call()
    legacy(older, COPIED, version="v4")
    legacy(stamped, COPIED, stamp=cs.ECHO_RULE)                                         # checked under the rule already
    legacy(other, COPIED, task_key="doc_summary")                                       # module 1's task: never looked at
    legacy(clean, CLEAN)
    t = task([c["slug"] for c in (older, stamped, other, clean)], FakeClaude(["ok"], reply=replies()))
    t.claude = None                                                                     # the AI steps are skipped
    assert go(t) == 0
    r = last_run()
    assert r["status"] == "success", r["error"]
    assert [outputs(c["doc"]) for c in (older, stamped)] == [[("published", "", 5, Decimal("0.00235"), v)]
                                                              for v in ("v4", "v5")]
    assert q("select status, content ->> 'echo_rule' from core.ai_output where document_id = %s", other["doc"]) == [
        ("published", None)]
    assert len(outputs(clean["doc"])) == 1 and r["rows"] == 0
    d = echo_detail(r)
    assert (d["looked"], d["unchanged"], d["superseded"], d["rejected"]) == (1, 1, 0, 0)
    resolve(f"ai_unavailable:calls:{month()}")
    resolve(f"check:{KEY}:ai_available")


@pytest.mark.db
def test_a_backfill_s_range_limits_the_recheck_to_the_calls_filed_in_it():
    early, late = make_call(filed=date(2026, 7, 20)), make_call(filed=date(2026, 10, 20))
    for c in (early, late):
        legacy(c, COPIED)
    fake = FakeClaude(["ok"], reply=replies())
    assert go(task([early["slug"], late["slug"]], fake), since=date(2026, 10, 1), until=date(2026, 10, 31)) == 0
    assert [len(outputs(c["doc"])) for c in (early, late)] == [1, 2] and echo_detail(last_run())["looked"] == 1
    assert go(task([early["slug"], late["slug"]], fake)) == 0                           # no range: the rest
    assert [len(outputs(c["doc"])) for c in (early, late)] == [2, 2] and echo_detail(last_run())["looked"] == 1


@pytest.mark.db
def test_the_run_fails_and_rolls_the_recheck_back_if_the_stored_summaries_still_hold_copied_text(monkeypatch):
    """Simulates a rule that doesn't hold: the read-back after the load finds the cut bullet still copying, the blocking
    `echo_clean` check fails the run, and the last good data stays."""
    c = make_call()
    legacy(c, COPIED)
    real, seen = cs.recheck_content, []

    def flaky(content, spans, text, transcript):
        seen.append(1)
        out = real(content, spans, text, transcript)
        if len(seen) > 1:                                                               # the read-back
            out.cut = [0]
        return out
    monkeypatch.setattr(cs, "recheck_content", flaky)
    assert go(task([c["slug"]], FakeClaude(["ok"], reply=replies()))) != 0 or last_run()["status"] != "success"
    r = last_run()
    assert r["status"] == "checks_failed" and not r["checks"]["echo_clean"]["passed"]
    assert "still hold copied text" in r["checks"]["echo_clean"]["detail"]
    assert [o[0] for o in outputs(c["doc"])] == ["published"]                           # rolled back
