"""The model wrapper (Prompt 01 B7; Architecture 03 §1.1): request settings, the two routes, the input hash, failures,
retries honouring Retry-After, the token limit, the dated price table, and the Voyage embedder. The Anthropic SDK runs
for real against a mock transport (tests/ai_fakes.py), so these check what would actually be sent."""
import base64
import json
import struct
from datetime import date
from decimal import Decimal

import httpx
import pytest
from ai_fakes import FakeClaude, FakeVoyage, claude

from ft.ai import client as ai
from ft.ai.client import (
    CallError,
    Embedder,
    Failed,
    NoPrice,
    Prompt,
    Request,
    Source,
    TooLong,
    Usage,
    VoyageAPIError,
    VoyageHTTP,
    cost_usd,
    halfvec_literal,
    load_prompt,
)

TEXT = ("The Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to "
        "4 to 4-1/4 percent. Inflation has moved up and remains somewhat elevated. The Committee is attentive to the "
        "risks to both sides of its dual mandate.")


def req(text: str = TEXT, *, task: str = "doc_summary", prompt_text: str = "Summarise.", instruction: str = "Go.",
        model: str | None = None, schema=None) -> Request:
    return Request(task, Prompt(task, "v1", prompt_text), (Source(7, text, "FOMC statement, 28 Oct 2026"),),
                   instruction, model=model, schema=schema)


def keys(obj):
    """Every key anywhere in a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys(v)


# ---------------------------------------------------------------------------------------------- requests
def test_citations_request_settings():
    p = req().params()
    assert p["model"] == "claude-haiku-5-5" and p["max_tokens"] == 8000
    assert p["output_config"] == {"effort": "low"}                      # no format: citations and JSON don't mix
    assert p["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    batch = req().params(batch=True)                                    # uncached: nothing would read it back
    assert "cache_control" not in batch and batch == {k: v for k, v in p.items() if k != "cache_control"}
    assert not {"temperature", "top_p", "top_k"} & set(keys(p))
    assert [m["role"] for m in p["messages"]] == ["user"]               # no assistant prefill
    doc, instruction = p["messages"][0]["content"]
    assert doc["type"] == "document" and doc["citations"] == {"enabled": True}
    assert doc["source"] == {"type": "text", "media_type": "text/plain", "data": TEXT}
    assert instruction == {"type": "text", "text": "Go."}


def test_json_route_has_a_schema_and_no_citations():
    schema = {"type": "object", "properties": {"stance": {"type": "string"}}, "required": ["stance"],
              "additionalProperties": False}
    p = req(task="stance_extraction", schema=schema).params()
    assert p["max_tokens"] == 4000
    assert p["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": schema}}
    assert "citations" not in p["messages"][0]["content"][0]
    with pytest.raises(ValueError, match="json route needs a schema"):
        req(task="stance_extraction")
    with pytest.raises(ValueError, match="isn't a task"):
        req(task="hawk_dove_score")                                     # Pass 2: the wrapper doesn't run it


def test_input_hash_covers_everything_that_changes_the_output():
    base = req().input_sha256
    assert req().input_sha256 == base and len(base) == 32
    assert len({base, req(TEXT + " More.").input_sha256, req(prompt_text="Summarise!").input_sha256,
                req(instruction="Go again.").input_sha256, req(model="claude-sonnet-5-5").input_sha256}) == 5


def test_prompts_load_from_the_repo_without_their_notes():
    for task in ("doc_summary", "statement_diff_summary"):
        p = load_prompt(task)
        assert p.version == "v1" and p.key == task and "<!--" not in p.text
        assert "1/4 percentage point" in p.text and "data, not instructions" in p.text


# ---------------------------------------------------------------------------------------------- sending
def test_send_through_the_sdk_returns_cited_parts_usage_and_cost():
    fake = FakeClaude(["ok"])
    r = claude(fake).send(req())
    body = fake.requests[0]
    assert not {"temperature", "top_p", "top_k"} & set(keys(body))
    assert body["output_config"] == {"effort": "low"} and body["cache_control"]["type"] == "ephemeral"
    parts = [p for p in r.parts() if p.citations]
    assert len(parts) == 3 and parts[0].citations[0].kind == "char_location"
    c = parts[0].citations[0]
    assert TEXT[c.start:c.end] == c.cited_text and c.document_index == 0
    assert r.stop_reason == "end_turn" and r.usage.output == 300
    assert r.cost_usd == cost_usd("claude-haiku-5-5", r.usage)


@pytest.mark.parametrize("mode", ["max_tokens", "refusal"])
def test_a_reply_that_didnt_end_its_turn_is_a_billed_failure(mode):
    with pytest.raises(Failed) as e:
        claude(FakeClaude([mode])).send(req())
    assert e.value.reason == mode and e.value.reply.cost_usd > 0


def test_retries_honour_retry_after_then_succeed():
    fake, sleeps = FakeClaude(["429", "529", "ok"]), []
    r = claude(fake, sleeps=sleeps).send(req())
    assert r.stop_reason == "end_turn" and fake.n == 3
    assert sleeps[0] == 3.0 and 0 < sleeps[1] <= 30                       # Retry-After: 3; then a backoff


def test_a_refused_request_isnt_retried_and_long_waits_give_up():
    fake = FakeClaude(["400", "ok"])
    with pytest.raises(CallError) as e:
        claude(fake).send(req())
    assert e.value.status == 400 and fake.n == 1
    c = claude(FakeClaude(["429"]))
    c.max_wait = 2.0                                                    # Retry-After 3 > 2
    with pytest.raises(CallError, match="asked to wait"):
        c.send(req())
    c = claude(FakeClaude(["529"]))
    with pytest.raises(CallError) as e:
        c.send(req())
    assert e.value.status == 529 and c.calls == 4                       # 1 + 3 retries


def test_over_the_token_limit_or_without_a_price_nothing_is_sent():
    fake = FakeClaude(["ok"])
    with pytest.raises(TooLong, match="100,000"):
        claude(fake).send(req("x" * 400_000))
    with pytest.raises(NoPrice):
        claude(fake).send(req(model="claude-opus-5-5"))
    assert fake.requests == []


# ---------------------------------------------------------------------------------------------- the Batch API (B7.2)
def test_batch_custom_id_keeps_only_what_the_api_allows():
    cid = "doc_summary:doc-123:0123456789abcdef"
    assert ai.batch_custom_id(cid) == "doc_summary_doc-123_0123456789abcdef"
    assert ai.BATCH_ID.fullmatch(ai.batch_custom_id(cid))
    assert ai.batch_custom_id("statement_diff_summary:doc-123456789:" + "f" * 16) == (
        "statement_diff_summary_doc-123456789_" + "f" * 16)
    assert len(ai.batch_custom_id("x:" * 50)) == 64


def test_create_batch_sends_the_batch_params_under_valid_unique_ids():
    fake = FakeClaude()
    c = claude(fake)
    info = c.create_batch([("doc_summary_doc-1_a", req()), ("doc_summary_doc-2_b", req(TEXT + " More."))])
    assert info.status == "in_progress" and info.counts["processing"] == 2 and not info.ended
    sent = fake.batches[info.id]["requests"]
    assert [r["custom_id"] for r in sent] == ["doc_summary_doc-1_a", "doc_summary_doc-2_b"]
    assert sent[0]["params"] == req().params(batch=True)                # the real-time shape, uncached
    assert "cache_control" not in sent[0]["params"]
    for bad in ([("doc:1", req())], [("a", req()), ("a", req())], []):
        with pytest.raises(ValueError):
            c.create_batch(bad)
    with pytest.raises(TooLong):                                         # one request too long: nothing is sent
        c.create_batch([("ok_1", req()), ("long_2", req("x" * 400_000))])
    assert fake.create_calls == 1


def test_a_batch_create_is_retried_only_where_the_api_refused_it():
    fake, sleeps = FakeClaude(creates=["429", "529", "ok"]), []
    info = claude(fake, sleeps=sleeps).create_batch([("a", req())])
    assert info.id in fake.batches and fake.create_calls == 3 and sleeps[0] == 3.0
    for mode in ("500", "400"):                                          # a 500 might have created it: never resent
        fake = FakeClaude(creates=[mode, "ok"])
        with pytest.raises(CallError) as e:
            claude(fake).create_batch([("a", req())])
        assert e.value.status == int(mode) and fake.create_calls == 1 and fake.batches == {}


def test_a_create_that_might_have_happened_is_told_apart_from_a_refusal():
    def err(kind, status=None):
        return CallError(kind, status, "x")
    for maybe in (err("APITimeoutError"), err("APIConnectionError"), err("InternalServerError", 500),
                  err("APIStatusError", 502), err("APIStatusError", 504), err("APIStatusError", 408),
                  err("ConflictError", 409)):
        assert ai.maybe_created(maybe), maybe
    for refused in (err("BadRequestError", 400), err("AuthenticationError", 401), err("RequestTooLarge", 413),
                    err("RateLimitError", 429), err("APIStatusError", 503), err("OverloadedError", 529),
                    err("RetryAfterTooLong"), err("NoKey")):
        assert not ai.maybe_created(refused), refused


def test_a_lost_create_raises_and_the_batch_it_made_is_in_the_list():
    fake = FakeClaude(creates=["lost"])
    c = claude(fake)
    with pytest.raises(CallError) as e:
        c.create_batch([("a", req()), ("b", req(TEXT + " More."))])
    assert ai.maybe_created(e.value) and fake.create_calls == 1               # a lost connection isn't retried
    (made,) = c.recent_batches()
    assert made.id in fake.batches and sum(made.counts.values()) == 2 and made.archived_at is None
    fake.archive(made.id)
    assert c.batch(made.id).archived_at is not None


def test_batch_results_come_priced_at_the_batch_rate_with_each_kind():
    fake = FakeClaude(["ok", "max_tokens", "400", "expired", "canceled"], ends_after=2)
    c = claude(fake)
    info = c.create_batch([(f"r{i}", req(TEXT + " " * i)) for i in range(5)])
    assert not c.batch(info.id).ended                                     # the first read: still processing
    info = c.batch(info.id)
    assert info.ended and info.counts == {"processing": 0, "succeeded": 2, "errored": 1, "canceled": 1, "expired": 1}
    got = {r.custom_id: r for r in c.batch_results(info, model_of=lambda cid: "claude-haiku-5-5")}
    assert {k: (r.kind, r.error) for k, r in got.items()} == {
        "r0": ("succeeded", None), "r1": ("succeeded", None), "r2": ("errored", "invalid_request_error"),
        "r3": ("expired", None), "r4": ("canceled", None)}
    ok, cut = got["r0"].reply, got["r1"].reply
    assert ok.stop_reason == "end_turn" and cut.stop_reason == "max_tokens" and cut.cost_usd > 0   # billed anyway
    assert ok.cost_usd == cost_usd("claude-haiku-5-5", ok.usage, batch=True)
    assert ok.cost_usd * 2 == cost_usd("claude-haiku-5-5", ok.usage)       # half the real-time price
    assert len([p for p in ok.parts() if p.citations]) == 3


def test_a_broken_results_stream_raises_after_the_results_before_it():
    fake = FakeClaude(["ok"], truncate=1)
    c = claude(fake)
    info = c.batch(c.create_batch([("a", req()), ("b", req(TEXT + " More."))]).id)
    seen = []
    with pytest.raises(CallError, match="ResultsStream"):
        for r in c.batch_results(info):
            seen.append(r.custom_id)
    assert seen == ["b"]                                                   # reverse order; the cut line raised
    assert sorted(r.custom_id for r in c.batch_results(info)) == ["a", "b"]   # a second read is whole


def test_results_of_a_batch_without_them_raise_call_errors():
    """The SDK raises its base AnthropicError when a batch has no results_url; a batch the API no longer has is a
    404. Both come out as CallError (the 404 with its status), never as an SDK exception."""
    fake = FakeClaude(ends_after=None)
    c = claude(fake)
    info = c.create_batch([("a", req())])
    with pytest.raises(CallError, match="ResultsStream"):
        list(c.batch_results(info))
    del fake.batches[info.id]
    with pytest.raises(CallError) as e:
        list(c.batch_results(info))
    assert e.value.status == 404


# ---------------------------------------------------------------------------------------------- prices
def test_cost_from_the_dated_price_table():
    u = Usage(input=1_000_000, output=1_000_000, cache_write_5m=1_000_000, cache_write_1h=1_000_000,
              cache_read=1_000_000)
    on = date(2026, 10, 28)
    # Above 100,000 prompt tokens Haiku 5.5 is on its long-context prices (5x).
    assert cost_usd("claude-haiku-5-5", u, on=on) == Decimal("4.6750")
    small = Usage(input=2_000, output=600, cache_write_5m=1_000, cache_read=500)
    assert cost_usd("claude-haiku-5-5", small, on=on) == Decimal("0.000630")      # 0.0002 + 0.0003 + 0.000125 + 0.000005
    assert cost_usd("claude-haiku-5-5", small, on=on, batch=True) == Decimal("0.000315")
    assert cost_usd("claude-sonnet-5-5", Usage(input=1_000, output=1_000), on=on) == Decimal("0.012000")
    with pytest.raises(NoPrice):
        cost_usd("claude-haiku-5-5", small, on=date(2026, 10, 1))                # before the first entry


def test_a_later_entry_takes_over_from_its_date(tmp_path):
    p = tmp_path / "prices.yaml"
    p.write_text("""anthropic:
  - from: 2026-10-23
    batch: 0.5
    models: {claude-haiku-5-5: {standard: {input: 0.10, cache_write_5m: 0.125, cache_write_1h: 0.20, cache_read: 0.01, output: 0.50}}}
  - from: 2026-12-01
    batch: 0.5
    models: {claude-haiku-5-5: {standard: {input: 1.00, cache_write_5m: 1.25, cache_write_1h: 2.00, cache_read: 0.10, output: 5.00}}}
""")
    u = Usage(input=1_000_000)
    assert cost_usd("claude-haiku-5-5", u, on=date(2026, 11, 30), path=p) == Decimal("0.100000")
    assert cost_usd("claude-haiku-5-5", u, on=date(2026, 12, 1), path=p) == Decimal("1.000000")


def test_usage_without_a_cache_breakdown_counts_writes_at_the_5_minute_rate():
    class U:
        input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, cache_creation = 10, 5, 7, 3, None
    assert Usage.of(U()) == Usage(10, 5, 7, 0, 3) and Usage.of(U()).prompt_tokens == 20


# ---------------------------------------------------------------------------------------------- embeddings
def test_embedder_sends_voyage_4_at_512_in_small_batches(monkeypatch):
    monkeypatch.setattr(ai, "EMBED_MAX_TEXTS", 3)
    fake = FakeVoyage()
    got = Embedder(client=fake).embed_documents([f"text {i}" for i in range(7)])
    assert [len(c["texts"]) for c in fake.calls] == [3, 3, 1] and got.requests == 3
    assert {(c["model"], c["input_type"], c["output_dimension"], c["truncation"]) for c in fake.calls} == {
        ("voyage-4", "document", 512, False)}
    assert len(got.vectors) == 7 and all(len(v) == 512 for v in got.vectors) and got.model == "voyage-4"
    Embedder(client=fake).embed_query("What did the RBI decide?")
    assert fake.calls[-1]["model"] == "voyage-4-lite" and fake.calls[-1]["input_type"] == "query"


def test_embedder_retries_honour_retry_after_and_check_dimensions():
    sleeps = []
    fake = FakeVoyage(fail=[VoyageAPIError(429, "slow down", {"retry-after": "2"}), VoyageAPIError(503, "busy"),
                            httpx.ConnectError("lost")])
    got = Embedder(client=fake, sleep=sleeps.append).embed_documents(["a", "b"])
    assert len(got.vectors) == 2 and sleeps[0] == 2.0 and len(sleeps) == 3
    with pytest.raises(CallError, match=r"AuthenticationError \(401\)"):
        Embedder(client=FakeVoyage(fail=[VoyageAPIError(401, "bad key")])).embed_documents(["a"])
    with pytest.raises(CallError, match="ReadTimeout"):
        Embedder(client=FakeVoyage(fail=[httpx.ReadTimeout("slow")] * 4), sleep=lambda s: None,
                 retries=3).embed_documents(["a"])
    with pytest.raises(CallError, match="RetryAfterTooLong"):
        Embedder(client=FakeVoyage(fail=[VoyageAPIError(429, "later", {"retry-after": "600"})]),
                 sleep=lambda s: None).embed_documents(["a"])
    with pytest.raises(CallError, match="BadEmbeddings"):
        Embedder(client=FakeVoyage(dimension=1024)).embed_documents(["a"])


def _b64(v):
    return base64.b64encode(struct.pack(f"<{len(v)}f", *v)).decode()


def _voyage(handler, key="al-fake-test-key"):
    return VoyageHTTP(key, transport=httpx.MockTransport(handler))


def test_voyage_http_sends_what_the_sdk_sent_and_decodes_base64_float32():
    """The request voyageai 0.5.0 built (the module docstring): the same path, headers, body and base64 encoding."""
    seen = []

    def handler(req):
        seen.append(req)
        body = json.loads(req.content)
        data = [{"object": "embedding", "embedding": _b64([i + 0.5, -0.25] + [0.0] * 510), "index": i}
                for i in range(len(body["input"]))]
        return httpx.Response(200, json={"object": "list", "data": data[::-1], "model": body["model"],
                                         "usage": {"total_tokens": 7}})

    got = Embedder(client=_voyage(handler)).embed_documents(["first", "second"])
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://ai.mongodb.com/v1/embeddings"
    assert req.headers["authorization"] == "Bearer al-fake-test-key"
    assert req.headers["content-type"] == "application/json"
    assert json.loads(req.content) == {"input": ["first", "second"], "model": "voyage-4", "input_type": "document",
                                       "truncation": False, "output_dtype": "float", "output_dimension": 512,
                                       "encoding_format": "base64"}
    # Put back in input order by `index`; the float32 values decode exactly.
    assert [v[:2] for v in got.vectors] == [[0.5, -0.25], [1.5, -0.25]] and got.tokens == 7 and got.requests == 1


def test_voyage_http_errors_keep_the_sdks_names_and_retry_after():
    answers = iter([httpx.Response(429, json={"detail": "slow down"}, headers={"retry-after": "3"}),
                    httpx.Response(200, json={"data": [{"embedding": [0.1] * 512, "index": 0}],
                                              "usage": {"total_tokens": 1}})])
    sleeps = []
    got = Embedder(client=_voyage(lambda req: next(answers)), sleep=sleeps.append).embed_query("q")
    assert sleeps == [3.0] and len(got) == 512
    with pytest.raises(CallError) as e:
        Embedder(client=_voyage(lambda req: httpx.Response(401, json={"detail": "Provided API key is invalid."})),
                 sleep=lambda s: None).embed_query("q")
    assert e.value.kind == "AuthenticationError" and e.value.status == 401 and "invalid" in str(e.value)
    with pytest.raises(CallError, match="BadResponse"):
        Embedder(client=_voyage(lambda req: httpx.Response(200, json={"data": [{"embedding": "AAA", "index": 0}],
                                                                     "usage": {"total_tokens": 1}}))).embed_query("q")


def test_the_voyage_endpoint_comes_from_the_key(monkeypatch):
    """voyageai 0.5.0's rule: an Atlas model API key ('al-…') goes to ai.mongodb.com, a Voyage key to
    api.voyageai.com. The keys here are fake; nothing is sent."""
    assert Embedder("al-fake-test-key").host == "ai.mongodb.com"
    assert Embedder("pa-fake-test-key").host == "api.voyageai.com"
    monkeypatch.setenv("VOYAGE_API_KEY", "al-from-the-environment")
    assert Embedder().host == "ai.mongodb.com"
    monkeypatch.delenv("VOYAGE_API_KEY")
    with pytest.raises(CallError, match="NoKey"):
        Embedder()


def test_halfvec_literal():
    assert halfvec_literal([0.5] * 512).startswith("[0.5,0.5,")
    with pytest.raises(ValueError):
        halfvec_literal([0.5] * 511)
