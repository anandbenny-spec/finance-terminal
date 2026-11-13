"""Rendering a PDF page to a PNG for the KPI-table pilot's image route (HDFC Bank's image-only quarters, Data Audit 10
K2, finding 4). Isolated here so the task and the tests import one function; the heavy libraries (pypdfium2, Pillow) are
in the `jobs` extra, not the API's base dependencies. Pure bytes in, PNG bytes out."""
from __future__ import annotations

import base64
import io

RENDER_SCALE = 2.0            # ~144 dpi at the default 72 dpi base: readable KPI tables without a huge image
PNG_MEDIA_TYPE = "image/png"


def page_count(pdf_bytes: bytes) -> int:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(doc)
    finally:
        doc.close()


def render_page_png(pdf_bytes: bytes, page_index: int, scale: float = RENDER_SCALE) -> bytes:
    """The 0-based `page_index` of `pdf_bytes` as PNG bytes."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        bitmap = doc[page_index].render(scale=scale)
        image = bitmap.to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


def render_page_base64(pdf_bytes: bytes, page_index: int, scale: float = RENDER_SCALE) -> str:
    return base64.b64encode(render_page_png(pdf_bytes, page_index, scale)).decode("ascii")
