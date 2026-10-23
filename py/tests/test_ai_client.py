"""The model wrapper (Prompt 01 B7; Architecture 03 §1.1): request settings, the two routes, the input hash, failures,
retries honouring Retry-After, the token limit, the dated price table, and the Voyage embedder. The Anthropic SDK runs
for real against a mock transport (tests/ai_fakes.py), so these check what would actually be sent."""
from datetime import date
from decimal import Decimal

import pytest
import voyageai
from ai_fakes import FakeClaude, FakeVoyage, claude
from voyageai import error as verr

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
    assert req().params(batch=True)["cache_control"]["ttl"] == "1h"     # a batch can outlast five minutes
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
    fake = FakeVoyage(fail=[verr.RateLimitError("slow down", headers={"retry-after": "2"}),
                            verr.ServiceUnavailableError("busy")])
    got = Embedder(client=fake, sleep=sleeps.append).embed_documents(["a", "b"])
    assert len(got.vectors) == 2 and sleeps[0] == 2.0 and len(sleeps) == 2
    with pytest.raises(CallError):
        Embedder(client=FakeVoyage(fail=[verr.AuthenticationError("bad key")])).embed_documents(["a"])
    with pytest.raises(CallError, match="BadEmbeddings"):
        Embedder(client=FakeVoyage(dimension=1024)).embed_documents(["a"])


def test_the_voyage_client_picks_its_endpoint_from_the_key():
    """voyageai 0.5.0: an Atlas model API key ('al-…') goes to ai.mongodb.com, a Voyage key to api.voyageai.com.
    The keys here are fake; nothing is sent."""
    assert Embedder(client=voyageai.Client(api_key="al-fake-test-key")).host == "ai.mongodb.com"
    assert Embedder(client=voyageai.Client(api_key="pa-fake-test-key")).host == "api.voyageai.com"


def test_halfvec_literal():
    assert halfvec_literal([0.5] * 512).startswith("[0.5,0.5,")
    with pytest.raises(ValueError):
        halfvec_literal([0.5] * 511)
