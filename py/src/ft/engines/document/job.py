"""The document-job interface (Spec 02A §5.3; Plan 02 §7.2; job 2A-07).

A document job is a task that finds documents at a source, fetches each once, and registers them: discover → fetch →
store (raw file) → register (a `core.document`, its event and a detail table). Module 1's Fed and RBI tasks have this
shape written out by hand (`policy.fed_documents`, `policy.rbi_documents`); results discovery (`equity.filings`) is the
first task built on this class. Moving the Fed and RBI tasks onto it is its own later job (Spec 02A §10 decision 11),
so nothing here is imported by them.

A subclass writes four things:

- `discover(ctx)`: the candidates, each with a stable `external_key` (the source's own file name or id), its URL and
  whatever metadata `register` needs. No network call other than listing calls (made through `ctx.fetcher`).
- `fetch_candidate(ctx, c)`: the bytes, through `ctx.fetcher` (the default fetches `c.url` for `source`); return `None`
  for a candidate the source can't serve (counted, named by a check).
- `register(tx, c, got, task_run_id, previous)`: the `core.document` (call `insert_document`), the event and the detail
  row. `previous` is the stored document the same key already has, or None.
- `document_checks(tx, harvest, task_run_id)`: the task's own checks; the loop adds none of its own beyond reporting.

The shared loop (`fetch` and `load`) is what makes every document task resumable:

- a candidate whose external key is stored with the **same content hash** (the raw file's SHA-256) is skipped, so a
  re-run writes no raw object, document, event or row;
- a changed hash under the same key is a **new document version** (`version_no` + 1, `supersedes_id`, never an
  overwrite) and raises an alert, because a filing that changes under one name is something a person should look at;
- a document is registered unpublished: a document without text (an XBRL instance) is never published (Arch 01 §1.3).

Built for the three later users the plan names (earnings-call transcripts, BRSR reports, IPO documents) without
building for them: nothing in this module knows about XBRL, companies or NSE.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ft.jobs.fetch import FetchError
from ft.jobs.runner import Alert, Check, Context, Task


@dataclass(frozen=True)
class Candidate:
    """A document found at the source. `meta` is the subclass's own: whatever `register` needs from the listing."""
    external_key: str
    url: str
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Got:
    """A candidate's bytes, stored as a raw file before any load."""
    candidate: Candidate
    raw_file_id: int
    sha256: bytes
    body: bytes
    media_type: str | None = None


@dataclass(frozen=True)
class Stored:
    """The latest stored version of a document key."""
    document_id: int
    version_no: int
    sha256: bytes | None            # the raw file's hash


@dataclass(frozen=True)
class Registered:
    document_id: int
    version_no: int
    new_version: bool               # True: the key was stored before, with other bytes


@dataclass
class Harvest:
    """What `fetch` returns: the candidates found and the bytes of those the source served."""
    candidates: list[Candidate]
    got: list[Got]
    unserved: list[tuple[Candidate, str]] = field(default_factory=list)     # (candidate, why not)
    notes: dict[str, Any] = field(default_factory=dict)                     # the subclass's own (listing counts, skips)


@dataclass(frozen=True)
class DocumentFields:
    """The columns `insert_document` writes. A document without text leaves the text columns null."""
    doc_type: str
    entity_id: int
    source_id: int
    external_key: str
    source_url: str
    title: str
    raw_file_id: int
    published_at: datetime | None = None
    event_id: int | None = None
    language: str = "en"


def latest_stored(tx, source_id: int, external_key: str) -> Stored | None:
    row = tx.execute(
        """select d.id, d.version_no, f.sha256 from core.document d left join core.raw_file f on f.id = d.raw_file_id
            where d.source_id = %s and d.external_key = %s order by d.version_no desc limit 1""",
        (source_id, external_key)).fetchone()
    return None if row is None else Stored(row[0], row[1], None if row[2] is None else bytes(row[2]))


def insert_document(tx, d: DocumentFields, previous: Stored | None) -> Registered:
    """A new `core.document` row, unpublished and without text: version 1, or `previous`'s version + 1 superseding it."""
    version, supersedes = (previous.version_no + 1, previous.document_id) if previous else (1, None)
    doc_id = tx.execute(
        """insert into core.document (doc_type, entity_id, event_id, title, source_id, external_key, source_url,
                                      raw_file_id, published_at, version_no, supersedes_id, language)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (d.doc_type, d.entity_id, d.event_id, d.title, d.source_id, d.external_key, d.source_url, d.raw_file_id,
         d.published_at, version, supersedes, d.language)).fetchone()[0]
    return Registered(doc_id, version, previous is not None)


class DocumentJob(Task):
    """Base class of the tasks that discover, fetch and register documents. See the module docstring."""
    source = ""                      # the core.source key the files come from (and the raw files are filed under)
    doc_type = ""                    # the core.doc_type key
    media_type: str | None = None

    def __init__(self):
        self.outcome: dict[str, Any] = {}      # the last load's counts and lists, for the checks and alerts

    # ------------------------------------------------------------------ what a subclass writes
    def discover(self, ctx: Context) -> tuple[list[Candidate], dict[str, Any]]:
        """The candidates in the run's window, and the subclass's own notes on the listing (counts, skips)."""
        raise NotImplementedError

    def fetch_candidate(self, ctx: Context, c: Candidate) -> Got | None:
        got = ctx.fetcher.get(c.url, source=self.source, media_type=self.media_type, allow=(404,))
        if got.raw_file_id is None:
            return None
        body = got.body if got.body is not None else ctx.fetcher.read(got.raw_file_id)
        return Got(c, got.raw_file_id, got.sha256 if got.sha256 is not None else b"", body, got.media_type)

    def register(self, tx, c: Candidate, got: Got, task_run_id: int, previous: Stored | None) -> Registered:
        raise NotImplementedError

    def document_checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        return []

    def changed_alert_key(self, external_key: str) -> str:
        return f"document_changed:{self.key}:{external_key}"

    # ------------------------------------------------------------------ the shared loop
    def fetch(self, ctx: Context) -> Harvest:
        candidates, notes = self.discover(ctx)
        got: list[Got] = []
        unserved: list[tuple[Candidate, str]] = []
        for c in candidates:
            try:
                g = self.fetch_candidate(ctx, c)
            except FetchError as e:
                unserved.append((c, str(e).removeprefix(f"{e.url}: ")))
                continue
            if g is None:
                unserved.append((c, "no such file"))
            else:
                got.append(g)
        return Harvest(candidates, got, unserved, notes)

    def load(self, tx, h: Harvest, task_run_id: int) -> int:
        source_id = tx.execute("select id from core.source where key = %s", (self.source,)).fetchone()[0]
        skipped, added, versions = 0, [], []
        for g in h.got:
            c = g.candidate
            previous = latest_stored(tx, source_id, c.external_key)
            if previous is not None and previous.sha256 == g.sha256:
                skipped += 1                                    # the same key with the same bytes: nothing to write
                continue
            r = self.register(tx, c, g, task_run_id, previous)
            (versions if r.new_version else added).append((c.external_key, r))
        self.outcome = {"skipped": skipped, "added": added, "versions": versions, "source_id": source_id}
        return len(added) + len(versions)

    def checks(self, tx, h: Harvest, task_run_id: int) -> list[Check]:
        return self.document_checks(tx, h, task_run_id)

    def alerts(self, db) -> list[Alert]:
        return [Alert(self.changed_alert_key(key), True, "warn",
                      f"{self.key}: the file {key} changed under the same name; it is stored as version {r.version_no} "
                      "and the earlier version is kept")
                for key, r in self.outcome.get("versions", [])]
