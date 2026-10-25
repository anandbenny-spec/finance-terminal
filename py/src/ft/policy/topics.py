"""Key changes' topic map (Spec 01 §2, "Topics (1b)"; Design 01 §1.16 layer 2): deterministic, no AI. Each diff pair gets
the topic of the sentence it sits at, read from the documents' own structure, with "Other" as the fallback. The rules
are in `config/topics.yaml`; its `version` is stored in the diff's params, so a change re-computes the stored diffs.

- FOMC: by anchor phrase, not position (the statement's paragraph order has varied).
- RBI: by heading (stand-alone short paragraphs in the stored text), both eras; text before the first heading is the
  decision and stance; under a heading mixing growth and inflation each sentence takes its own.
A pair takes the NEW side's topic, or the old side's when it was removed.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from ft.engines.document.text import sentence_spans

CONFIG = Path(__file__).resolve().parents[3] / "config" / "topics.yaml"
OTHER = "Other"


@lru_cache(maxsize=1)
def load(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or CONFIG).read_text())


def version(cfg: dict | None = None) -> int:
    return int((cfg or load())["version"])


def _has(low: str, phrases: list[str]) -> bool:
    return any(p in low for p in phrases)


def _blocks(text: str) -> list[tuple[int, int]]:
    """Spans of the blocks (separated by blank lines), as code-point offsets."""
    out, pos = [], 0
    for m in re.finditer(r"\n\s*\n", text):
        out.append((pos, m.start()))
        pos = m.end()
    out.append((pos, len(text)))
    return [(s, e) for s, e in out if e > s]


def _fomc_topics(text: str, cfg: dict) -> list[tuple[int, int, str]]:
    """(start, end, topic) for each sentence of a statement."""
    c = cfg["fomc"]
    out = []
    for bs, be in _blocks(text):
        para_topic = None                    # the topic of the paragraph's latest anchored sentence
        for s, e in sentence_spans(text, bs, be):
            low = " ".join(text[s:e].lower().split())
            topic = next((a["topic"] for a in c["anchored"] if _has(low, a["any"])), None)
            if topic:
                para_topic = topic
            elif para_topic:
                topic = para_topic
            elif _has(low, c["economy"]) and not ("inflation" in low and not _has(low, ["job", "labor", "employment"])):
                topic = "Economy and labour market"
            elif _has(low, c["inflation"]):
                topic = "Inflation"
            else:
                topic = OTHER
            out.append((s, e, topic))
    return out


def _rbi_topics(text: str, cfg: dict) -> list[tuple[int, int, str]]:
    c = cfg["rbi"]
    heads = {m: h for h in c["headings"] for m in h["match"]}
    out, current = [], {"topic": c["opening"], "split": False}
    for bs, be in _blocks(text):
        block = " ".join(text[bs:be].split())
        h = heads.get(block.lower())
        if h is not None:
            current = h
            continue                          # the heading itself is not a pair
        for s, e in sentence_spans(text, bs, be):
            topic = current["topic"]
            if current.get("split"):
                low = text[s:e].lower()
                if _has(low, c["inflation"]):
                    topic = "Inflation"
                elif _has(low, c["growth"]):
                    topic = "Growth"
            out.append((s, e, topic))
    return out


def sentence_topics(bank: str, text: str, cfg: dict | None = None) -> list[tuple[int, int, str]]:
    cfg = cfg or load()
    return _fomc_topics(text, cfg) if bank == "Fed" else _rbi_topics(text, cfg)


def _lookup(spans: list[tuple[int, int, str]], offset: int) -> str:
    for s, e, t in spans:
        if s <= offset < e:
            return t
    # a heading or a signature line between sentences: the nearest sentence before it
    before = [t for s, _, t in spans if s <= offset]
    return before[-1] if before else OTHER


def assign_topics(bank: str, segments: dict, old: str, new: str, cfg: dict | None = None) -> dict:
    """Fill `topic` on every pair of a version-2 diff (in place) and record the map's version in its params."""
    cfg = cfg or load()
    olds, news = sentence_topics(bank, old, cfg), sentence_topics(bank, new, cfg)
    for p in segments["pairs"]:
        if p["new"]:
            p["topic"] = _lookup(news, p["new"][0][0])
        elif p["old"]:
            p["topic"] = _lookup(olds, p["old"][0][0])
    segments["params"]["topics"] = version(cfg)
    return segments
