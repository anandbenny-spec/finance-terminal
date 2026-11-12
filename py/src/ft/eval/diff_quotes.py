"""#445 (R05-2): the measure for the What changed summaries' judge, never a publish check (the card: "a measure only").

Prompt v2 of `statement_diff_summary` asks the model to put words taken from a statement in double quotation marks. This
counts, for each bullet, the stretches of `MIN_WORDS` or more words copied from its cited passages that sit outside
quotation marks: v1's bullets have them ("changed from Economic activity is expanding at a solid pace. to …"), and v2's
should have none or fewer. The judge reads it beside v1's and v2's bullets (part 2); nothing here can drop or reject a
bullet.

Words are compared as written (case aside), after `summaries.match_form` (curly quotes straight, dashes one hyphen). A
word is inside quotation marks when an odd number of double quotation marks comes before it in the bullet. A stretch is
the longest run of consecutive words outside the marks that appears, word for word, in one of the bullet's passages.

`python -m ft.eval.diff_quotes <file.json>` reads outputs exported through the read-only dev connector, as
`[{"output_id": 650, "bullets": [{"text": "…", "passages": ["…", …]}, …]}, …]`, and prints each output's count as JSON
(ids and counts only, so a run log never holds a statement's text).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ft.ai.summaries import match_form

MIN_WORDS = 5
_WORD = re.compile(r"\w+(?:[-'/.,%]\w+)*")


def _words(text: str) -> list[tuple[str, bool]]:
    """Each word of `text` (in `match_form`), lower-cased, with whether it sits inside double quotation marks."""
    t = match_form(text)
    marks = [m.start() for m in re.finditer('"', t)]
    out = []
    for m in _WORD.finditer(t):
        inside = sum(1 for at in marks if at < m.start()) % 2 == 1
        out.append((m.group(0).lower(), inside))
    return out


def unquoted_copies(bullet: str, passages: list[str], min_words: int = MIN_WORDS) -> list[str]:
    """The stretches of `min_words` or more words of `bullet` copied from one of `passages` and outside quotation marks,
    longest first from the left, never overlapping."""
    words = _words(bullet)
    hay = [" " + " ".join(w for w, _ in _words(p)) + " " for p in passages]

    def copied(i: int, j: int) -> bool:
        run = " " + " ".join(w for w, _ in words[i:j]) + " "
        return any(run in h for h in hay)

    found, i = [], 0
    while i < len(words):
        if words[i][1]:
            i += 1
            continue
        j = i
        while j < len(words) and not words[j][1] and copied(i, j + 1):
            j += 1
        if j - i >= min_words:
            found.append(" ".join(w for w, _ in words[i:j]))
            i = j
        else:
            i += 1
    return found


def count(output: dict, min_words: int = MIN_WORDS) -> int:
    """An exported output's stretches, over its bullets."""
    return sum(len(unquoted_copies(b["text"], b.get("passages") or [], min_words)) for b in output.get("bullets") or [])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ft.eval.diff_quotes", description=__doc__.split("\n\n")[0])
    p.add_argument("file", type=Path, help="outputs exported as JSON (see the module docstring)")
    p.add_argument("--min-words", type=int, default=MIN_WORDS)
    args = p.parse_args(argv)
    outputs = json.loads(args.file.read_text(encoding="utf-8"))
    json.dump({str(o["output_id"]): count(o, args.min_words) for o in outputs}, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
