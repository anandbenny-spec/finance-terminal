""""Latest" document questions (K-13, #630; CLAUDE.md §1: right, labelled or absent): a question asking for the latest document or
meeting is answered only from that bank's latest meeting's documents. The search starts at that meeting's date when the bank is
known first (the date floor), and after the model an answer that cites none of that meeting's documents becomes "not found" with
the meeting's page. Questions without the words are unchanged."""
from datetime import UTC, date, datetime

import pytest
from ai_fakes import FakeClaude, cite, claude
from test_cmdbar_documents import FED, RBI, row, text_block, voyage
from test_cmdbar_route import FakeReader, Harness

from ft.cmdbar import cache
from ft.cmdbar import documents as d
from ft.cmdbar import route as r
from ft.cmdbar import sql as s

LATEST_Q = "What did the RBI say about inflation in its latest policy resolution?"
OLD_TEXT = "The MPC noted that inflation remained above the target band. It kept the policy stance accommodative."
NEW_TEXT = "The MPC noted that inflation had eased within the target band. It kept the stance neutral."
OLD = row(document_id=40, chunk_no=2, body=OLD_TEXT, doc_type="mpc_resolution",
          title="MPC resolution, 8 Dec 2021 · Press Release: 2021-2022/1300", bank="rbi", event_slug="rbi-mpc-2021-12-08",
          published_at=datetime(2021, 12, 8, 4, 30, tzinfo=UTC), source_url=None)
NEW = row(document_id=41, chunk_no=1, body=NEW_TEXT, doc_type="mpc_resolution",
          title="MPC resolution, 7 Oct 2026 · Press Release: 2026-2027/1100", bank="rbi", event_slug="rbi-mpc-2026-10-07",
          published_at=datetime(2026, 10, 7, 4, 30, tzinfo=UTC), source_url=None)
MEETINGS = {"RBI": date(2026, 10, 7), "Fed": date(2026, 10, 28)}
RBI_PAGE = {"href": "/policy/rbi/2026-10-07", "title": "The RBI's latest meeting"}


class LatestReader(FakeReader):
    """FakeReader with `semantic.policy_meetings`' latest held meeting per bank (`d.LATEST_SQL`), each lookup recorded."""

    def __init__(self, *script, passages=(), latest=None, fail=None):
        super().__init__(*script, passages=passages)
        self.latest, self.fail, self.looked = dict(MEETINGS if latest is None else latest), fail, []

    def select(self, sql, params):
        if "semantic.policy_meetings" in sql:
            assert sql == d.LATEST_SQL
            self.looked.append(params[0])
            if self.fail:
                raise self.fail
            v = self.latest.get(params[0])
            return s.Result(["meeting_date"], [(v,)] if v else [])
        return super().select(sql, params)


def first_cited(docs):
    """Every passage sent cited, one sentence each, words only (no figure to check)."""
    return [text_block(f"The MPC said {'inflation had eased' if 'eased' in t else 'inflation remained high'}. ",
                       cite(t, 0, t.index(".") + 1, i)) for i, (_, t) in enumerate(docs)]


def run(question=LATEST_Q, rows=(OLD,), banks=("rbi",), **kw):
    fake = FakeClaude(reply=lambda docs, mode: first_cited(docs))
    reader = LatestReader(passages=rows, **{k: v for k, v in kw.items() if k in ("latest", "fail")})
    ans = d.answer_documents(claude(fake), voyage()[0], reader, question, banks=banks,
                             **{k: v for k, v in kw.items() if k in ("date_from", "date_to")})
    return ans, reader, fake


# ------------------------------------------------------------------ the words
@pytest.mark.parametrize("q", [LATEST_Q, "What changed in the RBI's latest resolution?", "What did the most recent FOMC minutes say?",
                               "Summarise the newest Fed statement", "What did the MPC say at its last meeting?",
                               "What did the Fed say in its last policy statement?", "Inflation in the RBI's last MPC resolution?"])
def test_a_latest_question_is_recognised(q):
    assert d.asks_latest(q)


@pytest.mark.parametrize("q", ["What did the RBI say about inflation last year?", "When did the RBI last cut rates?",
                               "What stance did the MPC adopt in October 2024?", "How did the Fed describe the labour market last month?",
                               "Last time the Fed hiked?"])
def test_last_year_and_the_like_are_not_latest_questions(q):
    assert not d.asks_latest(q)


def test_the_banks_a_question_names():
    assert d.banks_named(LATEST_Q) == ("rbi",) and d.banks_named("the latest FOMC statement") == ("fed",)
    assert d.banks_named("Fed and Reserve Bank of India") == ("fed", "rbi") and d.banks_named("the latest statement") == ()


# ------------------------------------------------------------------ the check after the model
def test_a_reply_citing_only_older_documents_becomes_not_found_with_the_latest_meetings_page():
    ans, reader, fake = run(rows=(OLD,))
    assert ans.state == "not_found" and ans.summary == d.LATEST_NOT_FOUND_TEXT and ans.pages == [RBI_PAGE]
    assert ans.citations == [] and ans.documents == [] and ans.error_class == "latest: older documents"
    assert reader.looked == ["RBI"] and len(fake.requests) == 1


def test_a_reply_citing_the_latest_meetings_resolution_is_kept():
    ans, reader, _ = run(rows=(NEW, OLD))
    assert ans.state == "answered" and ans.pages == [] and reader.looked == ["RBI"] and len(ans.citations) == 2
    assert [doc["date"] for doc in ans.documents] == ["2026-10-07", "2021-12-08"]


def test_a_document_released_after_the_latest_meeting_counts_as_its_own():
    minutes = row(document_id=42, body=NEW_TEXT, doc_type="mpc_minutes", title="Minutes of the MPC meeting", bank="rbi",
                  event_slug="rbi-mpc-2026-10-07", published_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    assert run(rows=(minutes,))[0].state == "answered"


def test_a_question_without_the_words_is_unchanged():
    ans, reader, _ = run(question="What did the RBI say about inflation in October 2024?", rows=(OLD,))
    assert ans.state == "answered" and reader.looked == [] and ans.floor is None
    (_, _, _, _, d0, d1, _), = reader.searched
    assert (d0, d1) == (None, None)


def test_last_year_does_not_trigger_the_check():
    ans, reader, _ = run(question="What did the RBI say about inflation last year?", rows=(OLD,))
    assert ans.state == "answered" and reader.looked == []


def test_with_no_bank_named_the_cited_documents_bank_is_checked():
    ans, reader, _ = run(question="What did the latest policy resolution say about inflation?", rows=(OLD,), banks=())
    assert ans.state == "not_found" and ans.pages == [RBI_PAGE] and reader.looked == ["RBI"] and ans.floor is None
    ans, _, _ = run(question="What did the latest policy resolution say about inflation?", rows=(NEW,), banks=())
    assert ans.state == "answered"


def test_each_bank_asked_about_needs_its_latest_meeting_cited():
    fed_old = FED                                                       # an FOMC statement of 18 Sep 2024
    ans, reader, _ = run(question="What did the Fed and the RBI say in their latest statements?", rows=(NEW, fed_old),
                         banks=("fed", "rbi"))
    assert ans.state == "not_found" and ans.pages == [{"href": "/policy/fed/2026-10-28", "title": "The Fed's latest meeting"}]
    assert reader.looked == ["Fed", "RBI"]


def test_an_unknown_latest_meeting_fails_closed():
    ans, _, _ = run(rows=(NEW,), latest={})
    assert ans.state == "not_found" and ans.summary == d.LATEST_NOT_FOUND_TEXT and ans.pages == []


def test_a_failed_lookup_is_cant_answer_with_no_model_call():
    ans, reader, fake = run(rows=(NEW,), fail=s.QueryError("QueryCanceled"))
    assert (ans.state, ans.error_class) == ("cant_answer", "database: QueryCanceled") and fake.requests == []
    assert reader.searched == []                                                  # nothing searched either
    ans, _, _ = run(rows=(NEW,), fail=s.ReaderDown("OperationalError"))
    assert (ans.state, ans.error_class) == ("cant_answer", "database_unavailable")


# ------------------------------------------------------------------ the date floor
def test_the_search_starts_at_the_latest_meetings_date():
    ans, reader, _ = run(rows=(NEW,))
    (_, _, banks, doc_types, d0, d1, k), = reader.searched
    assert d0 == date(2026, 10, 7) and d1 is None and ans.floor == date(2026, 10, 7)
    assert banks == ["fed", "rbi"] and len(doc_types) == 7 and k == 8                 # the module's filters stay fixed (N3)


def test_the_routers_dates_are_kept_and_the_check_still_runs():
    ans, reader, _ = run(rows=(OLD,), date_from=date(2021, 1, 1), date_to=date(2021, 12, 31))
    (_, _, _, _, d0, d1, _), = reader.searched
    assert (d0, d1) == (date(2021, 1, 1), date(2021, 12, 31)) and ans.floor is None and ans.state == "not_found"


def test_nothing_stored_from_the_latest_meeting_on_is_not_found_with_its_page():
    ans, _, fake = run(rows=())
    assert ans.state == "not_found" and ans.summary == d.LATEST_NOT_FOUND_TEXT and ans.pages == [RBI_PAGE]
    assert fake.requests == []


# ------------------------------------------------------------------ the route
def latest_harness(rows=(OLD,), store=None):
    def answer(body):
        return {"route": "document", "page_slug": None,
                "filters": {"banks": ["rbi"], "doc_types": ["mpc_resolution"], "date_from": None, "date_to": None}}
    return Harness(store=store, fake=FakeClaude(answer=answer, reply=lambda docs, mode: first_cited(docs)),
                   reader=LatestReader(passages=rows))


def test_the_route_shows_not_found_with_the_meetings_page_and_never_caches_it():
    h = latest_harness()
    out = h.ask(LATEST_Q)
    assert (out["state"], out["route"], out["summary"], out["pages"]) == ("not_found", "document", d.LATEST_NOT_FOUND_TEXT, [RBI_PAGE])
    assert out["citations"] == [] and out["documents"] == [] and out["sources"] == [] and set(out) == set(r.FINAL_KEYS)
    assert h.store.entries == {} and h.store.logs[-1].error_class == "latest: older documents"
    kept = latest_harness(rows=(NEW,))
    assert kept.ask(LATEST_Q)["state"] == "answered" and len(kept.store.entries) == 1


def test_a_cached_latest_answer_stored_before_the_check_is_asked_again_and_any_other_is_served():
    h = latest_harness(rows=(NEW,))
    h.ask(LATEST_Q)
    entry = h.store.entries[cache.key(LATEST_Q)]
    assert "sources" in entry["answer"]
    again = latest_harness(rows=(NEW,), store=h.store)
    assert again.ask(LATEST_Q)["state"] == "answered" and again.fake.requests == []          # stored since the check: a hit
    del entry["answer"]["sources"]                                                           # stored before it
    third = latest_harness(rows=(OLD,), store=h.store)
    assert third.ask(LATEST_Q)["state"] == "not_found" and len(third.fake.requests) == 2      # asked again, through the check
    assert r.stored_before_latest_check({"route": "document"}, "What stance did the MPC adopt in October 2024?") is False
    assert r.stored_before_latest_check({"route": "data"}, LATEST_Q) is False


def test_the_eval_questions_go_through_the_same_check():
    """The judge's free-text run (`cmdbar-gates.yml` set `questions`) asks through `route.answer`: the same router banks reach it."""
    h = latest_harness()
    out, keep, row_ = r.answer(LATEST_Q, h.deps, h.store)
    assert out["state"] == "not_found" and not keep and out["pages"] == [RBI_PAGE] and row_.error_class == "latest: older documents"


def test_rbi_is_named_never_linked_in_the_latest_answer():
    h = latest_harness(rows=(NEW,))
    out = h.ask(LATEST_Q)
    assert out["documents"][0]["source_url"] is None and out["documents"][0]["press_release"] == "2026-2027/1100"
    assert RBI[12].startswith("https://rbi.org.in")                                           # the fixture's link is never shown
