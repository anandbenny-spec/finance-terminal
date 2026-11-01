"""Classifying an exchange announcement as a transcript, an intimation, an audio/video notice or other (Spec 02B
§5.1; job 2B-06). Ported from the audit kit's `kit/concalls.py` (V1.2); the keywords are in
`config/equity/call_classifier.yaml`.

A transcript is an announcement whose subject or file name says "transcript" and which is not an audio/video notice
or an intimation. Future-tense mentions ("the transcript will be uploaded") are ignored for the notice tests only.
"Intimation" in a file name alone is not used. The first page of a file (the third source of the kit's rule) is read
by job 2B-07, only for company-seasons with no transcript found here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

CONFIG = Path(__file__).resolve().parents[4] / "config" / "equity" / "call_classifier.yaml"
KINDS = ("transcript", "intimation", "audio_video", "other")


@dataclass(frozen=True)
class Classification:
    kind: str          # one of KINDS
    why: str           # which text decided it


@dataclass(frozen=True)
class _Rules:
    transcript_terms: tuple[str, ...]
    future: tuple[re.Pattern, ...]
    audio_video: tuple[str, ...]
    intimation: tuple[re.Pattern, ...]
    file_intimation: re.Pattern


@lru_cache(maxsize=1)
def rules() -> _Rules:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return _Rules(tuple(cfg["transcript_terms"]), tuple(re.compile(p) for p in cfg["future_transcript_patterns"]),
                  tuple(cfg["audio_video_terms"]), tuple(re.compile(p) for p in cfg["intimation_patterns"]),
                  re.compile(cfg["file_intimation_pattern"]))


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'")).strip().lower()


def file_name(url: str | None) -> str:
    """The last path segment of an attachment URL, percent-decoded ('' for none)."""
    return unquote(urlparse(url).path.rsplit("/", 1)[-1]) if url else ""


def file_words(name: str | None) -> str:
    """'INDUSINDBK1_28042026203725_Transcript_of_Earning_Call.pdf' → 'indusindbk1 28042026203725 transcript of earning
    call' (camelCase split, separators to spaces, lower case)."""
    stem = re.sub(r"\.(pdf|zip|htm|html)$", "", name or "", flags=re.IGNORECASE)
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    return re.sub(r"[_\-.+%]+", " ", stem).lower()


def says_transcript(text: str | None, strip_future: bool = False) -> bool:
    t = _norm(text)
    r = rules()
    if not any(term in t for term in r.transcript_terms):
        return False
    if strip_future:
        for p in r.future:
            t = p.sub(" ", t)
    return any(term in t for term in r.transcript_terms)


def is_audio_video(text: str | None) -> bool:
    t = _norm(text)
    return any(re.search(rf"\b{re.escape(w)}", t) for w in rules().audio_video) and not says_transcript(t, strip_future=True)


def is_intimation(text: str | None) -> bool:
    t = _norm(text)
    return any(p.search(t) for p in rules().intimation) and not says_transcript(t, strip_future=True)


def classify(subject: str | None, filename: str | None = "") -> Classification:
    """The kit's V1.2 rule on the subject and the file name."""
    fw = file_words(filename)
    s_says, f_says = says_transcript(subject), says_transcript(fw)
    if is_audio_video(subject):
        return Classification("audio_video", "subject is an audio/video notice")
    if is_intimation(subject):
        return Classification("intimation", "subject is an intimation")
    if s_says or f_says:
        return Classification("transcript", "subject" if s_says else "file name")
    if any(re.search(rf"\b{re.escape(w)}", fw) for w in rules().audio_video):
        return Classification("audio_video", "file name is an audio/video notice")
    if rules().file_intimation.search(fw):
        return Classification("intimation", "file name is a call schedule or invitation")
    return Classification("other", "")
