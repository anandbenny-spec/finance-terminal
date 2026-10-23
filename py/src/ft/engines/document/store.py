"""Writing documents, chunks and diffs inside a task's load transaction (Architecture 01 §1.3-§1.5).

- A document version's text is fixed once chunked; new text is a new version (`version_no` + 1, `supersedes_id`),
  and the same text again writes nothing, so a re-run adds no versions (Prompt 01 B8).
- Chunks can be replaced (re-chunking is allowed); they're checked here, and the database checks again at commit
  that they reproduce the text exactly.
- Publishing (`is_published`) is the task's call, after its checks.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ft.engines.document.chunk import Chunk, check_chunks
from ft.engines.document.diff import Diff
from ft.engines.document.text import Extracted


@dataclass(frozen=True)
class DocumentRef:
    """Where a document comes from and what it is: everything but its text."""
    doc_type: str
    entity_id: int
    source_id: int
    external_key: str          # stable key at the source: the URL path
    source_url: str            # the public link shown to visitors
    title: str
    event_id: int | None = None
    raw_file_id: int | None = None
    published_at: datetime | None = None
    language: str = "en"


@dataclass(frozen=True)
class Saved:
    document_id: int
    version_no: int
    new_text: bool             # False: the latest version already held this text, and nothing was written
    supersedes_id: int | None  # the version this one replaces, when it's a new version


def save_version(tx, ref: DocumentRef, ex: Extracted) -> Saved:
    """The document version holding this text: the latest one if its text matches; that one if it has no text yet
    (it gets this text); otherwise a new version that supersedes it."""
    row = tx.execute(
        """select id, version_no, text_sha256 from core.document where source_id=%s and external_key=%s
            order by version_no desc limit 1 for update""", (ref.source_id, ref.external_key)).fetchone()
    if row and row[2] is not None and bytes(row[2]) == ex.sha256:
        return Saved(row[0], row[1], False, None)
    if row and row[2] is None:
        tx.execute("""update core.document set extractor_version=%s, char_count=%s, text_sha256=%s, extracted_at=now(),
                             raw_file_id=coalesce(%s, raw_file_id) where id=%s""",
                   (ex.extractor_version, ex.char_count, ex.sha256, ref.raw_file_id, row[0]))
        return Saved(row[0], row[1], True, None)
    version, previous = (row[1] + 1, row[0]) if row else (1, None)
    new_id = tx.execute(
        """insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                      raw_file_id, published_at, version_no, supersedes_id, language, extractor_version,
                                      char_count, text_sha256, extracted_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) returning id""",
        (ref.doc_type, ref.entity_id, ref.event_id, ref.title, ref.source_id, ref.external_key, ref.source_url,
         ref.raw_file_id, ref.published_at, version, previous, ref.language, ex.extractor_version, ex.char_count,
         ex.sha256)).fetchone()[0]
    return Saved(new_id, version, True, previous)


def save_chunks(tx, document_id: int, text: str, chunks: Sequence[Chunk]) -> int:
    """Replace the document's chunks (its embeddings go with them, by cascade). Returns the number written."""
    check_chunks(text, list(chunks))
    tx.execute("delete from core.chunk where document_id=%s", (document_id,))
    with tx.cursor() as cur:
        cur.executemany(
            """insert into core.chunk (document_id, chunk_no, char_start, char_end, section, speaker, body)
               values (%s,%s,%s,%s,%s,%s,%s)""",
            [(document_id, c.chunk_no, c.char_start, c.char_end, c.section, c.speaker, c.body) for c in chunks])
    tx.execute("update core.document set chunked_at=now() where id=%s", (document_id,))
    return len(chunks)


def save_diff(tx, document_id: int, base_document_id: int, d: Diff) -> None:
    """The document's redline against its baseline (one per document; a re-run replaces it)."""
    tx.execute(
        """insert into core.document_diff (document_id, base_document_id, segments, words_added, words_removed, diff_version)
           values (%s,%s,%s,%s,%s,%s)
           on conflict (document_id) do update set base_document_id=excluded.base_document_id,
             segments=excluded.segments, words_added=excluded.words_added, words_removed=excluded.words_removed,
             diff_version=excluded.diff_version, created_at=now()""",
        (document_id, base_document_id, json.dumps(d.segments), d.words_added, d.words_removed, d.diff_version))
