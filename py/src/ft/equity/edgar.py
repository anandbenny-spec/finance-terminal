"""SEC EDGAR: a US company's company facts as `equity.financials` rows, one filing at a time (Spec 02A.1 §5.2; job
US-02, #401).

Generalised from the engine test's helper `py/tests/equity_edgar.py` (its second use: `CLAUDE.md` §6, reuse rule 2),
which imports its rules from here, its tests unchanged. The rules (Data Audit 02 rows D1–D3, red flag 10, 02a finding
14):
- facts from periodic reports only (10-K, 10-Q and their variants: a proxy statement's rounded figures lost to them in
  E1's 19 Oct run);
- durations classed by day count, never by the fiscal-period labels (`F.kind`);
- a chain of tags per field, the primary the first still current (`pick_tags`: a tag abandoned for over 400 days
  gives way, the revenue tag changes of red flag 10), the rest filling only what the primary lacks;
- year-end balances chosen by date (`fiscal_year_ends`), never by the form of the latest filing;
- CIK succession: a predecessor CIK's facts merged, the successor's winning (`merge_companyfacts`);
- equity fills net of non-controlling interests (a fill from the other equity concept adjusts by `MinorityInterest`
  at the same date in the same filing; a filing that presents no `MinorityInterest` at all has none to adjust by:
  Salesforce and Cisco, which had NCI years ago, on dev run 1 of 11 Nov 2026);
- Q4 = the fiscal year − the nine-month year to date; year-to-date cash flow differenced into quarters;
- cover-page shares summed across classes: from company facts' `dei:EntityCommonStockSharesOutstanding` for a
  single-class issuer, from the latest filing's cover page (inline XBRL, `read_cover`) for a multi-class one, whose
  per-class figures company facts doesn't hold.

**One filing at a time** (`Statements.rows`): each 10-K and 10-Q (and amendment) gives the rows of its own period, as
that filing filed them (a later filing's restated comparative never rewrites an earlier filing's row, as India's
results filings): `quarter` (the three months; a 10-K's from the year less the nine months, unless it files the
quarter), the year to date (`half_year`, `nine_months`, `year`) and `instant` (the balance sheet at the period end,
with the cover page's shares). The period end is the latest end of the filing's own durations that carries at least a fifth of its
busiest end's facts (it equals SEC's `reportDate` for every one of the 219 filings the saved submissions list; a
forward-looking line never sets it); the task checks it against SEC where SEC's
list holds the filing. The column map and the tag chains are `py/config/equity/us_tags.yaml`. A column whose meaning
isn't filed is null, never computed from other lines, except the quarter differences above, the equity fills, current
debt filed in parts (its filed captions summed: #401's question 1, option A; `Statements._parts`; other short-term
borrowings beside commercial paper: #470) and pre-tax income filed only as its domestic and foreign parts
(`Statements._all_parts`, #470); `tags` names the element behind each value or why it is held. A parts rule is off,
held, for a company whose filed totals don't tie with their parts (`Statements.ties`, the task's parts_tie warning).

**A row that lacks a figure never displaces one that has it** (`never_displace`, #455): a reader takes the latest
filed row for each period, so a later filing's row for a period an earlier filing's row already covers is stored only
if it has every figure that row has. Disney's FY2023 10-K/A (a Part III amendment that tags only net income) would
otherwise have replaced its 10-K's year, quarter and balance sheet rows (#403's dev runs, 12 Nov 2026).
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

# Bump it when a change alters what a filing's rows are: the task rebuilds older filings' rows from that run's bytes.
PARSER_VERSION = 3           # 3 (#470, 12 Nov 2026): IBM's filed total, other short-term borrowings beside commercial
                             # paper, pre-tax income in parts, the parts rules off where they don't tie;
                             # 2 (#455, 12 Nov 2026): current debt summed from its filed captions, no row displacing a
                             # fuller one, Visa's share classes (1: US-02's first load, #401)
TAGS_FILE = Path(__file__).resolve().parents[3] / "config" / "equity" / "us_tags.yaml"

PERIODIC_FORMS = {"10-K", "10-Q", "10-KT", "10-QT", "20-F", "40-F"}    # whose facts count (the helper's rule)
REPORT_FORMS = ("10-K", "10-Q", "10-KT", "10-QT")                      # what the task registers (and their /A)
STALE_DAYS = 400
MI = "MinorityInterest"
SHARES_TAG = "EntityCommonStockSharesOutstanding"
YTD_KIND = {"Q": "quarter", "6M": "half_year", "9M": "nine_months", "FY": "year"}
PRIOR_KIND = {"6M": "Q", "9M": "6M", "FY": "9M"}                         # the year to date before each
MONEY, PER_SHARE, SHARES = "USD", "USD/shares", "shares"
PERIOD_SHARE = 5                 # a period end carries at least a fifth of the busiest end's facts
ZERO = Decimal(0)


class EdgarError(Exception):
    """SEC's company facts can't be read as this module reads them (not JSON, no facts)."""


# ------------------------------------------------------------------------------------------------ facts
@dataclass(frozen=True)
class F:
    tag: str
    start: dt.date | None
    end: dt.date
    val: Decimal
    filed: dt.date
    form: str
    accn: str = ""
    unit: str = ""

    @property
    def days(self) -> int | None:
        return (self.end - self.start).days + 1 if self.start else None

    @property
    def kind(self) -> str:
        d = self.days
        if d is None:
            return "instant"
        for name, lo, hi in (("Q", 80, 100), ("6M", 170, 195), ("9M", 260, 285), ("FY", 350, 380)):
            if lo <= d <= hi:
                return name
        return f"{d}d"


def _d(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s) if s else None


def merge_companyfacts(primary: dict, predecessors: list[dict]) -> dict:
    """One company's facts from its CIK and its predecessors' (CIK succession), the successor's first: on equal filing
    dates its facts win."""
    if not predecessors:
        return primary
    out = {"cik": primary.get("cik"), "entityName": primary.get("entityName"), "facts": {}}
    for doc in [primary] + predecessors:
        for tax, tags in doc.get("facts", {}).items():
            for tag, body in tags.items():
                dst = out["facts"].setdefault(tax, {}).setdefault(tag, {"units": {}})
                for unit, entries in body.get("units", {}).items():
                    dst["units"].setdefault(unit, []).extend(entries)
    return out


def load_facts(cf: dict) -> dict[str, list[F]]:
    """tag → facts from periodic reports, one per (start, end), the latest filed winning (an amendment on a tie). For
    merged CIKs the successor's entries come first, so on equal filing dates they win."""
    out: dict[str, list[F]] = {}
    for tag, body in cf.get("facts", {}).get("us-gaap", {}).items():
        for unit, entries in body.get("units", {}).items():
            best: dict[tuple, F] = {}
            for e in entries:
                if (e.get("form") or "").split("/")[0] not in PERIODIC_FORMS:
                    continue
                try:
                    f = F(tag, _d(e.get("start")), _d(e["end"]), Decimal(str(e["val"])), _d(e["filed"]),
                          e.get("form", ""), e.get("accn", ""), unit)
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue
                k = (f.start, f.end)
                if k not in best or f.filed > best[k].filed or (f.filed == best[k].filed and f.form.endswith("/A")):
                    best[k] = f
            if best:
                out[tag] = list(best.values())
            break                                       # one unit family per tag
    return out


def pick_tags(facts: dict[str, list[F]], candidates: list[str], flow: bool) -> list[str]:
    """The candidates present, the primary first: the first with at least four facts of its type whose latest is
    within STALE_DAYS of the newest such tag's."""
    present = [c for c in candidates if c in facts]

    def typed(c):
        return [f for f in facts[c] if (f.start is not None) == flow]
    ok = [c for c in present if len(typed(c)) >= 4]
    if not ok:
        return present
    latest = {c: max(f.end for f in typed(c)) for c in ok}
    newest = max(latest.values())
    primary = next(c for c in ok if (newest - latest[c]).days <= STALE_DAYS)
    return [primary] + [c for c in present if c != primary]


def merged(facts: dict[str, list[F]], tags: list[str], flow: bool, adjust: dict[str, int] | None = None) -> list[F]:
    """One field's facts: the primary's periods, then each fallback's for the periods still missing. `adjust` maps a
    fallback tag to the sign its value takes `MinorityInterest` at the same date with (an equity fill); a fallback
    period with no `MinorityInterest` at its date is skipped when the company files that tag at all."""
    out: dict[tuple, F] = {}
    nci = None
    for i, t in enumerate(pick_tags(facts, tags, flow)):
        sign = (adjust or {}).get(t) if i > 0 else None
        if sign and nci is None:
            nci = {f.end: f.val for f in facts.get(MI, []) if f.start is None}
        for f in facts.get(t, []):
            k = (f.start, f.end)
            if k in out:
                continue
            if sign:
                if f.end in nci:
                    f = replace(f, val=f.val + sign * nci[f.end], tag=f"{t}+{MI}")
                elif MI in facts:
                    continue
            out[k] = f
    return list(out.values())


def fiscal_year_ends(facts: dict[str, list[F]], min_tags: int = 5) -> list[dt.date]:
    """The fiscal year ends: the 12-month 10-K end dates that at least `min_tags` tags (or half of those with any)
    share."""
    count: dict[dt.date, int] = defaultdict(int)
    n_tags = 0
    for fs in facts.values():
        ends = {f.end for f in fs if f.start is not None and f.kind == "FY" and f.form.startswith("10-K")}
        n_tags += bool(ends)
        for e in ends:
            count[e] += 1
    need = min(min_tags, max(1, n_tags // 2))
    return sorted(e for e, n in count.items() if n >= need)


def cover_shares(cf: dict) -> tuple[Decimal, dt.date] | None:
    """The newest cover page's `dei:EntityCommonStockSharesOutstanding` in company facts, summed across its entries."""
    entries = cf.get("facts", {}).get("dei", {}).get(SHARES_TAG, {}).get("units", {}).get(SHARES, [])
    groups: dict[tuple, list] = defaultdict(list)
    filed: dict[str, str] = {}
    for e in entries:
        groups[(e["accn"], e["end"])].append(Decimal(str(e["val"])))
        filed[e["accn"]] = e.get("filed") or ""
    if not groups:
        return None
    accn, end = max(groups, key=lambda k: (k[1], filed[k[0]]))
    return sum(groups[(accn, end)], Decimal(0)), dt.date.fromisoformat(end)


# ------------------------------------------------------------------------------------------------ the column map
@dataclass(frozen=True)
class Entry:
    tag: str
    nci: int = 0                     # +1: add MinorityInterest at the same date; -1: subtract it; 0: as filed


@dataclass(frozen=True)
class FiledTotal:
    """A company whose one filed caption is its whole current debt (IBM's short-term debt: #455's question 1; #470)."""
    tag: str
    label: str
    source: str
    never_below: tuple[str, ...] = ()   # a part it includes: a date where the first non-zero of them exceeds it is held


@dataclass(frozen=True)
class Column:
    name: str
    kind: str                        # flow, cash_flow, per_share, instant
    chain: tuple[Entry, ...]
    parts: tuple[str, ...] = ()      # instant columns filed in parts (no total in the chain): see Statements._parts
    sum: tuple[tuple[str, ...], ...] = ()    # the parts added: one from each group (its first non-zero), never two
    included: dict[str, tuple[str, ...]] = field(default_factory=dict)   # a part -> the parts it already includes
    not_summed: dict[str, str] = field(default_factory=dict)            # ticker -> why its parts are never added
    added_to: dict[str, tuple[str, ...]] = field(default_factory=dict)  # a group's pick -> parts added beside it (#470)
    tie: dict[str, tuple[str, ...]] = field(default_factory=dict)       # a filed total -> the parts it holds (parts_tie)
    filed_total: dict[str, FiledTotal] = field(default_factory=dict)    # ticker -> its one caption that is the whole
    all_parts: tuple[str, ...] = ()  # flows: where no chain tag is filed for a period and all of these are, their sum
    all_parts_label: str = ""

    @property
    def unit(self) -> str:
        return PER_SHARE if self.kind == "per_share" else MONEY

    @property
    def flow(self) -> bool:
        return self.kind != "instant"


@dataclass(frozen=True)
class Rate:
    """A share class's conversion rate into the reference class, with where it comes from."""
    ratio: Decimal
    source: str = ""
    effective: dt.date | None = None    # the day it took effect, where the source gives it (reviewed after review_days)
    as_of: dt.date | None = None        # the day the source states it


@dataclass(frozen=True)
class Extra:
    """Shares counted beside the cover page's classes, in the reference class (Visa's preferred stock as converted)."""
    shares: Decimal
    note: str


@dataclass(frozen=True)
class ShareClasses:
    note: str
    ratios: dict[str, Decimal]       # class member (local name) -> shares of the reference class per share
    rates: dict[str, Rate] = field(default_factory=dict)
    extra: tuple[Extra, ...] = ()
    review_days: int | None = None   # a rate with an effective date older than this before the cover page: a warning

    def stale(self, cover_end: dt.date) -> list[str]:
        """The rates with an effective date more than `review_days` before the cover page's date."""
        if not self.review_days:
            return []
        return [m for m, r in sorted(self.rates.items())
                if r.effective is not None and (cover_end - r.effective).days > self.review_days]


@dataclass(frozen=True)
class TagConfig:
    start: dt.date
    columns: dict[str, Column]
    liabilities: str
    balance_total: str
    temporary_equity: tuple[str, ...]
    temporary_equity_total: str
    tolerance: Decimal
    share_classes: dict[str, ShareClasses]
    coverage_exceptions: dict[str, str]

    def by_kind(self, *kinds: str) -> list[Column]:
        return [c for c in self.columns.values() if c.kind in kinds]


KINDS = ("flow", "cash_flow", "per_share", "instant")


def _positive(ticker: str, what: str, r) -> Decimal:
    try:
        v = Decimal(str(r))
    except InvalidOperation:
        v = ZERO
    if not v.is_finite() or v <= 0:
        raise EdgarError(f"us_tags.yaml: share_classes {ticker} {what}: a ratio is a positive number")
    return v


def _share_classes(ticker: str, c) -> ShareClasses:
    """One issuer's classes: each ratio a number, or {ratio, source, effective, as_of} where it changes over time or
    needs its source beside it; `extra` shares counted beside the cover page's classes; `review_days`."""
    if not (c or {}).get("note") or not isinstance(c.get("ratios"), dict) or not c["ratios"]:
        raise EdgarError(f"us_tags.yaml: share_classes {ticker} needs its note and ratios")
    ratios, rates = {}, {}
    for member, r in c["ratios"].items():
        if isinstance(r, dict):
            if not r.get("source"):
                raise EdgarError(f"us_tags.yaml: share_classes {ticker} {member}: a rate given as a mapping needs its source")
            for k in ("effective", "as_of"):
                if r.get(k) is not None and not isinstance(r[k], dt.date):
                    raise EdgarError(f"us_tags.yaml: share_classes {ticker} {member}: {k} is a date (YYYY-MM-DD)")
            ratios[member] = _positive(ticker, member, r.get("ratio"))
            rates[member] = Rate(ratios[member], str(r["source"]), r.get("effective"), r.get("as_of"))
        else:
            ratios[member] = _positive(ticker, member, r)
    extra = []
    for e in c.get("extra") or []:
        if not isinstance(e, dict) or not e.get("note"):
            raise EdgarError(f"us_tags.yaml: share_classes {ticker}: an extra entry needs its shares and note")
        extra.append(Extra(_positive(ticker, "extra shares", e.get("shares")), str(e["note"])))
    review = c.get("review_days")
    if review is not None and (not isinstance(review, int) or review <= 0):
        raise EdgarError(f"us_tags.yaml: share_classes {ticker}: review_days is a positive whole number")
    return ShareClasses(c["note"], ratios, rates, tuple(extra), review)


def _filed_total(col: str, ticker: str, c) -> FiledTotal:
    if not isinstance(c, dict) or not all(isinstance(c.get(k), str) and c[k].strip() for k in ("tag", "label", "source")):
        raise EdgarError(f"us_tags.yaml: {col}: filed_total {ticker} needs its tag, label and source")
    return FiledTotal(c["tag"], c["label"].strip(), " ".join(c["source"].split()), tuple(c.get("never_below") or ()))


def load_tags(path: Path | None = None) -> TagConfig:
    """`us_tags.yaml`, validated: a file that breaks a rule is refused, naming it."""
    raw = yaml.safe_load((path or TAGS_FILE).read_text("utf-8")) or {}
    start = raw.get("from")
    if not isinstance(start, dt.date):
        raise EdgarError("us_tags.yaml needs `from`, the first period end the task registers (YYYY-MM-DD)")
    cols: dict[str, Column] = {}
    for name, spec in (raw.get("columns") or {}).items():
        kind = (spec or {}).get("kind")
        if kind not in KINDS:
            raise EdgarError(f"us_tags.yaml: {name}'s kind {kind!r} isn't one of {', '.join(KINDS)}")
        chain = []
        for e in spec.get("chain") or []:
            if isinstance(e, str):
                chain.append(Entry(e))
            elif isinstance(e, dict) and isinstance(e.get("tag"), str) and e.get("nci") in ("plus", "minus"):
                chain.append(Entry(e["tag"], 1 if e["nci"] == "plus" else -1))
            else:
                raise EdgarError(f"us_tags.yaml: {name}'s chain entry {e!r} is a tag or {{tag, nci: plus|minus}}")
        parts = tuple(spec.get("parts") or ())
        if not chain and not parts:
            raise EdgarError(f"us_tags.yaml: {name} has no tags")
        if parts and kind != "instant":
            raise EdgarError(f"us_tags.yaml: {name}: parts are for balance-sheet lines only")
        groups = tuple(tuple(g) for g in (spec.get("sum") or ()))
        included = {k: tuple(v) for k, v in (spec.get("included") or {}).items()}
        added_to = {k: tuple(v) for k, v in (spec.get("added_to") or {}).items()}
        tie = {k: tuple(v) for k, v in (spec.get("tie") or {}).items()}
        totals = {str(k): _filed_total(name, str(k), v) for k, v in (spec.get("filed_total") or {}).items()}
        named = {t for g in groups for t in g}
        for m in (included, added_to, tie):
            named |= set(m) | {t for v in m.values() for t in v}
        named |= {t for ft in totals.values() for t in (ft.tag, *ft.never_below)}
        if named and not named <= set(parts):
            raise EdgarError(f"us_tags.yaml: {name}: sum, included, added_to, tie and filed_total name only its parts "
                             f"({', '.join(sorted(named - set(parts)))} isn't one)")
        not_summed = {str(k): str(v) for k, v in (spec.get("not_summed") or {}).items()}
        if set(not_summed) & set(totals):
            raise EdgarError(f"us_tags.yaml: {name}: {', '.join(sorted(set(not_summed) & set(totals)))} is in both "
                             "not_summed and filed_total")
        all_parts = tuple(spec.get("all_parts") or ())
        label = str(spec.get("all_parts_label") or "")
        if all_parts and (kind != "flow" or len(all_parts) < 2 or not label or set(all_parts) & {e.tag for e in chain}):
            raise EdgarError(f"us_tags.yaml: {name}: all_parts is for a flow column, two or more tags outside its chain, "
                             "with all_parts_label")
        cols[name] = Column(name, kind, tuple(chain), parts, groups, included, not_summed, added_to, tie, totals,
                            all_parts, label)
    bal = raw.get("balance_check") or {}
    classes = {ticker: _share_classes(ticker, c) for ticker, c in (raw.get("share_classes") or {}).items()}
    need = ("liabilities", "total", "temporary_equity", "temporary_equity_total", "tolerance")
    if any(bal.get(k) in (None, "", []) for k in need):
        raise EdgarError(f"us_tags.yaml: balance_check needs {', '.join(need)}")
    return TagConfig(start, cols, bal["liabilities"], bal["total"], tuple(bal["temporary_equity"]),
                     bal["temporary_equity_total"], Decimal(str(bal["tolerance"])), classes,
                     {str(k): str(v) for k, v in (raw.get("coverage_exceptions") or {}).items()})


# ------------------------------------------------------------------------------------------------ filings
def report_form(form: str) -> bool:
    return form.split("/")[0] in REPORT_FORMS


@dataclass
class Filing:
    """One periodic report's own facts in company facts (one accession number)."""
    accn: str
    cik: int                          # the CIK it was filed under (a predecessor's for its old filings)
    form: str
    filed: dt.date
    facts: dict[tuple, set[Decimal]] = field(default_factory=dict)     # (tag, unit, start, end) -> values
    shares: list[tuple[dt.date, Decimal]] = field(default_factory=list)  # cover-page dei entries

    @property
    def annual(self) -> bool:
        return self.form.split("/")[0] in ("10-K", "10-KT")

    @property
    def amendment(self) -> bool:
        return self.form.endswith("/A")

    def get(self, tag: str, unit: str, start: dt.date | None, end: dt.date) -> set[Decimal]:
        return self.facts.get((tag, unit, start, end), set())

    def has(self, tag: str, unit: str = MONEY) -> bool:
        return any(k[0] == tag and k[1] == unit for k in self.facts)

    def durations(self) -> list[F]:
        return [F(t, s, e, ZERO, self.filed, self.form, self.accn, u) for (t, u, s, e) in self.facts if s is not None]

    @property
    def period_end(self) -> dt.date | None:
        """The filing's report date: the latest end of its durations (a quarter to a year) that carries at least a
        fifth as many facts as its busiest end. A forward-looking line (P&G's FY2019 10-K tags a few figures for the
        year to 30 Jun 2020: dev run 3, 11 Nov 2026; Nvidia's and Walmart's 2011-14 10-Ks in the fixtures) has a
        handful of facts and never sets the period."""
        ends = Counter(f.end for f in self.durations() if f.kind in YTD_KIND)
        if not ends:
            return None
        busiest = max(ends.values())
        return max(e for e, n in ends.items() if n * PERIOD_SHARE >= busiest)

    def link(self) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{self.accn.replace('-', '')}/{self.accn}-index.htm"


def filings_of(cik: int, cf: dict) -> dict[str, Filing]:
    """Every 10-K and 10-Q (and amendment) in one CIK's company facts that holds us-gaap facts, by accession number."""
    out: dict[str, Filing] = {}
    for tag, body in cf.get("facts", {}).get("us-gaap", {}).items():
        for unit, entries in body.get("units", {}).items():
            for e in entries:
                form = e.get("form") or ""
                if not report_form(form) or not e.get("accn"):
                    continue
                try:
                    start, end, val, filed = _d(e.get("start")), _d(e["end"]), Decimal(str(e["val"])), _d(e["filed"])
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue
                f = out.get(e["accn"])
                if f is None:
                    f = out[e["accn"]] = Filing(e["accn"], cik, form, filed)
                f.facts.setdefault((tag, unit, start, end), set()).add(val)
    for e in cf.get("facts", {}).get("dei", {}).get(SHARES_TAG, {}).get("units", {}).get(SHARES, []):
        f = out.get(e.get("accn") or "")
        if f is not None:
            try:
                f.shares.append((_d(e["end"]), Decimal(str(e["val"]))))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
    return out


def history_of(docs: list[dict]) -> dict[tuple[str, str], list[F]]:
    """(tag, unit) → every fact of the periodic reports, not deduplicated (a quarter's earlier year to date is looked
    up as filed on or before the filing that needs it)."""
    out: dict[tuple[str, str], list[F]] = defaultdict(list)
    for cf in docs:
        for tag, body in cf.get("facts", {}).get("us-gaap", {}).items():
            for unit, entries in body.get("units", {}).items():
                for e in entries:
                    if not report_form(e.get("form") or ""):
                        continue
                    try:
                        out[(tag, unit)].append(F(tag, _d(e.get("start")), _d(e["end"]), Decimal(str(e["val"])),
                                                  _d(e["filed"]), e.get("form", ""), e.get("accn", ""), unit))
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        continue
    return out


# ------------------------------------------------------------------------------------------------ cover pages
@dataclass(frozen=True)
class Cover:
    """A filing's cover page: shares outstanding per class (the class member's local name; `total` without one)."""
    end: dt.date
    classes: dict[str, Decimal]


IX = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"


def _ix_number(el) -> Decimal | None:
    fmt = (el.get("format") or "").split(":")[-1]
    text = "".join(el.itertext()).strip()
    if fmt in ("fixed-zero", "fixedzero") or text in ("-", "—", "–"):
        v = ZERO
    else:
        digits = text.replace(",", "").replace(" ", "")
        if fmt in ("num-comma-decimal", "numcommadecimal"):
            digits = text.replace(".", "").replace(" ", "").replace(",", ".")
        try:
            v = Decimal(digits)
        except InvalidOperation:
            return None
    scale = el.get("scale")
    if scale:
        v = v.scaleb(int(scale))
    if v == v.to_integral_value():
        v = v.quantize(Decimal(1))
    return -v if el.get("sign") == "-" else v


def read_cover(body: bytes) -> Cover:
    """The cover page's `dei:EntityCommonStockSharesOutstanding` facts in a filing's inline XBRL document, per class
    (the context's `StatementClassOfStockAxis` member). The parser refuses DTDs, entities and the network."""
    from lxml import etree  # the jobs extra: the API service never reads a cover page

    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True, recover=True)
    try:
        root = etree.fromstring(body, parser)
    except etree.XMLSyntaxError as e:
        raise EdgarError(f"the filing document isn't XML ({e.__class__.__name__})") from None
    if root is None:
        raise EdgarError("the filing document is empty")
    contexts = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        when = c.find(f".//{{{XBRLI}}}instant")
        if when is None:
            when = c.find(f".//{{{XBRLI}}}endDate")
        dims = {m.get("dimension", "").split(":")[-1]: (m.text or "").strip().split(":")[-1]
                for m in c.iter(f"{{{XBRLDI}}}explicitMember")}
        contexts[c.get("id")] = (_d((when.text or "").strip()) if when is not None else None, dims)
    classes: dict[str, Decimal] = {}
    ends: set[dt.date] = set()
    for el in root.iter(f"{{{IX}}}nonFraction"):
        if (el.get("name") or "").split(":")[-1] != SHARES_TAG:
            continue
        ctx = contexts.get(el.get("contextRef"))
        v = _ix_number(el)
        if ctx is None or ctx[0] is None or v is None:
            raise EdgarError(f"a cover-page share figure has no readable context or value ({el.get('contextRef')})")
        end, dims = ctx
        member = dims.get("StatementClassOfStockAxis") or ("total" if not dims else "|".join(sorted(dims.values())))
        if member in classes and classes[member] != v:
            raise EdgarError(f"the cover page gives {member} two share figures")
        classes[member] = v
        ends.add(end)
    if not classes:
        raise EdgarError("the filing document has no cover-page share figure")
    if len(ends) > 1:
        raise EdgarError("the cover page's share figures are dated " + ", ".join(sorted(map(str, ends))))
    if "total" in classes and len(classes) > 1:
        del classes["total"]           # the per-class figures are the cover page's; a bare total beside them is theirs
    return Cover(ends.pop(), classes)


def cover_total(cover: Cover, classes: ShareClasses | None) -> tuple[Decimal | None, str]:
    """The shares across classes in the reference class (each class × its curated ratio), with how; None when a class
    has no curated ratio (the field is held, naming it)."""
    parts = sorted(cover.classes.items())
    if classes is None:
        if len(parts) == 1:
            return parts[0][1], f"cover page, as of {cover.end.isoformat()}"
        return None, ("held: the cover page lists classes " + ", ".join(m for m, _ in parts)
                      + " and no conversion ratios are curated")
    missing = [m for m, _ in parts if m not in classes.ratios]
    if missing:
        return None, ("held: no curated conversion ratio for " + ", ".join(missing) + " (classes on the cover page: "
                      + ", ".join(f"{m} {v}" for m, v in parts) + ")")
    total = sum((v * classes.ratios[m] for m, v in parts), ZERO) + sum((e.shares for e in classes.extra), ZERO)
    if total != total.to_integral_value():
        total = total.quantize(Decimal(1))        # a fractional rate gives a fraction of a share: counted to the share
    how = " + ".join([f"{m} {v} × {classes.ratios[m]}" for m, v in parts] + [f"{e.shares} ({e.note})" for e in classes.extra])
    dated = [f"{m} {r.ratio} in effect from {r.effective.isoformat()}" for m, r in sorted(classes.rates.items())
             if r.effective is not None]
    return total, (f"cover page, as of {cover.end.isoformat()}: {how}" + (f"; rates: {', '.join(dated)}" if dated else "")
                   + f" ({classes.note})")


# ------------------------------------------------------------------------------------------------ rows
@dataclass
class Row:
    period_kind: str
    period_start: dt.date | None
    period_end: dt.date
    values: dict[str, Decimal | None] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class Built:
    filing: Filing
    period_end: dt.date
    fy_start: dt.date | None
    rows: list[Row]
    balance: tuple[bool | None, str] | None = None  # 10-Ks: (ties, how); None ties: not checked
    notes: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # period kinds whose rows aren't stored (never_displace)


@dataclass(frozen=True)
class Tie:
    """One period where a filed total and all of a parts rule's parts are filed (the parts_tie warning; #470)."""
    column: str
    start: dt.date | None
    end: dt.date
    total: str
    parts: tuple[str, ...]
    ties: bool                       # within TIE_TOLERANCE in every filing that files them all for the period

    @property
    def period(self) -> str:
        return self.end.isoformat() if self.start is None else f"{self.start.isoformat()} to {self.end.isoformat()}"


TIE_TOLERANCE = Decimal(1_000_000)   # $1 million (#470)


class Statements:
    """One company's filings as rows. `docs` is its company facts, its own CIK's first, then each predecessor's, each
    with the CIK it was read under. `since`: the first period end the task registers (its window), from which the parts
    rules' totals are compared with their parts (`ties`)."""

    def __init__(self, docs: list[tuple[int, dict]], cfg: TagConfig, ticker: str | None = None,
                 since: dt.date | None = None):
        if not docs or not any(cf.get("facts", {}).get("us-gaap") for _, cf in docs):
            raise EdgarError("company facts hold no us-gaap facts (an IFRS filer?)")
        if any("ifrs-full" in cf.get("facts", {}) for _, cf in docs):
            raise EdgarError("company facts hold IFRS facts (the slice reads US GAAP filers only)")
        self.cfg = cfg
        self.ticker = ticker
        cfs = [cf for _, cf in docs]
        self.facts = load_facts(merge_companyfacts(cfs[0], cfs[1:]))
        self.history = history_of(cfs)
        self.filings: dict[str, Filing] = {}
        for cik, cf in docs:
            for accn, f in filings_of(cik, cf).items():
                self.filings.setdefault(accn, f)
        self.order: dict[str, list[Entry]] = {}
        for c in cfg.columns.values():
            by_tag = {e.tag: e for e in c.chain}
            self.order[c.name] = [by_tag[t] for t in pick_tags(self.facts, [e.tag for e in c.chain], c.flow)]
        self.ties = self._ties(since or cfg.start)
        self.untied = {t.column for t in self.ties if not t.ties}     # columns whose parts rule is off for this company

    # -------------------------------------------------------------- the parts rules' totals against their parts
    def _ties(self, since: dt.date) -> list[Tie]:
        """Every period of the window's filings (its own and its comparatives) where a filed total and all of a parts
        rule's parts are filed, one value each: `tie`'s total against its parts (an instant), and a flow's chain tag
        (the first of its order filed for the period) against its `all_parts`. A period ties if the sum is within
        TIE_TOLERANCE of the total in every filing that files them all."""
        found: dict[tuple, tuple[str, tuple[str, ...], bool]] = {}

        def one(f, tag, unit, start, end):
            v = f.get(tag, unit, start, end)
            return next(iter(v)) if len(v) == 1 else None

        def compare(col, start, end, total, tv, parts, pv):
            k = (col.name, start, end)
            ok = abs(sum(pv, ZERO) - tv) <= TIE_TOLERANCE
            prev = found.get(k)
            found[k] = (total, parts, ok and (prev is None or prev[2]))

        for f in self.filings.values():
            if f.period_end is None or f.period_end < since:
                continue
            for col in self.cfg.columns.values():
                for total, parts in col.tie.items():
                    for end in sorted({k[3] for k in f.facts if k[0] == total and k[1] == MONEY and k[2] is None}):
                        tv, pv = one(f, total, MONEY, None, end), [one(f, p, MONEY, None, end) for p in parts]
                        if tv is not None and None not in pv:
                            compare(col, None, end, total, tv, parts, pv)
                if col.all_parts:
                    periods = {(k[2], k[3]) for k in f.facts if k[0] == col.all_parts[0] and k[1] == col.unit
                               and k[2] is not None}
                    for start, end in sorted(periods):
                        pv = [one(f, p, col.unit, start, end) for p in col.all_parts]
                        if None in pv:
                            continue
                        e = next((e for e in self.order[col.name] if f.get(e.tag, col.unit, start, end)), None)
                        tv = one(f, e.tag, col.unit, start, end) if e is not None else None
                        if tv is not None:
                            compare(col, start, end, e.tag, tv, col.all_parts, pv)
        return [Tie(c, s, e, t, p, ok) for (c, s, e), (t, p, ok) in sorted(found.items(), key=lambda kv: (
            kv[0][0], kv[0][2], kv[0][1] or dt.date.min))]

    def _untied(self, col: Column) -> str:
        """Why a column's parts rule is off for this company (parts_tie), naming the periods; "" when it isn't."""
        bad = [t for t in self.ties if t.column == col.name and not t.ties]
        if not bad:
            return ""
        return (f"{' + '.join(bad[0].parts)} didn't tie with the filed {bad[0].total} for this company at "
                + ", ".join(t.period for t in bad[:4]) + (f" and {len(bad) - 4} more" if len(bad) > 4 else "")
                + " (parts_tie), so the rule is off until Anand rules")

    # -------------------------------------------------------------- one value
    def _value(self, f: Filing, col: Column, start, end) -> tuple[Decimal | None, str | None]:
        for e in self.order[col.name]:
            vals = f.get(e.tag, col.unit, start, end)
            if not vals:
                continue
            if len(vals) > 1:
                return None, f"held: {e.tag} has {len(vals)} different values for this period in this filing"
            v = next(iter(vals))
            if e.nci:
                mi = f.get(MI, MONEY, None, end)
                if len(mi) == 1:
                    return v + e.nci * next(iter(mi)), f"{e.tag}{'+' if e.nci > 0 else '-'}{MI}"
                if f.has(MI):
                    continue                   # this filing presents NCI, but not at this date: no fill
            return v, e.tag                    # (a filing that presents no NCI: the equity line is the whole)
        if col.all_parts:
            return self._all_parts(f, col, start, end)
        return None, None

    def _all_parts(self, f: Filing, col: Column, start, end) -> tuple[Decimal | None, str | None]:
        """No tag of the chain filed for the period (same start and end): the sum of `all_parts` where every one is
        filed for it (McDonald's pre-tax income as its domestic and foreign parts: #455's question 3; #470). One part
        alone leaves the field empty; a company whose parts don't tie with its filed totals (parts_tie) is held."""
        vals = [f.get(p, col.unit, start, end) for p in col.all_parts]
        if not all(vals):
            return None, None
        for p, v in zip(col.all_parts, vals, strict=True):
            if len(v) > 1:
                return None, f"held: {p} has {len(v)} different values for this period in this filing"
        parts = "+".join(col.all_parts)
        if (why := self._untied(col)):
            return None, f"held: filed only as {parts}; {why}"
        return sum((next(iter(v)) for v in vals), ZERO), f"{parts} ({col.all_parts_label}; no total is filed)"

    def _parts(self, f: Filing, col: Column, end) -> tuple[Decimal | None, str | None]:
        v, tag = self._value(f, col, None, end)
        if tag is not None:
            return v, tag
        total = col.filed_total.get(self.ticker or "")
        if total is not None:
            return self._filed_total(f, col, total, end)
        filed = {}
        for p in col.parts:
            vals = f.get(p, MONEY, None, end)
            if len(vals) > 1:
                return None, f"held: {p} has {len(vals)} different values for this date in this filing"
            if vals:
                filed[p] = next(iter(vals))
        nonzero = {p: v for p, v in filed.items() if v != 0}
        if len(nonzero) == 1:
            (p, v), = nonzero.items()
            return v, p
        if not nonzero:
            return (ZERO, "+".join(filed) + " (filed as zero)") if filed else (None, None)
        return self._summed(col, nonzero)

    def _filed_total(self, f: Filing, col: Column, total: FiledTotal, end) -> tuple[Decimal | None, str | None]:
        """The company's one caption that is its whole current debt (IBM's short-term debt, which includes its current
        maturities of long-term debt: #455's question 1; #470), nothing added to it. Held where it is below the first
        non-zero `never_below` part filed at the date (a part it includes can't exceed it), or where it isn't filed
        and another caption is (a part is never taken for the whole)."""
        vals = f.get(total.tag, MONEY, None, end)
        if len(vals) > 1:
            return None, f"held: {total.tag} has {len(vals)} different values for this date in this filing"
        if not vals:
            others = [p for p in col.parts if f.get(p, MONEY, None, end)]
            if not others:
                return None, None
            return None, (f"held: {total.tag}, {self.ticker}'s whole current debt as filed, isn't filed for this date "
                          f"(filed: {', '.join(others)})")
        v = next(iter(vals))
        for p in total.never_below:
            got = f.get(p, MONEY, None, end)
            if len(got) > 1:
                return None, f"held: {p} has {len(got)} different values for this date in this filing"
            if got and (c := next(iter(got))) != 0:
                if c > v:
                    return None, (f"held: {total.tag} ({self.ticker}'s {total.label}) is below its {p} at this date, "
                                  "which it includes")
                break
        return v, f"{total.tag} ({total.label})"

    def _summed(self, col: Column, nonzero: dict[str, Decimal]) -> tuple[Decimal | None, str]:
        """Two or more non-zero captions and no total (#401's question 1, option A; Anand, 12 Nov 2026): one from each
        of the column's `sum` groups, the first filed non-zero, added, never two of one group (for current debt: the
        current part of long-term debt, plus short-term borrowings, or commercial paper where no short-term borrowings
        line is filed, since short-term borrowings include it). Held, naming the parts, where the company's parts are
        never added (`not_summed`: one caption includes another) or a filed caption is outside the sum."""
        listed = "held: filed in parts (" + ", ".join(f"{p} {v}" for p, v in nonzero.items()) + ") and no total is filed"
        if self.ticker in col.not_summed:
            return None, f"{listed}; not added: {col.not_summed[self.ticker]}"
        used, beside, off = [], {}, ""
        for group in col.sum:
            got = next((p for p in group if p in nonzero), None)
            if got is None:
                continue
            used.append(got)
            for p in group:
                if p != got and p in nonzero:
                    beside[p] = f"{p} (never with {got})"
            for p in col.included.get(got, ()):
                if p in nonzero:
                    beside[p] = f"{p} (within {got})"
            # other short-term borrowings beside commercial paper, with no short-term borrowings line (#470)
            for p in col.added_to.get(got, ()):
                if p in nonzero and p not in beside and p not in used:
                    if (why := self._untied(col)):
                        off = off or f" (not added to {got}: {why})"
                    else:
                        used.append(p)
        left = [p for p in nonzero if p not in used and p not in beside]
        if not used or left:
            return None, f"{listed}; {', '.join(left or nonzero)} outside the decided sum{off}"
        how = "+".join(used) + (" (the sum of the filed captions; no total is filed" if len(used) > 1 else
                                " (no total is filed")
        if beside:
            how += "; not added: " + ", ".join(beside.values())
        return sum((nonzero[p] for p in used), ZERO), how + ")"

    def _prior(self, tag: str, unit: str, start: dt.date, kind: str, filed: dt.date) -> F | None:
        """The year to date of `kind` from `start`, as last filed on or before `filed`."""
        got = [x for x in self.history.get((tag, unit), []) if x.start == start and x.kind == kind and x.filed <= filed]
        return max(got, key=lambda x: (x.end, x.filed, x.form.endswith("/A"))) if got else None

    def _derived(self, f: Filing, col: Column, ytd_start, end, ytd_kind: str, q_start) -> tuple[Decimal | None, str | None, dt.date | None]:
        """A quarter as the year to date less the one before it (same tag, as filed by then)."""
        prior_kind = PRIOR_KIND.get(ytd_kind)
        if prior_kind is None:
            return None, None, None
        for e in self.order[col.name]:
            vals = f.get(e.tag, col.unit, ytd_start, end)
            if len(vals) != 1:
                continue
            p = self._prior(e.tag, col.unit, ytd_start, prior_kind, f.filed)
            if p is None:
                continue
            start = p.end + dt.timedelta(days=1)
            if q_start is not None and start != q_start:
                continue
            if not 80 <= (end - start).days + 1 <= 100:
                continue
            return next(iter(vals)) - p.val, f"derived: {e.tag} {ytd_kind} − {prior_kind} filed {p.filed.isoformat()}", start
        if col.all_parts and not self._untied(col):
            return self._derived_parts(f, col, ytd_start, end, ytd_kind, prior_kind, q_start)
        return None, None, None

    def _derived_parts(self, f: Filing, col: Column, ytd_start, end, ytd_kind: str, prior_kind: str,
                       q_start) -> tuple[Decimal | None, str | None, dt.date | None]:
        """A quarter from `all_parts`' sums, as `_derived` does with one tag: every part's year to date in this filing
        less every part's year to date before it, as one earlier filing filed them all (#470)."""
        vals = [f.get(p, col.unit, ytd_start, end) for p in col.all_parts]
        if any(len(v) != 1 for v in vals):
            return None, None, None
        prior = [self._prior(p, col.unit, ytd_start, prior_kind, f.filed) for p in col.all_parts]
        if None in prior or len({(p.end, p.accn) for p in prior}) != 1:
            return None, None, None
        start = prior[0].end + dt.timedelta(days=1)
        if (q_start is not None and start != q_start) or not 80 <= (end - start).days + 1 <= 100:
            return None, None, None
        v = sum((next(iter(x)) for x in vals), ZERO) - sum((p.val for p in prior), ZERO)
        return v, (f"derived: {'+'.join(col.all_parts)} {ytd_kind} − {prior_kind} filed {prior[0].filed.isoformat()} "
                   f"({col.all_parts_label})"), start

    # -------------------------------------------------------------- one filing
    def build(self, f: Filing, *, cover: tuple[Decimal | None, str] | None = None) -> Built | None:
        """The filing's rows; None when it holds no duration of a quarter to a year (a cover-only amendment)."""
        end = f.period_end
        if end is None:
            return None
        durs = [d for d in f.durations() if d.end == end and d.kind in YTD_KIND]
        starts = {k: Counter(d.start for d in durs if d.kind == k).most_common(1)[0][0]
                  for k in YTD_KIND if any(d.kind == k for d in durs)}
        ytd_kind = "FY" if f.annual else max((k for k in starts if k != "FY"), key=list(YTD_KIND).index, default=None)
        if ytd_kind is None or ytd_kind not in starts:
            return Built(f, end, None, [], notes=[f"no {'year' if f.annual else 'year-to-date'} duration ends at {end}"])
        fy_start = starts[ytd_kind]
        rows: list[Row] = []

        # the quarter: as filed; a 10-K's from the year less the nine months; cash flow (filed as the year to date)
        # from the year to date less the one before it
        q_start = starts.get("Q")
        q = Row("quarter", q_start, end)
        for col in self.cfg.by_kind("flow", "per_share", "cash_flow"):
            if ytd_kind == "Q":
                v, tag = self._value(f, col, fy_start, end)
            else:
                v, tag = self._value(f, col, q_start, end) if q_start else (None, None)
                if tag is None and (col.kind == "cash_flow" or (col.kind == "flow" and ytd_kind == "FY")):
                    v, tag, s = self._derived(f, col, fy_start, end, ytd_kind, q_start)
                    if tag is not None and q_start is None:
                        q_start = q.period_start = s
            q.values[col.name], q.tags[col.name] = v, tag
        rows.append(q)

        # the year to date (a Q1 10-Q's quarter is its year to date)
        if ytd_kind != "Q":
            y = Row(YTD_KIND[ytd_kind], fy_start, end)
            for col in self.cfg.by_kind("flow", "per_share", "cash_flow"):
                y.values[col.name], y.tags[col.name] = self._value(f, col, fy_start, end)
            rows.append(y)

        # the balance sheet and the cover page's shares
        b = Row("instant", None, end)
        for col in self.cfg.by_kind("instant"):
            b.values[col.name], b.tags[col.name] = self._parts(f, col, end) if col.parts else self._value(f, col, None, end)
        if cover is not None:
            b.values["shares_outstanding"], b.tags["shares_outstanding"] = cover
        else:
            b.values["shares_outstanding"], b.tags["shares_outstanding"] = self._shares(f)
        rows.append(b)

        kept = []
        for r in rows:
            r.tags = {k: v for k, v in r.tags.items() if v is not None}
            if any(v is not None for v in r.values.values()):
                r.values = {k: v for k, v in r.values.items() if v is not None}
                kept.append(r)
        out = Built(f, end, fy_start, kept)
        if f.annual:
            out.balance = self.balance(f, end, b.values.get("total_equity"))
        return out

    def _shares(self, f: Filing) -> tuple[Decimal | None, str | None]:
        if not f.shares:
            return None, None
        end = max(e for e, _ in f.shares)
        vals = [v for e, v in f.shares if e == end]
        how = f"dei:{SHARES_TAG} (cover page, as of {end.isoformat()})"
        return sum(vals, ZERO), how if len(vals) == 1 else f"{how}, summed across {len(vals)} entries"

    def balance(self, f: Filing, end: dt.date, equity: Decimal | None) -> tuple[bool | None, str]:
        """A year end's balance sheet: total assets = liabilities + temporary equity + total equity (± tolerance); where
        no total-liabilities line is filed, total assets = the filed balance sheet total. None: not checked (an
        amendment that presents no balance sheet). Where `Assets` has two values at the date in one filing (the column
        holds the field), the check passes only if exactly one of them ties, and says so."""
        def one(tag):
            v = f.get(tag, MONEY, None, end)
            return next(iter(v)) if len(v) == 1 else None
        found = sorted(f.get("Assets", MONEY, None, end))
        if not found:
            if f.amendment and not f.has("Assets"):
                return None, f"{f.form} {f.accn} presents no balance sheet (no total assets): not checked"
            dates = sorted({k[3] for k in f.facts if k[0] == "Assets" and k[1] == MONEY})
            return False, (f"no total-assets figure at the year end ({f.form} {f.accn}; Assets filed at "
                           + (", ".join(map(str, dates)) or "no date") + ")")
        liab = one(self.cfg.liabilities)
        if liab is not None:
            if equity is None:
                return False, f"no total-equity figure ({f.form} {f.accn})"
            temp = one(self.cfg.temporary_equity_total)
            if temp is None:
                temp = sum((x for t in self.cfg.temporary_equity if (x := one(t)) is not None), ZERO)
            other, side = liab + temp + equity, f"liabilities {liab} + temporary equity {temp} + equity {equity}"
        else:
            total = one(self.cfg.balance_total)
            if total is None:
                return False, f"neither a total-liabilities line nor a balance sheet total is filed ({f.form} {f.accn})"
            other, side = total, f"the balance sheet total {total} (no total-liabilities line is filed)"
        ties = [a for a in found if abs(a - other) <= abs(a) * self.cfg.tolerance]
        if len(found) == 1:
            return bool(ties), f"assets {found[0]} vs {side}"
        if len(ties) == 1:
            return True, (f"assets {ties[0]} vs {side} (Assets has {len(found)} values at the date in {f.form} {f.accn}: "
                          + ", ".join(map(str, found)) + "; the one that ties is the balance sheet's, and the column "
                          "holds the field)")
        return False, f"Assets has {len(found)} values at the date in {f.form} {f.accn} (" + ", ".join(map(str, found)) \
            + f") and {len(ties)} tie with {side}"

    def candidates(self, f: Filing, col: str, start: dt.date | None, end: dt.date, words: tuple[str, ...]) -> list[str]:
        """The us-gaap tags the filing files for this period (a duration from `start`, or the instant at `end`) whose names
        hold one of `words` and that aren't in the column's chain: where to look when the column is empty."""
        c = self.cfg.columns.get(col)
        chain = ({e.tag for e in c.chain} | set(c.parts)) if c else set()
        return sorted({t for (t, u, s, e) in f.facts if e == end and s == start and t not in chain
                       and any(w in t for w in words)})

    def switched(self, used: dict[str, set[str]]) -> list[str]:
        """Columns whose rows came from more than one tag of their chain, the primary named."""
        out = []
        for col, tags in sorted(used.items()):
            base = {(t.split()[1] if t.startswith("derived: ") else t).split("+")[0].split("-")[0].split(" ")[0]
                    for t in tags if not t.startswith("held")}
            if len(base) > 1:
                primary = self.order[col][0].tag if self.order.get(col) else "?"
                out.append(f"{col}: {primary} primary, also " + ", ".join(sorted(base - {primary})))
        return out


def never_displace(built: list[Built]) -> None:
    """A row that lacks a figure never displaces one that has it (#455). A reader takes the latest filed row for each
    period (India's `metrics.Book`, which the US metrics share), so among one company's filings, in filing order, a
    later filing's row for a period (its kind and end) that an earlier stored row covers is kept only if it has every
    figure that row has; otherwise it isn't stored, the filing's notes say why, and the earlier row stays the latest.
    A fuller or equal later row (an amendment that restates the statements) is kept and replaces it, as before."""
    latest: dict[tuple[str, dt.date], tuple[Row, Filing]] = {}
    for b in sorted(built, key=lambda b: (b.filing.filed, b.filing.accn)):
        kept = []
        for r in b.rows:
            k = (r.period_kind, r.period_end)
            prev = latest.get(k)
            if prev is not None:
                missing = [c for c in prev[0].values if r.values.get(c) is None]
                if missing:
                    b.dropped.append(r.period_kind)
                    b.notes.append(f"its {r.period_kind} row to {r.period_end.isoformat()} isn't stored: it lacks "
                                   f"{', '.join(missing)}, which {prev[1].form} {prev[1].accn} files for that period")
                    continue
            latest[k] = (r, b.filing)
            kept.append(r)
        b.rows = kept
