"""The versioned text extractor and the sentence splitter (Prompt 01 B2; Spec 01 §1.1, "Canonical text")."""
import pytest
from policy_docs import rbi_minutes_body, rbi_page, rbi_resolution_body, transcript_pages

from ft.engines.document.text import (
    Extracted,
    ExtractError,
    HtmlProfile,
    decode_html,
    extract_html,
    extract_pdf,
    normalize,
    paragraph_of,
    paragraph_starts,
    pdf_text,
    sentence_spans,
    transcript_speaker,
    words,
)

NBSP, THIN, ZWSP, SHY, BOM = chr(0xA0), chr(0x2009), chr(0x200B), chr(0xAD), chr(0xFEFF)
LSQUO, RSQUO, LDQUO, RDQUO, FI = chr(0x2018), chr(0x2019), chr(0x201C), chr(0x201D), chr(0xFB01)


def sentences(text):
    return [text[s:e] for s, e in sentence_spans(text)]


def check_shape(ex: Extracted):
    """The canonical text's invariants: blocks in order, separated by exactly one blank line, nothing outside them."""
    assert ex.text == ex.text.strip() and "\n\n\n" not in ex.text
    assert ex.blocks[0].start == 0 and ex.blocks[-1].end == len(ex.text)
    for a, b in zip(ex.blocks, ex.blocks[1:], strict=False):
        assert ex.text[a.end:b.start] == "\n\n"
    for b in ex.blocks:
        body = ex.block_text(b)
        assert body and body == body.strip() and "\n\n" not in body
        assert all(line == line.strip() and "  " not in line for line in body.split("\n"))


# ------------------------------------------------------------------ normalisation
def test_normalize_spaces_quotes_ligatures_and_controls():
    raw = (f"The{NBSP}Committee{THIN}decided{ZWSP} to{SHY} {LSQUO}hold{RSQUO}  at {LDQUO}5.50{RDQUO}\r\n"
           f"\x00of{FI}cial\x07 ₹1,23,456 − 25 bps – 2026–27 — end  ")
    assert normalize(raw) == "The Committee decided to 'hold' at \"5.50\"\nofficial ₹1,23,456 − 25 bps – 2026–27 — end"
    assert normalize("e" + chr(0x301)) == chr(0xE9)                     # NFC: one code point, as Postgres counts it
    assert normalize("a" + chr(0x2028) + "b") == "a\nb"


def test_decoding_uses_only_the_bytes():
    assert decode_html(BOM.encode() + "<p>₹</p>".encode()) == "<p>₹</p>"
    latin = b'<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"><p>RBI\x92s</p>'
    assert decode_html(latin).endswith("RBI" + RSQUO + "s</p>")         # ISO-8859-1 is read as Windows-1252
    assert decode_html(("<p>caf" + chr(0xE9) + "</p>").encode()) == "<p>caf" + chr(0xE9) + "</p>"
    assert decode_html(b"<p>caf\xe9</p>") == "<p>caf" + chr(0xE9) + "</p>"   # not UTF-8: Windows-1252


# ------------------------------------------------------------------ HTML
def test_rbi_resolution_body_only_with_structure():
    page = rbi_page(rbi_resolution_body()).encode()
    ex = extract_html(page, "rbi_press_release")
    check_shape(ex)
    assert ex.extractor_version == "html-1/rbi_press_release"
    t = ex.text
    assert t.startswith("On the basis of an assessment") and t.endswith("Press Release: 2026-2027/1234")
    for furniture in ("Date : Oct", "322 kb", "Press Releases", "January", "All Rights Reserved", "theForm", "border",
                      "hidden text", "opens in a new window", "editor's comment"):
        assert furniture not in t
    assert "by 25 basis points to 5.50 per cent with immediate effect." in t                  # &nbsp; is a space
    assert "change the stance to 'calibrated tightening'." in t                              # entities, quotes
    assert "CPI inflation | 4.8 per cent\nReal GDP growth | 6.5 per cent" in t               # a data table; empty cell
    assert "(Puneet Pancholy)\nChief General Manager" in t                                   # <br> inside a block
    headings = [(ex.block_text(b), b.level) for b in ex.blocks if b.kind == "heading"]
    assert headings == [("Monetary Policy Decisions", 7), ("Growth and Inflation Outlook", 7),
                        ("Rationale for Monetary Policy Decisions", 3)]
    assert [b.kind for b in ex.blocks].count("item") == 1 and [b.kind for b in ex.blocks].count("table") == 1


def test_extraction_is_deterministic_and_counts_code_points():
    page = rbi_page(rbi_resolution_body() + "<p>Markets 📈 rose.</p>").encode()
    a, b = extract_html(page, "rbi_press_release"), extract_html(page, "rbi_press_release")
    assert a == b and a.sha256 == b.sha256
    assert a.char_count == len(a.text) and len("📈") == 1                                  # one code point, not two
    import hashlib
    assert a.sha256 == hashlib.sha256(a.text.encode("utf-8")).digest()


def test_minutes_headings_and_vote_table():
    ex = extract_html(rbi_page(rbi_minutes_body()).encode(), "rbi_press_release")
    check_shape(ex)
    heads = [ex.block_text(b) for b in ex.blocks if b.kind == "heading"]
    assert heads[0].startswith("Minutes of the Monetary Policy Committee Meeting")
    assert "Statement by Dr. Nagesh Kumar" in heads and heads[-1] == "Annex"
    assert "Member | Vote\nDr. Nagesh Kumar | No\nProf. Ram Singh | No\nDr. Poonam Gupta | Yes" in ex.text


def test_line_breaks_layout_tables_and_hidden_elements():
    body = """<div>First line<br>second line<br><br>a new block after two breaks</div>
<table><tr><td><p>Layout cell one.</p></td><td><p>Layout cell two.</p></td></tr></table>
<p hidden>gone</p><p aria-hidden="true">gone too</p><noscript>gone</noscript><p>Kept <img alt="no alt text"> text.</p>
<hr><p>After the rule.</p>"""
    ex = extract_html(rbi_page(body).encode(), "rbi_press_release")
    check_shape(ex)
    assert [ex.block_text(b) for b in ex.blocks] == [
        "First line\nsecond line", "a new block after two breaks", "Layout cell one.", "Layout cell two.",
        "Kept text.", "After the rule."]


def test_a_page_without_the_body_fails_loudly():
    with pytest.raises(ExtractError, match="no body found"):
        extract_html(b"<html><body><p>The page was redesigned.</p></body></html>", "rbi_press_release")
    with pytest.raises(ExtractError, match="no text"):
        extract_html(rbi_page("<p> </p><script>x()</script>").encode(), "rbi_press_release")


def test_profiles_take_outermost_matches_in_order():
    html = b"<html><body><div class='doc'><p>One.</p><div class='doc'><p>Two.</p></div></div><div class='doc'><p>Three.</p></div></body></html>"
    ex = extract_html(html, HtmlProfile("test_doc", "//div[@class='doc']"))
    assert ex.text == "One.\n\nTwo.\n\nThree." and ex.extractor_version == "html-1/test_doc"


# ------------------------------------------------------------------ PDF
def test_pdf_text_drops_page_furniture_and_rebuilds_paragraphs():
    ex = pdf_text(transcript_pages())
    check_shape(ex)
    paras = [ex.block_text(b) for b in ex.blocks]
    assert not any("Press Conference FINAL" in p or "Page " in p for p in paras)
    assert paras[0].startswith("CHAIR POWELL. Good afternoon. My colleagues and I remain squarely focused on "
                               "achieving our dual-mandate goals")
    assert paras[0].endswith("decided to lower our policy interest rate by 1/4 percentage point.")
    assert paras[1] == "I'll come back to monetary policy after briefly reviewing economic develop-ments. The economy has been growing at a moderate pace."
    assert [transcript_speaker(p) for p in paras] == ["CHAIR POWELL", None, "MICHELLE SMITH", "STEVE LIESMAN", "CHAIR POWELL"]
    assert paras[3].endswith("cut again in December?")                       # a turn continues across pages
    assert ex.extractor_version == "pdf-1"


def _tiny_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF with one text line per entry, for pypdf to read back."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        content = "BT /F1 11 Tf 14 TL 72 740 Td " + " ".join(f"({esc(ln)}) Tj T*" for ln in lines) + " ET"
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_extract_pdf_reads_a_real_pdf():
    pages = [p.split("\n") for p in transcript_pages()]
    ex = extract_pdf(_tiny_pdf(pages))
    assert ex.extractor_version == "pdf-1"
    assert ex.text == pdf_text(transcript_pages()).text
    with pytest.raises(ExtractError):
        extract_pdf(_tiny_pdf([[""]]))


# ------------------------------------------------------------------ sentences and words
def test_sentences_respect_abbreviations_initials_and_numbers():
    text = ("The U.S. economy grew. Dr. Nagesh Kumar and Prof. Ram Singh dissented, as did Smt. Rao. "
            "MPC No. 39 met on Nov. 3. Jerome H. Powell spoke at 2:30 p.m. EDT. Inflation was 2.9 per cent! "
            "Was it 3.0? \"Yes,\" he said. (It rose.) 2. The MPC noted that GDP grew 7. The end")
    assert sentences(text) == [
        "The U.S. economy grew.", "Dr. Nagesh Kumar and Prof. Ram Singh dissented, as did Smt. Rao.",
        "MPC No. 39 met on Nov. 3.", "Jerome H. Powell spoke at 2:30 p.m. EDT.", "Inflation was 2.9 per cent!",
        "Was it 3.0?", "\"Yes,\" he said.", "(It rose.)", "2. The MPC noted that GDP grew 7.", "The end"]


def test_sentence_spans_never_cross_line_breaks_and_exclude_whitespace():
    text = "Member | Vote\nDr. A | Yes\n\nA paragraph. Another one.  "
    spans = sentence_spans(text)
    assert [text[s:e] for s, e in spans] == ["Member | Vote", "Dr. A | Yes", "A paragraph.", "Another one."]
    assert all(not text[s].isspace() and not text[e - 1].isspace() for s, e in spans)
    assert sentence_spans(text, 14, 25) == [(14, 25)]


def test_words_and_paragraph_numbers():
    assert words("The U.S. rate, 5.50 per cent (₹1,23,456), 2026-27, the RBI's 1/4-point move.") == [
        "The", "U.S", "rate", "5.50", "per", "cent", "1,23,456", "2026-27", "the", "RBI's", "1/4-point", "move"]
    text = "One.\n\nTwo a. Two b.\n\nThree."
    starts = paragraph_starts(text)
    assert starts == [0, 6, 21]
    assert [paragraph_of(starts, text.index(w)) for w in ("One", "Two a", "Two b", "Three")] == [1, 2, 2, 3]
