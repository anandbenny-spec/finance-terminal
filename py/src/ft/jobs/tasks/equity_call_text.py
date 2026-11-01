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

Only the document a call row points at is analysed: a revised version 2B-07 registered (a later version of the same key)
gets no turns and no chunks before 18 Nov (§13.3). A call whose document already has chunks is skipped, so a second run
writes nothing.

Checks, B blocking, W warning:
- (B) `text_matches`: each transcript's text, re-derived from its stored file, has the stored hash;
- (B) `chunks_reproduce`: the chunks reproduce the text exactly and each lies inside one turn (or before the first);
- (B) `turns_contiguous`: turns are contiguous, don't overlap and lie inside the text;
- (W) `unknown_roles`: calls above 10% unknown-role words, named with the speakers whose role couldn't be read;
- (W) `no_handover`: calls with no hand-over to questions found (all `qa`), named;
- (W) `quarter_conflicts`: calls held on a quarter conflict or with no quarter, named (they stay unpublished);
- (W) `calls_analysed`: counts (always passes).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ft.engines.document.chunk import Chunk, chunk_text
from ft.engines.document.store import save_chunks
from ft.equity.calls import pdfs, turns
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

SHOWN = 12


def _names(items: list[str]) -> str:
    return "; ".join(items[:SHOWN]) + (f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@dataclass
class Call:
    event_id: int
    slug: str
    document_id: int
    text: str = ""
    analysis: turns.Analysis | None = None
    chunks: list[Chunk] = field(default_factory=list)
    problem: str | None = None            # why it couldn't be analysed: text_mismatch | chunks | turns | unreadable


@dataclass
class Fetched:
    calls: list[Call] = field(default_factory=list)                 # transcripts to analyse
    publish_events: list[int] = field(default_factory=list)         # calls with no transcript text to analyse
    held: list[str] = field(default_factory=list)


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
        out = Fetched()
        for event_id, slug, reason, doc_id, raw_id, sha, chunked, ev_pub in db.execute(
                """select ec.event_id, ev.slug, ec.status_reason, ec.transcript_document_id, d.raw_file_id, d.text_sha256,
                          d.chunked_at, ev.is_published
                     from equity.earnings_call ec
                     join core.event ev on ev.id = ec.event_id
                     left join core.document d on d.id = ec.transcript_document_id
                    order by ev.slug""").fetchall():
            if reason is not None:
                out.held.append(f"{slug} ({reason})")                 # held: not analysed, not published
                continue
            if doc_id is None:
                if not ev_pub:
                    out.publish_events.append(event_id)               # on_company_website / unreadable: the label or link
                continue
            if chunked is not None:
                continue                                              # already done: a second run writes nothing
            call = Call(event_id, slug, doc_id)
            out.calls.append(call)
            try:
                if raw_id is None or sha is None:
                    raise ValueError("no stored file or text hash")
                info = pdfs.read_pdf(ctx.fetcher.read(raw_id))
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
                call.chunks = chunk_text(ct.extracted, speakers="call", marks=call.analysis.marks)
            except ValueError as e:
                call.problem = "turns" if "turn" in str(e) else "chunks"
                continue
            if not inside_one_turn(call.chunks, call.analysis):
                call.problem = "chunks"
        return out

    # ------------------------------------------------------------------ load
    def load(self, tx, h: Fetched, task_run_id: int) -> int:
        self.done = []
        n = 0
        for call in h.calls:
            if call.problem or call.analysis is None:
                continue                                              # a blocking check fails the run: nothing is kept
            an, doc = call.analysis, call.document_id
            save_chunks(tx, doc, call.text, call.chunks)
            tx.execute("delete from equity.call_turn where document_id = %s", (doc,))
            with tx.cursor() as cur:
                cur.executemany(
                    """insert into equity.call_turn (document_id, turn_no, char_start, char_end, speaker, role, section, words)
                       values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(doc, t.turn_no, t.char_start, t.char_end, t.speaker, t.role, t.section, t.words) for t in an.turns])
            tx.execute("""update equity.earnings_call set unknown_role_share = %s,
                                 words = coalesce(words, %s) where event_id = %s""",
                       (round(an.unknown_share, 4), an.total_words, call.event_id))
            tx.execute("update core.document set is_published = true where id = %s and not is_published", (doc,))
            tx.execute("update core.event set is_published = true, updated_at = now() where id = %s and not is_published",
                       (call.event_id,))
            self.done.append(call)
            n += 1 + len(an.turns) + len(call.chunks)
        for event_id in h.publish_events:
            n += tx.execute("update core.event set is_published = true, updated_at = now() where id = %s and not is_published",
                            (event_id,)).rowcount
        return n

    # ------------------------------------------------------------------ checks
    def checks(self, tx, h: Fetched, task_run_id: int) -> list[Check]:
        by = {p: [c.slug for c in h.calls if c.problem and c.problem.startswith(p)]
              for p in ("text_mismatch", "unreadable", "chunks", "turns")}
        out = [
            Check("text_matches", not by["text_mismatch"] and not by["unreadable"],
                  ("text differs from the stored hash or the file can't be read: " + _names(by["text_mismatch"] + by["unreadable"]))
                  if by["text_mismatch"] or by["unreadable"] else f"{len(h.calls)} transcripts re-derived to their stored hash"),
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
        held_roles = [f"{c.slug} ({c.analysis.unknown_share:.0%}: " + ", ".join(c.analysis.unknown_speakers[:5]) + ")"
                      for c in ok if c.analysis.role_held]
        out.append(Check("unknown_roles", not held_roles,
                         ("speaker roles couldn't be read (above 10% unknown words), extraction held: " + _names(held_roles))
                         if held_roles else "no call above 10% unknown-role words", blocking=False))
        nohand = [c.slug for c in ok if not c.analysis.handover]
        out.append(Check("no_handover", not nohand,
                         ("no hand-over to questions found, all Q&A: " + _names(nohand)) if nohand
                         else "a hand-over to questions found in every call", blocking=False))
        out.append(Check("quarter_conflicts", not h.held,
                         ("held on a quarter conflict or with no quarter, not analysed or published: " + _names(h.held))
                         if h.held else "no call held on its quarter", blocking=False))
        shares = sorted(round(c.analysis.unknown_share, 2) for c in ok)
        out.append(Check("calls_analysed", True,
                         f"{len(ok)} calls analysed ({sum(c.analysis.handover for c in ok)} with a hand-over); "
                         f"{len(h.publish_events)} calls published with no transcript to analyse; unknown-role shares {shares}",
                         blocking=False))
        return out
