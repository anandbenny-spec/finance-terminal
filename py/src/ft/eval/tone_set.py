"""Choosing the 30 passages Anand labels for the tone gate (set `earnings_tone`; Spec 02B §5.8; job 2B-12, #175).

**The rule (version `RULE_VERSION`), fixed before any `call_tone` output is read** (picking by results, not by model output, keeps
the set from being mostly positive, which would make the gate trivial):
- candidates: a management passage as the production prompt sees it (a chunk wholly inside a management turn of the `opening`,
  `qa` or `closing` section, at least `MIN_WORDS` words) of a stored call that isn't role-held (`unknown_role_share` over 0.10)
  and has a consolidated `pat_owners_yoy` for its quarter (2A's metric; a call without the figure is left out);
- **thinned first**: for each call and each group (opening or closing; answers) only the passage with the smallest
  `md5(seed:document:chunk)` stays, so the pick is deterministic and no call crowds the set;
- **strata**: the quarter's PAT to owners fell more than 10% year on year, moved within +-10%, or rose more than 10%; each
  stratum gets 5 opening-or-closing passages and 5 from answers (10 per stratum, 15 and 15 in all, 30 in all);
- at most one passage per company, at most 4 per peer group, and at least 6 of the 8 seasons (fiscal quarters);
- the cells are filled round-robin, five rounds, the cell with the fewest companies first, each time taking its best candidate
  that breaks no cap: first the one from the season least used so far, then the smallest hash.

The items are listed by chunk id only (no transcript text in the repo). Reading is through a read-only session: on GitHub's
runners (`python -m ft.eval.tone_set`, which prints the file as annotations) or through the read-only dev connector with
`CANDIDATE_SQL` (the same query, the same thinning). Writes nothing, calls no model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass

RULE_VERSION = 1
SET_NAME = "earnings_tone"
MIN_WORDS = 40
PAT_BAND = 0.10
STRATA = ("fell", "within", "rose")
GROUPS = ("open_close", "answers")
PER_CELL = 5
PEER_CAP = 4
MIN_SEASONS = 6
SEED = "earnings_tone-1"

CANDIDATE_SQL = """
with calls as (
  select ec.event_id, ec.entity_id, n.slug, ec.fiscal_label, ec.transcript_document_id as doc,
         coalesce(co.peer_group_key, 'none') as peer_group, mv.value as pat_yoy
    from equity.earnings_call ec
    join core.event ev on ev.id = ec.event_id and ev.status = 'stored'
    join core.entity n on n.id = ec.entity_id
    left join equity.company co on co.entity_id = ec.entity_id
    join equity.metric_value mv on mv.entity_id = ec.entity_id and mv.metric_key = 'pat_owners_yoy'
         and mv.basis = 'consolidated' and mv.period_kind = 'quarter' and mv.period_end = ec.period_end
         and mv.value is not null
   where ec.status_reason is null and ec.fiscal_label is not null and ec.transcript_document_id is not null
     and coalesce(ec.unknown_role_share, 0) <= 0.10
), ranked as (
  select c.*, k.chunk_no, t.section,
         case when t.section = 'qa' then 'answers' else 'open_close' end as grp,
         md5(%(seed)s || ':' || c.doc || ':' || k.chunk_no) as h
    from calls c
    join equity.call_turn t on t.document_id = c.doc and t.role = 'management' and t.section in ('opening', 'qa', 'closing')
    join core.chunk k on k.document_id = c.doc and k.char_start >= t.char_start and k.char_end <= t.char_end
   where cardinality(regexp_split_to_array(btrim(k.body), '\\s+')) >= %(min_words)s
)
select slug, fiscal_label, peer_group, pat_yoy, doc, chunk_no, section, grp, h
  from (select r.*, row_number() over (partition by doc, grp order by h) as rn from ranked r) x
 where rn = 1
 order by slug, fiscal_label, grp
"""


@dataclass(frozen=True)
class Cand:
    slug: str
    season: str                 # the fiscal quarter, 'Q2 FY27'
    peer_group: str
    pat_yoy: float
    document_id: int
    chunk_no: int
    section: str                # opening | qa | closing
    group: str                  # open_close | answers
    h: str                      # md5 of seed:document:chunk

    @property
    def stratum(self) -> str:
        return "fell" if self.pat_yoy < -PAT_BAND else "rose" if self.pat_yoy > PAT_BAND else "within"

    @property
    def id(self) -> str:
        return f"{SET_NAME}:{self.document_id}-{self.chunk_no}"


class NotEnough(Exception):
    pass


def rank(seed: str, document_id: int, chunk_no: int) -> str:
    return hashlib.md5(f"{seed}:{document_id}:{chunk_no}".encode()).hexdigest()


def thin(cands: list[Cand]) -> list[Cand]:
    """Per call and group the passage with the smallest hash (what CANDIDATE_SQL does in the database)."""
    best: dict[tuple[int, str], Cand] = {}
    for c in cands:
        k = (c.document_id, c.group)
        if k not in best or c.h < best[k].h:
            best[k] = c
    return list(best.values())


def pick(cands: list[Cand]) -> list[Cand]:
    """The set: PER_CELL per (stratum, group), one per company, PEER_CAP per peer group, seasons spread. Deterministic."""
    cells = [(s, g) for s in STRATA for g in GROUPS]
    pool = {cell: sorted((c for c in cands if (c.stratum, c.group) == cell), key=lambda c: (c.h, c.slug, c.season)) for cell in cells}
    # The scarcest cell (fewest companies to choose from) goes first in every round, since one passage per company is the
    # tightest cap: the order is fixed by the data alone, so the pick stays deterministic.
    cells.sort(key=lambda cell: (len({c.slug for c in pool[cell]}), cell))
    chosen: list[Cand] = []
    companies: set[str] = set()
    peers: Counter = Counter()
    seasons: Counter = Counter()
    for _ in range(PER_CELL):
        for cell in cells:
            ok = [c for c in pool[cell] if c.slug not in companies and peers[c.peer_group] < PEER_CAP]
            if not ok:
                raise NotEnough(f"{cell[0]}/{cell[1]}: {len(chosen)} chosen so far, no candidate left within the caps "
                                f"({len(pool[cell])} in the cell)")
            c = min(ok, key=lambda c: (seasons[c.season], c.h, c.slug))
            chosen.append(c)
            companies.add(c.slug)
            peers[c.peer_group] += 1
            seasons[c.season] += 1
    if len(seasons) < MIN_SEASONS:
        raise NotEnough(f"only {len(seasons)} seasons in the set, {MIN_SEASONS} needed")
    return sorted(chosen, key=lambda c: c.id)


def item(c: Cand) -> dict:
    return {"id": c.id, "document_id": c.document_id, "chunk_no": c.chunk_no, "section": c.section, "group": c.group,
            "stratum": c.stratum, "slug": c.slug, "season": c.season, "peer_group": c.peer_group}


def payload(chosen: list[Cand], chosen_from: str) -> dict:
    return {"rule_version": RULE_VERSION, "seed": SEED, "chosen_from": chosen_from, "set": SET_NAME,
            "rule": "Spec 02B §5.8; ft/eval/tone_set.py", "items": [item(c) for c in chosen]}


def load(conn) -> list[Cand]:
    rows = conn.execute(CANDIDATE_SQL, {"seed": SEED, "min_words": MIN_WORDS}).fetchall()
    return [Cand(slug, season, peer, float(pat), doc, no, section, grp, h)
            for slug, season, peer, pat, doc, no, section, grp, h in rows]


def annotation(title: str, message: str) -> str:
    return f"::notice title={title}::{message.replace('%', '%25').replace(chr(13), '%0D').replace(chr(10), '%0A')}"


def main(argv: list[str] | None = None, *, env=None, out=print) -> int:
    p = argparse.ArgumentParser(prog="python -m ft.eval.tone_set", description=__doc__.split("\n\n")[0])
    p.add_argument("--target", choices=("dev", "production"), default="dev", help="logged; the workflow picks the credentials")
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (for target dev, the workflow reads the DEV_ secrets)")
    from ft.jobs.runner import connect

    with connect(dsn, autocommit=True, role="eval-items") as conn:
        conn.execute("set default_transaction_read_only = on")
        cands = load(conn)
    try:
        chosen = pick(cands)
    except NotEnough as e:
        out(f"::error title=tone items::not enough candidates: {e}")
        return 1
    body = json.dumps(payload(chosen, f"{args.target}"), ensure_ascii=False, separators=(",", ":"))
    out(annotation("tone items", body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
