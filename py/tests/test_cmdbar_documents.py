"""The command bar's document route (Spec 07 §4 step 5b, N3; K-06, #584): retrieval over Fed and RBI policy documents only (a
published, embedded earnings-call transcript is never searched, cited or shown), answers with native citations, every sentence
without a verified citation dropped ("not found" when none is left), quotes of at most 400 code points, and an RBI document named
by its title, date and press-release number, never linked. Also the route's document branch (status lines, log, cache) and the
`retrieval` set in the gate runner. K-11's go-live check reads item 3 here (Spec 07 §10)."""
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from ai_fakes import FakeClaude, FakeVoyage, cite, claude
from test_cmdbar_route import NOW, FakeReader, FakeStore, Harness

from ft.ai.client import AIUnavailable, Embedder, VoyageAPIError, halfvec_literal
from ft.cmdbar import cache
from ft.cmdbar import documents as d
from ft.cmdbar import route as r
from ft.cmdbar import sql as s
from ft.eval import cmdbar as ev

FED_TEXT = ("The Committee decided to lower the target range for the federal funds rate by 1/4 percentage point. "
            "Inflation remains somewhat elevated.")
RBI_TEXT = ("The MPC decided to change the stance from withdrawal of accommodation to neutral. "
            "The policy repo rate was kept unchanged at 6.50 per cent.")
FED_AT = datetime(2024, 9, 18, 18, 0, tzinfo=UTC)
RBI_AT = datetime(2024, 10, 9, 4, 30, tzinfo=UTC)
QUESTION = "What stance did the MPC adopt in October 2024?"


def row(document_id=11, chunk_no=0, char_start=0, body=FED_TEXT, doc_type="fomc_statement", title="FOMC statement, 18 Sep 2024",
        bank="fed", event_slug="fomc-2024-09-18", published_at=FED_AT,
        source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20240918a.htm", score=0.03, section=None,
        speaker=None):
    """One search_chunks row, in SEARCH_SQL's column order."""
    return (document_id, chunk_no, char_start, char_start + len(body), section, speaker, body, doc_type, title, bank, event_slug,
            published_at, source_url, score)


FED = row()
RBI = row(document_id=22, chunk_no=3, char_start=1200, body=RBI_TEXT, doc_type="mpc_resolution",
          title="MPC resolution, 9 Oct 2024 · Press Release: 2024-2025/1234", bank="rbi", event_slug="mpc-2024-10-09",
          published_at=RBI_AT, source_url="https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=58998")
TRANSCRIPT = row(document_id=33, body="Revenue grew to ₹1,200 crore this quarter, and the stance stays neutral.",
                 doc_type="call_transcript", title="Example Ltd call", bank="example-co", event_slug="example-co-call-q2-fy25",
                 source_url="https://example.com/call.pdf")


def chunks(*rows):
    return [d.Chunk(*x[:13], x[13]) for x in rows]


def voyage():
    fake = FakeVoyage()
    return Embedder(client=fake, sleep=lambda s: None), fake


def text_block(text, *cites):
    return {"type": "text", "text": text, "citations": list(cites) or None}


def answering(blocks_of):
    """A fake Claude whose citations-route reply is `blocks_of(docs)` (docs: the request's (title, text) documents)."""
    return FakeClaude(reply=lambda docs, mode: blocks_of(docs))


def ask(blocks_of, *rows, question=QUESTION):
    fake = answering(blocks_of)
    emb, vo = voyage()
    reader = FakeReader(passages=rows)
    ans = d.answer_documents(claude(fake), emb, reader, question)
    return ans, fake, reader, vo


def both_cited(docs):
    (_, fed), (_, rbi) = docs
    a = fed.index("Inflation")
    return [text_block("The FOMC said inflation remains somewhat elevated", cite(fed, a, len(fed), 0)),
            text_block(". "),
            text_block("The MPC changed its stance to neutral.", cite(rbi, 0, rbi.index(" The policy") + 1, 1))]


# ------------------------------------------------------------------ retrieval (N3)
def test_retrieval_reads_fed_and_rbi_policy_documents_only_with_k_8_and_the_routers_dates():
    emb, vo = voyage()
    reader = FakeReader(passages=[FED])
    got, foreign = d.search(reader, emb.embed_query(QUESTION), QUESTION, date_from=date(2024, 1, 1), date_to=date(2024, 12, 31))
    (vec, text, banks, doc_types, d0, d1, k), = reader.searched
    assert banks == ["fed", "rbi"] and k == 8 and (d0, d1) == (date(2024, 1, 1), date(2024, 12, 31)) and text == QUESTION
    assert doc_types == ["fomc_statement", "fomc_minutes", "fomc_presconf_transcript", "mpc_resolution", "mpc_minutes",
                         "rbi_governor_statement", "rbi_presconf_transcript"]
    assert "call_transcript" not in doc_types and vec.startswith("[") and vec.count(",") == 511
    assert "banks => %s::text[]" in d.SEARCH_SQL and "doc_types => %s::text[]" in d.SEARCH_SQL and "k => %s" in d.SEARCH_SQL
    assert [c.document_id for c in got] == [11] and foreign == 0 and vo.calls[0]["model"] == "voyage-4"


def test_a_row_that_isnt_a_fed_or_rbi_policy_document_never_reaches_the_model_or_the_visitor():
    ans, fake, _, _ = ask(lambda docs: [text_block("The FOMC lowered the target range.",
                                                   cite(docs[0][1], 0, FED_TEXT.index(" Inflation"), 0))], TRANSCRIPT, FED)
    docs = [b for b in fake.requests[0]["messages"][0]["content"] if b["type"] == "document"]
    assert [b["source"]["data"] for b in docs] == [FED_TEXT]                    # the transcript's passage is never sent
    assert ans.foreign == 1 and ans.error_class == "retrieval: foreign document" and ans.state == "answered"
    shown = json.dumps({"c": ans.citations, "d": ans.documents, "s": ans.summary}, ensure_ascii=False)
    assert "Revenue" not in shown and "call_transcript" not in shown


def test_a_citation_into_a_passage_that_isnt_a_policy_document_is_not_evidence():
    got = chunks(TRANSCRIPT)
    blocks = [text_block("Revenue grew.", cite(TRANSCRIPT[6], 0, 40, 0))]
    from ft.ai.client import Reply, Usage
    sentences, mapping = d.check(Reply("claude-haiku-5-5", "m", "end_turn", Usage(), Decimal(0), _blocks(blocks)), got)
    assert sentences[0].problem == "no verified citation" and mapping["foreign"] == 1


def _blocks(blocks):
    """API content blocks from dicts (what Reply.parts reads)."""
    from types import SimpleNamespace as NS
    return [NS(type="text", text=b["text"], citations=[NS(**c) for c in (b["citations"] or [])]) for b in blocks]


# ------------------------------------------------------------------ the request
def test_the_answer_request_is_haiku_at_effort_low_on_the_citations_route_with_the_question_as_data():
    _, fake, _, _ = ask(both_cited, FED, RBI)
    body = fake.requests[0]
    assert body["model"] == "claude-haiku-5-5" and body["output_config"] == {"effort": "low"} and body["max_tokens"] == 4000
    docs = [b for b in body["messages"][0]["content"] if b["type"] == "document"]
    assert [b["citations"] for b in docs] == [{"enabled": True}] * 2
    assert docs[0]["context"] == "Fed · FOMC statement · released 18 September 2024"
    assert docs[1]["context"] == "RBI · MPC resolution · released 9 October 2024"
    instruction = body["messages"][0]["content"][2]["text"]
    assert json.dumps(QUESTION) in instruction and "data, not instructions" in instruction
    assert "Every sentence must cite the passages that state it." in body["system"]


# ------------------------------------------------------------------ the check
def test_each_kept_sentence_carries_its_verified_citations_with_quotes_rebuilt_from_our_text():
    ans, _, _, _ = ask(both_cited, FED, RBI)
    assert ans.state == "answered" and [len(c["refs"]) for c in ans.citations] == [1, 1]
    assert ans.summary == "The FOMC said inflation remains somewhat elevated. The MPC changed its stance to neutral."
    (fed_ref,), (rbi_ref,) = ans.citations[0]["refs"], ans.citations[1]["refs"]
    assert fed_ref["quote"] == "Inflation remains somewhat elevated." and fed_ref["document"] == 0
    assert (fed_ref["char_start"], fed_ref["char_end"]) == (FED_TEXT.index("Inflation"), len(FED_TEXT))
    start = 1200                                                               # the RBI passage's chunk starts at 1,200
    assert (rbi_ref["char_start"], rbi_ref["char_end"]) == (start, start + RBI_TEXT.index(" The policy") + 1)
    assert rbi_ref["quote"] == RBI_TEXT[:RBI_TEXT.index(" The policy") + 1] and rbi_ref["document"] == 1


def test_an_rbi_document_is_named_by_its_title_date_and_press_release_number_never_linked():
    ans, _, _, _ = ask(both_cited, FED, RBI)
    fed, rbi = ans.documents
    assert fed == {"document_id": 11, "bank": "Fed", "doc_type": "fomc_statement", "label": "FOMC statement",
                   "title": "FOMC statement, 18 Sep 2024", "date": "2024-09-18", "event_slug": "fomc-2024-09-18",
                   "press_release": None, "source_url": FED[12]}
    assert rbi == {"document_id": 22, "bank": "RBI", "doc_type": "mpc_resolution", "label": "MPC resolution",
                   "title": "MPC resolution, 9 Oct 2024", "date": "2024-10-09", "event_slug": "mpc-2024-10-09",
                   "press_release": "2024-2025/1234", "source_url": None}
    assert "rbi.org.in" not in json.dumps({"c": ans.citations, "d": ans.documents, "s": ans.summary})


def test_an_uncited_sentence_is_dropped():
    def blocks(docs):
        fed = docs[0][1]
        return [text_block("The FOMC lowered the target range by 1/4 percentage point.", cite(fed, 0, fed.index(" Inflation"), 0)),
                text_block(" Markets expect two more cuts this year.")]
    ans, _, _, _ = ask(blocks, FED)
    assert ans.state == "answered" and [c["text"] for c in ans.citations] == [
        "The FOMC lowered the target range by 1/4 percentage point."]
    assert [x.problem for x in ans.dropped] == ["no verified citation"] and "Markets" not in ans.summary


@pytest.mark.parametrize("reply", ["all_uncited", "not_found"])
def test_an_answer_with_no_cited_sentence_left_is_not_found(reply):
    text = "The FOMC lowered rates. Inflation is elevated." if reply == "all_uncited" else "Not found."
    ans, fake, _, _ = ask(lambda docs: [text_block(text)], FED)
    assert ans.state == "not_found" and ans.citations == [] and ans.documents == [] and ans.summary is None
    assert len(fake.requests) == 1 and ans.cost_usd > 0                       # billed, and nothing shown


def test_no_passage_returned_is_not_found_with_no_model_call():
    ans, fake, reader, _ = ask(both_cited)
    assert ans.state == "not_found" and fake.requests == [] and len(reader.searched) == 1


def test_a_citation_whose_text_isnt_ours_is_not_evidence():
    def blocks(docs):
        bad = {"type": "char_location", "cited_text": "The Committee raised the target range.", "document_index": 0,
               "document_title": "x", "start_char_index": 0, "end_char_index": 38}
        return [text_block("The FOMC raised the target range.", bad)]
    ans, _, _, _ = ask(blocks, FED)
    assert ans.state == "not_found" and ans.mapping["failed"] == 1


def test_offsets_that_are_off_still_verify_when_the_cited_text_is_found_once():
    def blocks(docs):
        fed = docs[0][1]
        c = cite(fed, fed.index("Inflation"), len(fed), 0)
        c["start_char_index"] += 2
        c["end_char_index"] += 2
        return [text_block("Inflation remains somewhat elevated.", c)]
    ans, _, _, _ = ask(blocks, FED)
    assert ans.state == "answered" and ans.mapping["search"] == 1
    assert ans.citations[0]["refs"][0]["char_start"] == FED_TEXT.index("Inflation")


def test_a_figure_not_in_the_cited_passage_drops_the_sentence_but_the_cited_documents_date_is_allowed():
    def blocks(docs):
        rbi = docs[0][1]
        stance = cite(rbi, 0, rbi.index(" The policy") + 1, 0)
        rate = cite(rbi, rbi.index("The policy"), len(rbi), 0)
        return [text_block("In its 9 October 2024 resolution, the MPC changed its stance to neutral.", stance),
                text_block(" The repo rate was cut to 6.25 per cent.", rate)]
    ans, _, _, _ = ask(blocks, RBI)
    assert [c["text"] for c in ans.citations] == ["In its 9 October 2024 resolution, the MPC changed its stance to neutral."]
    assert ans.dropped[0].problem == "numbers not in its cited passages: 6.25"


def test_a_sentence_with_a_web_address_is_dropped_and_a_passage_holding_rbis_address_is_no_quote():
    body = "Details are on the website at https://rbi.org.in/Scripts/x.aspx. The stance is neutral."
    rbi_row = row(document_id=22, body=body, doc_type="mpc_resolution", title="MPC resolution, 9 Oct 2024", bank="rbi",
                  event_slug="mpc-2024-10-09", published_at=RBI_AT, source_url=None)

    def blocks(docs):
        t = docs[0][1]
        return [text_block("See www.example.org/stance for more.", cite(t, t.index("The stance"), len(t), 0)),
                text_block(" The details are on RBI's website.", cite(t, 0, t.index(" The stance"), 0))]
    ans, _, _, _ = ask(blocks, rbi_row)
    assert ans.state == "not_found" and [x.problem for x in ans.sentences] == ["a web address", "no verified citation"]
    assert ans.mapping["rbi_address"] == 1


def test_quotes_are_at_most_400_code_points():
    body = "The MPC noted that food prices rose by ₹ measures. " + "Vegetable prices stayed volatile across regions. " * 12
    long_row = row(document_id=22, body=body, doc_type="mpc_minutes", title="MPC minutes, 23 Oct 2024", bank="rbi",
                   event_slug="mpc-2024-10-09", published_at=RBI_AT, source_url=None)
    ans, _, _, _ = ask(lambda docs: [text_block("Vegetable prices stayed volatile.", cite(docs[0][1], 0, len(body), 0))], long_row)
    ref = ans.citations[0]["refs"][0]
    assert len(body) > 400 and 390 <= len(ref["quote"]) <= 400 and ref["quote"].endswith("…") and "₹" in ref["quote"]
    assert body.startswith(ref["quote"][:-1])
    assert (ref["char_start"], ref["char_end"]) == (0, len(body))             # the span itself is whole


def test_a_blocks_citations_go_to_the_sentences_its_words_overlap_not_one_it_only_ends():
    def carried(docs):
        rbi = docs[0][1]
        return [text_block("The MPC changed its stance to neutral"),
                text_block(". It kept the repo rate at 6.50 per cent", cite(rbi, rbi.index("The policy"), len(rbi), 0)),
                text_block(".")]
    ans, _, _, _ = ask(carried, RBI)
    assert [x.problem for x in ans.sentences] == ["no verified citation", None]
    assert ans.summary == "It kept the repo rate at 6.50 per cent."

    def spanning(docs):
        rbi = docs[0][1]
        return [text_block("The MPC changed its stance to neutral. It kept the repo rate at 6.50 per cent.",
                           cite(rbi, 0, len(rbi), 0))]
    ans, _, _, _ = ask(spanning, RBI)
    assert [x.problem for x in ans.sentences] == [None, None] and len(ans.citations) == 2


def test_list_markers_are_not_part_of_a_sentence():
    ans, _, _, _ = ask(lambda docs: [text_block("- "), text_block("Inflation remains somewhat elevated.",
                                                                   cite(docs[0][1], FED_TEXT.index("Inflation"),
                                                                        len(docs[0][1]), 0))], FED)
    assert ans.citations[0]["text"] == "Inflation remains somewhat elevated."


# ------------------------------------------------------------------ failures
def test_failures_are_cant_answer_with_their_class_and_no_key_passes_through():
    emb = Embedder(client=FakeVoyage(fail=[VoyageAPIError(400, "bad")]), sleep=lambda s: None)
    fake = answering(both_cited)
    got = d.answer_documents(claude(fake), emb, FakeReader(passages=[FED]), QUESTION)
    assert got.state == "cant_answer" and got.error_class == "embedding: InvalidRequestError" and fake.requests == []

    class Down(FakeReader):
        def select(self, sql, params):
            raise s.QueryError("QueryCanceled")
    got = d.answer_documents(claude(fake), voyage()[0], Down(), QUESTION)
    assert got.state == "cant_answer" and got.error_class == "database: QueryCanceled"

    assert got.searches == 2                                  # the timeout is retried once, then it is "can't answer"

    fake = FakeClaude(script=("max_tokens",), reply=lambda docs, mode: both_cited(docs))
    got = d.answer_documents(claude(fake), voyage()[0], FakeReader(passages=[FED, RBI]), QUESTION)
    assert got.state == "cant_answer" and got.error_class == "model_output" and got.cost_usd > 0

    with pytest.raises(AIUnavailable):
        d.answer_documents(claude(fake), Embedder(client=FakeVoyage(fail=[VoyageAPIError(401, "no")]), sleep=lambda s: None),
                           FakeReader(passages=[FED]), QUESTION)
    with pytest.raises(AIUnavailable):
        d.answer_documents(claude(FakeClaude(script=("402",))), voyage()[0], FakeReader(passages=[FED]), QUESTION)


def test_a_search_cancelled_by_the_timeout_is_run_once_more_and_other_database_errors_are_not():
    """The cold first search (K-10, #587): one more try at the same 3-second limit, never a longer one."""
    class ColdOnce(FakeReader):
        def select(self, sql, params):
            if not self.searched:
                self.searched.append(params)
                raise s.QueryError("QueryCanceled")
            return super().select(sql, params)
    fake = answering(both_cited)
    reader = ColdOnce(passages=[FED, RBI])
    got = d.answer_documents(claude(fake), voyage()[0], reader, QUESTION)
    assert got.state == "answered" and got.searches == 2 and len(reader.searched) == 2 and got.error_class is None

    class Broken(FakeReader):
        def select(self, sql, params):
            self.searched.append(params)
            raise s.QueryError("UndefinedFunction")
    reader = Broken()
    got = d.answer_documents(claude(fake), voyage()[0], reader, QUESTION)
    assert (got.state, got.error_class, got.searches, len(reader.searched)) == ("cant_answer", "database: UndefinedFunction", 1, 1)


# ------------------------------------------------------------------ the route's document branch
def doc_harness(rows=(FED, RBI), blocks_of=both_cited, store=None):
    def answer(body):
        return {"route": "document", "page_slug": None,
                "filters": {"banks": ["rbi"], "doc_types": [], "date_from": "2024-10-01", "date_to": "2024-10-31"}}
    h = Harness(store=store, fake=FakeClaude(answer=answer, reply=lambda docs, mode: blocks_of(docs)),
                reader=FakeReader(passages=rows))
    return h


def test_a_document_question_answers_through_the_route_with_citations_documents_and_a_log_row():
    h = doc_harness()
    out = h.ask(QUESTION)
    assert out["state"] == "answered" and out["route"] == "document" and set(out) == set(r.FINAL_KEYS)
    assert h.lines == [r.UNDERSTANDING, r.SEARCHING] and len(out["citations"]) == 2 and len(out["documents"]) == 2
    assert out["sql"] is None and out["rows"] == [] and out["documents"][1]["source_url"] is None
    (_, _, banks, doc_types, d0, d1, _), = h.reader.searched
    assert banks == ["fed", "rbi"] and len(doc_types) == 7 and (d0, d1) == (date(2024, 10, 1), date(2024, 10, 31))
    assert h.reader.closed and h.voyage.calls == [QUESTION]
    log = h.store.logs[-1]
    assert (log.route, log.answered, log.rows_returned, log.generated_sql, log.error_class) == ("document", True, 2, None, None)
    assert log.models == ["claude-haiku-5-5", "claude-haiku-5-5"] and log.cost_usd > 0


def test_a_cached_document_answer_is_served_without_a_call_until_a_policy_task_refreshes():
    h = doc_harness()
    h.ask(QUESTION)
    again = doc_harness(store=h.store)
    assert again.ask(QUESTION)["state"] == "answered" and again.fake.requests == [] and again.voyage.calls == []
    h.store.fresh["equity.prices"] = h.store.fresh["equity.prices"] + timedelta(hours=1)     # not the documents' task
    third = doc_harness(store=h.store)
    assert third.ask(QUESTION)["state"] == "answered" and third.fake.requests == []
    h.store.fresh["policy.rbi_documents"] = NOW + timedelta(minutes=1)
    fourth = doc_harness(store=h.store)
    assert fourth.ask(QUESTION)["state"] == "answered" and len(fourth.fake.requests) == 2


def test_the_cache_version_of_a_document_answer_follows_the_policy_tasks():
    fresh = {"policy.fed_documents": datetime(2026, 11, 1, tzinfo=UTC), "equity.prices": datetime(2026, 11, 9, tzinfo=UTC)}
    assert cache.answer_version({"route": "document", "sql": None}, fresh) == "2026-11-01T00:00:00+00:00"
    assert cache.answer_version({"route": "page", "sql": None}, fresh) == cache.STATIC


def test_not_found_and_failed_document_answers_are_never_cached():
    h = doc_harness(blocks_of=lambda docs: [text_block("Not found.")])
    assert h.ask(QUESTION)["state"] == "not_found" and h.store.entries == {}
    assert h.ask(QUESTION)["state"] == "not_found" and len(h.fake.requests) == 4     # asked again, not served from the cache

    class Down(FakeReader):
        def select(self, sql, params):
            raise s.ReaderDown("OperationalError")
    h = doc_harness()
    h.reader = Down()
    out = h.ask(QUESTION)
    assert out["state"] == "cant_answer" and h.store.entries == {} and h.store.logs[-1].error_class == "database_unavailable"


def test_no_voyage_key_or_credit_mid_ask_is_paused():
    h = doc_harness()
    h.voyage = Embedder(client=FakeVoyage(fail=[VoyageAPIError(402, "billing")]), sleep=lambda s: None)
    out = h.ask(QUESTION)
    assert out["state"] == "paused" and h.store.logs[-1].error_class == "ai_unavailable" and h.store.entries == {}


def test_the_switch_off_makes_no_voyage_call():
    h = doc_harness(store=FakeStore(enabled=False))
    assert h.ask(QUESTION)["state"] == "paused" and h.voyage.calls == [] and h.built["embedder"] == 0


# ------------------------------------------------------------------ the gate runner's retrieval set
ITEM = {"id": "ret-dev04", "bank": "rbi", "doc_type": "mpc_resolution", "event_slug": "mpc-2024-10-09", "char_start": 1210,
        "char_end": 1220}


def test_the_expected_passage_is_found_by_document_and_overlapping_span():
    assert ev.expected_rank(ITEM, chunks(FED, RBI)) == 2
    assert ev.expected_rank({**ITEM, "char_start": 5000, "char_end": 5010}, chunks(FED, RBI)) is None
    assert ev.expected_rank({**ITEM, "event_slug": "mpc-2024-12-06"}, chunks(FED, RBI)) is None


def test_a_span_list_item_is_a_hit_when_any_of_its_spans_overlaps_a_returned_passage():
    """K-06's option B for round 2's items (#587): every passage stating the answer is listed; any one overlapping counts."""
    base = {k: v for k, v in ITEM.items() if k not in ("char_start", "char_end")}
    assert ev.expected_rank({**base, "spans": [[5000, 5010], [1210, 1220]]}, chunks(FED, RBI)) == 2
    assert ev.expected_rank({**base, "spans": [[5000, 5010], [6000, 6010]]}, chunks(FED, RBI)) is None
    assert ev.item_spans({**base, "spans": [[1, 2], [3, 4]]}) == [(1, 2), (3, 4)] and ev.item_spans(ITEM) == [
        (ITEM["char_start"], ITEM["char_end"])]


def _ret(rank, foreign=0):
    return ev.ItemResult("i", rank is not None, "hit" if rank else "miss", rank=rank, foreign=foreign)


def test_the_retrieval_rule_needs_recall_of_0_85_and_no_foreign_row():
    m = ev.retrieval_metrics([_ret(1)] * 17 + [_ret(None)] * 3)
    assert m["recall_at_8"] == 0.85 and m["mrr"] == 0.85 and ev.retrieval_rule_met(m)
    assert not ev.retrieval_rule_met(ev.retrieval_metrics([_ret(1)] * 16 + [_ret(None)] * 4))
    assert not ev.retrieval_rule_met(ev.retrieval_metrics([_ret(1)] * 19 + [_ret(1, foreign=1)]))
    assert ev.retrieval_metrics([_ret(2), _ret(4)])["mrr"] == 0.375


class RetrievalReader(FakeReader):
    """search_chunks as the runner sees it: the expected passage second, and a sentinel passage to keep out of the log."""

    def __init__(self):
        super().__init__(passages=[row(body="SENTINEL-PASSAGE. " + FED_TEXT), RBI])

    def select(self, sql, params=()):
        if "has_function_privilege" in sql:
            return s.Result(["ok"], [(True,)])
        return super().select(sql, params)


def test_the_retrieval_dry_run_sends_nothing_and_a_sent_run_scores_records_and_logs_no_text():
    from test_cmdbar_runner import FakeWriter, run_main
    fake = answering(both_cited)
    emb, vo = voyage()
    code, lines = run_main(["--set", "retrieval", "--split", "dev"], client=claude(fake), reader=RetrievalReader(), embedder=emb)
    assert code == 0 and fake.requests == [] and vo.calls == [] and '"search_chunks_executable":true' in "\n".join(lines)

    wr = FakeWriter()
    code, lines = run_main(["--set", "retrieval", "--split", "dev", "--send", "--items", "ret-dev04"], client=claude(fake),
                           reader=RetrievalReader(), writer=wr, embedder=emb)
    assert code == 0 and len(fake.requests) == 1 and len(vo.calls) == 1
    eval_set, _, model_id, prompt_version, n, metrics, passed, _ = wr.runs[0]
    assert (eval_set, model_id, prompt_version, n, passed) == ("retrieval", "claude-haiku-5-5", "v1", 1, False)
    m = metrics.obj
    assert m["recall_at_8"] == 0.0 and m["foreign"] == 0 and m["answers"]["transcript_chars"] == 0
    assert m["answers"]["rbi_links"] == 0 and m["doc_types"] == list(d.DOC_TYPES)
    item_key, expected, predicted = wr.results[0][1], wr.results[0][2].obj, wr.results[0][3].obj
    assert item_key == "ret-dev04" and expected["event_slug"] == "mpc-2024-10-09" and predicted["retrieved"] == [[11, 0], [22, 3]]
    text = "\n".join(lines)
    assert "SENTINEL" not in text and "neutral" not in text and "rbi.org.in" not in text


def test_the_query_model_comparison_embeds_with_the_other_model_and_is_never_recorded_as_the_gate():
    """Architecture 03 §0: the retrieval evaluation also runs the other model's questions (K-10, #587). Only the questions' model
    changes, the run says which model it used, and a comparison run is never `passed`; the route embeds with voyage-4."""
    from test_cmdbar_runner import FakeWriter, run_main
    fake = answering(both_cited)
    emb, vo = voyage()
    wr = FakeWriter()
    code, lines = run_main(["--set", "retrieval", "--split", "test", "--send", "--items", "ret-t01", "--query-model",
                            "voyage-4-lite"], client=claude(fake), reader=RetrievalReader(), writer=wr, embedder=emb)
    assert code == 0 and [(c["model"], c["input_type"], c["output_dimension"]) for c in vo.calls] == [("voyage-4-lite", "query", 512)]
    metrics, passed, notes = wr.runs[0][5:]
    assert metrics.obj["embedding_model"] == "voyage-4-lite" and passed is False and "comparison" in notes
    assert '"query_model":"voyage-4-lite"' in lines[0]

    emb, vo = voyage()                                       # the default is the route's own model
    wr = FakeWriter()
    code, _ = run_main(["--set", "retrieval", "--split", "test", "--send", "--items", "ret-t01"], client=claude(fake),
                       reader=RetrievalReader(), writer=wr, embedder=emb)
    assert code == 0 and [c["model"] for c in vo.calls] == ["voyage-4"] and wr.runs[0][5].obj["embedding_model"] == "voyage-4"
    assert "comparison" not in wr.runs[0][7]


def test_the_query_model_option_is_refused_outside_the_retrieval_set():
    from test_cmdbar_runner import run_main
    with pytest.raises(SystemExit):
        run_main(["--set", "cmdbar_router", "--split", "test", "--query-model", "voyage-4-lite"])


def test_the_retrieval_price_is_cents_and_its_worst_case_above_its_estimate():
    p = ev.price_run("retrieval", ev.load_items("retrieval", "dev"), None, date(2026, 11, 17))
    assert Decimal(0) < p["estimate"] < p["worst_case"] < Decimal("0.05") and len(p["per_item_worst"]) == 5


# ------------------------------------------------------------------ on the test database
@pytest.mark.db
def test_the_seven_labels_are_core_doc_types():
    import psycopg
    from conftest import dsn
    with psycopg.connect(dsn("postgres")) as db:
        got = dict(db.execute("select key, label from core.doc_type where key = any(%s)", (list(d.DOC_TYPES),)).fetchall())
    assert got == d.DOC_LABEL


@pytest.mark.db
def test_a_published_embedded_transcript_chunk_is_never_retrieved_or_cited():
    """Spec 07 N3 and CLAUDE.md §1: the transcript is published, chunked and embedded with the question's own vector (so it would
    rank first), and holds the question's words; search_chunks without the filters returns it. The route's search never does,
    and an answer never cites it."""
    import uuid

    import psycopg
    from conftest import dsn
    tag = uuid.uuid4().hex[:10]                                               # slugs unique per run (event_type, slug)
    question = "Why did the Committee lower the target range for the federal funds rate?"
    vec = halfvec_literal(Embedder(client=FakeVoyage()).embed_query(question))
    transcript = "The Committee lower the target range question came up, and the CFO said the federal funds rate helps."
    statement = "The Committee decided to lower the target range for the federal funds rate. Inflation remains elevated."
    with psycopg.connect(dsn("postgres")) as db:
        ent = db.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                            values ('company', %s, 'K06 Test Ltd', 'IN', 'INR', true) returning id""",
                         (f"k06-co-{tag}",)).fetchone()[0]
        call = db.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id, is_published)
                             values ('earnings_call', %s, %s, 'K06 call', '2026-10-20', 'stored',
                                     (select id from core.source where key = 'nse'), true) returning id""",
                          (ent, f"k06-co-{tag}-call")).fetchone()[0]
        meet = db.execute("""insert into core.event (event_type, entity_id, slug, title, starts_on, ends_on, occurs_at, status,
                                                     source_id, source_url, is_published)
                             select 'policy_meeting', id, %s, 'FOMC, January 2031', '2031-01-28', '2031-01-29',
                                    '2031-01-29 14:00 America/New_York', 'held', (select id from core.source where key = 'fed'),
                                    'https://www.federalreserve.gov/x', true
                               from core.entity where slug = 'fed' returning id""", (f"fomc-k06-{tag}",)).fetchone()[0]
        docs = {}
        for key, (doc_type, event, entity_sql, source, text) in {
                "call": ("call_transcript", call, "%s", "company_web", transcript),
                "fed": ("fomc_statement", meet, "(select id from core.entity where slug = 'fed')", "fed", statement)}.items():
            docs[key] = db.execute(
                f"""insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                               published_at, char_count, text_sha256, chunked_at, is_published)
                    values (%s, {entity_sql}, %s, %s, (select id from core.source where key = %s), %s, 'https://example.com/k06',
                            now(), char_length(%s), sha256(convert_to(%s, 'UTF8')), now(), true) returning id""",
                ((doc_type, ent, event, f"K06 {key}", source, f"k06-{key}-{tag}", text, text) if key == "call"
                 else (doc_type, event, f"K06 {key}", source, f"k06-{key}-{tag}", text, text))).fetchone()[0]
            db.execute("""insert into core.chunk (document_id, chunk_no, char_start, char_end, section, body)
                          values (%s, 0, 0, char_length(%s), null, %s)""", (docs[key], text, text))
            db.execute("""insert into core.chunk_embedding (document_id, chunk_no, model, embedding)
                          values (%s, 0, 'voyage-4-lite@512', %s::extensions.halfvec(512))""", (docs[key], vec))
    try:
        reader = s.Reader.connect(dsn("sql_reader"))
        try:
            raw = reader.select("select document_id from semantic.search_chunks(%s::extensions.halfvec(512), %s)", (vec, question))
            assert docs["call"] in {x[0] for x in raw.rows}                     # the setup is real: unfiltered, it comes back
            got, foreign = d.search(reader, Embedder(client=FakeVoyage()).embed_query(question), question)
            assert docs["fed"] in {c.document_id for c in got} and foreign == 0
            assert docs["call"] not in {c.document_id for c in got} and {c.doc_type for c in got} <= set(d.DOC_TYPES)
            ans = d.answer_documents(claude(FakeClaude()), Embedder(client=FakeVoyage()), reader, question)
            assert ans.state == "answered" and ans.chunks and all(c.is_policy for c in ans.chunks)
            shown = json.dumps({"c": ans.citations, "d": ans.documents, "s": ans.summary}, ensure_ascii=False)
            assert "CFO" not in shown and "call_transcript" not in shown
            assert {x["doc_type"] for x in ans.documents} <= set(d.DOC_TYPES)
        finally:
            reader.close()
    finally:
        with psycopg.connect(dsn("postgres")) as db:
            ids = list(docs.values())
            db.execute("delete from core.chunk_embedding where document_id = any(%s)", (ids,))
            db.execute("delete from core.chunk where document_id = any(%s)", (ids,))
            db.execute("delete from core.document where id = any(%s)", (ids,))
            db.execute("delete from core.event where id = any(%s)", ([call, meet],))
            db.execute("delete from core.entity where id = %s", (ent,))
