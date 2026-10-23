"""Test doubles for the AI wrapper (B7): a fake Messages API behind the real Anthropic SDK (httpx2's MockTransport, so
the SDK builds and parses real requests and replies) and a fake Voyage client. Nothing here reaches the network."""
from __future__ import annotations

import hashlib
import json
import random

import anthropic
import httpx2

from ft.ai.client import Client
from ft.engines.document.text import sentence_spans


def cite(text: str, s: int, e: int, index: int, title: str = "doc") -> dict:
    return {"type": "char_location", "cited_text": text[s:e], "document_index": index, "document_title": title,
            "start_char_index": s, "end_char_index": e}


def message(blocks: list[dict], *, stop: str = "end_turn", input_tokens: int = 1200, output_tokens: int = 300,
            model: str = "claude-haiku-5-5", cache_write: int = 0, cache_read: int = 0, mid: str = "msg_test") -> dict:
    return {"id": mid, "type": "message", "role": "assistant", "model": model, "content": blocks,
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "cache_creation_input_tokens": cache_write, "cache_read_input_tokens": cache_read,
                      "cache_creation": {"ephemeral_5m_input_tokens": cache_write, "ephemeral_1h_input_tokens": 0}}}


def bullets(items: list[tuple[str, list[dict]]], preamble: str = "") -> list[dict]:
    """Text blocks as the API returns them with citations on: markers and newlines uncited, each claim a block."""
    out = [{"type": "text", "text": preamble}] if preamble else []
    for i, (claim, cites) in enumerate(items):
        out.append({"type": "text", "text": ("\n" if i else "") + "- "})
        out.append({"type": "text", "text": claim, "citations": cites or None})
    return out


def documents_of(body: dict) -> tuple[list[tuple[str, str]], str]:
    """(the request's documents as (title, text), its instruction)."""
    content = body["messages"][0]["content"]
    docs = [(b.get("title", ""), b["source"]["data"]) for b in content if b["type"] == "document"]
    instruction = next(b["text"] for b in content if b["type"] == "text")
    return docs, instruction


def good_summary(docs: list[tuple[str, str]]) -> list[dict]:
    """A doc_summary reply: the first three sentences, each quoted as a bullet and cited at its offsets."""
    title, text = docs[0]
    spans = sentence_spans(text)[:3]
    return bullets([(text[s:e], [cite(text, s, e, 0, title)]) for s, e in spans])


def good_diff(docs: list[tuple[str, str]]) -> list[dict]:
    """A statement_diff_summary reply: up to three sentences that differ, added ones cited in the new document
    (index 1), removed ones in the old (index 0)."""
    (t0, old), (t1, new) = docs
    olds = {old[s:e] for s, e in sentence_spans(old)}
    news = {new[s:e] for s, e in sentence_spans(new)}
    added = [("Added: " + new[s:e], [cite(new, s, e, 1, t1)]) for s, e in sentence_spans(new) if new[s:e] not in olds]
    removed = [("Removed: " + old[s:e], [cite(old, s, e, 0, t0)]) for s, e in sentence_spans(old)
               if old[s:e] not in news]
    return bullets((added[:2] + removed[:1] + added[2:] + removed[1:])[:3])


class FakeClaude:
    """The Messages API. `script` is a list of behaviours, one per request (the last one repeats):
    'ok' · 'bad_number' (the first bullet writes '25 basis points', which its passage doesn't) · 'uncited' (the first
    bullet has no citation) · 'wrong_offsets' (offsets shifted, cited text right: found by search) · 'two_bullets' ·
    'max_tokens' · 'refusal' · '400' · '429' (with Retry-After: 3) · '529' (no Retry-After)."""

    def __init__(self, script=("ok",)):
        self.script, self.requests, self.n = list(script), [], 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        mode = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        if mode in ("400", "429", "529"):
            status = int(mode)
            headers = {"retry-after": "3"} if mode == "429" else {}
            kind = {"400": "invalid_request_error", "429": "rate_limit_error", "529": "overloaded_error"}[mode]
            return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": f"fake {mode}"}},
                                   headers=headers)
        docs, _ = documents_of(body)
        blocks = good_diff(docs) if len(docs) == 2 else good_summary(docs)
        if mode == "bad_number":
            blocks[1] = {**blocks[1], "text": "The Committee cut the rate by 25 basis points."}
        elif mode == "uncited":
            blocks[1] = {"type": "text", "text": blocks[1]["text"]}
        elif mode == "wrong_offsets":
            for b in blocks:
                for c in b.get("citations") or []:
                    c["start_char_index"] += 3
                    c["end_char_index"] += 3
        elif mode == "two_bullets":
            blocks = blocks[:4]
        stop = mode if mode in ("max_tokens", "refusal") else "end_turn"
        chars = sum(len(t) for _, t in docs)
        return httpx2.Response(200, json=message(blocks, stop=stop, input_tokens=600 + chars // 4,
                                                 mid=f"msg_{self.n}"))


def claude(fake: FakeClaude, *, sleeps: list | None = None) -> Client:
    api = anthropic.Anthropic(api_key="test-not-a-key", max_retries=0,
                              http_client=httpx2.Client(transport=httpx2.MockTransport(fake)))
    return Client(anthropic_client=api, sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


class _Result:
    def __init__(self, embeddings, total_tokens):
        self.embeddings, self.total_tokens = embeddings, total_tokens


class FakeVoyage:
    """voyageai.Client.embed: deterministic unit vectors from each text's hash; records every call. `fail` raises
    the given exceptions first, one per call."""

    def __init__(self, *, fail=(), dimension: int = 512):
        self.calls, self.fail, self.dimension = [], list(fail), dimension
        self._params = {"base_url": "https://ai.mongodb.com/v1"}

    def embed(self, texts, model=None, input_type=None, truncation=True, output_dtype=None, output_dimension=None):
        self.calls.append({"texts": list(texts), "model": model, "input_type": input_type, "truncation": truncation,
                           "output_dimension": output_dimension})
        if self.fail:
            raise self.fail.pop(0)
        out = []
        for t in texts:
            rng = random.Random(hashlib.sha256(t.encode()).digest())
            v = [rng.uniform(-1, 1) for _ in range(self.dimension)]
            norm = sum(x * x for x in v) ** 0.5
            out.append([x / norm for x in v])
        return _Result(out, sum(len(t) // 4 for t in texts))
