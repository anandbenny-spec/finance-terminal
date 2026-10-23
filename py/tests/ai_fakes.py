"""Test doubles for the AI wrapper (B7): a fake Messages API behind the real Anthropic SDK (httpx2's MockTransport, so
the SDK builds and parses real requests and replies) and a fake Voyage client. Nothing here reaches the network."""
from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from datetime import UTC, datetime, timedelta

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


def schema_of(body: dict) -> dict | None:
    """A JSON-route request's schema (output_config.format), or None for the citations route."""
    fmt = (body.get("output_config") or {}).get("format")
    return fmt["schema"] if fmt else None


def good_stance(docs: list[tuple[str, str]]) -> dict:
    """A stance_extraction answer: the first sentence that says the MPC decided a stance, read as B6's parser reads
    it, quoted exactly."""
    from ft.policy import rbi_resolution
    _, text = docs[0]
    for s, e in sentence_spans(text):
        sent = text[s:e]
        if "decided" in sent and (label := rbi_resolution.stance_label(sent)):
            return {"stance": label, "quote": sent}
    return {"stance": "other", "quote": text[:80]}


def json_message(answer, *, stop: str = "end_turn", mid: str = "msg_json", chars: int = 0) -> dict:
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return message([{"type": "text", "text": text}], stop=stop, input_tokens=900 + chars // 4, output_tokens=120,
                   mid=mid)


def _error(mode: str) -> httpx2.Response:
    status = int(mode)
    headers = {"retry-after": "3"} if mode == "429" else {}
    kind = {"400": "invalid_request_error", "429": "rate_limit_error", "500": "api_error", "503": "api_error",
            "529": "overloaded_error"}[mode]
    return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": f"fake {mode}"}},
                           headers=headers)


class FakeClaude:
    """The Messages API and the Message Batches API. `script` is a list of behaviours, one per request answered (real
    time, or each request of a batch when its results are made; the last one repeats):
    'ok' · 'bad_number' (the first bullet writes '25 basis points', which its passage doesn't) · 'uncited' (the first
    bullet has no citation) · 'wrong_offsets' (offsets shifted, cited text right: found by search) · 'two_bullets' ·
    'max_tokens' · 'refusal' · 'nul' (the first bullet ends with a NUL character, which Postgres refuses) · '400' ·
    '429' (with Retry-After: 3) · '529' (no Retry-After). In a batch, a request's '400' is an `errored` result, and
    'expired' and 'canceled' are those results.
    Batches: `creates` is a script for each batch create ('ok', '400', '429', '500', '503', 'lost': the batch is created
    and then the connection times out, 'lost_before': it times out before anything is created); a batch ends on its
    `ends_after`-th status read (None: never); `truncate` cuts the first results download after that many lines, mid
    -line (`cut='mid'`) or cleanly (`cut='clean'`, a short read). `archive(id)` archives an ended batch. Results come
    back in reverse order (the API promises no order). The list endpoint returns the newest batches first.
    JSON-route requests (structured outputs) are answered by `answer(body)` (default: `good_stance` for a stance
    schema, no projections otherwise), or, in 'not_json', with text that isn't JSON; the error and stop modes apply to
    them too, and `answered` lists (schema's first property, answer) for each."""

    def __init__(self, script=("ok",), *, creates=("ok",), ends_after: int | None = 1, truncate: int | None = None,
                 cut: str = "mid", answer=None):
        self.script, self.requests, self.n = list(script), [], 0
        self.creates, self.ends_after, self.truncate, self.cut = list(creates), ends_after, truncate, cut
        self.answer, self.answered = answer, []
        self.batches: dict[str, dict] = {}          # id -> {"requests", "reads", "results", "created_at", ...}
        self.create_calls = 0                       # batch creates attempted, refused ones included

    def archive(self, bid: str) -> None:
        self.batches[bid]["archived_at"] = datetime.now(UTC)

    def _mode(self) -> str:
        mode = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        return mode

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/v1/messages/batches" and request.method == "POST":
            return self._create(json.loads(request.content), request)
        if path == "/v1/messages/batches":
            newest = sorted(self.batches, key=lambda k: self.batches[k]["created_at"], reverse=True)[:20]
            data = [self._batch(k) for k in newest]
            return httpx2.Response(200, json={"data": data, "has_more": False,
                                              "first_id": data[0]["id"] if data else None,
                                              "last_id": data[-1]["id"] if data else None})
        if m := re.fullmatch(r"/v1/messages/batches/([^/]+)/results", path):
            return self._results(m.group(1))
        if m := re.fullmatch(r"/v1/messages/batches/([^/]+)", path):
            return self._retrieve(m.group(1))
        body = json.loads(request.content)
        self.requests.append(body)
        mode = self._mode()
        if mode in ("400", "429", "529"):
            return _error(mode)
        return httpx2.Response(200, json=self._message(body, mode))

    def _message(self, body: dict, mode: str) -> dict:
        docs, _ = documents_of(body)
        if (schema := schema_of(body)) is not None:
            kind = next(iter(schema["properties"]))
            if self.answer is not None:
                got = self.answer(body)
            else:
                got = good_stance(docs) if kind == "stance" else {"projections": []}
            got = "Here is the stance: neutral" if mode == "not_json" else got
            self.answered.append((kind, got))
            stop = mode if mode in ("max_tokens", "refusal") else "end_turn"
            return json_message(got, stop=stop, mid=f"msg_{self.n}", chars=sum(len(t) for _, t in docs))
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
        elif mode == "nul":
            blocks[1] = {**blocks[1], "text": blocks[1]["text"] + "\x00"}
        stop = mode if mode in ("max_tokens", "refusal") else "end_turn"
        chars = sum(len(t) for _, t in docs)
        return message(blocks, stop=stop, input_tokens=600 + chars // 4, mid=f"msg_{self.n}")

    # ---------------------------------------------------------------------------------------- batches
    def _create(self, body: dict, request: httpx2.Request) -> httpx2.Response:
        mode = self.creates[min(self.create_calls, len(self.creates) - 1)]
        self.create_calls += 1
        if mode == "lost_before":
            raise httpx2.ReadTimeout("fake timeout before the batch was created", request=request)
        if mode not in ("ok", "lost"):
            return _error(mode)
        bid = f"msgbatch_{uuid.uuid4().hex[:24]}"           # unique: ops.ai_batch.provider_batch_id is
        self.batches[bid] = {"requests": body["requests"], "reads": 0, "results": None,
                             "created_at": datetime.now(UTC), "ended_at": None, "downloads": 0, "archived_at": None}
        if mode == "lost":
            raise httpx2.ReadTimeout("fake timeout after the batch was created", request=request)
        return httpx2.Response(200, json=self._batch(bid))

    def _retrieve(self, bid: str) -> httpx2.Response:
        if bid not in self.batches:
            return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error",
                                                                         "message": "fake 404"}})
        b = self.batches[bid]
        b["reads"] += 1
        if b["results"] is None and self.ends_after is not None and b["reads"] >= self.ends_after:
            b["results"] = [self._result(r) for r in b["requests"]]
            b["ended_at"] = datetime.now(UTC)
        return httpx2.Response(200, json=self._batch(bid))

    def _result(self, r: dict) -> dict:
        self.requests.append(r["params"])
        mode = self._mode()
        if mode in ("400", "429", "529"):
            res = {"type": "errored", "error": {"type": "error", "error": {"type": "invalid_request_error",
                                                                        "message": "fake"}, "request_id": None}}
        elif mode in ("expired", "canceled"):
            res = {"type": mode}
        else:
            res = {"type": "succeeded", "message": self._message(r["params"], mode)}
        return {"custom_id": r["custom_id"], "result": res}

    def _batch(self, bid: str) -> dict:
        b = self.batches[bid]
        n, ended = len(b["requests"]), b["results"] is not None
        counts = {"processing": 0 if ended else n, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0}
        for r in b["results"] or []:
            counts[r["result"]["type"]] += 1
        created = b["created_at"]
        return {"id": bid, "type": "message_batch", "processing_status": "ended" if ended else "in_progress",
                "request_counts": counts, "created_at": created.isoformat(),
                "expires_at": (created + timedelta(hours=24)).isoformat(),
                "ended_at": b["ended_at"].isoformat() if ended else None,
                "archived_at": b["archived_at"].isoformat() if b.get("archived_at") else None,
                "cancel_initiated_at": None,
                "results_url": f"https://api.anthropic.com/v1/messages/batches/{bid}/results" if ended else None}

    def _results(self, bid: str) -> httpx2.Response:
        b = self.batches[bid]
        b["downloads"] += 1
        lines = [json.dumps(r) for r in reversed(b["results"])]
        body = "\n".join(lines) + "\n"
        if self.truncate is not None and b["downloads"] == 1:
            body = "\n".join(lines[: self.truncate]) + "\n"
            if self.cut == "mid":
                body += lines[self.truncate][:40]                                       # cut mid-line
        return httpx2.Response(200, content=body.encode(), headers={"content-type": "application/binary"})


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
