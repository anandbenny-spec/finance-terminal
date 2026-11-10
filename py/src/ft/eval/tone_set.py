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

**The second set, `earnings_tone_2`** (rule version 2; Anand, 10 Nov 2026, 22:24-22:33 IST: the gate's second round, after both
models failed the first set): the same rule with its own seed, leaving out the first set's 30 passages (which raters, models and
Anand had all seen) before the thinning, so a call whose first-set passage was the smallest hash offers its next one. `SETS` holds
each set's seed, rule version and the sets it leaves out; `--set` picks one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

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
# Each set: its seed, its rule version and the earlier sets whose passages it leaves out (`--set`; the default is the first set).
SETS = {
    SET_NAME: {"seed": SEED, "rule_version": RULE_VERSION, "excludes": ()},
    "earnings_tone_2": {"seed": "earnings_tone-2", "rule_version": 2, "excludes": (SET_NAME,)},
}
SETS_DIR = Path(__file__).resolve().parents[3] / "config" / "eval" / "sets"
MAX_NODES = 200_000      # a bound on the search; the data never needs more than a few hundred

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
     and (c.doc || ':' || k.chunk_no) <> all(%(exclude)s::text[])
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
    set_name: str = SET_NAME    # the set the passage is a candidate for (its id's prefix)

    @property
    def stratum(self) -> str:
        return "fell" if self.pat_yoy < -PAT_BAND else "rose" if self.pat_yoy > PAT_BAND else "within"

    @property
    def id(self) -> str:
        return f"{self.set_name}:{self.document_id}-{self.chunk_no}"


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
    """The set: PER_CELL per (stratum, group), one per company, PEER_CAP per peer group, seasons spread. Deterministic.

    The rule's greedy order (scarcest cell first, round-robin; each time the candidate from the least-used season, then the smallest
    hash, that breaks no cap) is tried first. A choice is refused when it would leave a later pick without a company (forward check:
    every cell still has as many companies free as it has picks to make), and a dead end backs up to the last choice that had an
    alternative. So when the plain greedy order succeeds the result is exactly its result; when it doesn't (a scarce stratum shares
    its few companies with the others), the set is the first one in that same order that satisfies every rule.
    """
    cells = [(s, g) for s in STRATA for g in GROUPS]
    pool = {cell: sorted((c for c in cands if (c.stratum, c.group) == cell), key=lambda c: (c.h, c.slug, c.season)) for cell in cells}
    # The scarcest cell (fewest companies to choose from) goes first in every round, since one passage per company is the
    # tightest cap: the order is fixed by the data alone, so the pick stays deterministic.
    cells.sort(key=lambda cell: (len({c.slug for c in pool[cell]}), cell))
    order = [cell for _ in range(PER_CELL) for cell in cells]
    chosen: list[Cand] = []
    companies: set[str] = set()
    peers: Counter = Counter()
    seasons: Counter = Counter()
    nodes = 0
    deepest = 0

    def short(i: int) -> bool:
        """The picks still to make can no longer all be filled: a flow from the cells' remaining picks through the free companies
        (one pick each) and their peer groups (room left under the cap) is smaller than the picks left."""
        left = Counter(order[i:])
        co_cells: dict[str, set] = {}
        co_peer: dict[str, str] = {}
        for cell in left:
            for c in pool[cell]:
                if c.slug not in companies and peers[c.peer_group] < PEER_CAP:
                    co_cells.setdefault(c.slug, set()).add(cell)
                    co_peer[c.slug] = c.peer_group
        room = {pg: PEER_CAP - peers[pg] for pg in set(co_peer.values())}
        owner: dict[str, tuple] = {}                       # company -> the cell currently using it

        def place(cell, seen: set, pg_used: Counter) -> bool:
            for co in sorted(k for k, v in co_cells.items() if cell in v):
                if co in seen:
                    continue
                seen.add(co)
                if co not in owner:
                    if pg_used[co_peer[co]] < room[co_peer[co]]:
                        owner[co] = cell
                        pg_used[co_peer[co]] += 1
                        return True
                elif place(owner[co], seen, pg_used):
                    owner[co] = cell
                    return True
            return False

        used: Counter = Counter()
        for cell, n in left.items():
            for _ in range(n):
                if not place(cell, set(), used):
                    return True
        return False

    def go(i: int) -> bool:
        nonlocal nodes, deepest
        deepest = max(deepest, i)
        if i == len(order):
            return len({c.season for c in chosen}) >= MIN_SEASONS
        nodes += 1
        if nodes > MAX_NODES:
            return False
        cell = order[i]
        ok = [c for c in pool[cell] if c.slug not in companies and peers[c.peer_group] < PEER_CAP]
        for c in sorted(ok, key=lambda c: (seasons[c.season], c.h, c.slug)):
            chosen.append(c)
            companies.add(c.slug)
            peers[c.peer_group] += 1
            seasons[c.season] += 1
            if not short(i + 1) and go(i + 1):
                return True
            chosen.pop()
            companies.discard(c.slug)
            peers[c.peer_group] -= 1
            seasons[c.season] -= 1
        return False

    if not go(0):
        raise NotEnough(f"no set satisfies every rule (reached {deepest} of {len(order)} picks in {nodes} steps; "
                        f"{len({c.season for c in cands})} seasons among {len(cands)} candidates)")
    return sorted(chosen, key=lambda c: c.id)


def item(c: Cand) -> dict:
    return {"id": c.id, "document_id": c.document_id, "chunk_no": c.chunk_no, "section": c.section, "group": c.group,
            "stratum": c.stratum, "slug": c.slug, "season": c.season, "peer_group": c.peer_group}


def payload(chosen: list[Cand], chosen_from: str, set_name: str = SET_NAME) -> dict:
    spec = SETS[set_name]
    out = {"rule_version": spec["rule_version"], "seed": spec["seed"], "chosen_from": chosen_from, "set": set_name,
           "rule": "Spec 02B §5.8; ft/eval/tone_set.py", "items": [item(c) for c in chosen]}
    if spec["excludes"]:
        out["excludes"] = list(spec["excludes"])
    return out


def items_file(set_name: str) -> Path:
    return SETS_DIR / f"{set_name}_items.json"


def excluded(set_name: str) -> list[str]:
    """'document:chunk' of every passage in the sets this set leaves out (their committed items files)."""
    out = []
    for other in SETS[set_name]["excludes"]:
        out += [f"{i['document_id']}:{i['chunk_no']}" for i in json.loads(items_file(other).read_text(encoding="utf-8"))["items"]]
    return sorted(out)


def load(conn, set_name: str = SET_NAME) -> list[Cand]:
    spec = SETS[set_name]
    rows = conn.execute(CANDIDATE_SQL, {"seed": spec["seed"], "min_words": MIN_WORDS, "exclude": excluded(set_name)}).fetchall()
    return [Cand(slug, season, peer, float(pat), doc, no, section, grp, h, set_name)
            for slug, season, peer, pat, doc, no, section, grp, h in rows]


def annotation(title: str, message: str) -> str:
    return f"::notice title={title}::{message.replace('%', '%25').replace(chr(13), '%0D').replace(chr(10), '%0A')}"


def main(argv: list[str] | None = None, *, env=None, out=print) -> int:
    p = argparse.ArgumentParser(prog="python -m ft.eval.tone_set", description=__doc__.split("\n\n")[0])
    p.add_argument("--target", choices=("dev", "production"), default="dev", help="logged; the workflow picks the credentials")
    p.add_argument("--set", choices=sorted(SETS), default=SET_NAME, help="which set to pick (SETS)")
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (for target dev, the workflow reads the DEV_ secrets)")
    from ft.jobs.runner import connect

    with connect(dsn, autocommit=True, role="eval-items") as conn:
        conn.execute("set default_transaction_read_only = on")
        cands = load(conn, args.set)
    try:
        chosen = pick(cands)
    except NotEnough as e:
        out(f"::error title=tone items::not enough candidates: {e}")
        return 1
    body = json.dumps(payload(chosen, f"{args.target}", args.set), ensure_ascii=False, separators=(",", ":"))
    out(annotation("tone items", body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
