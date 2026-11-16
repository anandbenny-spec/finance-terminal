"""equity.call_text: turns, sections, speaker roles and chunks of each stored earnings-call transcript, and the call's
publication (Spec 02B §5.3, §13.3; job 2B-09). Database and the stored raw files only; no network, no AI.

Per call written by `equity.call_transcripts` (2B-07) whose quarter isn't in doubt (a call held on a quarter conflict or
with no quarter stays as it is: not analysed, not published):

- the transcript's text is re-derived from its stored PDF (`pdfs.read_pdf` + `pdfs.call_text`, the same steps 2B-07 ran)
  and must hash to the document's `text_sha256`;
- `ft/equity/calls/turns.py` cuts it into turns, sections and roles, written to `equity.call_turn`;
- the existing chunker cuts it into chunks that each lie inside one turn, with the turn's speaker and section
  ("Opening remarks", "Q&A", "Closing remarks"), written through `store.save_chunks`; nothing is embedded (after 18 Nov);
- `earnings_call.unknown_role_share` is the unknown-role words over the turn words (`words` is kept as 2B-07 wrote it);
  above 10% the call is role-held: 2B-13 skips its extraction, its summary still runs;
- the transcript document and the call's event are published once the checks pass. The document's text stays private
  (`doc_type.text_public = false`, 2B-05): anon reads its row and its `transcript_page` rows, never a chunk. A call that is
  `on_company_website` or `unreadable` has its event published once its row exists.

**The analysis version and the stored files (job 2B-09b, #341; Anand, 9 Nov 2026).** Supabase counts every byte read out of
Storage as egress, and this task used to re-read every analysed call's PDF on every run. `earnings_call.analysis_version`
(migration 0033) now records the version of the rules a call was last analysed under: `analysis_version()`, a hash of the
sources that decide a transcript's text, turns, roles and chunks (`ft.engines.document.text`, `pdfs`, `turns`, `chunk`), the
parser's version and the PDF library's version. A rule that changes changes the hash, so no change can be forgotten.

- A call whose document is already chunked and whose stored version equals the current one is **not read**: its file stays
  in Storage. It is still published again if it was set aside and restored (#285), which reads no file.
- A call that is new (not chunked yet), has no version or another one, is re-derived from its stored PDF as before, and
  its version is written when the load passes. So a changed rule re-reads every call once.
- **Integrity slice:** each run also re-reads one rotating slice of the other calls: those whose stable hash (of the call's
  event id), modulo `call_text_integrity_slices` (`config/equity/calls.yaml`, 4), equals the run's week number modulo the
  same: about a quarter of the files, every call once in four consecutive weekly runs. The week number counts Mondays without
  a break across New Year (`week_index`), so the rotation has no gap or repeat. Stored text cannot change once chunked, so
  the slice only catches drift (a changed library, a corrupted object).
- Until migration 0033 reaches a project, `analysis_version` doesn't exist there: the task reads every call as before,
  writes no version, and its warning check `analysis_versions` says so.

A call that is re-derived (not skipped) is rewritten only if the rules now give other turns or roles (its chunks only if
their speakers or sections changed), so a new role rule reaches calls already published.

Only the document a call row points at is analysed: a revised version 2B-07 registered (a later version of the same key)
gets no turns and no chunks before 18 Nov (§13.3). A second run with the same rules writes nothing.

Checks, B blocking, W warning (each says how many calls it covered: the calls skipped under the current version are not
re-derived, so `text_matches`, `chunks_reproduce`, `turns_contiguous`, `unknown_roles` and `no_handover` speak for the
calls re-derived in the run; their details say how many were skipped and how many stored files were read):
- (B) `text_matches`: each transcript's text, re-derived from its stored file, has the stored hash;
- (B) `chunks_reproduce`: the chunks reproduce the text exactly and each lies inside one turn (or before the first);
- (B) `turns_contiguous`: turns are contiguous, don't overlap and lie inside the text;
- (W) `unknown_roles`: calls above 10% unknown-role words, named with the speakers whose role couldn't be read;
- (W) `no_handover`: calls with no hand-over to questions found (all `qa`), named;
- (W) `quarter_conflicts`: calls held on a quarter conflict or with no quarter, named (they stay unpublished);
- (W) `analysis_versions`: the migration that records the versions is applied (otherwise every call is re-read every run);
- (W) `calls_analysed`: counts, with the skipped calls and the stored files read (count and MB) (always passes).
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from ft.engines.document.chunk import Chunk, chunk_text
from ft.engines.document.store import save_chunks
from ft.equity.calls import pdfs, turns
from ft.equity.calls.discovery import load_calls_config
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12

# The sources whose rules decide what is derived from a stored PDF, in a fixed order: the text extractor with its canonical
# text, sentence and speaker rules; the call parser; the turn, section and role rules; the chunker. This module is left out on
# purpose: a setting or a wording change here must not make every call re-read (the slice size lives in calls.yaml).
VERSIONED_MODULES = ("ft.engines.document.text", "ft.equity.calls.pdfs", "ft.equity.calls.turns", "ft.engines.document.chunk")
VERSION_LENGTH = 16                       # hex characters kept of the SHA-256


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


def _source(module_name: str) -> tuple[str, bytes]:
    """A versioned module's name and the bytes of its source file."""
    return module_name, Path(import_module(module_name).__file__).read_bytes()


def _pdf_library_version() -> str:
    try:
        return package_version("pypdf")
    except PackageNotFoundError:
        return "none"


def version_of(parts: Iterable[tuple[str, bytes]], parser_version: str, library_version: str) -> str:
    """A version string from the named source files, the parser's version and the PDF library's: any byte that changes in
    any of them gives another string, and the same inputs always give the same one."""
    h = hashlib.sha256()
    h.update(f"parser={parser_version}\nlibrary=pypdf {library_version}\n".encode())
    for name, data in parts:
        h.update(f"\n--{name} {len(data)}\n".encode())
        h.update(data)
    return h.hexdigest()[:VERSION_LENGTH]


def analysis_version() -> str:
    """The version of the rules this task analyses a transcript under (what `earnings_call.analysis_version` records)."""
    return version_of((_source(m) for m in VERSIONED_MODULES), pdfs.PARSER_VERSION, _pdf_library_version())


def run_day(now: datetime) -> date:
    return (now.astimezone(UTC) if now.tzinfo else now).date()


def week_index(day: date) -> int:
    """Whole Monday-to-Sunday weeks since the calendar's first day (a Monday): consecutive weeks have consecutive numbers,
    also across New Year, where the ISO week number repeats or skips (a 53-week year)."""
    return (day.toordinal() - 1) // 7


def slice_of(event_id: int, slices: int) -> int:
    """Which of `slices` slices a call is in: a stable hash of its id, so the same call is always in the same one."""
    return int.from_bytes(hashlib.sha256(str(event_id).encode()).digest()[:8], "big") % slices


def in_slice(event_id: int, day: date, slices: int) -> bool:
    """True when the call is in the slice this run re-reads, as the integrity check."""
    return slice_of(event_id, slices) == week_index(day) % slices


def read_reason(*, chunked: bool, stored: str | None, current: str, event_id: int, day: date, slices: int) -> str | None:
    """Why a call is read from storage in this run, or None when it is skipped: `new` (not chunked yet), `version` (no version
    or another one stored) or `slice` (analysed under the current version, in this run's integrity slice)."""
    if not chunked:
        return "new"
    if stored != current:
        return "version"
    return "slice" if in_slice(event_id, day, slices) else None


def _has_version_column(db) -> bool:
    """Migration 0033 is applied to this project (the column exists)."""
    return db.execute("""select 1 from pg_attribute where attrelid = 'equity.earnings_call'::regclass
                            and attname = 'analysis_version' and not attisdropped""").fetchone() is not None


REASON_LABELS = {"new": "new or not chunked yet", "version": "with no or another analysis version",
                 "legacy": "read because versions aren't recorded here yet"}


def coverage(h: Fetched) -> str:
    """What a run read and skipped, for the checks' details: nothing is silent (job 2B-09b)."""
    skipped = sum(c.skipped for c in h.calls)
    mb = h.bytes_read / 1_048_576
    why = [f"{n} {REASON_LABELS[k]}" for k, n in h.reasons.items() if k in REASON_LABELS and n]
    if h.reasons.get("slice"):
        why.append(f"{h.reasons['slice']} in this run's integrity slice ({h.slice_no + 1} of {h.slices})")
    read = f"{h.files_read} stored files read ({mb:.1f} MB)" + (": " + ", ".join(why) if why else "")
    if not h.versioned:
        return f"analysis versions aren't recorded here (migration 0033 not applied), so every call is read; {read}"
    return f"{skipped} skipped, analysed under version {h.version}; {read}"


@dataclass
class Call:
    event_id: int
    slug: str
    document_id: int
    stored_version: str | None = None     # the analysis version stored with the call (null until analysed under one)
    text: str = ""
    analysis: turns.Analysis | None = None
    chunks: list[Chunk] = field(default_factory=list)
    problem: str | None = None            # why it couldn't be analysed: text_mismatch | chunks | turns | unreadable
    refresh: bool = False                 # already chunked and published: only its roles (or turns) changed under newer rules
    unchanged: bool = False               # ... and the rules still give exactly what is stored: nothing to write
    rechunk: bool = False                 # ... and its chunks' speakers or sections changed too
    skipped: bool = False                 # ... and analysed under the current version, outside this run's slice: file not read


@dataclass
class Fetched:
    calls: list[Call] = field(default_factory=list)                 # transcripts to analyse
    publish_events: list[int] = field(default_factory=list)         # calls with no transcript text to analyse
    held: list[str] = field(default_factory=list)
    versioned: bool = True                # the project has `earnings_call.analysis_version` (migration 0033)
    version: str = ""                     # the current analysis version
    slices: int = 1
    slice_no: int = 0                     # the slice this run re-reads
    files_read: int = 0                   # stored PDFs read in this run
    bytes_read: int = 0
    reasons: dict[str, int] = field(default_factory=dict)          # why calls were read: new | version | slice | legacy


def inside_one_turn(chunks: list[Chunk], an: turns.Analysis) -> bool:
    """Every chunk lies inside one turn, or before the first turn (text with no speaker)."""
    if not an.turns:
        return True
    first = an.turns[0].char_start
    for c in chunks:
        if c.char_end <= first:
            continue
        if c.char_start < first:
            return False
        if not any(t.char_start <= c.char_start and c.char_end <= t.char_end for t in an.turns):
            return False
    return True


@register
class EquityCallText(Task):
    key = "equity.call_text"
    requires = "equity.earnings_call"

    def __init__(self):
        self.done: list[Call] = []

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires=self.requires)

    # ------------------------------------------------------------------ fetch (reads the database and the stored files)
    def fetch(self, ctx: Context) -> Fetched:
        db = ctx.fetcher.db
        day, slices = run_day(ctx.now), load_calls_config().call_text_integrity_slices
        out = Fetched(versioned=_has_version_column(db), version=analysis_version(), slices=slices,
                      slice_no=week_index(day) % slices)
        # Without the column (migration 0033 not applied here yet) the stored version reads as null, so every call is read.
        stored_version = "ec.analysis_version" if out.versioned else "null::text"
        for event_id, slug, reason, doc_id, raw_id, sha, chunked, ev_pub, stored in db.execute(
                """select ec.event_id, ev.slug, ec.status_reason, ec.transcript_document_id, d.raw_file_id, d.text_sha256,
                          d.chunked_at, ev.is_published, """ + stored_version + """
                     from equity.earnings_call ec
                     join core.event ev on ev.id = ec.event_id
                     left join core.document d on d.id = ec.transcript_document_id
                    where ec.entity_id in (select id from core.entity where market = 'IN')
                    order by ev.slug""").fetchall():
            if reason is not None:
                out.held.append(f"{slug} ({reason})")                 # held: not analysed, not published
                continue
            if doc_id is None:
                if not ev_pub:
                    out.publish_events.append(event_id)               # on_company_website / unreadable: the label or link
                continue
            call = Call(event_id, slug, doc_id, stored_version=stored)
            if chunked is not None:
                call.refresh = True                                   # done before: rewritten only if the rules now say otherwise
            out.calls.append(call)
            why = read_reason(chunked=chunked is not None, stored=stored, current=out.version, event_id=event_id, day=day,
                              slices=slices)
            if why == "version" and not out.versioned:
                why = "legacy"                                        # no column to compare with: read, as before 2B-09b
            if why is None:
                call.skipped = True                                   # analysed under the current rules: its file stays in Storage
                continue
            out.reasons[why] = out.reasons.get(why, 0) + 1
            try:
                if raw_id is None or sha is None:
                    raise ValueError("no stored file or text hash")
                pdf = ctx.fetcher.read(raw_id)
                out.files_read += 1
                out.bytes_read += len(pdf)
                info = pdfs.read_pdf(pdf)
                ct = pdfs.call_text(info)
            except Exception as e:                                    # noqa: BLE001 - named by a check, never raw
                call.problem = f"unreadable: {type(e).__name__}"
                continue
            if ct.extracted.sha256 != bytes(sha):
                call.problem = "text_mismatch"
                continue
            try:
                call.analysis = turns.analyse(ct.extracted, ct.labels)
                call.text = ct.extracted.text
                # the text with its blocks cut where a turn opens inside a paragraph (#542): the same text, `ct.extracted`'s
                # own blocks for a call without an inline label
                call.chunks = chunk_text(call.analysis.doc, speakers="call", marks=call.analysis.marks)
            except ValueError as e:
                call.problem = "turns" if "turn" in str(e) else "chunks"
                continue
            if not inside_one_turn(call.chunks, call.analysis):
                call.problem = "chunks"
            if call.refresh and call.problem is None:
                same_turns = [tuple(r) for r in db.execute(
                    """select turn_no, char_start, char_end, speaker, role, section, words from equity.call_turn
                        where document_id = %s order by turn_no""", (doc_id,)).fetchall()] == [
                    (t.turn_no, t.char_start, t.char_end, t.speaker, t.role, t.section, t.words) for t in call.analysis.turns]
                same_chunks = [tuple(r) for r in db.execute(
                    """select chunk_no, char_start, char_end, section, speaker from core.chunk where document_id = %s
                        order by chunk_no""", (doc_id,)).fetchall()] == [
                    (c.chunk_no, c.char_start, c.char_end, c.section, c.speaker) for c in call.chunks]
                if same_turns and same_chunks:
                    call.unchanged = True                             # a second run writes nothing
                else:
                    call.rechunk = not same_chunks
        return out

    @staticmethod
    def _publish(tx, doc: int, event_id: int) -> int:
        """Publish an analysed call's transcript and event if they aren't. Returns the rows changed (0 for a published call)."""
        n = tx.execute("update core.document set is_published = true where id = %s and not is_published", (doc,)).rowcount
        return n + tx.execute("update core.event set is_published = true, updated_at = now() where id = %s and not is_published",
                              (event_id,)).rowcount

    # ------------------------------------------------------------------ load
    def load(self, tx, h: Fetched, task_run_id: int) -> int:
        self.done = []
        n = 0
        for call in h.calls:
            if (call.unchanged or call.skipped) and call.problem is None:
                # Nothing to rewrite, but a call set aside and restored (#285) is published again; a published one writes nothing.
                n += self._publish(tx, call.document_id, call.event_id)
                if call.unchanged and h.versioned and call.stored_version != h.version:
                    n += tx.execute("""update equity.earnings_call set analysis_version = %s
                                        where event_id = %s and analysis_version is distinct from %s""",
                                    (h.version, call.event_id, h.version)).rowcount      # analysed under these rules: say so
                continue
            if call.problem or call.analysis is None:
                continue                                              # a blocking check fails the run: nothing is kept
            an, doc = call.analysis, call.document_id
            if not call.refresh or call.rechunk:
                save_chunks(tx, doc, call.text, call.chunks)
            tx.execute("delete from equity.call_turn where document_id = %s", (doc,))
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into equity.call_turn (document_id, turn_no, char_start, char_end, speaker, role, section, words)
                       values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(doc, t.turn_no, t.char_start, t.char_end, t.speaker, t.role, t.section, t.words) for t in an.turns])
            if h.versioned:
                tx.execute("""update equity.earnings_call set unknown_role_share = %s, words = coalesce(words, %s),
                                     analysis_version = %s where event_id = %s""",
                           (round(an.unknown_share, 4), an.total_words, h.version, call.event_id))
            else:
                tx.execute("""update equity.earnings_call set unknown_role_share = %s,
                                     words = coalesce(words, %s) where event_id = %s""",
                           (round(an.unknown_share, 4), an.total_words, call.event_id))
            self._publish(tx, doc, call.event_id)
            self.done.append(call)
            n += 1 + len(an.turns) + (len(call.chunks) if not call.refresh or call.rechunk else 0)
        for event_id in h.publish_events:
            n += tx.execute("update core.event set is_published = true, updated_at = now() where id = %s and not is_published",
                            (event_id,)).rowcount
        return n

    # ------------------------------------------------------------------ checks
    def checks(self, tx, h: Fetched, task_run_id: int) -> list[Check]:
        by = {p: [c.slug for c in h.calls if c.problem and c.problem.startswith(p)]
              for p in ("text_mismatch", "unreadable", "chunks", "turns")}
        rederived = [c for c in h.calls if not c.skipped]
        out = [
            Check("text_matches", not by["text_mismatch"] and not by["unreadable"],
                  ("text differs from the stored hash or the file can't be read: " + _names(by["text_mismatch"] + by["unreadable"]))
                  if by["text_mismatch"] or by["unreadable"]
                  else f"{len(rederived)} transcripts re-derived to their stored hash; {coverage(h)}"),
            Check("chunks_reproduce", not by["chunks"],
                  ("chunks don't reproduce the text or cross a turn: " + _names(by["chunks"])) if by["chunks"]
                  else "every chunk reproduces the text and lies inside one turn"),
            Check("turns_contiguous", not by["turns"],
                  ("turns not contiguous: " + _names(by["turns"])) if by["turns"]
                  else "turns are contiguous, don't overlap and lie inside the text"),
        ]
        # what the database holds after this load: the chunks reproduce the text (the deferred trigger checks again at commit)
        ids = [c.document_id for c in self.done]
        if ids:
            bad = [r[0] for r in tx.execute(
                """select d.id from core.document d
                    where d.id = any(%s) and (select coalesce(sum(char_length(body)), 0) from core.chunk where document_id = d.id)
                          <> d.char_count""", (ids,)).fetchall()]
            out.append(Check("chunks_stored", not bad, f"stored chunks don't add up to the text of documents {bad}" if bad
                             else f"{len(ids)} documents' stored chunks add up to their text"))
        ok = [c for c in h.calls if c.analysis is not None]
        skipped = len(h.calls) - len(rederived)
        scope = f" (of the {len(ok)} calls re-derived in this run; {skipped} skipped)" if skipped else ""
        held_roles = [f"{c.slug} ({c.analysis.unknown_share:.0%}: " + ", ".join(c.analysis.unknown_speakers[:5]) + ")"
                      for c in ok if c.analysis.role_held]
        out.append(Check("unknown_roles", not held_roles,
                         ("speaker roles couldn't be read (above 10% unknown words), extraction held: " + _names(held_roles))
                         if held_roles else "no call above 10% unknown-role words" + scope, blocking=False))
        nohand = [c.slug for c in ok if not c.analysis.handover]
        out.append(Check("no_handover", not nohand,
                         ("no hand-over to questions found, all Q&A: " + _names(nohand)) if nohand
                         else "a hand-over to questions found in every call" + scope, blocking=False))
        out.append(Check("quarter_conflicts", not h.held,
                         ("held on a quarter conflict or with no quarter, not analysed or published: " + _names(h.held))
                         if h.held else "no call held on its quarter", blocking=False))
        out.append(Check("analysis_versions", h.versioned,
                         f"analysis versions are recorded (current version {h.version})" if h.versioned else
                         "migration 0033 isn't applied here: no analysis version is recorded, so every call's file is read "
                         "from storage in every run", blocking=False))
        shares = sorted(round(c.analysis.unknown_share, 2) for c in ok)
        out.append(Check("calls_analysed", True,
                         f"{len(ok)} calls analysed ({sum(c.analysis.handover for c in ok)} with a hand-over); "
                         f"{len(h.publish_events)} calls published with no transcript to analyse; unknown-role shares {shares}; "
                         f"{coverage(h)}", blocking=False))
        return out
