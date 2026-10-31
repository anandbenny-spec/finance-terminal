"""The banned-words scan (Spec 02A §5.9; Brief §11), defined once for the Python tests, as `web/tests/banned-words.ts`
is for the pages: whole words and phrases, case-insensitive, over the site's own text (labels, reason sentences,
notes), never over text quoted from a source (a corporate action's "Buy Back" subject, a filing's line names)."""
import re

BANNED_WORDS = re.compile(r"\b(overvalued|undervalued|cheap|expensive|upside|fair value|target|buy|sell)\b", re.IGNORECASE)


def banned_words_in(text: str) -> list[str]:
    """The banned words found in `text`, lower-cased, each once, in order of first appearance."""
    out: list[str] = []
    for m in BANNED_WORDS.finditer(text):
        w = m.group(1).lower()
        if w not in out:
            out.append(w)
    return out
