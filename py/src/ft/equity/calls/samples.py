"""Drawing the gate samples for #178 (2B-15) by code, with recorded seeds (Spec 02B §5.11, §13.8, §13.11 L12; Spec 02B.1 C7;
Plan 04b P11, P13).

What this holds, and what it never holds:

- the **rules** for each sample (sizes, strata, caps), the **seeds**, and the draw itself, a pure function of the candidate
  pool and the seed: the same pool and seed give the same items, whatever order the pool arrives in;
- the **queries** that read the candidate pool (ids and strata only), the queries that fetch an item's fields and quote for
  the session with Anand, and the one that checks every transcript's page map;
- the SQL that records Anand's verdicts in `ops.eval_run` and `ops.eval_result`.

It holds **no transcript text and no sample**: the items it chooses are ids, printed as annotations by the `gate-samples`
workflow (dev only, a read-only session). Quotes are fetched through the read-only dev connector into the chat and nowhere
else (§13.11 L12). A re-draw after a failed gate uses a **new seed** and is a new sample (§9.4); never re-judge a sample.

The samples. Sitting 1 (7 Nov 2026; run with `--gate all`):

| gate | pool | size | rules |
|---|---|---|---|
| `targets` | management targets (`speaker_role = 'management'`) of published extraction outputs, duplicates out | 40 | cells: range 10+2, point 8+2, floor or ceiling 6+2, direction 8+2 (scored + not scored: 8 not scored in all); at most 2 a company; at least 8 from role-held calls; metric families: P&L 8, margins 8, capex and balance sheet 5, banks 3 (as available) |
| `unattributed` | separate-list targets (`speaker_role = 'unknown'`), duplicates out | 20 | at most 2 a company; at least 3 from calls with no management turn, where such calls have items (Spec 02B.1 C7) |
| `stated` | stated figures of published extraction outputs, duplicates out | 20 | at most 2 a company; families: P&L 6, margins 4, capex and balance sheet 3, banks 4 (as available) |
| `links` | verified transcript citations into a document with a page map | 5 | different documents and companies; 2 from cited summary bullets, 3 from extracted figures |

Sitting 2 (8 Nov 2026; Plan 04b P9 to P11; run with `--gate sitting2`):

| gate | pool | size | rules |
|---|---|---|---|
| `summaries` | published `call_summary` outputs of the final prompt version (`SUMMARY_VERSION`) | 10 | at most 1 a company and 2 a peer group; at least 3 from role-held calls; at least 1 from a call with no management turn, where one is stored |
| `recall` | stored calls whose roles were read (`unknown_role_share` at most 0.10) and whose extraction is published | 5, then up to 3 more | the 5: one each from IT services, banks, consumer, industrials or autos, and energy or materials (`RECALL_BUCKETS`), at most 1 a company; the extra 3 come from any sector in the same seeded order, one a company, and are read only while the settled targets are under 15 (Spec 02B §5.11 row 2) |
| `scoring` | scored outcomes (`delivered` or `missed`) of management targets | 20 | at most 2 a company; at least 3 of each type (range, point, floor, ceiling, direction) where the pool has them; at least 3 bank metrics; all of them if the pool is smaller (Spec 02B §5.11 row 3) |

A minimum or a cell the pool can't fill is reported as a **shortfall**, never filled by loosening another rule. A minimum marked
"where present" (the scoring types, the summaries' no-management-turn call) is first cut to what the pool holds, so it isn't a
shortfall when the pool has none; the bank metrics are not marked, so a pool with fewer than 3 is reported.

Decided by Claude (standing delegation, 24 Oct 2026), each with its alternative:
- Metric families (the spec names four, not their members): P&L = revenue, revenue_growth, pat, pat_growth; margins =
  ebitda_margin, ebit_margin, tax_rate; capex and balance sheet = capex, net_debt; banks = advances_growth, deposits_growth,
  gnpa, nnpa, cet1. Alternative: tax_rate under P&L.
- Eight not-scored targets, two in each type group (§5.11: "about 8"). Drawn from every not-scored target, so most are
  metric `other` (76% of the pool), which is what the pool holds.
- Seeds are the sitting's date and the gate's number (2026110701 to 2026110704), fixed before any item is read.
- Rule version 2 (7 Nov 2026, before any item was shown to Anand). Version 1's first draw on dev came back short for two
  reasons that were the script's, not the pool's: a summary bullet's company was read from `core.ai_output.entity_id`, which
  is null for every summary, so the one-a-company cap allowed one summary link; and the greedy fill let common cells use up
  the company slots of the five companies that hold every scored floor, leaving that cell empty. Version 2 reads a summary's
  company from its document and fills the scarcest cell first, preferring items that help a minimum still unmet; if a draw
  is still short it tries the next of at most 300 attempts (each its own seeded order) and keeps the first with no shortfall,
  else the one missing least. The attempt number is recorded with the seed, so the draw can be repeated. No rule was loosened.

Sitting 2, decided by Claude (standing delegation, 24 Oct 2026), each with its alternative; rule version 3 (8 Nov 2026, before any
item was drawn or read):
- The summary gate draws from **published prompt-v11 outputs** (v6 for the first draw of sitting 2, v7 for its re-draw, v8 for the third, v9 for the
  fourth, before v9 and v11 existed; v10, the Sonnet round, was drawn from its own outputs; v11 is v9's text plus the Fable review's picks, written by
  Opus 5.5 at effort high, the Opus round of 10 Nov, and its draw is judged by a fresh Fable session, never Opus), not
  re-checked v5 ones: #301's 5 Nov note said v5, but Anand chose the structured route (option 1, 6 Nov), whose outputs are v6 and
  from 8 Nov v7 (v6 plus a rule on guidance verbs, after the first draw failed on "reiterated") and v8 (v7 plus four rules and a check on
  firm verbs, after the re-draw failed on a hedge and a partial cause) and v9 (v8 plus three rules and a check on extent words, after the
  v8 draw failed on a tense, an exclusion and a dropped extent word), and #301's report says the gate reads them. One published v5 summary stays out of the pool (Shriram Finance Q2 FY27). Alternative: the v5 outputs, which the
  final version replaced.
- At most 2 summaries a peer group (the spec says "across peer groups" and gives no number). Alternative: no cap.
- Recall's five sector buckets map peer groups as `RECALL_BUCKETS` says (the spec names the sectors, not their members); the
  other peer groups (pharma, NBFCs, hospitals, insurers, capital markets, telecom) only supply the extra calls. Alternative: the
  Nifty 50's own industry field.
- At most 2 scored outcomes a company, as the other samples (the spec sets no cap). Alternative: none.
- Seeds are the sitting's date and the gate's number (2026110801 to 2026110803), fixed before any item is read.

The prompt round of #511 (2B-15d, 13 Nov 2026; `--round`, rule version 4, decided by Claude under the standing delegation of 24 Oct
2026 and posted on #511 with the seeds before any run). The four samples of a fresh draw (recall, targets, scoring and stated, with
sitting 1's and 2's rules unchanged) come from the round's dev re-run only (`ROUND_VERSION`): the targets, stated figures and
scored outcomes of its published outputs, and the recall calls it re-ran (an output of that version in any status, so a call
where the round found nothing still counts against it). **No call of set A or set B is drawn, and no other call of their
companies** (`ROUND_EXCLUDED_EVENTS`): #511's card excludes the calls, and excluding the companies too only tightens, since the
prompt was written after reading those companies' misses (the alternative: the ten calls only). The recall comparison reads the
round's published rows only (`recall_extraction_sql(..., prompt_version=ROUND_VERSION)`), never a superseded output's.

The v6 round of #533 (2B-15f, 14 Nov 2026; `--round 2b15f`, rule version 5; posted on #533 with its seeds before any run). The same
four samples with the same rules, from the v6 dev re-run, on new seeds. **What is left out (Anand, 14 Nov 2026, "Strict recall, calls
out"):** #533's card left out every company with a judged call or item, which leaves 7 companies with 1 scorable target and 1 scored
outcome on dev, so nothing could be drawn. Instead:
- **recall** keeps the card's rule: no call of a company that had a call read in full for a recall gate (#178's sets A and B, #511's
  five) or an item judged in #511 (its 50 `equity.guidance` ids and 20 `equity.stated_figure` ids). No IT-services company and no
  bank is left, so two peer groups stand in (`ROUND_F_STAND_INS`): life insurance for banks (the closest: financials), and power
  for IT services (none of the six companies left is close to IT services; energy and materials is the bucket with the most
  companies left, so one of its peer groups gives the fifth call);
- **targets, scoring and stated figures** leave out the judged calls only: a call read for a recall gate, or one that held a
  judged item. Another call of the same company can be drawn.
The lists are computed by the pool queries from the judged ids (`ROUND_F_CALLS`, `ROUND_F_GUIDANCE`, `ROUND_F_STATED`); the rows of a
superseded output stay, so a judged v5 row still names its call and its company after the v6 re-run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

RULE_VERSION = 3                # 2: sitting 1's final rules; 3: adds sitting 2's gates and the "where present" minimums
ATTEMPTS = 300                  # a seeded retry limit, see `draw`
SUMMARY_VERSION = "v11"         # the prompt version the summary gate judges (#301, option 1; v7 to v11 from #178; v11 is the Opus round, on this branch only until the gate passes)
RECALL_EXTRA = 3                # calls after the five, read only while the settled targets are under 15
SEEDS = {"targets": 2026110701, "unattributed": 2026110702, "stated": 2026110703, "links": 2026110704,
         "summaries": 2026110801, "recall": 2026110802, "scoring": 2026110803}
GATES = ("targets", "unattributed", "stated", "links")           # sitting 1: `--gate all`
GATES_2 = ("summaries", "recall", "scoring")                     # sitting 2: `--gate sitting2`
ALL_GATES = GATES + GATES_2
SITTING_OF = {**dict.fromkeys(GATES, 1), **dict.fromkeys(GATES_2, 2)}

# #511's prompt round (module notes): a fresh draw from the round's dev re-run, its own seeds, sets A and B's companies left out.
ROUND = "2b15d"
ROUND_RULE_VERSION = 4
ROUND_VERSION = "v5"            # the round's prompt version (equity_calls_ai.GUIDANCE_RERUN_VERSION)
ROUND_GATES = ("recall", "targets", "scoring", "stated")
ROUND_SEEDS = {"recall": 2026111301, "targets": 2026111302, "scoring": 2026111303, "stated": 2026111304}
ROUND_SET_A = (3743, 3780, 4002, 3855, 3863)     # #178's 8 Nov development calls (call event ids)
ROUND_SET_B = (3689, 3850, 4003, 4011, 3714)     # #178's 12 Nov gate draw, on whose misses the round's prompt was written
ROUND_EXCLUDED_EVENTS = ROUND_SET_A + ROUND_SET_B

# #533's v6 round (module notes): the judged ids its pools leave out, and recall's two stand-in peer groups.
ROUND_F = "2b15f"
ROUND_F_RULE_VERSION = 5
ROUND_F_VERSION = "v6"          # equity_calls_ai.GUIDANCE_RERUN_VERSION
ROUND_F_SEEDS = {"recall": 2026111401, "targets": 2026111402, "scoring": 2026111403, "stated": 2026111404}
ROUND_F_CALLS = ROUND_SET_A + ROUND_SET_B + (3945, 3952, 3933, 3886, 3716)       # read in full for a recall gate (call event ids)
ROUND_F_GUIDANCE = (                                                             # #511's judged extraction and scoring rows
    2418, 3459, 3452, 2258, 2563, 3809, 2371, 4162, 2209, 1861, 1844, 4401, 3240, 4295, 3334, 2864, 3734, 4063, 3423, 2691,
    2042, 3846, 2055, 3842, 2099, 3422, 4015, 2056, 3318, 2796, 3915, 3927, 4170, 3238, 3914, 4445, 2083, 3299, 2186, 1920,
    4010, 2302, 3122, 2150, 2136, 3156, 4048, 2015, 3836, 2685)
ROUND_F_STATED = (1881, 1525, 2347, 1982, 1631, 1798, 2372, 2275, 2361, 1935, 2321, 2400, 2075, 1446, 1451, 1505, 1682, 2253,
                  2274, 1532)                                                    # #511's judged stated figures
ROUND_F_STAND_INS = {"life-insurance": "banks", "power": "it_services"}          # peer group -> the recall bucket it stands in for
ROUNDS = (ROUND, ROUND_F)

# Spec 02B §5.11 row 2 names five sectors; the peer groups that stand for each are a decision of the sampler (see the module notes).
RECALL_BUCKETS = {
    "it_services": ("it-services",),
    "banks": ("banks",),
    "consumer": ("fmcg", "consumer-retail"),
    "industrials_autos": ("autos", "engineering-defence", "transport"),
    "energy_materials": ("energy-fuels", "power", "metals-mining", "steel", "building-materials"),
}
_BUCKET_OF = {peer: bucket for bucket, peers in RECALL_BUCKETS.items() for peer in peers}

FAMILY = {
    "revenue": "pnl", "revenue_growth": "pnl", "pat": "pnl", "pat_growth": "pnl",
    "ebitda_margin": "margins", "ebit_margin": "margins", "tax_rate": "margins",
    "capex": "capex_balance", "net_debt": "capex_balance",
    "advances_growth": "banks", "deposits_growth": "banks", "gnpa": "banks", "nnpa": "banks", "cet1": "banks",
}
KIND_GROUP = {"range": "range", "point": "point", "floor": "floor_ceiling", "ceiling": "floor_ceiling", "direction": "direction"}


def family_of(metric: str) -> str:
    return FAMILY.get(metric, "other")


def bucket_of(peer_group: str) -> str:
    """Recall's sector bucket for a peer group, or '' (such a call can only be one of the extras)."""
    return _BUCKET_OF.get(peer_group, "")


@dataclass(frozen=True)
class Item:
    """One candidate: an id and the strata the rules use. No text."""
    id: int | str
    company: int
    group: str = ""             # targets: the type group
    family: str = ""
    not_scored: bool = False
    role_held: bool = False     # the call's unknown-role share is above `turns.ROLE_HOLD_SHARE` (0.10, in POOL_TARGETS)
    no_mgmt_turn: bool = False  # the call has no management turn at all
    source: str = ""            # links: 'summary' or 'figure'
    document: int = 0
    peer: str = ""              # summaries: the company's peer group
    bucket: str = ""            # recall: the sector bucket of the peer group
    kind: str = ""              # scoring: range, point, floor, ceiling or direction
    counted: bool = False       # scoring: in the hit rate (reported with the strata, not a rule)


@dataclass(frozen=True)
class Rules:
    total: int
    per_company: int
    cell: Callable[[Item], str] = lambda it: ""
    quotas: dict[str, int] = field(default_factory=dict)       # cell -> slots; empty means no cells
    minimums: tuple[tuple[str, Callable[[Item], bool], int], ...] = ()    # (name, test, at least), filled in this order
    per_peer: int = 0                                          # at most this many from one peer group; 0 means no cap
    where_present: frozenset[str] = frozenset()                # minimums cut to what the pool holds ("where present")


def _target_cell(it: Item) -> str:
    return f"{it.group}/{'ns' if it.not_scored else 'sc'}"


RULES = {
    "targets": Rules(
        total=40, per_company=2, cell=_target_cell,
        quotas={"range/sc": 10, "range/ns": 2, "point/sc": 8, "point/ns": 2,
                "floor_ceiling/sc": 6, "floor_ceiling/ns": 2, "direction/sc": 8, "direction/ns": 2},
        minimums=(("role_held", lambda it: it.role_held, 8),
                  ("pnl", lambda it: it.family == "pnl", 8), ("margins", lambda it: it.family == "margins", 8),
                  ("capex_balance", lambda it: it.family == "capex_balance", 5), ("banks", lambda it: it.family == "banks", 3))),
    "unattributed": Rules(
        total=20, per_company=2,
        minimums=(("no_mgmt_turn", lambda it: it.no_mgmt_turn, 3),)),
    "stated": Rules(
        total=20, per_company=2,
        minimums=(("pnl", lambda it: it.family == "pnl", 6), ("margins", lambda it: it.family == "margins", 4),
                  ("capex_balance", lambda it: it.family == "capex_balance", 3), ("banks", lambda it: it.family == "banks", 4))),
    "links": Rules(
        total=5, per_company=1, cell=lambda it: it.source, quotas={"summary": 2, "figure": 3}),
    # sitting 2
    "summaries": Rules(
        total=10, per_company=1, per_peer=2,
        minimums=(("role_held", lambda it: it.role_held, 3), ("no_mgmt_turn", lambda it: it.no_mgmt_turn, 1)),
        where_present=frozenset({"no_mgmt_turn"})),
    "recall": Rules(
        total=5, per_company=1, cell=lambda it: it.bucket, quotas=dict.fromkeys(RECALL_BUCKETS, 1)),
    "scoring": Rules(
        total=20, per_company=2,
        # filled in this order, the scarcest first, so the commonest types can't use up a company's two slots before it
        minimums=(("banks", lambda it: it.family == "banks", 3),)
                 + tuple((k, (lambda it, k=k: it.kind == k), 3) for k in ("ceiling", "floor", "direction", "point", "range")),
        where_present=frozenset({"range", "point", "floor", "ceiling", "direction"})),
}


def rank(seed: int, gate: str, item_id: int | str) -> str:
    """The seeded order key: a hash, so it doesn't depend on Python's hash seed or on the pool's order."""
    return hashlib.sha256(f"{seed}:{gate}:{item_id}".encode()).hexdigest()


@dataclass
class Draw:
    gate: str
    seed: int
    items: list[Item]                   # in seeded order, so a batch is a mix of strata
    pool: int
    shortfalls: list[str]
    attempt: int = 0                    # which seeded order gave this draw (0 is the plain one)
    extra: list[Item] = field(default_factory=list)     # recall: the calls after the five, in the order they're read

    def strata(self) -> dict[str, int]:
        rules = RULES[self.gate]
        c: Counter[str] = Counter()
        for it in self.items:
            if rules.quotas:
                c[rules.cell(it)] += 1
            for name, test, _ in rules.minimums:
                if test(it):
                    c[name] += 1
            if self.gate == "scoring" and it.counted:
                c["counted"] += 1
        c["companies"] = len({it.company for it in [*self.items, *self.extra]})
        if self.gate == "recall":
            c["extra"] = len(self.extra)
        if self.gate == "summaries":
            c["peer_groups"] = len({it.peer for it in self.items})
        return dict(sorted(c.items()))


def _cell_order(pool: list[Item], rules: Rules) -> list[str]:
    """Scarcest cell first: the fewest companies in the pool for its slots, so common cells can't use up its companies."""
    if not rules.quotas:
        return [""]
    companies: dict[str, set[int]] = {c: set() for c in rules.quotas}
    for it in pool:
        c = rules.cell(it)
        if c in companies:
            companies[c].add(it.company)
    return sorted(rules.quotas, key=lambda c: (len(companies[c]) / rules.quotas[c], c))


def _once(order: list[Item], rules: Rules, cell_order: list[str]) -> tuple[list[Item], list[str], int]:
    """One greedy pass over a seeded order: (items, shortfalls, how many slots are missing). Without cells, each minimum is
    filled first in the order the rules list them, then the rest. With cells, each cell in turn, scarcest first, first with
    the items that help a minimum still unmet, then with any. An item is taken only while its company is under the cap and its
    cell has room."""
    chosen: list[Item] = []
    per_company: Counter[int] = Counter()
    per_peer: Counter[str] = Counter()
    cells: Counter[str] = Counter()
    mins: Counter[str] = Counter()
    taken: set[int | str] = set()

    def can(it: Item) -> bool:
        if len(chosen) >= rules.total or per_company[it.company] >= rules.per_company:
            return False
        if rules.per_peer and per_peer[it.peer] >= rules.per_peer:
            return False
        if rules.quotas:
            c = rules.cell(it)
            return c in rules.quotas and cells[c] < rules.quotas[c]
        return True

    def take(it: Item) -> None:
        chosen.append(it)
        taken.add(it.id)
        per_company[it.company] += 1
        per_peer[it.peer] += 1
        cells[rules.cell(it)] += 1
        for name, test, _ in rules.minimums:
            if test(it):
                mins[name] += 1

    def helps(it: Item) -> bool:
        return any(test(it) and mins[name] < at_least for name, test, at_least in rules.minimums)

    if not rules.quotas:
        for name, test, at_least in rules.minimums:
            for it in order:
                if mins[name] >= at_least:
                    break
                if it.id not in taken and test(it) and can(it):
                    take(it)
        for it in order:
            if len(chosen) >= rules.total:
                break
            if it.id not in taken and can(it):
                take(it)
    else:
        for c in cell_order:
            for only_helpers in (True, False):
                for it in order:
                    if cells[c] >= rules.quotas[c] or len(chosen) >= rules.total:
                        break
                    if it.id in taken or rules.cell(it) != c or not can(it) or (only_helpers and not helps(it)):
                        continue
                    take(it)

    short = [f"{name}: {mins[name]} of {at_least}" for name, _t, at_least in rules.minimums if mins[name] < at_least]
    short += [f"cell {c}: {cells[c]} of {q}" for c, q in rules.quotas.items() if cells[c] < q]
    if len(chosen) < rules.total:
        short.append(f"total: {len(chosen)} of {rules.total}")
    missing = (sum(at_least - mins[name] for name, _t, at_least in rules.minimums if mins[name] < at_least)
               + sum(q - cells[c] for c, q in rules.quotas.items() if cells[c] < q)
               + (rules.total - len(chosen)))
    return chosen, short, missing


def _effective(rules: Rules, pool: list[Item]) -> Rules:
    """The rules with each "where present" minimum cut to what the pool can give (never more than the per-company cap allows),
    so a type the pool doesn't hold isn't a shortfall. Every other minimum is left as written."""
    if not rules.where_present:
        return rules
    mins = []
    for name, test, at_least in rules.minimums:
        if name in rules.where_present:
            have = [it for it in pool if test(it)]
            at_least = min(at_least, len(have), rules.per_company * len({it.company for it in have}))
        mins.append((name, test, at_least))
    return replace(rules, minimums=tuple(mins))


def draw(items: Iterable[Item], gate: str, seed: int | None = None) -> Draw:
    """Seeded: the same pool and seed give the same items. Attempt 0 orders the pool by `rank(seed, gate, id)`; if it comes
    back short, attempt k tries the order of `rank(seed, "gate#k", id)`, up to ATTEMPTS, and the first with no shortfall wins
    (else the one missing least). What no attempt can meet is reported, not relaxed."""
    seed = SEEDS[gate] if seed is None else seed
    pool = sorted(items, key=lambda it: (str(it.id),))
    rules = _effective(RULES[gate], pool)
    cell_order = _cell_order(pool, rules)
    best: tuple[int, int, list[Item], list[str]] | None = None
    for attempt in range(ATTEMPTS):
        key = gate if attempt == 0 else f"{gate}#{attempt}"
        order = sorted(pool, key=lambda it: (rank(seed, key, it.id), str(it.id)))
        chosen, short, missing = _once(order, rules, cell_order)
        if best is None or missing < best[0]:
            best = (missing, attempt, chosen, short)
        if missing == 0:
            break
    assert best is not None
    _missing, attempt, chosen, short = best
    chosen.sort(key=lambda it: (rank(seed, gate, it.id), str(it.id)))
    return Draw(gate, seed, chosen, len(pool), short, attempt)


def draw_recall(items: Iterable[Item], seed: int | None = None) -> Draw:
    """Recall's calls (Spec 02B §5.11 row 2): the five, one from each sector bucket (a `draw`), then up to RECALL_EXTRA more, in
    the order of `rank(seed, "recall#extra", id)`, from any sector and any company not already drawn, one a company. The extras
    are read only while the settled targets of the calls read so far are under 15, in this order, up to eight calls in all."""
    pool = list(items)
    seed = SEEDS["recall"] if seed is None else seed
    d = draw(pool, "recall", seed)
    used = {it.company for it in d.items}
    for it in sorted((x for x in pool if x.company not in used), key=lambda x: (rank(seed, "recall#extra", x.id), str(x.id))):
        if len(d.extra) >= RECALL_EXTRA:
            break
        if it.company not in {x.company for x in d.extra}:
            d.extra.append(it)
    if len(d.extra) < RECALL_EXTRA:
        d.shortfalls.append(f"extra: {len(d.extra)} of {RECALL_EXTRA}")
    return d


# ---------------------------------------------------------------------------------------------- the candidate pools
# Read-only; ids and strata, never text. A published output is what a visitor would see once its field policy is flipped;
# duplicates are left out, as `api.call_figures` leaves them out.

POOL_TARGETS = """
select g.id, g.entity_id, g.kind, g.metric, g.not_scored_reason is not null,
       coalesce(ec.unknown_role_share > 0.10, false),
       not exists (select 1 from equity.call_turn t where t.document_id = g.document_id and t.role = 'management'),
       g.document_id
from equity.guidance g
join core.ai_output o on o.id = g.ai_output_id and o.status = 'published' and o.task = 'guidance_extraction'
join equity.earnings_call ec on ec.event_id = g.call_event_id
where g.speaker_role = %s and g.duplicate_of is null
order by g.id"""

POOL_STATED = """
select s.id, s.entity_id, s.metric, s.document_id
from equity.stated_figure s
join core.ai_output o on o.id = s.ai_output_id and o.status = 'published' and o.task = 'guidance_extraction'
where s.duplicate_of is null
order by s.id"""

POOL_LINKS = """
with cit as (
  select c.ai_output_id, c.citation_no, c.document_id, c.char_start, sd.entity_id, 'summary' as source
  from core.ai_citation c
  join core.ai_output o on o.id = c.ai_output_id and o.status = 'published' and o.task = 'call_summary'
  join core.document sd on sd.id = c.document_id
  where c.verified and c.kind = 'document_span' and sd.entity_id is not null
  union all
  select g.ai_output_id, g.citation_no, g.document_id, c.char_start, g.entity_id, 'figure'
  from equity.guidance g
  join core.ai_output o on o.id = g.ai_output_id and o.status = 'published' and o.task = 'guidance_extraction'
  join core.ai_citation c on c.ai_output_id = g.ai_output_id and c.citation_no = g.citation_no and c.verified
  where g.duplicate_of is null and g.speaker_role = 'management'
  union all
  select s.ai_output_id, s.citation_no, s.document_id, c.char_start, s.entity_id, 'figure'
  from equity.stated_figure s
  join core.ai_output o on o.id = s.ai_output_id and o.status = 'published' and o.task = 'guidance_extraction'
  join core.ai_citation c on c.ai_output_id = s.ai_output_id and c.citation_no = s.citation_no and c.verified
  where s.duplicate_of is null
)
select cit.ai_output_id::text || ':' || cit.citation_no, cit.entity_id, cit.source, cit.document_id
from cit
join core.document d on d.id = cit.document_id and d.source_url is not null
join equity.transcript_page p on p.document_id = cit.document_id
                             and cit.char_start >= p.char_start and cit.char_start < p.char_end
order by 1"""


# Sitting 2. A summary's company comes from its document (`core.ai_output.entity_id` is null for every summary: rule version 2).
POOL_SUMMARIES = """
select o.id, d.entity_id, coalesce(ec.unknown_role_share > 0.10, false),
       not exists (select 1 from equity.call_turn t where t.document_id = o.document_id and t.role = 'management'),
       c.peer_group_key, o.document_id
from core.ai_output o
join core.document d on d.id = o.document_id
join equity.earnings_call ec on ec.transcript_document_id = o.document_id
join equity.company c on c.entity_id = d.entity_id
where o.task = 'call_summary' and o.status = 'published' and o.prompt_version = %s
order by o.id"""

# Calls whose roles were read, so the extraction's management list is the one the reader's list is compared with.
POOL_RECALL = """
select ec.event_id, ec.entity_id, c.peer_group_key, ec.transcript_document_id
from equity.earnings_call ec
join equity.company c on c.entity_id = ec.entity_id
where ec.transcript_document_id is not null and ec.unknown_role_share <= 0.10
  and exists (select 1 from core.ai_output o where o.document_id = ec.transcript_document_id
                and o.task = 'guidance_extraction' and o.status = 'published')
order by ec.event_id"""

POOL_SCORING = """
select o.guidance_id, g.entity_id, g.kind, g.metric, g.document_id, o.counted
from equity.guidance_outcome o
join equity.guidance g on g.id = o.guidance_id
join core.ai_output ao on ao.id = g.ai_output_id and ao.status = 'published' and ao.task = 'guidance_extraction'
where o.outcome in ('delivered', 'missed') and o.speaker_role = 'management' and g.duplicate_of is null
order by o.guidance_id"""


# #511's round: the same pools, limited to the round's re-run and with sets A and B's companies left out. Parameters, in order: the
# pool's own (targets: the role), then the round's prompt version, then the excluded call events.
_ROUND_COMPANIES = "(select ecx.entity_id from equity.earnings_call ecx where ecx.event_id = any(%s))"

POOL_TARGETS_ROUND = POOL_TARGETS.replace(
    "where g.speaker_role = %s and g.duplicate_of is null",
    f"where g.speaker_role = %s and g.duplicate_of is null and o.prompt_version = %s and g.entity_id not in {_ROUND_COMPANIES}")

POOL_STATED_ROUND = POOL_STATED.replace(
    "where s.duplicate_of is null",
    f"where s.duplicate_of is null and o.prompt_version = %s and s.entity_id not in {_ROUND_COMPANIES}")

POOL_SCORING_ROUND = POOL_SCORING.replace(
    "where o.outcome in ('delivered', 'missed') and o.speaker_role = 'management' and g.duplicate_of is null",
    "where o.outcome in ('delivered', 'missed') and o.speaker_role = 'management' and g.duplicate_of is null"
    f" and ao.prompt_version = %s and g.entity_id not in {_ROUND_COMPANIES}")

POOL_RECALL_ROUND = POOL_RECALL.replace(
    "order by ec.event_id",
    """  and exists (select 1 from core.ai_output r where r.document_id = ec.transcript_document_id
                and r.task = 'guidance_extraction' and r.prompt_version = %s)
  and ec.entity_id not in """ + _ROUND_COMPANIES + """
order by ec.event_id""")


# #533's round: the pools of the v6 re-run. Parameters, in order: the pool's own (targets: the role), the prompt version, then the
# judged call events, guidance ids and stated-figure ids. Recall leaves out the judged companies; the others the judged calls.
_JUDGED = """(select ecx.{call} from equity.earnings_call ecx where ecx.event_id = any(%s)
        union select gx.{item} from equity.guidance gx where gx.id = any(%s)
        union select sx.{item} from equity.stated_figure sx where sx.id = any(%s))"""
_JUDGED_CALLS = _JUDGED.format(call="event_id", item="call_event_id")
_JUDGED_COMPANIES = _JUDGED.format(call="entity_id", item="entity_id")

POOL_TARGETS_ROUND_F = POOL_TARGETS.replace(
    "where g.speaker_role = %s and g.duplicate_of is null",
    f"where g.speaker_role = %s and g.duplicate_of is null and o.prompt_version = %s and g.call_event_id not in {_JUDGED_CALLS}")

POOL_STATED_ROUND_F = POOL_STATED.replace(
    "where s.duplicate_of is null",
    f"where s.duplicate_of is null and o.prompt_version = %s and s.call_event_id not in {_JUDGED_CALLS}")

POOL_SCORING_ROUND_F = POOL_SCORING.replace(
    "where o.outcome in ('delivered', 'missed') and o.speaker_role = 'management' and g.duplicate_of is null",
    "where o.outcome in ('delivered', 'missed') and o.speaker_role = 'management' and g.duplicate_of is null"
    f" and ao.prompt_version = %s and g.call_event_id not in {_JUDGED_CALLS}")

POOL_RECALL_ROUND_F = POOL_RECALL.replace(
    "order by ec.event_id",
    """  and exists (select 1 from core.ai_output r where r.document_id = ec.transcript_document_id
                and r.task = 'guidance_extraction' and r.prompt_version = %s)
  and ec.entity_id not in """ + _JUDGED_COMPANIES + """
order by ec.event_id""")
assert all(a != b for a, b in ((POOL_TARGETS_ROUND_F, POOL_TARGETS), (POOL_STATED_ROUND_F, POOL_STATED),
                               (POOL_SCORING_ROUND_F, POOL_SCORING), (POOL_RECALL_ROUND_F, POOL_RECALL)))    # each replace took


def read_pool(conn, gate: str, *, round_: bool | str = False) -> list[Item]:
    if round_ == ROUND_F:
        return _read_round_f_pool(conn, gate)
    if round_:
        return _read_round_pool(conn, gate)
    if gate in ("targets", "unattributed"):
        role = "management" if gate == "targets" else "unknown"
        return [Item(id=i, company=co, group=KIND_GROUP[kind], family=family_of(metric), not_scored=ns,
                     role_held=rh, no_mgmt_turn=nm, document=doc)
                for i, co, kind, metric, ns, rh, nm, doc in conn.execute(POOL_TARGETS, (role,)).fetchall()]
    if gate == "stated":
        return [Item(id=i, company=co, family=family_of(metric), document=doc)
                for i, co, metric, doc in conn.execute(POOL_STATED).fetchall()]
    if gate == "summaries":
        return [Item(id=i, company=co, role_held=rh, no_mgmt_turn=nm, peer=peer or "", document=doc)
                for i, co, rh, nm, peer, doc in conn.execute(POOL_SUMMARIES, (SUMMARY_VERSION,)).fetchall()]
    if gate == "recall":
        return [Item(id=i, company=co, peer=peer or "", bucket=bucket_of(peer or ""), document=doc)
                for i, co, peer, doc in conn.execute(POOL_RECALL).fetchall()]
    if gate == "scoring":
        return [Item(id=i, company=co, kind=kind, family=family_of(metric), document=doc, counted=bool(counted))
                for i, co, kind, metric, doc, counted in conn.execute(POOL_SCORING).fetchall()]
    return [Item(id=i, company=co, source=src, document=doc) for i, co, src, doc in conn.execute(POOL_LINKS).fetchall()]


def _read_round_pool(conn, gate: str) -> list[Item]:
    ex = list(ROUND_EXCLUDED_EVENTS)
    if gate == "targets":
        return [Item(id=i, company=co, group=KIND_GROUP[kind], family=family_of(metric), not_scored=ns,
                     role_held=rh, no_mgmt_turn=nm, document=doc)
                for i, co, kind, metric, ns, rh, nm, doc in conn.execute(POOL_TARGETS_ROUND, ("management", ROUND_VERSION, ex)).fetchall()]
    if gate == "stated":
        return [Item(id=i, company=co, family=family_of(metric), document=doc)
                for i, co, metric, doc in conn.execute(POOL_STATED_ROUND, (ROUND_VERSION, ex)).fetchall()]
    if gate == "recall":
        return [Item(id=i, company=co, peer=peer or "", bucket=bucket_of(peer or ""), document=doc)
                for i, co, peer, doc in conn.execute(POOL_RECALL_ROUND, (ROUND_VERSION, ex)).fetchall()]
    if gate == "scoring":
        return [Item(id=i, company=co, kind=kind, family=family_of(metric), document=doc, counted=bool(counted))
                for i, co, kind, metric, doc, counted in conn.execute(POOL_SCORING_ROUND, (ROUND_VERSION, ex)).fetchall()]
    raise ValueError(f"the round draws {', '.join(ROUND_GATES)} only, not {gate!r}")


def _read_round_f_pool(conn, gate: str) -> list[Item]:
    judged = (list(ROUND_F_CALLS), list(ROUND_F_GUIDANCE), list(ROUND_F_STATED))
    if gate == "targets":
        return [Item(id=i, company=co, group=KIND_GROUP[kind], family=family_of(metric), not_scored=ns,
                     role_held=rh, no_mgmt_turn=nm, document=doc)
                for i, co, kind, metric, ns, rh, nm, doc
                in conn.execute(POOL_TARGETS_ROUND_F, ("management", ROUND_F_VERSION, *judged)).fetchall()]
    if gate == "stated":
        return [Item(id=i, company=co, family=family_of(metric), document=doc)
                for i, co, metric, doc in conn.execute(POOL_STATED_ROUND_F, (ROUND_F_VERSION, *judged)).fetchall()]
    if gate == "recall":
        return [Item(id=i, company=co, peer=peer or "", bucket=ROUND_F_STAND_INS.get(peer or "") or bucket_of(peer or ""), document=doc)
                for i, co, peer, doc in conn.execute(POOL_RECALL_ROUND_F, (ROUND_F_VERSION, *judged)).fetchall()]
    if gate == "scoring":
        return [Item(id=i, company=co, kind=kind, family=family_of(metric), document=doc, counted=bool(counted))
                for i, co, kind, metric, doc, counted in conn.execute(POOL_SCORING_ROUND_F, (ROUND_F_VERSION, *judged)).fetchall()]
    raise ValueError(f"the round draws {', '.join(ROUND_GATES)} only, not {gate!r}")


# ---------------------------------------------------------------------------------------------- showing items to Anand
# Run through the read-only dev connector, in the session only (§13.11 L12). `quote` is the cited span of the document's
# text, rebuilt from the chunks that cover it (at most 400 code points: §13.5). `quote_len_ok` says it came back whole.

_QUOTE = """(select string_agg(substr(ch.body, greatest({s}, ch.char_start) - ch.char_start + 1,
                                     least({e}, ch.char_end) - greatest({s}, ch.char_start)), '' order by ch.chunk_no)
          from core.chunk ch where ch.document_id = {doc} and ch.char_start < {e} and ch.char_end > {s})"""


def _quote(alias: str) -> str:
    return _QUOTE.format(s=f"{alias}.char_start", e=f"{alias}.char_end", doc=f"{alias}.document_id")


def _ids(ids: Iterable[int]) -> str:
    out = [str(int(i)) for i in ids]          # int() refuses anything but numbers
    if not out:
        raise ValueError("no ids")
    return ", ".join(out)


_SPAN = """left join equity.call_turn t on t.document_id = x.document_id and x.char_start >= t.char_start and x.char_start < t.char_end
left join equity.transcript_page pf on pf.document_id = x.document_id and x.char_start >= pf.char_start and x.char_start < pf.char_end
left join equity.transcript_page pl on pl.document_id = x.document_id and x.char_end - 1 >= pl.char_start and x.char_end - 1 < pl.char_end
join core.document d on d.id = x.document_id"""

_SHOWN = """coalesce(e.short_name, e.name) as company, e.currency, ec.fiscal_label as quarter, ec.call_date, round(ec.unknown_role_share * 100) as unknown_pct,
       t.speaker, t.role as turn_role, t.section,
       {quote} as quote, x.char_end - x.char_start as span_len,
       pf.pdf_page as page_first, pl.pdf_page as page_last,
       case when pf.pdf_page is not null then regexp_replace(d.source_url, '#.*$', '') || '#page=' || pf.pdf_page end as link"""


def item_sql(gate: str, ids: Iterable[int]) -> str:
    """The fields, quote, speaker and page link of the chosen target or stated-figure ids (`links` has its own function)."""
    if gate in ("targets", "unattributed"):
        fields = """x.id, x.kind, x.metric, x.metric_label, x.metric_text, x.value, x.low, x.high, x.unit, x.direction, x.comparison,
       x.period_text, x.period_kind, x.target_start, x.target_end, x.basis, x.growth_basis, x.scope_text, x.not_scored_reason,
       x.speaker_role,"""
        table = "equity.guidance"
    elif gate == "stated":
        fields = """x.id, x.metric, x.metric_label, x.metric_text, x.value, x.unit, x.period_text, x.period_kind,
       x.period_start, x.period_end, x.basis, x.growth_basis, x.scope_text,"""
        table = "equity.stated_figure"
    else:
        raise ValueError(gate)
    return f"""select {fields}
       {_SHOWN.format(quote=_quote('x'))}
from {table} x
join core.entity e on e.id = x.entity_id
join equity.earnings_call ec on ec.event_id = x.call_event_id
{_SPAN}
where x.id in ({_ids(ids)})
order by x.id"""


def _citation_rows(where: str) -> str:
    return f"""select x.ai_output_id, x.citation_no, o.task, x.claim_path, x.document_id,
       {_SHOWN.format(quote=_quote('x'))}
from core.ai_citation x
join core.ai_output o on o.id = x.ai_output_id
{_SPAN}
join core.entity e on e.id = d.entity_id
left join equity.earnings_call ec on ec.transcript_document_id = x.document_id
where {where}
order by x.ai_output_id, x.citation_no"""


def link_sql(keys: Iterable[tuple[int, int] | str]) -> str:
    """Page links for (ai_output_id, citation_no) pairs, or the "o:n" keys the draw prints: the claim path, the quote, the page
    and the source's own link. The company comes from the document (an output's entity_id is null for every summary)."""
    def pair(k) -> str:
        o, n = k.split(":") if isinstance(k, str) else k
        return f"({int(o)}, {int(n)})"
    pairs = ", ".join(pair(k) for k in keys)
    if not pairs:
        raise ValueError("no keys")
    return _citation_rows(f"(x.ai_output_id, x.citation_no) in ({pairs})")


# ---- sitting 2: what the session shows or hands out. Text comes back only through the read-only dev connector.

def summary_sql(output_ids: Iterable[int]) -> str:
    """The chosen summaries: company, quarter and the bullets as stored (`content -> 'bullets'`). Their cited quotes come from
    `summary_citations_sql`, joined to the bullets by claim path."""
    return f"""select o.id as output_id, coalesce(e.short_name, e.name) as company, ec.fiscal_label as quarter, ec.call_date,
       round(ec.unknown_role_share * 100) as unknown_pct, o.prompt_version, o.content -> 'bullets' as bullets
from core.ai_output o
join core.document d on d.id = o.document_id
join core.entity e on e.id = d.entity_id
join equity.earnings_call ec on ec.transcript_document_id = o.document_id
where o.id in ({_ids(output_ids)})
order by o.id"""


def summary_citations_sql(output_ids: Iterable[int]) -> str:
    """Every citation of the chosen summaries: claim path, quote, speaker, section and page link."""
    return _citation_rows(f"x.ai_output_id in ({_ids(output_ids)})")


def recall_calls_sql(event_ids: Iterable[int]) -> str:
    """What a reader needs to find a call's text, not the text: the document, its chunks and the company's fiscal year end."""
    return f"""select ec.event_id, coalesce(e.short_name, e.name) as company, c.fy_end_month, ec.fiscal_label as quarter, ec.call_date,
       ec.transcript_document_id as document_id, ec.words,
       (select min(ch.chunk_no) from core.chunk ch where ch.document_id = ec.transcript_document_id) as first_chunk,
       (select max(ch.chunk_no) from core.chunk ch where ch.document_id = ec.transcript_document_id) as last_chunk
from equity.earnings_call ec
join core.entity e on e.id = ec.entity_id
join equity.company c on c.entity_id = ec.entity_id
where ec.event_id in ({_ids(event_ids)})
order by ec.event_id"""


def recall_chunks_sql(document_id: int, first_chunk: int, last_chunk: int) -> str:
    """A page of a transcript for the reader: its chunks in order, each with its speaker, role and section."""
    return f"""select ch.chunk_no, ch.char_start, ch.char_end, ch.section, ch.speaker, t.role, ch.body
from core.chunk ch
left join equity.call_turn t on t.document_id = ch.document_id and ch.char_start >= t.char_start and ch.char_start < t.char_end
where ch.document_id = {int(document_id)} and ch.chunk_no between {int(first_chunk)} and {int(last_chunk)}
order by ch.chunk_no"""


def recall_extraction_sql(event_ids: Iterable[int], prompt_version: str | None = None) -> str:
    """The extraction's management targets of the chosen calls, with their quotes: for comparing with the reader's list. The
    reader never sees this. With `prompt_version` (#511's round), only the rows of that version's published output, so a call
    the round re-ran is compared with the round's rows and never with a superseded output's (and a call where the round found
    nothing has none)."""
    version = ""
    if prompt_version is not None:
        if not re.fullmatch(r"v\d{1,3}", prompt_version):
            raise ValueError(f"not a prompt version: {prompt_version!r}")
        version = (f"\n  and exists (select 1 from core.ai_output o where o.id = x.ai_output_id and o.status = 'published'"
                   f" and o.task = 'guidance_extraction' and o.prompt_version = '{prompt_version}')")
    return f"""select x.call_event_id, x.id, x.kind, x.metric, x.metric_text, x.value, x.low, x.high, x.unit, x.direction,
       x.comparison, x.period_text, x.not_scored_reason, x.char_start, x.char_end, {_quote('x')} as quote
from equity.guidance x
where x.call_event_id in ({_ids(event_ids)}) and x.speaker_role = 'management' and x.duplicate_of is null{version}
order by x.call_event_id, x.char_start"""


def scoring_packet_sql(guidance_ids: Iterable[int]) -> str:
    """What the recomputer is given: the statement it must score and nothing of the scorer's result."""
    return f"""select x.id as guidance_id, e.id as entity_id, coalesce(e.short_name, e.name) as company, e.currency,
       c.fy_end_month, ec.fiscal_label as call_quarter, ec.call_date,
       x.metric, x.kind, x.value, x.low, x.high, x.unit, x.direction, x.comparison,
       x.period_text, x.period_kind, x.target_start, x.target_end, x.basis, x.growth_basis, x.scope_text
from equity.guidance x
join core.entity e on e.id = x.entity_id
join equity.company c on c.entity_id = x.entity_id
join equity.earnings_call ec on ec.event_id = x.call_event_id
where x.id in ({_ids(guidance_ids)})
order by x.id"""


def scoring_stored_sql(guidance_ids: Iterable[int]) -> str:
    """The scorer's stored result for the chosen outcomes: for comparing with the recomputer's. The recomputer never sees this."""
    return f"""select o.guidance_id, o.outcome, o.relation, o.actual, o.base, o.reason, o.is_first, o.counted, o.revision,
       o.horizon_share, o.map_version, o.rule_version, o.inputs
from equity.guidance_outcome o
where o.guidance_id in ({_ids(guidance_ids)})
order by o.guidance_id"""


# ---------------------------------------------------------------------------------------------- the page-map check
# The code check on every transcript (§13.8): the page rows of every stored transcript are ordered, don't overlap, tile the
# text from the first page to its end, and lie inside it. (The word-level check against the PDF's own pages ran when each
# document was stored: `equity.call_transcripts`' `page_maps` check names a document whose map failed, and a failed map
# writes no rows. The five links Anand opens test the pages themselves.)

PAGE_MAP_SQL = """
with pages as (
  select p.document_id, p.pdf_page, p.char_start, p.char_end,
         lag(p.char_end) over w as prev_end
  from equity.transcript_page p
  window w as (partition by p.document_id order by p.pdf_page)
), per_doc as (
  select d.id, d.char_count, count(p.pdf_page) as n_pages,
         count(*) filter (where p.prev_end is not null and p.char_start < p.prev_end) as n_overlap,
         count(*) filter (where p.prev_end is not null and p.char_start > p.prev_end) as n_gap,
         count(*) filter (where p.char_end > d.char_count) as n_past_end,
         max(p.char_end) as last_end, min(p.char_start) as first_start
  from core.document d
  left join pages p on p.document_id = d.id
  where d.doc_type = 'call_transcript'
  group by d.id, d.char_count
)
select count(*) as transcripts,
       count(*) filter (where n_pages > 0) as with_pages,
       count(*) filter (where n_pages = 0) as without_pages,
       count(*) filter (where n_overlap > 0) as with_overlaps,
       count(*) filter (where n_gap > 0) as with_gaps,
       count(*) filter (where n_past_end > 0) as past_the_text,
       count(*) filter (where n_pages > 0 and last_end <> char_count) as short_of_the_end,
       count(*) filter (where n_pages > 0 and first_start <> 0) as not_from_the_start
from per_doc"""


# ---------------------------------------------------------------------------------------------- recording verdicts
# `ops.eval_run` and `ops.eval_result` are written by Anand in dev's SQL editor (the dev connector is read-only). Verdicts go
# in by item id; `predicted` holds the extracted fields as stored (no transcript text), `expected` Anand's verdict.

def lit(v) -> str:
    """A SQL literal: null, a number, a boolean or a quoted string (quotes doubled)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def jlit(v) -> str:
    if v is None:
        return "null::jsonb"
    return lit(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) + "::jsonb"


def record_sql(runs: list[dict]) -> str:
    """One transaction with an `ops.eval_run` row and its `ops.eval_result` rows per run.
    run: eval_set, set_version, model, prompt_version, n_items, metrics, passed, notes, results [{item_key, expected, predicted, correct}]."""
    parts = ["begin;"]
    for r in runs:
        rows = ",\n  ".join(f"({lit(x['item_key'])}, {jlit(x['expected'])}, {jlit(x.get('predicted'))}, {lit(x.get('correct'))}::boolean)"
                            for x in r["results"])
        if len(r["results"]) != r["n_items"]:
            raise ValueError(f"{r['eval_set']}: n_items {r['n_items']} but {len(r['results'])} results")
        parts.append(f"""with run as (
  insert into ops.eval_run (eval_set, set_version, model, prompt_version, n_items, metrics, passed, notes)
  values ({lit(r['eval_set'])}, {lit(r['set_version'])}, {lit(r['model'])}, {lit(r.get('prompt_version'))}, {int(r['n_items'])},
          {jlit(r['metrics'])}, {lit(r['passed'])}, {lit(r.get('notes'))})
  returning id
)
insert into ops.eval_result (eval_run_id, item_key, expected, predicted, correct)
select run.id, v.item_key, v.expected, v.predicted, v.correct
from run, (values
  {rows}
) as v(item_key, expected, predicted, correct);""")
    parts.append("commit;")
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------------------------- the CLI

def annotation(title: str, text: str) -> str:
    """One GitHub notice annotation (Claude reads results from annotations: its workspace can't download logs)."""
    esc = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::notice title={title}::{esc}"


def main(argv: list[str] | None = None, *, env=None, out=print) -> int:
    p = argparse.ArgumentParser(prog="python -m ft.equity.calls.samples", description=__doc__.split("\n\n")[0])
    p.add_argument("--gate", choices=(*ALL_GATES, "all", "sitting2", "round"), default="all",
                   help='"all" is sitting 1\'s four gates and the page-map check; "sitting2" is summaries, recall and scoring')
    p.add_argument("--seed", type=int, default=None, help="a new sample after a failed gate: one gate only; record the seed")
    p.add_argument("--target", choices=("dev",), default="dev", help="logged; the workflow reads dev's credentials only")
    p.add_argument("--round", choices=ROUNDS, default=None,
                   help=f"a round's fresh draw: {ROUND} is #511's, from the {ROUND_VERSION} re-run with sets A and B's companies left out; "
                        f"{ROUND_F} is #533's, from the {ROUND_F_VERSION} re-run with the judged calls left out: --gate round, or one of its gates")
    args = p.parse_args(argv)
    if args.seed is not None and args.gate in ("all", "sitting2", "round"):
        p.error("--seed needs one --gate")
    if args.round is None and args.gate == "round":
        p.error("--gate round needs --round " + " or ".join(ROUNDS))
    if args.round is not None and args.gate not in (*ROUND_GATES, "round"):
        p.error(f"--round draws {', '.join(ROUND_GATES)} (or --gate round), not {args.gate}")
    env = os.environ if env is None else env
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (the workflow reads the DEV_ secret)")
    from ft.jobs.runner import connect

    gates = {"all": GATES, "sitting2": GATES_2, "round": ROUND_GATES}.get(args.gate, (args.gate,))
    on_round = args.round is not None
    with connect(dsn, autocommit=True, role="gate-samples") as conn:
        conn.execute("set default_transaction_read_only = on")
        for gate in gates:
            pool = read_pool(conn, gate, round_=args.round or False)
            seeds = ROUND_F_SEEDS if args.round == ROUND_F else ROUND_SEEDS
            seed = args.seed if args.seed is not None else (seeds[gate] if on_round else None)
            d = draw_recall(pool, seed) if gate == "recall" else draw(pool, gate, seed)
            rule_version = {ROUND: ROUND_RULE_VERSION, ROUND_F: ROUND_F_RULE_VERSION}.get(args.round, RULE_VERSION)
            body = {"gate": gate, "sitting": SITTING_OF[gate], "rule_version": rule_version,
                    "seed": d.seed, "attempt": d.attempt,
                    "target": args.target, "pool": d.pool, "n": len(d.items), "ids": [it.id for it in d.items],
                    "strata": d.strata(), "shortfalls": d.shortfalls}
            if args.round == ROUND_F:
                body.update({"round": args.round, "prompt_version": ROUND_F_VERSION, "left_out": "recall: judged companies; others: judged calls",
                             "judged_calls": len(ROUND_F_CALLS), "judged_guidance": len(ROUND_F_GUIDANCE),
                             "judged_stated": len(ROUND_F_STATED), "recall_stand_ins": ROUND_F_STAND_INS})
            elif on_round:
                body.update({"round": args.round, "prompt_version": ROUND_VERSION,
                             "excluded_events": list(ROUND_EXCLUDED_EVENTS)})
            if gate == "recall":
                body["extra_ids"] = [it.id for it in d.extra]
            out(annotation(f"sample {gate} seed {d.seed}", json.dumps(body, separators=(",", ":"))))
        if args.gate == "all":
            row = conn.execute(PAGE_MAP_SQL).fetchone()
            cols = ("transcripts", "with_pages", "without_pages", "with_overlaps", "with_gaps", "past_the_text",
                    "short_of_the_end", "not_from_the_start")
            out(annotation("page map check", json.dumps(dict(zip(cols, row, strict=True)), separators=(",", ":"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
