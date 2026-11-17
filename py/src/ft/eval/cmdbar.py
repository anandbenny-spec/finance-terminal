"""The command bar's gate runner (Spec 07 §6, N4, N5; K-04 #582, run by K-10 #587 and the K-11 judge): runs a set's split
through the route's own code (`ft/cmdbar`) on dev and scores it with the rules Spec 07 §6 fixed before any run.

    python -m ft.eval.cmdbar --set cmdbar_sql --split dev            # the price and the golds only: no model call
    python -m ft.eval.cmdbar --set cmdbar_sql --split dev --send     # answers every item, scores, records

**Sets.** `cmdbar_sql`: each item's question goes through `ft.cmdbar.sql.answer_data` (prompt, guard, the read-only run as
`sql_reader`, one Sonnet retry, the summary); its gold SQL goes through the same guard and runs on the same database
(golds are SQL, never rows: N4). `cmdbar_router`: each question goes through `ft.cmdbar.router.route`. `retrieval` (K-06, #584):
each question goes through `ft.cmdbar.documents.answer_documents`, the document route's own code (`voyage-4` questions,
`search_chunks` over Fed and RBI policy documents only with `k => 8`, then the cited answer), with no router filters (as the set
says); the item's expected passage (its document and character span) is scored against the passages retrieval returned, and the
answer's cited sentences are counted beside it. `--query-model voyage-4-lite` (K-10, #587) embeds the questions with the other
model, for Architecture 03 §0's comparison ("the better of the two ships": `voyage-4` did, so the route uses it since 17 Nov
2026): recorded beside the route's run, never as the gate.

**Scoring** (Spec 07 §6):
- `cmdbar_sql`, result-set match: the model's rows equal the gold's as multisets of tuples on the gold's columns; each gold column
  is matched by one model column with equal values in every row (extra model columns allowed); numbers are equal at the gold's
  precision (each gold column's largest number of decimal places; half rounds up); row order counts only where the item says
  `ordered`. An unanswerable item is right only if the model says so. Cross-currency items are also wrong, whatever their rows,
  if the query ranks, sums or compares **money** across currencies (`money_comparison`: an ORDER BY or unpartitioned window on
  money, an aggregate of money not grouped by market, company or series, or an extra column combining two rows' money). The
  match alone would pass such an answer on dev, where no US company is published. Currency-neutral figures (ratios, margins,
  growth, percent changes) may be ranked across markets (Anand's option 1B, 17 Nov 2026, #587, narrowing the 16 Nov 2026
  version of #582 that failed any ORDER BY on a figure and any extra numeric column). Pass: at least 85%, and 100% of the
  cross-currency items.
- `cmdbar_router`, route accuracy; filter agreement is reported beside it where the gold states filters. Pass: at least 90%,
  and no unanswerable (`unsupported`) item routed to data or documents.
- `retrieval`, recall within the top 8: an item is a hit when any returned passage of its document (bank, document type and
  meeting) overlaps its span; the mean reciprocal rank is reported. Pass: recall at least 0.85, and no returned row that isn't a
  Fed or RBI policy document (N3: a tightening, decided by Claude under the standing delegation, 17 Nov 2026, #584). Reported
  beside it, never part of the rule: each answer's state, sentences kept and dropped, its verified citations, whether one cites
  the expected document, transcript characters (always 0) and RBI links (always 0).
A dev-split run is prompt work, never the gate: its `passed` is false whatever its numbers (N5).

**Spend.** Without `--send` nothing reaches a model: it prices the run from `config/ai_prices.yaml` (the estimate and the worst
case, every request using its whole token cap and every SQL item retried on Sonnet) and, for `cmdbar_sql`, runs the golds (for
`retrieval`, it checks that `sql_reader` may call `search_chunks`; no question is embedded). With `--send`, the run stops before
any item whose worst case would take its actual spend past `--max-cost`. `retrieval`'s Voyage tokens (about 20 a question) are
within Voyage's free allowance, so they are counted at no cost (config/ai_prices.yaml's `voyage` note).

**Writes** one `ops.eval_run` and one `ops.eval_result` per item on dev, as `job_writer` (`JOB_DATABASE_URL`): counts, the
query that ran and the summary line, never the rows. **Prints** annotations with ids, counts, classes and costs only: never a
row, a summary or a figure from the data (the repository and its run logs are public: CLAUDE.md §4). At most four: the run's
price and golds, then one for all the items and one for the result (GitHub keeps only the first 10 notices of a step).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path

import yaml
from pglast import ast, parse_sql, stream
from pglast.enums.parsenodes import A_Expr_Kind
from pglast.enums.primnodes import BoolExprType, SubLinkType
from pglast.parser import ParseError

from ft.ai import sqlguard
from ft.ai.client import CONFIG, MILLION, QUERY_MODEL, AIError, Client, Embedder, Request, price_entry
from ft.cmdbar import documents as documents_mod
from ft.cmdbar import router as router_mod
from ft.cmdbar import sql as sql_mod
from ft.cmdbar import summary as summary_mod

SETS_DIR = CONFIG / "eval" / "sets"
SETS = ("cmdbar_sql", "cmdbar_router", "retrieval")
QUERY_MODELS = ("voyage-4", "voyage-4-lite")   # the route's questions model, and Architecture 03 §0's comparison
SPLITS = ("dev", "test")
DEFAULT_MAX_COST = Decimal("0.50")
SUMMARY_INPUT_TOKENS_EST = 2_500          # a summary request with 50 rows of a wide view, over-estimated
PASSAGE_CHARS_EST = 2_500                 # one retrieved passage (the chunker cuts about 1,800 characters), over-estimated
MAX_ASSIGNMENTS = 20_000                  # column-matching search limit (sets have a handful of columns)
GATE = {"cmdbar_sql": "accuracy >= 0.85 and cross-currency accuracy == 1.0 (test split; Spec 07 §6)",
        "cmdbar_router": "route accuracy >= 0.90 and no unanswerable item answered (test split; Spec 07 §6)",
        "retrieval": "recall within the top 8 >= 0.85 and no row that isn't a Fed or RBI policy document (test split; Spec 07 §6, N3)"}
SEARCH_EXECUTABLE_SQL = ("select has_function_privilege('semantic.search_chunks(extensions.halfvec, text, text[], text[], date, "
                         "date, integer)', 'execute')")


# ---------------------------------------------------------------------------------------------- the sets
def set_path(name: str) -> Path:
    return SETS_DIR / f"{name}.yaml"


def load_items(name: str, split: str, ids: list[str] | None = None) -> list[dict]:
    """The split's items, in file order (or only `ids`, each of which must be in the split)."""
    if name not in SETS or split not in SPLITS:
        raise ValueError(f"set {name!r} split {split!r}: not one this runner reads")
    items = [i for i in yaml.safe_load(set_path(name).read_text())["items"] if i["split"] == split]
    if ids:
        known = {i["id"] for i in items}
        if missing := [x for x in ids if x not in known]:
            raise ValueError(f"not in the {split} split of {name}: {', '.join(missing)}")
        items = [i for i in items if i["id"] in set(ids)]
    return items


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_sha(env) -> str:
    if sha := (env.get("GITHUB_SHA") or "").strip():
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=SETS_DIR).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# ---------------------------------------------------------------------------------------------- result-set match
def _is_num(v) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def column_scale(values) -> int:
    """A gold column's precision: its numbers' largest count of decimal places (0 for whole numbers)."""
    places = 0
    for v in values:
        if _is_num(v):
            d = _dec(v)
            if d.is_finite() and d.as_tuple().exponent < 0:
                places = max(places, -d.as_tuple().exponent)
    return places


def norm(v, scale: int):
    """A cell as the match compares it: numbers rounded to the gold column's precision (half up), dates and instants as ISO
    text (instants in UTC), text as it is. Types stay apart: the number 5 never equals the text '5'."""
    if v is None:
        return ("null",)
    if isinstance(v, bool):
        return ("bool", v)
    if _is_num(v):
        d = _dec(v)
        if not d.is_finite():
            return ("num", str(d))
        with localcontext() as ctx:
            ctx.prec = 80
            try:
                q = d.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
            except InvalidOperation:
                return ("num", str(d))
        return ("num", q + 0)                  # + 0 folds -0 into 0
    if isinstance(v, datetime):
        return ("instant", (v.astimezone(UTC) if v.tzinfo else v).isoformat())
    if isinstance(v, date):
        return ("date", v.isoformat())
    return ("text", str(v))


@dataclass(frozen=True)
class Match:
    ok: bool
    reason: str                                # match | row_count | columns | rows
    mapping: tuple[int, ...] = ()              # gold column j -> model column mapping[j]


def rows_match(gold_cols: list[str], gold_rows: list[tuple], model_cols: list[str], model_rows: list[tuple],
               ordered: bool) -> Match:
    """Spec 07 §6's result-set match (module docstring)."""
    if len(gold_rows) != len(model_rows):
        return Match(False, "row_count")
    k = len(gold_cols)
    if len(model_cols) < k:
        return Match(False, "columns")
    scales = [column_scale(r[j] for r in gold_rows) for j in range(k)]
    gold = [tuple(norm(r[j], scales[j]) for j in range(k)) for r in gold_rows]
    cands = []
    for j in range(k):
        want = Counter(g[j] for g in gold)
        cj = [m for m in range(len(model_cols)) if Counter(norm(r[m], scales[j]) for r in model_rows) == want]
        if not cj:
            return Match(False, "columns")
        cands.append(cj)
    target = gold if ordered else Counter(gold)
    tried = 0

    def search(j: int, used: tuple[int, ...]):
        nonlocal tried
        if j == k:
            tried += 1
            got = [tuple(norm(r[used[i]], scales[i]) for i in range(k)) for r in model_rows]
            return used if (got if ordered else Counter(got)) == target else None
        for m in cands[j]:
            if m in used or tried >= MAX_ASSIGNMENTS:
                continue
            if (found := search(j + 1, used + (m,))) is not None:
                return found
        return None

    found = search(0, ())
    return Match(True, "match", found) if found is not None else Match(False, "rows")


# The semantic views' money columns: amounts in the row's currency, plus market_daily's `value` (an index level is in its
# index's currency; the view's comment forbids comparing levels across currencies). Ratios, multiples, margins, growth rates,
# returns, yields, shares in percent, counts and dates are currency-neutral and absent. `call_guidance` and the other
# India-only views can't mix currencies, so they add nothing here.
MONEY_COLUMNS = frozenset({
    # company_valuations
    "price", "market_cap", "ev", "traded_value_median_3m",
    # company_financials
    "revenue", "other_income", "total_income", "employee_cost", "dna", "finance_cost", "total_expenses",
    "share_of_associates", "pbt_before_exceptional", "exceptional", "pbt", "tax", "pat", "pat_owners", "pat_nci",
    "eps_basic", "eps_diluted", "total_assets", "current_assets", "cash", "bank_balances_other", "current_investments",
    "noncurrent_investments", "trade_receivables", "inventories", "total_equity", "equity_owners", "share_capital", "nci",
    "borrowings_current", "borrowings_noncurrent", "lease_current", "lease_noncurrent", "trade_payables", "cfo", "cfi", "cff",
    "capex", "dividends_paid", "paid_up_capital", "face_value", "interest_earned", "interest_expended", "operating_profit",
    "provisions", "segment_assets_total", "deposits", "advances", "gross_premium", "net_premium", "assets_net",
    "current_liabilities_provisions", "ebit_derived",
    # market_daily
    "value",
})
# The keys naming one company or one series. An equality on one of them between two row sources (`m0.series_name =
# m1.series_name`, `v.nse_symbol = c.nse_symbol`) makes both sources the same company or series.
ENTITY_KEYS = frozenset({"company", "short_name", "nse_symbol", "ticker", "isin", "bse_code", "cik", "series_name", "series_key",
                         "subject_key", "subject_name"})
# A partition, group, sort prefix or WHERE equality on one of these keeps every row of a group in one currency.
ONE_CURRENCY_KEYS = ENTITY_KEYS | {"market", "currency"}
_MONEY_AGGREGATES = frozenset({"sum", "avg", "min", "max", "stddev", "stddev_pop", "stddev_samp", "variance", "var_pop",
                               "var_samp", "percentile_cont", "percentile_disc", "mode", "median"})
_NOT_MONEY_FUNCS = frozenset({"count", "row_number", "rank", "dense_rank", "ntile", "percent_rank", "cume_dist"})
_ARITHMETIC = frozenset({"+", "-", "*", "/"})
_FILTER_SUBLINKS = frozenset({SubLinkType.EXISTS_SUBLINK, SubLinkType.ANY_SUBLINK, SubLinkType.ALL_SUBLINK,
                              SubLinkType.ROWCOMPARE_SUBLINK})


def _colname(node) -> str | None:
    if isinstance(node, ast.ColumnRef) and isinstance(node.fields[-1], ast.String):
        return node.fields[-1].sval
    return None


def _qualifier(node: ast.ColumnRef) -> str | None:
    return node.fields[-2].sval if len(node.fields) > 1 and isinstance(node.fields[-2], ast.String) else None


def _funcname(node: ast.FuncCall) -> str:
    return node.funcname[-1].sval.lower()


def _nodes(node, skip=lambda n: False):
    """Every node under `node`, depth first, not descending into a node `skip` accepts (it is still yielded)."""
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _nodes(n, skip)
        return
    if not isinstance(node, ast.Node):
        return
    yield node
    if skip(node):
        return
    for attr in node:
        yield from _nodes(getattr(node, attr), skip)


def _equalities(node):
    """The `a = b` terms among a condition's top-level AND terms."""
    if node is None:
        return
    if isinstance(node, ast.BoolExpr) and node.boolop == BoolExprType.AND_EXPR:
        for a in node.args:
            yield from _equalities(a)
    elif isinstance(node, ast.A_Expr) and node.kind == A_Expr_Kind.AEXPR_OP and node.name[-1].sval == "=":
        yield node


class _Scope:
    """One SELECT's row sources (the alias of each FROM item) and which of them name the same company or series: those an
    equality on one entity key ties together, in a JOIN's ON or USING or among the WHERE's top-level AND terms (transitively:
    `m1 = l` and `m0 = l` on series_name make m1 and m0 one series)."""

    def __init__(self, sel: ast.SelectStmt):
        self.aliases: list[str] = []
        self._parent: dict[str, str] = {}
        conditions = [sel.whereClause]
        for item in sel.fromClause or ():
            self._add(item, conditions)
        for cond in conditions:
            for eq in _equalities(cond):
                a, b = eq.lexpr, eq.rexpr
                if (isinstance(a, ast.ColumnRef) and isinstance(b, ast.ColumnRef) and _colname(a) == _colname(b)
                        and _colname(a) in ENTITY_KEYS):
                    qa, qb = self.source(a), self.source(b)
                    if qa is not None and qb is not None:
                        self._union(qa, qb)

    def _add(self, item, conditions: list) -> list[str]:
        if isinstance(item, ast.RangeVar):
            names = [item.alias.aliasname if item.alias else item.relname]
        elif isinstance(item, ast.RangeSubselect):
            names = [item.alias.aliasname] if item.alias else []
        elif isinstance(item, ast.JoinExpr):
            left, right = self._add(item.larg, conditions), self._add(item.rarg, conditions)
            names = left + right
            if item.quals is not None:
                conditions.append(item.quals)
            if any(isinstance(u, ast.String) and u.sval in ENTITY_KEYS for u in item.usingClause or ()):
                for a in left:
                    for b in right:
                        self._union(a, b)
        else:
            names = []
        for n in names:
            if n not in self._parent:
                self.aliases.append(n)
                self._parent[n] = n
        return names

    def _find(self, a: str) -> str:
        while self._parent[a] != a:
            a = self._parent[a]
        return a

    def _union(self, a: str, b: str) -> None:
        self._parent[self._find(a)] = self._find(b)

    def source(self, ref: ast.ColumnRef) -> str | None:
        """The row source a column reference reads: its qualifier, or for an unqualified one the SELECT's only source (or any
        source, when every source names the same company or series); None when that can't be told (refused, not guessed)."""
        q = _qualifier(ref)
        if q is not None:
            return q if q in self._parent else None
        if self.aliases and len({self._find(a) for a in self.aliases}) == 1:
            return self.aliases[0]
        return None

    def same_entity(self, sources: set) -> bool:
        return None not in sources and len({self._find(s) for s in sources}) == 1


def _money_sources(expr, money: set[str], scope: _Scope) -> set:
    """The row sources of the money `expr` carries (None for one that can't be placed: a scalar subquery, a window that isn't
    partitioned by one company or series, an outer reference). A window partitioned by an entity key (`lag(value) over
    (partition by series_key order by date)`) reads the current row's own company or series, so it keeps its argument's source."""
    out: set = set()

    def walk(n):
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
            return
        if not isinstance(n, ast.Node) or isinstance(n, ast.WindowDef):
            return
        if isinstance(n, ast.SubLink):
            if n.subLinkType not in _FILTER_SUBLINKS and _value_refs(n.subselect, money):
                out.add(None)
            return
        if isinstance(n, ast.FuncCall):
            if _funcname(n) in _NOT_MONEY_FUNCS:
                return
            if n.over is not None and not any(_colname(p) in ENTITY_KEYS for p in n.over.partitionClause or ()):
                if _value_refs(n.args, money):
                    out.add(None)
                return
            walk(n.args)
            walk(n.agg_order)
            return
        if _own_ratio(n, money, scope):
            return                                            # a nested own ratio carries no money
        if isinstance(n, ast.ColumnRef):
            if _colname(n) in money:
                out.add(scope.source(n))
            return
        for attr in n:
            walk(getattr(n, attr))

    walk(expr)
    return out


def _own_ratio(n, money: set[str], scope: _Scope | None) -> bool:
    """`a / b` where both sides carry money of one company or one series (one row's own columns, or two sources an equality on
    an entity key ties together): a margin, a growth rate, a percent change, so currency-neutral."""
    if scope is None or not (isinstance(n, ast.A_Expr) and n.kind == A_Expr_Kind.AEXPR_OP and n.name[-1].sval == "/"):
        return False
    left, right = _money_sources(n.lexpr, money, scope), _money_sources(n.rexpr, money, scope)
    return bool(left) and bool(right) and scope.same_entity(left | right)


def _value_refs(expr, money: set[str], scope: _Scope | None = None) -> list[ast.ColumnRef]:
    """The money column references whose value `expr` carries: not those that only order or partition a window, sit in a
    count or a ranking function, filter in an EXISTS or IN subquery, or (given the SELECT's scope) sit in a ratio of one
    company's or one series' own money (`_own_ratio`)."""
    def skip(n):
        return (isinstance(n, ast.WindowDef) or (isinstance(n, ast.FuncCall) and _funcname(n) in _NOT_MONEY_FUNCS)
                or (isinstance(n, ast.SubLink) and n.subLinkType in _FILTER_SUBLINKS) or _own_ratio(n, money, scope))
    out = []
    for n in _nodes(expr, skip):
        if isinstance(n, ast.FuncCall) and n.over is not None:
            out.extend(_value_refs(n.args, money, scope))    # a window aggregate's argument carries its value
            continue
        if isinstance(n, ast.ColumnRef) and _colname(n) in money:
            out.append(n)
    return out


def _selects(stmt):
    """Every SELECT of the query with its scope, and its own output columns (not those of a nested SELECT)."""
    for sel in (n for n in _nodes(stmt) if isinstance(n, ast.SelectStmt)):
        yield sel, _Scope(sel), [t for t in sel.targetList or () if isinstance(t, ast.ResTarget) and t.val is not None]


def _money_names(stmt) -> set[str]:
    """MONEY_COLUMNS plus every alias, at any level of the query, whose expression carries money (to a fixed point). An alias
    that is a ratio of one company's or one series' own money is currency-neutral, so it isn't one."""
    names = set(MONEY_COLUMNS)
    targets = [(t, scope) for _, scope, ts in _selects(stmt) for t in ts if t.name]
    grew = True
    while grew:
        grew = False
        for t, scope in targets:
            if t.name not in names and _value_refs(t.val, names, scope):
                names.add(t.name)
                grew = True
    return names


def _pinned(sel: ast.SelectStmt) -> bool:
    """True when the SELECT's WHERE holds `<one-currency key> = <constant>` among its top-level AND terms."""
    w = sel.whereClause
    terms = w.args if isinstance(w, ast.BoolExpr) and w.boolop == BoolExprType.AND_EXPR else (w,) if w is not None else ()
    for t in terms:
        if isinstance(t, ast.A_Expr) and t.kind == A_Expr_Kind.AEXPR_OP and t.name[-1].sval == "=":
            sides = (t.lexpr, t.rexpr)
            if any(_colname(x) in ONE_CURRENCY_KEYS for x in sides) and any(isinstance(x, ast.A_Const) for x in sides):
                return True
    return False


def _key_expr(sel: ast.SelectStmt, node):
    """A sort or group key: a position (`order by 2`) stands for that output column's expression."""
    if isinstance(node, ast.A_Const) and isinstance(getattr(node, "val", None), ast.Integer):
        i = node.val.ival - 1
        tl = sel.targetList or ()
        return tl[i].val if 0 <= i < len(tl) else None
    return node


def _one_currency(sel: ast.SelectStmt, keys) -> bool:
    return any(_colname(_key_expr(sel, k)) in ONE_CURRENCY_KEYS for k in keys or ())


def _sorts_money(sel: ast.SelectStmt, sort, money: set[str], scope: _Scope | None = None) -> bool:
    """An ORDER BY that ranks money: a key carrying money with no one-currency key before it."""
    for sb in sort or ():
        node = _key_expr(sel, sb.node)
        if node is None:
            return True                                       # an unknown position: refuse rather than guess
        if _colname(node) in ONE_CURRENCY_KEYS:
            return False                                      # sorted within one market or company first
        if _value_refs(node, money, scope) or _colname(node) in money:
            return True
    return False


def _combines_money(expr, money: set[str], scope: _Scope | None = None) -> bool:
    """Arithmetic, GREATEST or LEAST over money from two row sources (two table aliases, or a scalar subquery). A ratio of one
    company's or one series' own money (`_own_ratio`) combines nothing across currencies, so it isn't looked into."""
    for n in _nodes(expr, skip=lambda x: _own_ratio(x, money, scope)):
        if _own_ratio(n, money, scope):
            continue
        if isinstance(n, ast.A_Expr) and n.kind == A_Expr_Kind.AEXPR_OP and n.name[-1].sval in _ARITHMETIC:
            operands = (n.lexpr, n.rexpr)
        elif isinstance(n, ast.MinMaxExpr):
            operands = tuple(n.args or ())
        else:
            continue
        sources = set()
        for side in operands:
            if isinstance(side, ast.SubLink) and _value_refs(side.subselect, money):
                sources.add(("subquery", id(side)))
            for ref in _value_refs(side, money, scope):
                sources.add(("alias", _qualifier(ref)))
        if len(sources) > 1:
            return True
    return False


def money_comparison(model_sql: str, model_cols: list[str], model_rows: list[tuple], mapping: tuple[int, ...]) -> str | None:
    """For a cross-currency item whose rows matched: a reason the answer still ranks, sums or compares **money** across
    currencies (Spec 07 §6), else None. Money is `MONEY_COLUMNS` and any alias carrying it; currency-neutral figures (ratios,
    multiples, margins, growth, returns, percent changes) may be ranked and compared across markets (`CLAUDE.md` §1).
    - `orders_by_figure`: an ORDER BY (or a window's ORDER BY) on money, unless the SELECT is pinned to one market, company or
      series by a WHERE equality, or a one-currency key comes first (`order by market, market_cap`), or the window is
      partitioned by one (`partition by market`).
    - `sums_money`: sum, avg, min, max (and the like) over money, unless grouped, partitioned or pinned by a one-currency key.
    - `extra_numeric_column`: a numeric column beside the matched ones whose expression combines money from two row sources
      (a difference of two companies' revenue), or whose expression can't be found in the query (refused rather than guessed).
    Narrowed on Anand's answer (17 Nov 2026, #587; option 1B): the first version (16 Nov 2026, #582) failed any ORDER BY on a
    figure and any extra numeric column, which failed answers ranking P/E or margins, and the set's own sql-x06 gold.
    **A ratio of one company's or one series' own money is currency-neutral** (`_own_ratio`: both sides read the same row, or
    two row sources an equality on `company`, `nse_symbol`, `ticker`, `series_name`, `series_key` or another entity key ties
    together, so a percent change of one index between two dates or a margin is not money); a ratio across companies or series
    still counts as money. Loosened with Anand's approval (17 Nov 2026, 14:13 IST, #587, point 1 of option A), after round 2's
    sql-r2-x07 failed for ranking two indices' 12-month percent changes computed from their own levels; `CLAUDE.md` §1 counts
    growth rates as currency-neutral."""
    try:
        stmt = parse_sql(model_sql)[0].stmt
    except ParseError:                                   # it passed the guard, so it parses; refuse if it doesn't
        return "orders_by_figure"
    money = _money_names(stmt)
    selects = list(_selects(stmt))
    for sel, scope, _ in selects:
        if sel.valuesLists or _pinned(sel):
            continue
        if _sorts_money(sel, sel.sortClause, money, scope):
            return "orders_by_figure"
        grouped = _one_currency(sel, sel.groupClause)
        for n in _nodes(sel.targetList, skip=lambda x: isinstance(x, (ast.SelectStmt, ast.SubLink))):
            if isinstance(n, ast.FuncCall) and n.over is not None:
                w = n.over
                if _one_currency(sel, w.partitionClause):
                    continue
                if _sorts_money(sel, w.orderClause, money, scope):
                    return "orders_by_figure"
                if _funcname(n) in _MONEY_AGGREGATES and _value_refs(n.args, money, scope):
                    return "sums_money"
            elif (isinstance(n, ast.FuncCall) and _funcname(n) in _MONEY_AGGREGATES and _value_refs(n.args, money, scope)
                  and not grouped):
                return "sums_money"
    targets = [(t, scope) for _, scope, ts in selects for t in ts]
    matched = set(mapping)
    for m in range(len(model_cols)):
        if m in matched or not any(_is_num(r[m]) for r in model_rows):
            continue
        exprs = [(t.val, scope) for t, scope in targets if (t.name or _colname(t.val)) == model_cols[m]]
        if not exprs or any(_combines_money(e, money, scope) for e, scope in exprs):
            return "extra_numeric_column"
    return None


# ---------------------------------------------------------------------------------------------- items
@dataclass
class ItemResult:
    id: str
    correct: bool | None                       # None: unscored (the gold didn't run)
    reason: str
    family: str | None = None
    cross_currency: bool = False
    gold_answerable: bool | None = None
    gold_rows: int | None = None
    model_rows: int | None = None
    state: str | None = None
    sql: str | None = None
    error_class: str | None = None
    models: list[str] = field(default_factory=list)
    guard_verdict: str | None = None
    summary: str | None = None
    summary_grounded: bool | None = None
    route: str | None = None
    gold_route: str | None = None
    filters_agree: bool | None = None
    predicted_filters: dict | None = None
    cost_usd: Decimal = Decimal(0)
    ms: int = 0
    rank: int | None = None                    # retrieval: the first returned passage holding the expected span (1-based)
    returned: int | None = None                # retrieval: the Fed and RBI policy passages returned
    foreign: int | None = None                 # retrieval: returned rows that weren't such a passage (always 0: N3)
    retrieved: list | None = None              # retrieval: [document_id, chunk_no] of each returned passage, best first
    answer: dict | None = None                 # retrieval: the cited answer's counts (`answer_counts`)

    def annotation(self) -> dict:
        """What the log may show: ids, classes, counts, costs. Never a row, a summary, a passage or the SQL."""
        out = {"id": self.id, "correct": self.correct, "reason": self.reason}
        for k in ("gold_rows", "model_rows", "error_class", "route", "gold_route", "filters_agree", "rank", "returned",
                  "foreign", "answer"):
            if (v := getattr(self, k)) is not None:
                out[k] = v
        if self.models:
            out["models"] = self.models
        out["cost_usd"] = f"{self.cost_usd:.6f}"
        out["ms"] = self.ms
        return out


def run_gold(reader: sql_mod.Reader, item: dict) -> tuple[sql_mod.Result | None, str | None]:
    """The gold through the same guard and the same read-only run. (result, None) or (None, error class)."""
    try:
        return reader.run(sqlguard.guard(item["gold_sql"])), None
    except sqlguard.Rejected as e:
        return None, f"guard: {sql_mod.guard_class(e)}"
    except sql_mod.QueryError as e:
        return None, f"database: {e.kind}"
    except sql_mod.ReaderDown:
        return None, "database_unavailable"


def score_sql_item(item: dict, gold: sql_mod.Result | None, gold_error: str | None, ans: sql_mod.DataAnswer) -> ItemResult:
    r = ItemResult(item["id"], None, "", item.get("family"), bool(item.get("cross_currency")), bool(item["answerable"]),
                   state=ans.state, sql=ans.sql, error_class=ans.error_class, models=ans.models,
                   guard_verdict=ans.guard_verdict, summary=ans.summary,
                   summary_grounded=ans.summary_grounded if ans.state == "answered" else None,
                   model_rows=len(ans.rows) if ans.state == "answered" else None, cost_usd=ans.cost_usd, ms=ans.ms)
    if not item["answerable"]:
        r.correct = ans.state == "unanswerable"
        r.reason = {"unanswerable": "said_unanswerable", "answered": "answered_unanswerable"}.get(
            ans.state, f"cant_answer: {ans.error_class}")
        return r
    if gold is None:
        r.reason = f"gold_error: {gold_error}"
        return r
    r.gold_rows = len(gold.rows)
    if ans.state != "answered":
        r.correct = False
        r.reason = "said_unanswerable" if ans.state == "unanswerable" else f"cant_answer: {ans.error_class}"
        return r
    m = rows_match(gold.columns, gold.rows, ans.columns, ans.rows, bool(item.get("ordered")))
    r.correct, r.reason = m.ok, m.reason
    if m.ok and r.cross_currency and (why := money_comparison(_inner_sql(ans.sql), ans.columns, ans.rows, m.mapping)):
        r.correct, r.reason = False, why
    return r


def _inner_sql(guarded: str) -> str:
    """The model's query inside the guard's wrapper (`SELECT * FROM (<query>) AS q LIMIT n`), for its ORDER BY."""
    stmt = parse_sql(guarded)[0].stmt
    return stream.RawStream()(stmt.fromClause[0].subquery)


def score_router_item(item: dict, got: router_mod.Route | None, error_class: str | None, cost: Decimal, ms: int) -> ItemResult:
    r = ItemResult(item["id"], False, "", gold_route=item["route"], cost_usd=cost, ms=ms)
    if got is None:
        r.reason, r.error_class = f"error: {error_class}", error_class
        return r
    r.route = got.route
    r.correct = got.route == item["route"]
    r.reason = "route_match" if r.correct else "route_differs"
    r.predicted_filters = got.filters.as_json()
    if item.get("filters"):
        want, have = item["filters"], got.filters.as_json()
        agree = True
        for key in ("banks", "doc_types"):
            if key in want and set(want[key]) != set(have[key]):
                agree = False
        for key in ("date_from", "date_to"):
            if key in want and str(want[key]) != str(have[key]):
                agree = False
        r.filters_agree = agree
    return r


def item_spans(item: dict) -> list[tuple[int, int]]:
    """The item's expected passages: `spans` (round 2 items, K-06's option B: every passage of the document stating the whole
    answer; Anand, 17 Nov 2026, #587) or the one `char_start`/`char_end` span of the round 1 items."""
    if "spans" in item:
        return [(int(a), int(b)) for a, b in item["spans"]]
    return [(item["char_start"], item["char_end"])]


def expected_rank(item: dict, chunks: list[documents_mod.Chunk]) -> int | None:
    """The rank (1-based) of the first returned passage of the item's document that overlaps one of its spans, or None."""
    want = (item["bank"], item["doc_type"], item["event_slug"])
    spans = item_spans(item)
    for n, c in enumerate(chunks, 1):
        if (c.bank, c.doc_type, c.event_slug) == want and any(c.char_start < e and c.char_end > st for st, e in spans):
            return n
    return None


def answer_counts(item: dict, ans: documents_mod.DocAnswer) -> dict:
    """The cited answer's counts (never its text): sentences kept and dropped, verified citations, whether one cites the
    expected document, transcript characters and RBI links in what a visitor would see (both always 0)."""
    want = (item["bank"], item["doc_type"], item["event_slug"])
    refs = [r for s in ans.kept for r in s.refs]
    seen = json.dumps({"c": ans.citations, "d": ans.documents}, ensure_ascii=False).lower()
    transcript = sum(len(r.quote) for r in refs if not ans.chunks[r.chunk].is_policy)
    return {"state": ans.state, "kept": len(ans.kept), "dropped": len(ans.dropped), "refs": len(refs),
            "cites_expected": any((ans.chunks[r.chunk].bank, ans.chunks[r.chunk].doc_type, ans.chunks[r.chunk].event_slug) == want
                                  for r in refs),
            "transcript_chars": transcript, "rbi_links": seen.count("rbi.org.in")}


def score_retrieval_item(item: dict, ans: documents_mod.DocAnswer | None, error_class: str | None, cost: Decimal,
                         ms: int) -> ItemResult:
    r = ItemResult(item["id"], False, "", cost_usd=cost, ms=ms)
    if ans is None:
        r.reason, r.error_class = f"error: {error_class}", error_class
        return r
    r.rank = expected_rank(item, ans.chunks)
    r.correct, r.reason = r.rank is not None, ("hit" if r.rank is not None else "miss")
    r.returned, r.foreign = len(ans.chunks), ans.foreign
    r.retrieved = [[c.document_id, c.chunk_no] for c in ans.chunks]
    r.state, r.summary, r.error_class = ans.state, ans.summary, ans.error_class
    r.models = [ans.model] if ans.model else []
    r.answer = answer_counts(item, ans)
    if ans.error_class and not ans.chunks:
        r.reason = f"error: {ans.error_class}"
    return r


# ---------------------------------------------------------------------------------------------- metrics
def _frac(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def sql_metrics(results: list[ItemResult]) -> dict:
    scored = [r for r in results if r.correct is not None]
    cc = [r for r in scored if r.cross_currency]
    unans = [r for r in scored if r.gold_answerable is False]
    answered = [r for r in results if r.state == "answered"]
    return {
        "n": len(results), "scored": len(scored), "unscored": len(results) - len(scored),
        "correct": sum(1 for r in scored if r.correct), "accuracy": _frac(sum(1 for r in scored if r.correct), len(scored)),
        "cross_currency": {"n": len(cc), "correct": sum(1 for r in cc if r.correct),
                           "accuracy": _frac(sum(1 for r in cc if r.correct), len(cc))},
        "unanswerable": {"n": len(unans), "correct": sum(1 for r in unans if r.correct)},
        "by_family": {f: {"n": sum(1 for r in scored if r.family == f), "correct": sum(1 for r in scored if r.family == f and r.correct)}
                      for f in sorted({r.family for r in scored if r.family})},
        "reasons": dict(Counter(r.reason for r in results)),
        "retried": sum(1 for r in results if len(r.models) > 1),
        "guard_rejections": sum(1 for r in results if (r.error_class or "").startswith("guard")),
        "summaries": {"answered": len(answered), "grounded": sum(1 for r in answered if r.summary_grounded)},
    }


def sql_rule_met(m: dict) -> bool:
    acc, cc = m["accuracy"], m["cross_currency"]
    return (m["unscored"] == 0 and acc is not None and acc >= 0.85
            and (cc["n"] == 0 or cc["correct"] == cc["n"]))


def router_metrics(results: list[ItemResult]) -> dict:
    with_filters = [r for r in results if r.filters_agree is not None]
    confusion = Counter(f"{r.gold_route}->{r.route}" for r in results)
    return {
        "n": len(results), "correct": sum(1 for r in results if r.correct),
        "accuracy": _frac(sum(1 for r in results if r.correct), len(results)),
        "unanswerable": {"n": sum(1 for r in results if r.gold_route == "unsupported"),
                         "answered": sum(1 for r in results if r.gold_route == "unsupported" and r.route in ("data", "document"))},
        "filters": {"n": len(with_filters), "agree": sum(1 for r in with_filters if r.filters_agree)},
        "confusion": dict(sorted(confusion.items())),
        "errors": sum(1 for r in results if r.route is None),
    }


def router_rule_met(m: dict) -> bool:
    return m["accuracy"] is not None and m["accuracy"] >= 0.90 and m["unanswerable"]["answered"] == 0


def retrieval_metrics(results: list[ItemResult]) -> dict:
    n = len(results)
    hits = sum(1 for r in results if r.correct)
    answers = [r.answer for r in results if r.answer is not None]
    return {
        "n": n, "hits": hits, "recall_at_8": _frac(hits, n),
        "mrr": round(sum(1 / r.rank for r in results if r.rank) / n, 4) if n else None,
        "foreign": sum(r.foreign or 0 for r in results), "errors": sum(1 for r in results if r.reason.startswith("error")),
        "reasons": dict(Counter(r.reason for r in results)),
        "answers": {"n": len(answers), "states": dict(Counter(a["state"] for a in answers)),
                    "sentences_kept": sum(a["kept"] for a in answers), "sentences_dropped": sum(a["dropped"] for a in answers),
                    "refs": sum(a["refs"] for a in answers), "cites_expected": sum(1 for a in answers if a["cites_expected"]),
                    "transcript_chars": sum(a["transcript_chars"] for a in answers),
                    "rbi_links": sum(a["rbi_links"] for a in answers)},
    }


def retrieval_rule_met(m: dict) -> bool:
    return m["recall_at_8"] is not None and m["recall_at_8"] >= 0.85 and m["foreign"] == 0


# ---------------------------------------------------------------------------------------------- price
def _price(model: str, on: date) -> dict:
    m, _, _ = price_entry(model, on)
    return {k: Decimal(str(v)) for k, v in m["standard"].items()}


def request_cost(req: Request, on: date, *, output_tokens: int | None = None, cached: bool = False) -> Decimal:
    """A request's cost from its estimated input (uncached, or read from the cache) and `output_tokens` (default: its cap)."""
    p = _price(req.model_id, on)
    inp = Decimal(req.estimated_tokens()) * (p["cache_read"] if cached else p["input"])
    out = Decimal(req.spec.max_tokens if output_tokens is None else output_tokens) * p["output"]
    return (inp + out) / MILLION


def sql_item_worst(question: str, prompt, on: date) -> Decimal:
    first = sql_mod.request(question, prompt)
    retry = sql_mod.request(question, prompt, model=sql_mod.RETRY_MODEL, note=sql_mod.retry_note("guard: x"))
    p = _price(sql_mod.FIRST_MODEL, on)            # the summary runs on the first model (Haiku 5.5)
    summary = (Decimal(SUMMARY_INPUT_TOKENS_EST) * p["input"] + Decimal(3_000) * p["output"]) / MILLION
    return request_cost(first, on) + request_cost(retry, on) + summary


def answer_request_est(question: str) -> Request:
    """A document answer's request with 8 passages of PASSAGE_CHARS_EST characters: what retrieval could send, over-estimated."""
    chunk = documents_mod.Chunk(0, 0, 0, PASSAGE_CHARS_EST, None, None, "x" * PASSAGE_CHARS_EST, "fomc_statement",
                                "FOMC statement, 1 Jan 2026", "fed", None, None, None)
    return documents_mod.request(question, [chunk] * documents_mod.K)


def price_run(set_name: str, items: list[dict], prompt, on: date) -> dict:
    """{estimate, worst_case, per_item_worst} in USD. The estimate: the first request uncached once, then cache reads, about
    1,500 output tokens, a summary, no retry. The worst case: every request at its cap, every SQL item retried on Sonnet. A
    retrieval item: one answer request over 8 passages (uncached: each has its own passages), about 400 output tokens."""
    est = worst = Decimal(0)
    per = {}
    for n, it in enumerate(items):
        if set_name == "retrieval":
            req = answer_request_est(it["question"])
            w = request_cost(req, on)
            e = request_cost(req, on, output_tokens=400)
        elif set_name == "cmdbar_sql":
            req = sql_mod.request(it["question"], prompt)
            w = sql_item_worst(it["question"], prompt, on)
            p = _price(req.model_id, on)
            e = (request_cost(req, on, output_tokens=1_500, cached=n > 0)
                 + (Decimal(1_500) * p["input"] + Decimal(150) * p["output"]) / MILLION)
        else:
            req = router_mod.request(it["question"])
            w = request_cost(req, on)
            e = request_cost(req, on, output_tokens=300, cached=n > 0)
        per[it["id"]] = w
        est, worst = est + e, worst + w
    return {"estimate": est, "worst_case": worst, "per_item_worst": per}


# ---------------------------------------------------------------------------------------------- record
def record(conn, set_name: str, split: str, set_version: str, model: str, prompt_version: str, metrics: dict,
           passed: bool, notes: str, results: list[ItemResult], expected_of) -> int:
    from psycopg.types.json import Jsonb
    with conn.transaction():
        run_id = conn.execute(
            """insert into ops.eval_run (eval_set, set_version, model, prompt_version, n_items, metrics, passed, notes)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (set_name, set_version, model, prompt_version, len(results), Jsonb(metrics), passed, notes)).fetchone()[0]
        for r in results:
            predicted = {"state": r.state, "route": r.route, "sql": r.sql, "rows": r.model_rows, "error_class": r.error_class,
                         "models": r.models, "guard": r.guard_verdict, "reason": r.reason, "summary": r.summary,
                         "summary_grounded": r.summary_grounded, "filters": r.predicted_filters, "rank": r.rank,
                         "retrieved": r.retrieved, "foreign": r.foreign, "answer": r.answer}
            conn.execute(
                """insert into ops.eval_result (eval_run_id, item_key, expected, predicted, correct, latency_ms, cost_usd)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (run_id, r.id, Jsonb(expected_of(r)), Jsonb({k: v for k, v in predicted.items() if v is not None}),
                 r.correct, r.ms, r.cost_usd))
    return run_id


# ---------------------------------------------------------------------------------------------- the CLI
def annotation(title: str, payload) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"), default=str)
    esc = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::notice title={title}::{esc}"


class QueryModel:
    """The retrieval set's questions embedded with another Voyage 4 model, for Architecture 03 §0's comparison ("the retrieval
    evaluation also runs `voyage-4` questions: the better of the two ships"). It wraps an `Embedder` and changes only the
    question's model: the dimension, input type and retries are the Embedder's. The route itself always embeds questions with
    `QUERY_MODEL` (`voyage-4` since 17 Nov 2026); only this runner uses the wrapper."""

    def __init__(self, embedder: Embedder, model: str):
        self._embedder, self.model = embedder, model

    def embed_query(self, text: str) -> list[float]:
        return self._embedder._embed([text], self.model, "query").embeddings[0]


def _connect_writer(dsn: str):
    import psycopg
    conn = psycopg.connect(dsn, autocommit=True, application_name="ft-cmdbar-gates")
    conn.execute("set time zone 'UTC'")
    return conn


def main(argv: list[str] | None = None, *, env=None, out=print, client=None, reader=None, writer=None, embedder=None,
         today=lambda: datetime.now(UTC).date()) -> int:
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(prog="python -m ft.eval.cmdbar", description=__doc__.split("\n\n")[0])
    p.add_argument("--set", required=True, choices=SETS)
    p.add_argument("--split", required=True, choices=SPLITS)
    p.add_argument("--items", default="", help="only these item ids of the split (comma or space separated)")
    p.add_argument("--send", action="store_true", help="answer the items (spends); without it: the price and the golds only")
    p.add_argument("--max-cost", type=Decimal, default=DEFAULT_MAX_COST, help="stop before spend could pass this (USD)")
    p.add_argument("--query-model", choices=QUERY_MODELS, default=QUERY_MODEL,
                   help="retrieval only: the Voyage model for the questions (the route's is voyage-4)")
    args = p.parse_args(argv)
    if args.query_model != QUERY_MODEL and args.set != "retrieval":
        p.error("--query-model applies to the retrieval set only")
    ids = [x for x in args.items.replace(",", " ").split() if x]
    try:
        items = load_items(args.set, args.split, ids or None)
    except ValueError as e:
        p.error(str(e))
    needs_reader = args.set in ("cmdbar_sql", "retrieval")
    if needs_reader and reader is None and not (env.get("SQL_READER_DATABASE_URL") or "").strip():
        p.error("SQL_READER_DATABASE_URL is not set (the workflow reads the DEV_ secret)")
    if args.send:
        if client is None and not (env.get("ANTHROPIC_API_KEY") or "").strip():
            p.error("ANTHROPIC_API_KEY is not set")
        if args.set == "retrieval" and embedder is None and not (env.get("VOYAGE_API_KEY") or "").strip():
            p.error("VOYAGE_API_KEY is not set")
        if writer is None and not (env.get("JOB_DATABASE_URL") or "").strip():
            p.error("JOB_DATABASE_URL is not set (the workflow reads the DEV_ secret)")
    on = today()
    set_version = git_sha(env)
    info = {"set": args.set, "split": args.split, "items": len(items), "set_version": set_version,
            "set_file_sha256": file_sha256(set_path(args.set))}

    prompt = None
    golds: dict[str, tuple] = {}
    own_reader = False
    try:
        if needs_reader and reader is None:
            try:
                reader = sql_mod.Reader.connect(env["SQL_READER_DATABASE_URL"].strip())
            except sql_mod.ReaderDown as e:
                out(f"::error title=sql_reader::could not connect as sql_reader ({e})")
                return 1
            own_reader = True
        if args.set == "retrieval":
            try:
                info["search_chunks_executable"] = bool(reader.select(SEARCH_EXECUTABLE_SQL).rows[0][0])
                info["query_model"] = args.query_model
            except (sql_mod.QueryError, sql_mod.ReaderDown) as e:
                out(f"::error title=sql_reader::could not check search_chunks as sql_reader ({type(e).__name__})")
                return 1
        elif needs_reader:
            docs, groups = reader.describe()
            missing = sorted(sqlguard.VIEWS - {d.name for d in docs})
            prompt = sql_mod.sql_prompt(sql_mod.render_views(docs, groups))
            info |= {"views_described": len(docs), "views_missing": missing, "prompt_sha256": prompt.sha256}
            for it in items:
                if it["answerable"]:
                    t0 = time.monotonic()
                    res, err = run_gold(reader, it)
                    golds[it["id"]] = (res, err, int((time.monotonic() - t0) * 1000))
            info["golds"] = {"run": sum(1 for g in golds.values() if g[0] is not None),
                             "errors": {k: g[1] for k, g in golds.items() if g[1]},
                             "rows": {k: len(g[0].rows) for k, g in golds.items() if g[0] is not None},
                             "ms": {k: g[2] for k, g in golds.items()}}
            if missing:
                out(f"::warning title=views::sql_reader can't describe {', '.join(missing)}")
        price = price_run(args.set, items, prompt, on)
        info["price_usd"] = {"estimate": f"{price['estimate']:.4f}", "worst_case": f"{price['worst_case']:.4f}",
                             "max_cost": str(args.max_cost)}
        out(annotation(f"{args.set} {args.split}", info))
        if not args.send:
            out(annotation("dry run", "nothing sent to a model; run again with send on to answer and record"))
            return 0

        claude = client or Client(env["ANTHROPIC_API_KEY"].strip())
        voyage = embedder
        if args.set == "retrieval" and voyage is None:
            voyage = Embedder(env["VOYAGE_API_KEY"].strip())
        if args.set == "retrieval" and args.query_model != QUERY_MODEL:
            voyage = QueryModel(voyage, args.query_model)
        results: list[ItemResult] = []
        spent, stopped = Decimal(0), False
        for it in items:
            if spent + price["per_item_worst"][it["id"]] > args.max_cost:
                stopped = True
                out(f"::warning title=budget stop::stopped before {it['id']}: spend {spent:.4f} USD plus its worst case "
                    f"would pass {args.max_cost}")
                break
            if args.set == "cmdbar_sql":
                ans = sql_mod.answer_data(claude, reader, it["question"], prompt)
                g = golds.get(it["id"], (None, None, 0))
                r = score_sql_item(it, g[0], g[1], ans)
            elif args.set == "retrieval":
                t0 = time.monotonic()
                try:
                    doc = documents_mod.answer_documents(claude, voyage, reader, it["question"])
                    r = score_retrieval_item(it, doc, None, doc.cost_usd, doc.ms)
                except AIError as e:                        # no key or no credit (Voyage's or Claude's): recorded, not raised
                    r = score_retrieval_item(it, None, type(e).__name__, Decimal(0), int((time.monotonic() - t0) * 1000))
            else:
                t0 = time.monotonic()
                try:
                    got = router_mod.route(claude, it["question"])
                    r = score_router_item(it, got, None, got.reply.cost_usd, int((time.monotonic() - t0) * 1000))
                except AIError as e:                        # one item's failure is recorded, not raised
                    cost = getattr(getattr(e, "reply", None), "cost_usd", Decimal(0))
                    r = score_router_item(it, None, type(e).__name__, cost, int((time.monotonic() - t0) * 1000))
            spent += r.cost_usd
            results.append(r)

        if args.set == "cmdbar_sql":
            metrics = sql_metrics(results)
            met = sql_rule_met(metrics)
            prompt_version, model = sql_mod.PROMPT_VERSION, sql_mod.FIRST_MODEL
            metrics |= {"prompt_sha256": prompt.sha256, "summary_prompt": summary_mod.PROMPT_VERSION,
                        "retry_model": sql_mod.RETRY_MODEL}
            gold_rows = {k: (len(g[0].rows) if g[0] is not None else None) for k, g in golds.items()}

            def expected_of(r: ItemResult) -> dict:
                it = next(i for i in items if i["id"] == r.id)
                return {"answerable": it["answerable"], "rows": gold_rows.get(r.id), "cross_currency": bool(it.get("cross_currency")),
                        "ordered": bool(it.get("ordered")), "family": it.get("family")}
        elif args.set == "retrieval":
            metrics = retrieval_metrics(results)
            met = retrieval_rule_met(metrics)
            prompt_version, model = documents_mod.PROMPT_VERSION, documents_mod.MODEL
            metrics |= {"embedding_model": args.query_model, "k": documents_mod.K, "banks": list(documents_mod.BANKS),
                        "doc_types": list(documents_mod.DOC_TYPES)}

            def expected_of(r: ItemResult) -> dict:
                it = next(i for i in items if i["id"] == r.id)
                return {**{k: it[k] for k in ("bank", "doc_type", "event_slug")}, "spans": [list(x) for x in item_spans(it)]}
        else:
            metrics = router_metrics(results)
            met = router_rule_met(metrics)
            prompt_version, model = router_mod.PROMPT_VERSION, router_mod.request(items[0]["question"]).model_id

            def expected_of(r: ItemResult) -> dict:
                it = next(i for i in items if i["id"] == r.id)
                return {"route": it["route"], **({"filters": it["filters"]} if it.get("filters") else {})}
        complete = not stopped and len(results) == len(items)
        comparison = args.set == "retrieval" and args.query_model != QUERY_MODEL     # not the route's model: never the gate
        passed = args.split == "test" and met and complete and not comparison
        metrics |= {"split": args.split, "gate_rule": GATE[args.set], "rule_met": met, "complete": complete,
                    "stopped_by_budget": stopped, "cost_usd": f"{spent:.6f}", "set_file_sha256": info["set_file_sha256"],
                    "mean_ms": int(sum(r.ms for r in results) / len(results)) if results else 0,
                    "max_ms": max((r.ms for r in results), default=0)}
        notes = ("dev split: prompt work, not the gate (Spec 07 N5)" if args.split == "dev"
                 else f"test split: questions embedded with {args.query_model}, a comparison beside the route's {QUERY_MODEL}, "
                      "not the gate (Architecture 03 §0)" if comparison
                 else "test split: the gate run (Spec 07 §6)")
        conn = writer or _connect_writer(env["JOB_DATABASE_URL"].strip())
        try:
            run_id = record(conn, args.set, args.split, set_version, model, prompt_version, metrics, passed, notes,
                            results, expected_of)
        finally:
            if writer is None:
                conn.close()
        # One annotation for every item, then the result: GitHub keeps only the first 10 notices of a step, so one per item
        # would drop the result of any split over 9 items (the 16 Nov dev run of cmdbar_router lost its last item and result).
        out(annotation(f"{args.set} {args.split} items", [r.annotation() for r in results]))
        out(annotation(f"{args.set} {args.split} result", {"eval_run_id": run_id, "passed": passed, **metrics}))
        return 0
    finally:
        if own_reader:
            reader.close()


if __name__ == "__main__":
    sys.exit(main())
