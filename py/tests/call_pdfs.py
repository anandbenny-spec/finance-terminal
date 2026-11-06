"""Synthetic earnings-call PDFs for the selection tests (job 2B-07b, #285). A hand-written PDF with a text layer (pypdf reads
it, as it reads the saved ones): a header, then speaker-labelled paragraphs of seeded pseudo-words, enough of them to pass
the full test (3,000 words, 10 speaker lines). Two seeds give two different texts; the same seed gives the same text, so a
re-filing is `call_pdf(..., seed=1)` twice. No fixtures and no network: these tests run anywhere."""
import random

LINES_PER_PAGE = 58
SPEAKERS = ("Rahul Mehta", "Priya Nair", "Anil Kapoor", "Sunita Rao", "Vikram Shah", "Meera Iyer", "Karan Bose", "Neha Desai")


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """A PDF whose page i shows the lines of pages[i], one text line each, in Helvetica."""
    objs: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, lines in enumerate(pages):
        content_id = 4 + 2 * i
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
        stream = "\n".join(f"BT /F1 9 Tf 40 {752 - 12 * n} Td ({_escape(line)}) Tj ET" for n, line in enumerate(lines)).encode("latin-1")
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def _words(rnd: random.Random, n: int) -> str:
    return " ".join("".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(3, 8))) for _ in range(n))


def call_pdf(header: list[str], seed: int = 1, labels: int = 14, body_lines: int = 300, mention: str | None = None) -> bytes:
    """A transcript: `header` lines (the page before the first speaker label), then `labels` speaker-labelled paragraphs among
    `body_lines` lines of pseudo-words. `mention` is a sentence put into the body after the first label (an earnings call
    that talks about the AGM, say); the header never holds it."""
    rnd = random.Random(seed)
    lines = list(header)
    every = max(1, body_lines // labels)
    for n in range(body_lines):
        text = _words(rnd, 12)
        if n % every == 0:
            text = f"{SPEAKERS[(n // every) % len(SPEAKERS)]}: {text}"
        if mention and n == every + 1:
            text = f"{mention} {text}"
        lines.append(text[:110])
    return make_pdf([lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)])
