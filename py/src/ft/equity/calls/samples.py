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

The samples (sitting 1; the summary gate, recall and scoring belong to sitting 2):

| gate | pool | size | rules |
|---|---|---|---|
| `targets` | management targets (`speaker_role = 'management'`) of published extraction outputs, duplicates out | 40 | cells: range 10+2, point 8+2, floor or ceiling 6+2, direction 8+2 (scored + not scored: 8 not scored in all); at most 2 a company; at least 8 from role-held calls; metric families: P&L 8, margins 8, capex and balance sheet 5, banks 3 (as available) |
| `unattributed` | separate-list targets (`speaker_role = 'unknown'`), duplicates out | 20 | at most 2 a company; at least 3 from calls with no management turn, where such calls have items (Spec 02B.1 C7) |
| `stated` | stated figures of published extraction outputs, duplicates out | 20 | at most 2 a company; families: P&L 6, margins 4, capex and balance sheet 3, banks 4 (as available) |
| `links` | verified transcript citations into a document with a page map | 5 | different documents and companies; 2 from cited summary bullets, 3 from extracted figures |

A minimum or a cell the pool can't fill is reported as a **shortfall**, never filled by loosening another rule.

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
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

RULE_VERSION = 2
ATTEMPTS = 300                  # a seeded retry limit, see `draw`
SITTING = 1
SEEDS = {"targets": 2026110701, "unattributed": 2026110702, "stated": 2026110703, "links": 2026110704}
GATES = tuple(SEEDS)

FAMILY = {
    "revenue": "pnl", "revenue_growth": "pnl", "pat": "pnl", "pat_growth": "pnl",
    "ebitda_margin": "margins", "ebit_margin": "margins", "tax_rate": "margins",
    "capex": "capex_balance", "net_debt": "capex_balance",
    "advances_growth": "banks", "deposits_growth": "banks", "gnpa": "banks", "nnpa": "banks", "cet1": "banks",
}
KIND_GROUP = {"range": "range", "point": "point", "floor": "floor_ceiling", "ceiling": "floor_ceiling", "direction": "direction"}


def family_of(metric: str) -> str:
    return FAMILY.get(metric, "other")


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


@dataclass(frozen=True)
class Rules:
    total: int
    per_company: int
    cell: Callable[[Item], str] = lambda it: ""
    quotas: dict[str, int] = field(default_factory=dict)       # cell -> slots; empty means no cells
    minimums: tuple[tuple[str, Callable[[Item], bool], int], ...] = ()    # (name, test, at least), filled in this order


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

    def strata(self) -> dict[str, int]:
        rules = RULES[self.gate]
        c: Counter[str] = Counter()
        for it in self.items:
            if rules.quotas:
                c[rules.cell(it)] += 1
            for name, test, _ in rules.minimums:
                if test(it):
                    c[name] += 1
        c["companies"] = len({it.company for it in self.items})
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
    cells: Counter[str] = Counter()
    mins: Counter[str] = Counter()
    taken: set[int | str] = set()

    def can(it: Item) -> bool:
        if len(chosen) >= rules.total or per_company[it.company] >= rules.per_company:
            return False
        if rules.quotas:
            c = rules.cell(it)
            return c in rules.quotas and cells[c] < rules.quotas[c]
        return True

    def take(it: Item) -> None:
        chosen.append(it)
        taken.add(it.id)
        per_company[it.company] += 1
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


def draw(items: Iterable[Item], gate: str, seed: int | None = None) -> Draw:
    """Seeded: the same pool and seed give the same items. Attempt 0 orders the pool by `rank(seed, gate, id)`; if it comes
    back short, attempt k tries the order of `rank(seed, "gate#k", id)`, up to ATTEMPTS, and the first with no shortfall wins
    (else the one missing least). What no attempt can meet is reported, not relaxed."""
    rules = RULES[gate]
    seed = SEEDS[gate] if seed is None else seed
    pool = sorted(items, key=lambda it: (str(it.id),))
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


def read_pool(conn, gate: str) -> list[Item]:
    if gate in ("targets", "unattributed"):
        role = "management" if gate == "targets" else "unknown"
        return [Item(id=i, company=co, group=KIND_GROUP[kind], family=family_of(metric), not_scored=ns,
                     role_held=rh, no_mgmt_turn=nm, document=doc)
                for i, co, kind, metric, ns, rh, nm, doc in conn.execute(POOL_TARGETS, (role,)).fetchall()]
    if gate == "stated":
        return [Item(id=i, company=co, family=family_of(metric), document=doc)
                for i, co, metric, doc in conn.execute(POOL_STATED).fetchall()]
    return [Item(id=i, company=co, source=src, document=doc) for i, co, src, doc in conn.execute(POOL_LINKS).fetchall()]


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

_SHOWN = """e.short_name as company, ec.fiscal_label as quarter, ec.call_date, round(ec.unknown_role_share * 100) as unknown_pct,
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


def link_sql(keys: Iterable[tuple[int, int]]) -> str:
    """Page links for (ai_output_id, citation_no) pairs: the claim path, the quote, the page and the source's own link."""
    pairs = ", ".join(f"({int(o)}, {int(n)})" for o, n in keys)
    if not pairs:
        raise ValueError("no keys")
    return f"""select x.ai_output_id, x.citation_no, o.task, x.claim_path,
       e.short_name as company, ec.fiscal_label as quarter, x.document_id,
       {_SHOWN.format(quote=_quote('x'))}
from core.ai_citation x
join core.ai_output o on o.id = x.ai_output_id
join core.entity e on e.id = o.entity_id
left join equity.earnings_call ec on ec.transcript_document_id = x.document_id
{_SPAN}
where (x.ai_output_id, x.citation_no) in ({pairs})
order by x.ai_output_id, x.citation_no"""


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
    p.add_argument("--gate", choices=(*GATES, "all"), default="all")
    p.add_argument("--seed", type=int, default=None, help="a new sample after a failed gate: one gate only; record the seed")
    p.add_argument("--target", choices=("dev",), default="dev", help="logged; the workflow reads dev's credentials only")
    args = p.parse_args(argv)
    if args.seed is not None and args.gate == "all":
        p.error("--seed needs one --gate")
    env = os.environ if env is None else env
    dsn = (env.get("JOB_DATABASE_URL") or "").strip()
    if not dsn:
        p.error("JOB_DATABASE_URL is not set (the workflow reads the DEV_ secret)")
    from ft.jobs.runner import connect

    gates = GATES if args.gate == "all" else (args.gate,)
    with connect(dsn, autocommit=True, role="gate-samples") as conn:
        conn.execute("set default_transaction_read_only = on")
        for gate in gates:
            d = draw(read_pool(conn, gate), gate, args.seed)
            body = {"gate": gate, "sitting": SITTING, "rule_version": RULE_VERSION, "seed": d.seed, "attempt": d.attempt, "target": args.target,
                    "pool": d.pool, "n": len(d.items), "ids": [it.id for it in d.items], "strata": d.strata(),
                    "shortfalls": d.shortfalls}
            out(annotation(f"sample {gate} seed {d.seed}", json.dumps(body, separators=(",", ":"))))
        if args.gate == "all":
            row = conn.execute(PAGE_MAP_SQL).fetchone()
            cols = ("transcripts", "with_pages", "without_pages", "with_overlaps", "with_gaps", "past_the_text",
                    "short_of_the_end", "not_from_the_start")
            out(annotation("page map check", json.dumps(dict(zip(cols, row, strict=True)), separators=(",", ":"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
