"""equity.calls_ai (Spec 02B §5.6, §13.3; job 2B-11, #174) through the runner and the Batch API path against the test database as
job_writer: the cited summary of each stored call, its role rules, the retry, the limits and S2's degrade path. The Messages and
Message Batches APIs are fakes (tests/ai_fakes.py); the Anthropic SDK itself runs. The calls are synthetic (tests/call_fixtures.py):
a company, a `stored` earnings-call event, a published transcript document with its chunks and `equity.call_turn` rows. Each test
makes its own calls (unique slugs) and the task sees only those (`scope`)."""
import json
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal

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

from ft.ai import call_summary as cs
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
        c.execute(f"delete from core.ai_output where document_id in {docs}")
        c.execute(f"delete from equity.call_turn where document_id in {docs}")
        c.execute(f"delete from equity.earnings_call where entity_id in {ids}")
        c.execute(f"update core.document set supersedes_id = null where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'cai-%'")
        c.execute("delete from core.source where key like 'cai\\_%' and key like '%suppressed'")
        c.execute("delete from ops.alert where dedupe_key like 'ai_rejected:call_summary:%' "
                  "or dedupe_key like 'ai_unavailable:calls:%'")


@pytest.fixture(scope="module", autouse=True)
def clean():
    wipe()
    yield
    wipe()


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
              held: bool = False, publish_doc: bool = True, status: str = "stored", with_doc: bool = True) -> dict:
    """A stored call: company, event, transcript document (published, chunked), its turns and the earnings_call row."""
    tag = uuid.uuid4().hex[:8]
    lines = [(sp, (role_of or {}).get(i, role), t) for i, (sp, role, t) in enumerate(lines)]
    b = build(lines)
    src = source(src_key, suppressed=src_key.endswith("_suppressed"))
    eid = q("""insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
               values ('company', %s, 'Acme Limited', 'Acme', 'IN', 'INR', true) returning id""", f"cai-{tag}")[0][0]
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
