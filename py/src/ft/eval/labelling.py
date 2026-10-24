"""Choosing the items Anand labels for Pass 2's evaluation sets (Prompt 01 E1; Architecture 03 §1.9; Spec 01 §7).

Three sets, balanced by bank and by easing, holding and tightening (a meeting's phase is its own decision: a cut,
a hold or a hike of the bank's policy rate):

- `policy_hawk_dove`: 30 statements and resolutions, 15 per bank, 5 per phase;
- `policy_stance`: 20 statements and resolutions, 10 per bank (3 easing, 4 holding, 3 tightening), from other
  meetings than the hawk/dove set's where a class has enough (a short class, such as RBI's few hikes since 2019,
  is filled up from the hawk/dove set's meetings of that phase);
- `policy_member_hawk_dove`: 20 MPC members' statements from the minutes (7 easing, 7 holding, 6 tightening), one
  per meeting, spread across the members.

Inside each class the items are spread evenly over time (sorted by decision date, the i-th of n is the one at
(i + ½)/n of the way through), so every era is in the set. The rule is deterministic: the same database gives the
same items. The scoring guide's anchor statements and resolutions (`ANCHORS`, from `config/eval/sets/GUIDE.md`)
are never items, so Anand never labels an example he was shown (a member's statement in an anchor meeting's minutes
is another text, so it may be).

Only 2019→ meetings that took a rate decision and were held, with a published, unsuppressed decision document
(the latest version), are candidates: the 2018 baselines and RBI's special meeting no. 39 are out.

The chosen items live in `config/eval/sets/labelling_items.json` (with a byte-identical copy in
`web/lib/labelling/items.json` for the labelling page; a test keeps them equal). The file's git SHA is
`ops.eval_run.set_version` once the labels are in. This module only reads: run it on GitHub's runners (the
`eval-items` workflow, dev by default; Claude's workspace can't reach the database) and copy the printed items into
the file. It also prints, for each RBI item of `policy_stance`, the resolution's sentences that name a stance, so
Claude can draft that factual half for Anand to check (the job text: "Claude drafts it from the resolutions").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

RULE_VERSION = 1
START = date(2019, 1, 1)
PHASES = ("easing", "holding", "tightening")
BANKS = ("Fed", "RBI")
DECISION_DOC = {"Fed": "fomc_statement", "RBI": "mpc_resolution"}
MIN_STATEMENT_CHARS = 600

# The scoring guide's anchor examples (config/eval/sets/GUIDE.md). Kept out of every set.
ANCHORS = frozenset({
    "fomc-2020-03-15", "fomc-2019-07-31", "fomc-2025-01-29", "fomc-2022-03-16", "fomc-2022-06-15",
    "fomc-2024-09-18", "fomc-2019-01-30", "fomc-2023-07-26",
    "mpc-2020-03-27", "mpc-2025-02-07", "mpc-2025-08-06", "mpc-2023-02-08", "mpc-2022-05-04",
})

HAWK_DOVE = {"easing": 5, "holding": 5, "tightening": 5}          # per bank
STANCE = {"easing": 3, "holding": 4, "tightening": 3}             # per bank
MEMBER = {"easing": 7, "holding": 7, "tightening": 6}             # RBI only

SETS_DIR = Path(__file__).resolve().parents[3] / "config" / "eval" / "sets"
ITEMS_FILE = SETS_DIR / "labelling_items.json"


@dataclass(frozen=True)
class Decision:
    bank: str              # 'Fed' or 'RBI'
    slug: str              # fomc-YYYY-MM-DD / mpc-YYYY-MM-DD
    decision_date: date
    change_bps: int


@dataclass(frozen=True)
class MemberStatement:
    slug: str              # the meeting
    speaker: str           # as stored on the minutes' chunks
    chars: int


class NotEnough(Exception):
    """A class has fewer candidates than the set needs: the set isn't balanced, so nothing is chosen."""


def phase(change_bps: int) -> str:
    return "easing" if change_bps < 0 else "tightening" if change_bps > 0 else "holding"


def spread(items: list, n: int) -> list:
    """n items spread evenly over a date-sorted list (all of them when n equals its length)."""
    if n > len(items):
        raise NotEnough(f"needs {n}, has {len(items)}")
    return [items[int((i + 0.5) * len(items) / n)] for i in range(n)]


def _item(set_name: str, d: Decision, *, speaker: str | None = None) -> dict:
    item = {"id": f"{set_name}:{d.slug}" + (f":{_slug(speaker)}" if speaker else ""), "bank": d.bank,
            "meeting": d.slug, "decision_date": d.decision_date.isoformat(),
            "doc_type": "mpc_minutes" if speaker else DECISION_DOC[d.bank], "phase": phase(d.change_bps)}
    if speaker:
        item["speaker"] = speaker
    return item


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _by_phase(decisions: list[Decision], bank: str, exclude: set[str], *, anchors: bool = True) -> dict[str, list[Decision]]:
    """A bank's candidates by phase, date-sorted. The anchors are the guide's statements and resolutions, so they're
    left out of those sets; a member's statement in the same meeting's minutes is another text (`anchors=False`)."""
    out: dict[str, list[Decision]] = {p: [] for p in PHASES}
    for d in sorted(decisions, key=lambda d: (d.decision_date, d.slug)):
        if (d.bank == bank and d.decision_date >= START and not (anchors and d.slug in ANCHORS)
                and d.slug not in exclude):
            out[phase(d.change_bps)].append(d)
    return out


def _take(classes: dict[str, list[Decision]], counts: dict[str, int], what: str,
          fallback: dict[str, list[Decision]] | None = None) -> list[Decision]:
    """`counts[p]` items of each phase, spread over time. With `fallback`, a class that runs short is filled up
    from the fallback's (also spread), rather than leaving the set unbalanced."""
    picked = []
    for p in PHASES:
        own = classes[p]
        short = max(counts[p] - len(own), 0) if fallback else 0
        try:
            picked += spread(own, counts[p] - short) + (spread(fallback[p], short) if short else [])
        except NotEnough as e:
            raise NotEnough(f"{what}, {p}: {e}") from None
    return sorted(picked, key=lambda d: d.decision_date)


def choose(decisions: list[Decision], statements: list[MemberStatement]) -> dict[str, list[dict]]:
    """The three sets' items, in decision-date order within each bank."""
    hawk_dove, stance = [], []
    for bank in BANKS:
        hd = _take(_by_phase(decisions, bank, set()), HAWK_DOVE, f"policy_hawk_dove {bank}")
        hawk_dove += [_item("policy_hawk_dove", d) for d in hd]
        # Other meetings than the hawk/dove set's where there are enough (RBI has few hikes since 2019); a short
        # class is filled up from the hawk/dove set's meetings: the same text, a different question.
        st = _take(_by_phase(decisions, bank, {d.slug for d in hd}), STANCE, f"policy_stance {bank}",
                   fallback={p: [d for d in hd if phase(d.change_bps) == p] for p in PHASES})
        stance += [_item("policy_stance", d) for d in st]

    # Member statements: meetings whose minutes hold at least one statement long enough to judge, spread by phase;
    # at each (in date order) the member picked least so far, ties broken by a stable hash, not the alphabet.
    eligible: dict[str, list[str]] = {}
    for s in statements:
        if s.chars >= MIN_STATEMENT_CHARS:
            eligible.setdefault(s.slug, []).append(s.speaker)
    rbi = [d for d in decisions if d.bank == "RBI" and d.slug in eligible]
    meetings = _take(_by_phase(rbi, "RBI", set(), anchors=False), MEMBER, "policy_member_hawk_dove")
    used: Counter[str] = Counter()
    members = []
    for d in meetings:
        speaker = min(sorted(set(eligible[d.slug])),
                      key=lambda s: (used[s], hashlib.sha256(f"{d.slug}|{s}".encode()).hexdigest()))
        used[speaker] += 1
        members.append(_item("policy_member_hawk_dove", d, speaker=speaker))
    return {"policy_hawk_dove": hawk_dove, "policy_stance": stance, "policy_member_hawk_dove": members}


# ---------------------------------------------------------------------------------------------- the database

_DECISIONS = """
with latest as (
  select distinct on (d.event_id, d.doc_type) d.event_id, d.id, d.doc_type
    from core.document d join core.source s on s.id = d.source_id
   where d.is_published and not s.is_suppressed and d.doc_type in ('fomc_statement', 'mpc_resolution', 'mpc_minutes')
   order by d.event_id, d.doc_type, d.version_no desc)
select case b.slug when %(fed)s then 'Fed' else 'RBI' end, ev.slug, coalesce(ev.ends_on, ev.starts_on), pr.change_bps,
       dd.id, mn.id
  from policy.meeting m
  join core.event ev on ev.id = m.event_id
  join core.entity b on b.id = ev.entity_id and b.slug in (%(fed)s, %(rbi)s)
  join policy.rate_setting pr on pr.meeting_event_id = m.event_id
   and pr.instrument = case b.slug when %(fed)s then 'fed_target_upper' else 'repo' end
  join latest dd on dd.event_id = ev.id and dd.doc_type = case b.slug when %(fed)s then 'fomc_statement'
                                                                       else 'mpc_resolution' end
  left join latest mn on mn.event_id = ev.id and mn.doc_type = 'mpc_minutes'
 where ev.status = 'held' and m.is_rate_decision and coalesce(ev.ends_on, ev.starts_on) >= %(start)s
 order by 3, 2
"""

_STATEMENTS = """
select document_id, speaker, sum(char_end - char_start)::int from core.chunk
 where document_id = any(%s) and speaker is not null group by 1, 2
"""


def load(conn, *, fed: str = "fed", rbi: str = "rbi") -> tuple[list[Decision], list[MemberStatement], dict[str, int]]:
    """Candidates from the database (as job_writer): decisions, member statements, and each meeting's decision
    document id (for the stance sentences)."""
    rows = conn.execute(_DECISIONS, {"fed": fed, "rbi": rbi, "start": START}).fetchall()
    decisions = [Decision(bank, slug, day, int(bps)) for bank, slug, day, bps, _, _ in rows]
    decision_doc = {slug: doc for _, slug, _, _, doc, _ in rows}
    minutes = {doc: slug for _, slug, _, _, _, doc in rows if doc is not None}
    found = conn.execute(_STATEMENTS, (list(minutes),)).fetchall() if minutes else []
    stmts = [MemberStatement(minutes[doc], speaker, chars) for doc, speaker, chars in found]
    return decisions, stmts, decision_doc


def document_text(conn, document_id: int) -> str:
    return "".join(b for (b,) in conn.execute(
        "select body from core.chunk where document_id = %s order by chunk_no", (document_id,)).fetchall())


_SENTENCE = re.compile(r"[^.!?\n]*\bstance\b[^.!?\n]*[.!?]?", re.IGNORECASE)


def stance_sentences(text: str, limit: int = 3, width: int = 400) -> list[str]:
    """The sentences that name a stance, for drafting RBI's half of `policy_stance` (never published)."""
    out = []
    for m in _SENTENCE.finditer(text):
        s = " ".join(m.group(0).split())
        if s and s not in out:
            out.append(s[:width])
        if len(out) == limit:
            break
    return out


# ---------------------------------------------------------------------------------------------- the CLI

def annotation(title: str, text: str) -> str:
    """One GitHub notice annotation (Claude reads results from annotations: its workspace can't download logs).
    GitHub cuts a message at 4,096 characters, so long text goes through `chunks` first."""
    esc = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::notice title={title}::{esc}"


def chunks(text: str, size: int = 3900) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def main(argv: list[str] | None = None, *, env=None, out=print) -> int:
    p = argparse.ArgumentParser(prog="python -m ft.eval.labelling", description=__doc__.split("\n\n")[0])
    p.add_argument("--target", choices=("dev", "production"), default="dev", help="logged; the workflow picks the credentials")
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (for target dev, the workflow reads the DEV_ secrets)")
    from ft.jobs.runner import connect

    with connect(dsn, autocommit=True, role="eval-items") as conn:
        conn.execute("set default_transaction_read_only = on")
        decisions, stmts, decision_doc = load(conn)
        counts = Counter(f"{d.bank} {phase(d.change_bps)}" for d in decisions)
        out(annotation("labelling candidates", f"target {args.target}; "
                       + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"; member statements {len(stmts)}"))
        try:
            sets = choose(decisions, stmts)
        except NotEnough as e:
            out(f"::error title=labelling items::not enough candidates: {e}")
            return 1
        drafts = {it["id"]: stance_sentences(document_text(conn, decision_doc[it["meeting"]]))
                  for it in sets["policy_stance"] if it["bank"] == "RBI"}
    body = json.dumps({"rule_version": RULE_VERSION, "sets": sets}, ensure_ascii=False, separators=(",", ":"))
    parts = chunks(body)
    for i, part in enumerate(parts, 1):
        out(annotation(f"labelling items {i}/{len(parts)}", part))
    stance = json.dumps(drafts, ensure_ascii=False, separators=(",", ":"))
    parts = chunks(stance)
    for i, part in enumerate(parts, 1):
        out(annotation(f"RBI stance sentences {i}/{len(parts)}", part))
    return 0


if __name__ == "__main__":
    sys.exit(main())
