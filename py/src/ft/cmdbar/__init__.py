"""The command bar's AI routes (Spec 07; Architecture 03 §1.7, §1.8): the pieces the live route in `/api` (K-05) and the gate
runner (`ft/eval/cmdbar.py`) share, so the gates measure the route's own code.

- `router`: Haiku 5.5 sends a question to a page, the data, the Fed and RBI documents, or "unsupported" (§4 step 4).
- `sql`: the data route (§4 step 5a): the text-to-SQL prompt with the views' descriptions read from their COMMENTs, the guard,
  the read-only run as `sql_reader`, and one retry on Sonnet 5.5 told the error's class only.
- `summary`: the one-line summary of the rows, kept only if every number in it is in the rows (`numbers_grounded`).

Every model call goes through `ft/ai/client.py`. The question is data in every prompt, never instructions. Nothing here shows a
visitor a raw error: failures carry an error class (a short, fixed word), and the caller decides what the visitor sees. Nothing
here reads the kill switch or the limits: that is the route's job (K-05), and the route is paused until the judge passes (§3).
"""
from __future__ import annotations

import json
import re

MAX_QUESTION_CHARS = 500        # a search-box question; longer input is refused before any model call


class BadQuestion(ValueError):
    """The question is empty or too long: nothing was sent."""


def clean_question(question: str) -> str:
    """The question with its whitespace collapsed (control characters too). BadQuestion if empty or over the limit."""
    q = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", question or "").split())
    if not q:
        raise BadQuestion("empty question")
    if len(q) > MAX_QUESTION_CHARS:
        raise BadQuestion(f"question over {MAX_QUESTION_CHARS} characters")
    return q


def as_data(question: str) -> str:
    """The question as a JSON string: quoted and escaped, so nothing in it can close the frame it is sent in."""
    return json.dumps(question, ensure_ascii=False)
