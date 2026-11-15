"""Extracting the KPI metrics a results document states, page by page, through `ft/ai/client.py` (DD-04, #512; Plan 05
§1.3; Data Audit 10 §5.1, §6). Pure: it builds the per-page request, parses and normalises the reply into `filters.Row`s,
applies the post-filters and the sign convention (`filters`), and the document-level tile check. The network (sending the
request) and the database (writing `equity.kpi_value`) are the task's.

- One page a call (Data Audit 10 §6): a text page as a plain-text document with the previous page's last 10 lines as the
  context, an image page (HDFC Bank, no text layer) as an image block.
- The reply is the schema's JSON ({"metrics": [{label, member, measure, value, unit}]}); a value is read as a Decimal.
- Each row is marked kept or filtered (`filters.classify`), its sign fixed for a change measure (`filters.signed_value`),
  and, across the document, an infographic-tile figure is kept only when it also appears in a table (`filters.reconcile`).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ft.ai.client import Prompt, Request, Source
from ft.equity.kpi import filters as fl

TASK = "kpi_extraction"
CONTEXT_LINES = 10              # the previous page's last 10 lines, as context for a text page (Data Audit 10 §6)
MAX_LABEL = 400                 # a sane cap on a label we store as printed


@dataclass(frozen=True)
class Page:
    """One page to read: its physical number, and either its text (a text page) or a base64 image (an image page)."""
    page: int
    text: str | None = None
    image_base64: str | None = None
    image_media_type: str | None = None

    @property
    def is_image(self) -> bool:
        return self.image_base64 is not None


def _context(prev: Page | None) -> str | None:
    """The previous text page's last CONTEXT_LINES lines (Data Audit 10 §6), to tell the model which period this page's
    figures belong to. An image previous page gives no text context."""
    if prev is None or prev.text is None:
        return None
    lines = [ln for ln in prev.text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-CONTEXT_LINES:])
    return tail or None


def page_request(document_id: int, company: str, quarter: str, page: Page, prev: Page | None, prompt: Prompt,
                 schema: dict) -> Request:
    """The request for one page. `document_id` is the kpi_document id (the input hash is stable per document and page);
    the KPI documents are not core.document rows, so this id is used only to key the request, never for citations."""
    title = f"{company} — {quarter} — page {page.page}"
    instruction = (f"This is page {page.page} of {company}'s {quarter} results document. List every KPI metric it "
                   "states for the quarter, following the rules. Return an empty list if it states none.")
    if page.is_image:
        src = Source(document_id, "", title, context=_context(prev), image_base64=page.image_base64,
                     image_media_type=page.image_media_type)
    else:
        src = Source(document_id, page.text or "", title, context=_context(prev))
    return Request(TASK, prompt, (src,), instruction, schema=schema)


def _decimal(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_rows(answer: dict, page: int) -> list[fl.Row]:
    """The reply's JSON as `filters.Row`s for one page. A malformed item is skipped (counted by the caller against the
    schema), never guessed. A label is required and kept as printed (capped)."""
    rows: list[fl.Row] = []
    for item in (answer or {}).get("metrics", []) or []:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        measure = item.get("measure")
        if not isinstance(label, str) or not label.strip() or not isinstance(measure, str) or not measure:
            continue
        member = item.get("member")
        unit = item.get("unit")
        rows.append(fl.Row(
            label=label.strip()[:MAX_LABEL],
            member=member.strip() if isinstance(member, str) and member.strip() else None,
            measure=measure,
            value=_decimal(item.get("value")),
            unit=unit.strip() if isinstance(unit, str) and unit.strip() else None,
            page=page,
        ))
    return rows


@dataclass(frozen=True)
class Extracted:
    """One extracted row with its final value and verdict, ready for equity.kpi_value."""
    row: fl.Row
    value: Decimal | None           # the sign-corrected value (filters.signed_value)
    filter_status: str
    filter_reason: str | None


def tile_pages(pages: list[Page]) -> frozenset[int]:
    """The pages whose figures must be confirmed by a table of the same document: the image pages, when the document also
    has text pages. An image-only document (HDFC Bank's image quarters) has no text table to confirm against, so it gets
    no tile check (#512's fix round: dev run 2 filtered every row of those three quarters as `tile_unconfirmed`)."""
    image = frozenset(p.page for p in pages if p.is_image)
    return image if len(image) < len(pages) else frozenset()


def filter_rows(rows: list[fl.Row], pages: list[Page]) -> list[Extracted]:
    """The post-filters, the sign convention and the tile check over one document's rows (in their stored order): what
    `extract_document` applies to a fresh extraction, and the task re-applies to a reused document's stored rows."""
    verdicts = {i: fl.classify(r, _page_text(pages, r.page)) for i, r in enumerate(rows)}
    verdicts = fl.reconcile_tiles(rows, verdicts, tile_pages(pages))
    return [Extracted(r, fl.signed_value(r), verdicts[i].status, verdicts[i].reason) for i, r in enumerate(rows)]


def extract_document(pages: list[Page], rows_by_page: dict[int, list[fl.Row]], tile_pages: frozenset[int] = frozenset()
                     ) -> list[Extracted]:
    """Assemble one document's rows: classify each row, fix the sign of a change measure, then apply the tile check
    across the whole document. `rows_by_page` is `parse_rows` per page (the caller sends the requests and collects the
    replies). `tile_pages` are the pages the task marked as infographic/summary pages (`tile_pages(pages)`). Rows come
    out in page then extraction order, so `row_no` in equity.kpi_value is stable."""
    ordered: list[fl.Row] = []
    for p in pages:
        ordered.extend(rows_by_page.get(p.page, []))
    verdicts = {i: fl.classify(r, _page_text(pages, r.page)) for i, r in enumerate(ordered)}
    verdicts = fl.reconcile_tiles(ordered, verdicts, tile_pages)
    out: list[Extracted] = []
    for i, r in enumerate(ordered):
        v = verdicts[i]
        out.append(Extracted(r, fl.signed_value(r), v.status, v.reason))
    return out


def _page_text(pages: list[Page], n: int) -> str:
    for p in pages:
        if p.page == n:
            return p.text or ""
    return ""
