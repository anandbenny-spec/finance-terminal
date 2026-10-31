"""Statements: a results filing's figures as `equity.financials` rows, with the per-filing checks (Spec 02A §5.4; job
2A-09). The XBRL reader is `ft/equity/xbrl.py`; the element map is `py/config/equity/xbrl_tags.json`.

One row per period kind the filing holds:

- `quarter`: the quarter's P&L (the `One` column), with shares and, for banks, the filed ratios;
- the year to date (the `Four` column) as `half_year`, `nine_months` or `year`: its P&L and the cash flow (results
  filings carry no quarterly cash flow, so a Q1 filing's quarter row is also its year to date);
- `instant`: the balance sheet at the period end (half-years), and banks' total segment assets every quarter.

Every value is as filed, in full rupees (ratios as filed fractions); `tags` names the element behind each value (or how
it was derived). A filing that fails a check gets no rows: `StatementError` names the check and why, and the task stores
that as the filing's parse note (the bad figures are never stored). The checks:

- `basis`: the file's own basis (its header) equals the listing's;
- `period`: the file's period end and financial-year start equal the listing's; the quarter is the three months to the
  period end and the year to date runs from the financial-year start; an Integrated Filing file needed no re-dating;
- `unit`: every money value is in INR;
- `pat`: PAT = PBT − tax (± 0.1% or ₹1 lakh), counting the lines the format places between tax and PAT (see `_pat_tie`);
- `total_income`: total income = revenue + other income (Ind AS and NBFC formats; the same tolerance);
- `balance_sheet`: total assets = equity + liabilities (± 0.1%) at the instant;
- `shares`: shares outstanding (paid-up capital ÷ face value) > 0 on every row, and present on the quarter's;
- `figures`: the quarter's PBT, tax and PAT are in the file (a filing whose statements can't be read isn't stored empty);
- `ambiguous`: no column's element has two different values in one period.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from ft.equity import xbrl

PARSER_VERSION = 1          # bump it when a change alters what a filing parses to: the task re-parses older filings
TAGS = json.loads((Path(__file__).resolve().parents[3] / "config" / "equity" / "xbrl_tags.json").read_text("utf-8"))
LAKH = Decimal(100_000)
TIE = Decimal("0.001")      # 0.1%
INSURERS = ("insurer_life", "insurer_general")
PER_SHARE = frozenset({"eps_basic", "eps_diluted", "face_value"})
RATIOS = frozenset({"gnpa_ratio", "nnpa_ratio", "roa_filed", "cet1_ratio", "at1_ratio", "solvency_ratio"})
NOT_MONEY = PER_SHARE | RATIOS | {"shares_outstanding"}

# every column of equity.financials that a filing can fill (Spec 02A §5.4), in the table's order
COLUMNS = (
    "revenue", "other_income", "total_income", "employee_cost", "dna", "finance_cost", "total_expenses",
    "share_of_associates", "pbt_before_exceptional", "exceptional", "pbt", "tax", "pat", "pat_owners", "pat_nci",
    "eps_basic", "eps_diluted",
    "total_assets", "current_assets", "cash", "bank_balances_other", "current_investments", "noncurrent_investments",
    "trade_receivables", "inventories", "total_equity", "equity_owners", "share_capital", "nci", "borrowings_current",
    "borrowings_noncurrent", "lease_current", "lease_noncurrent", "trade_payables",
    "cfo", "cfi", "cff", "capex", "dividends_paid",
    "paid_up_capital", "face_value", "shares_outstanding",
    "interest_earned", "interest_expended", "operating_profit", "provisions", "gnpa_ratio", "nnpa_ratio", "roa_filed",
    "cet1_ratio", "at1_ratio", "segment_assets_total", "deposits", "advances",
    "gross_premium", "net_premium", "assets_net", "current_liabilities_provisions", "solvency_ratio",
)
DURATION_KINDS = ("quarter", "half_year", "nine_months", "year")


class StatementError(ValueError):
    """A filing that fails a check. `check` names it; the message says why (the filing's parse note)."""

    def __init__(self, check: str, why: str):
        super().__init__(f"{check}: {why}")
        self.check = check


@dataclass(frozen=True)
class Filing:
    """What the filings task registered about a filing (equity.filing and its document)."""
    file_name: str
    basis: str                  # consolidated | standalone
    format: str                 # ind_as | bank | nbfc | insurer_life | insurer_general
    period_end: date
    fy_start: date | None


@dataclass
class Row:
    period_kind: str
    period_start: date | None
    period_end: date
    values: dict[str, Decimal] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class Parsed:
    rows: list[Row]
    note: str                   # what the parse did, for the filing's parse note


def section(fmt: str) -> dict:
    """The element map of a format: banks' and insurers' own sections; Ind AS for the Ind AS and NBFC formats, with
    the NBFC additions merged in."""
    if fmt == "bank":
        return TAGS["bank"]
    if fmt in INSURERS:
        return TAGS["insurer"]
    sec = TAGS["ind_as"]
    if fmt == "nbfc":
        sec = {**sec, "duration": {**sec["duration"], **TAGS["nbfc"]["duration"]},
               "instant": {**sec["instant"], **TAGS["nbfc"]["instant"]}}
    return sec


def is_integrated(file_name: str) -> bool:
    return file_name.upper().startswith("INTEGRATED_FILING")


# ---------------------------------------------------------------------------------------- reading facts
class _Periods:
    """The filing's facts in its three periods (contexts without dimensions, or whose only dimensions name the filing's
    own basis): the quarter, the year to date and the instant at the period end."""

    def __init__(self, inst: xbrl.Instance, basis: str):
        d = inst.dating
        assert d is not None
        self.quarter, self.ytd = d.quarter, d.year_to_date
        self.facts: dict[str, dict[str, list[xbrl.Fact]]] = {"quarter": {}, "ytd": {}, "instant": {}}
        kinds: dict[str, list[str]] = {}
        for cid, c in inst.contexts.items():
            if not _basis_only(c, basis):
                continue
            ks = []
            if c.instant is not None:
                if c.instant == d.quarter[1]:
                    ks.append("instant")
            elif (c.start, c.end) == d.quarter:
                ks.append("quarter")
                if self.ytd == d.quarter:
                    ks.append("ytd")
            elif self.ytd is not None and (c.start, c.end) == self.ytd:
                ks.append("ytd")
            if ks:
                kinds[cid] = ks
        for f in inst.facts:
            for k in kinds.get(f.context, ()):
                self.facts[k].setdefault(f.name, []).append(f)

    def get(self, kind: str, names: list[str], column: str) -> tuple[xbrl.Fact, Decimal] | None:
        """The first candidate with a value in `kind`'s contexts. Two different values for one element in one period
        make the filing ambiguous."""
        for n in names:
            fs = [f for f in self.facts[kind].get(n, []) if _value(f, column) is not None]
            if not fs:
                continue
            vals = {_value(f, column) for f in fs}
            if len(vals) > 1:
                raise StatementError("ambiguous", f"{n} has {len(vals)} values in the {kind} "
                                                  f"({', '.join(sorted({f.context for f in fs}))})")
            return fs[0], vals.pop()
        return None

    def unitless(self, kind: str, names: list[str]) -> Decimal | None:
        """The number in the text of the first candidate filed with neither a unit nor decimals (a filer's error: the
        fact isn't numeric in XBRL terms), or None."""
        for n in names:
            for f in self.facts[kind].get(n, []):
                if f.value is None and f.unit is None and f.decimals is None:
                    v = xbrl.to_decimal(f.text)
                    if v is not None:
                        return v
        return None


def _basis_only(c: xbrl.Context, basis: str) -> bool:
    if not c.dims:
        return True
    return all(m.lower().startswith(basis) for m in c.dims.values())


def _value(f: xbrl.Fact, column: str) -> Decimal | None:
    """A fact's value for a column. A ratio filed without a unit is still read from its text (old bank files file the
    net NPA ratio that way)."""
    if f.value is not None:
        return f.value
    if column in RATIOS and f.unit is None and f.decimals is None:
        return xbrl.to_decimal(f.text)
    return None


# ---------------------------------------------------------------------------------------- the filing
def parse_filing(body: bytes, filing: Filing, *, face_value: Decimal | None = None) -> Parsed:
    """The filing's rows, or StatementError. `face_value` is the security master's (EQUITY_L), used when the filing
    files none (insurers)."""
    try:
        inst = xbrl.parse(body)
    except xbrl.XbrlError as e:
        raise StatementError("unreadable", str(e)) from None
    if inst.basis is None:
        raise StatementError("basis", "the file doesn't say whether it is consolidated or standalone")
    if inst.basis != filing.basis:
        raise StatementError("basis", f"the file is {inst.basis}, the listing says {filing.basis}")
    pe = xbrl.to_date(inst.header.get("period_end"))
    if pe != filing.period_end:
        raise StatementError("period", f"the file's period ends {inst.header.get('period_end') or '(not given)'}, "
                                       f"the listing's {filing.period_end.isoformat()}")
    if xbrl.months_between(date(pe.year, pe.month, 1), pe) != 1:
        raise StatementError("period", f"the period end {pe.isoformat()} isn't a month end")
    fs = xbrl.to_date(inst.header.get("fy_start"))
    if fs and filing.fy_start and fs != filing.fy_start:
        raise StatementError("period", f"the file's financial year starts {fs.isoformat()}, "
                                       f"the listing's {filing.fy_start.isoformat()}")
    fs = fs or filing.fy_start
    dating = xbrl.date_columns(inst, pe, fs)
    if is_integrated(filing.file_name) and (dating.changed or dating.synthesized):
        raise StatementError("period", "an Integrated Filing file needed re-dating "
                                       f"({_dating_note(dating)}); its columns should be dated as filed")
    if dating.year_to_date is not None and xbrl.period_kind(*dating.year_to_date) is None:
        raise StatementError("period", f"the year to date {dating.year_to_date[0].isoformat()} to "
                                       f"{pe.isoformat()} isn't 3, 6, 9 or 12 months")
    per = _Periods(inst, filing.basis)
    sec = section(filing.format)
    rows, notes = _rows(per, sec, filing, face_value)
    _check(rows, per, sec, filing)
    kinds = ", ".join(f"{r.period_kind}" for r in rows)
    return Parsed(rows, "; ".join([f"{len(rows)} rows ({kinds})", _dating_note(dating), *notes]))


def _dating_note(d: xbrl.Dating) -> str:
    parts = []
    if d.changed:
        parts.append("re-dated " + ", ".join(f"{k} ×{n}" for k, n in sorted(d.changed.items())))
    if d.synthesized:
        parts.append("dated from the header " + ", ".join(d.synthesized))
    return "; ".join(parts) or "columns dated as filed"


def _rows(per: _Periods, sec: dict, filing: Filing, face_value: Decimal | None) -> tuple[list[Row], list[str]]:
    """The rows, and notes on what was left out."""
    q, ytd = per.quarter, per.ytd
    fmt, standalone = filing.format, filing.basis == "standalone"
    out: list[Row] = []
    notes: list[str] = []
    plan: list[tuple[str, str, date | None]] = [("quarter", "quarter", q[0])]
    if ytd is not None and ytd != q:
        plan.append(("ytd", xbrl.period_kind(*ytd), ytd[0]))
    for kind, period_kind, start in plan:
        r = Row(period_kind, start, q[1])
        if fmt in ("ind_as", "nbfc"):
            _minority_layout(r, per, kind, sec)
        _read(r, per, kind, sec["duration"])
        if fmt == "bank" and standalone:
            _read(r, per, kind, sec["ratios"], zero_is_missing=frozenset(sec["zero_is_not_filed"]))
        is_ytd = kind == "ytd" or ytd == q
        if is_ytd:
            _read(r, per, kind, sec["cash_flow"])
            if "cfo" not in r.values and kind == "ytd" and period_kind in ("half_year", "year"):
                # Files of Sep 2020 to Mar 2021 put the half-year's or year's cash flow in the quarter column, dated
                # like the quarter, and none in the year-to-date one; there is no quarterly cash flow to confuse it with.
                _read(r, per, "quarter", sec["cash_flow"],
                      note=" (filed in the quarter column; the year to date's: results carry no quarterly cash flow)")
        if fmt in INSURERS:
            _insurer_duration(r, per, kind, sec)
        if _filed_as_zeros(r):
            # Q4 FY19 consolidated files of companies that then published consolidated results for the year only
            # (HINDUNILVR, ITC, SBIN and others) fill the quarter column with zeros: it isn't filed, so not stored
            notes.append(f"the {period_kind} column is filed as zeros: not stored")
            continue
        _shares(r, per, kind, face_value, fmt)
        out.append(r)
    inst_row = Row("instant", None, q[1])
    _read(inst_row, per, "instant", sec["instant"])
    if fmt == "bank" and "segment_assets_total" not in inst_row.values:
        _read(inst_row, per, "quarter", {"segment_assets_total": sec["instant"]["segment_assets_total"]},
              note=" (as at the quarter end, filed in the quarter's duration context)")
    if fmt == "bank":
        if "total_assets" not in inst_row.values and per.unitless("instant", sec["instant"]["total_assets"]):
            # Old bank files (2023-24) file Assets without a unit; the capital-and-liabilities total, in INR, is the
            # same figure (the balance-sheet check compares the two)
            cl = _instant(per, sec["tie"]["capital_and_liabilities"], "total_assets")
            if cl is not None:
                inst_row.values["total_assets"] = cl[1]
                inst_row.tags["total_assets"] = f"{cl[0].name} (Assets is filed without a unit; the totals are equal)"
        _sum(inst_row, per, "total_equity", "share_capital", sec["tie"]["reserves"])
    if fmt in ("ind_as", "nbfc"):
        _shareholders_funds(inst_row, per, sec)
    if fmt in INSURERS:
        _insurer_instant(inst_row, per, sec)
    if inst_row.values.get("total_assets") == 0 or inst_row.values.get("assets_net") == 0:
        notes.append("the balance sheet is filed as zeros: not stored")
    elif inst_row.values:
        _shares(inst_row, per, "instant", face_value, fmt)
        out.append(inst_row)
    return out, notes


PL_LINES = ("revenue", "total_income", "pbt", "tax", "pat")


def _filed_as_zeros(r: Row) -> bool:
    """A duration row whose filed P&L lines are all 0: the column isn't filed."""
    filed = [r.values[c] for c in PL_LINES if c in r.values]
    return bool(filed) and all(v == 0 for v in filed)


def _read(r: Row, per: _Periods, kind: str, mapping: dict, *, zero_is_missing: frozenset[str] = frozenset(),
          note: str = "") -> None:
    for col, names in mapping.items():
        if col.startswith("_") or col in r.values:
            continue
        got = per.get(kind, names, col)
        if got is None:
            continue
        f, v = got
        if col in zero_is_missing and v == 0:
            continue
        if col not in NOT_MONEY and (f.unit or "").upper() != "INR":
            raise StatementError("unit", f"{f.name} ({col}) is in {f.unit or 'no unit'}, not INR")
        r.values[col] = v
        r.tags[col] = f.name + note


def _minority_layout(r: Row, per: _Periods, kind: str, sec: dict) -> None:
    """The layout that files PAT after minority interest as ProfitLossForThePeriod (xbrl_tags.json, `minority_layout`):
    PAT, PAT to owners and the minority's share, before the general map reads ProfitLossForThePeriod as PAT."""
    m = sec["minority_layout"]
    before = per.get(kind, m["before_minority"], "pat")
    minority = per.get(kind, m["minority"], "pat_nci")
    after = per.get(kind, m["after_minority"], "pat_owners")
    if before is None or minority is None or after is None:
        return
    for f, _ in (before, minority, after):
        if (f.unit or "").upper() != "INR":
            raise StatementError("unit", f"{f.name} is in {f.unit or 'no unit'}, not INR")
    assoc = per.get(kind, sec["duration"]["share_of_associates"], "share_of_associates")
    r.values["pat"] = before[1] + (assoc[1] if assoc else 0)
    r.tags["pat"] = before[0].name + (f" + {assoc[0].name}" if assoc else "") + " (derived: this layout files PAT after minority interest)"
    r.values["pat_owners"], r.tags["pat_owners"] = after[1], after[0].name
    r.values["pat_nci"], r.tags["pat_nci"] = -minority[1], f"− {minority[0].name}"


def _instant(per: _Periods, names: list[str], col: str) -> tuple[xbrl.Fact, Decimal] | None:
    got = per.get("instant", names, col)
    if got is not None and (got[0].unit or "").upper() != "INR":
        raise StatementError("unit", f"{got[0].name} ({col}) is in {got[0].unit or 'no unit'}, not INR")
    return got


def _sum(r: Row, per: _Periods, col: str, first: str, second_names: list[str]) -> None:
    """`col` = the row's `first` column + the first of `second_names` at the instant, when `col` isn't filed itself."""
    if col in r.values or first not in r.values:
        return
    got = _instant(per, second_names, col)
    if got is None:
        return
    r.values[col] = r.values[first] + got[1]
    r.tags[col] = f"{r.tags[first]} + {got[0].name}"


def _shareholders_funds(r: Row, per: _Periods, sec: dict) -> None:
    """The pre-Ind AS balance-sheet layout (xbrl_tags.json, `minority_layout`): ShareholdersFunds and MinorityInterest
    where Equity isn't filed."""
    if "total_equity" in r.values:
        return
    m = sec["minority_layout"]
    funds = _instant(per, m["shareholders_funds"], "equity_owners")
    if funds is None:
        return
    mi = _instant(per, m["minority_interest"], "nci")
    r.values.setdefault("equity_owners", funds[1])
    r.tags.setdefault("equity_owners", funds[0].name)
    r.values["total_equity"] = funds[1] + (mi[1] if mi else 0)
    r.tags["total_equity"] = funds[0].name + (f" + {mi[0].name}" if mi else "") + " (derived)"
    if mi is not None:
        r.values.setdefault("nci", mi[1])
        r.tags.setdefault("nci", mi[0].name)


def _insurer_duration(r: Row, per: _Periods, kind: str, sec: dict) -> None:
    v, t = r.values, r.tags
    sr = v.get("solvency_ratio")
    if sr is not None and 0 < sr < Decimal("0.5"):
        v["solvency_ratio"] = sr * 100
        t["solvency_ratio"] += " × 100 (filed 100 times too small; flagged)"
    if "total_income" not in v:
        parts = [per.get(kind, sec["tie"][k], k) for k in ("gi_operating_income", "gi_sh_investment_income",
                                                          "gi_sh_other_income")]
        if all(p is not None for p in parts):
            v["total_income"] = sum((p[1] for p in parts), Decimal(0))
            t["total_income"] = " + ".join(p[0].name for p in parts) + " (derived)"


def _insurer_instant(r: Row, per: _Periods, sec: dict) -> None:
    v, t = r.values, r.tags
    if "current_liabilities_provisions" not in v:
        a = per.get("instant", sec["tie"]["current_liabilities"], "current_liabilities_provisions")
        b = per.get("instant", sec["tie"]["provisions"], "current_liabilities_provisions")
        if a is not None and b is not None:
            v["current_liabilities_provisions"] = a[1] + b[1]
            t["current_liabilities_provisions"] = f"{a[0].name} + {b[0].name}"
    if "assets_net" in v and "current_liabilities_provisions" in v:
        v["total_assets"] = v["assets_net"] + v["current_liabilities_provisions"]
        t["total_assets"] = f"{t['assets_net']} + {t['current_liabilities_provisions']} (IRDAI net to gross)"
    _sum(r, per, "total_equity", "share_capital", sec["tie"]["reserves"])


def _shares(r: Row, per: _Periods, kind: str, face_value: Decimal | None, fmt: str) -> None:
    """Paid-up capital and face value from the row's own context, then the other duration column, the instant and the
    quarter; a 0 is not filed (some files tag the quarter column's 0 and the year's value: HINDUNILVR Q4 FY19). Shares
    = paid-up ÷ face value. An insurer files no face value: the security master's is used, noted (Spec 02A §5.4);
    for any other format a face value the filing doesn't give is left out, since today's face value may not be the
    filing's (a split since)."""
    order = [kind] + [k for k in ("ytd", "quarter", "instant") if k != kind]
    for col in ("paid_up_capital", "face_value"):
        for k in order:
            got = per.get(k, TAGS["shares"][col], col)
            if got is None or got[1] == 0:
                continue
            f, v = got
            if col == "paid_up_capital" and (f.unit or "").upper() != "INR":
                raise StatementError("unit", f"{f.name} (paid_up_capital) is in {f.unit or 'no unit'}, not INR")
            r.values[col] = v
            r.tags[col] = f.name + ("" if k == kind else f" (from the {'year-to-date' if k == 'ytd' else k} context)")
            break
    if "face_value" not in r.values and face_value is not None and fmt in INSURERS:
        r.values["face_value"] = face_value
        r.tags["face_value"] = "EQUITY_L FACE VALUE (not in the filing)"
    if "paid_up_capital" in r.values and "face_value" in r.values and r.values["face_value"] != 0:
        r.values["shares_outstanding"] = r.values["paid_up_capital"] / r.values["face_value"]
        r.tags["shares_outstanding"] = f"{r.tags['paid_up_capital']} ÷ {r.tags['face_value']}"


# ---------------------------------------------------------------------------------------- the checks
def _close(a: Decimal, b: Decimal, *, floor: Decimal = LAKH) -> bool:
    return abs(a - b) <= max(abs(a) * TIE, floor)


def _check(rows: list[Row], per: _Periods, sec: dict, filing: Filing) -> None:
    fmt = filing.format
    durations = [r for r in rows if r.period_kind in DURATION_KINDS]
    if not durations:
        raise StatementError("figures", "neither the quarter's nor the year to date's statement is in the file "
                                        "(absent, or filed as zeros)")
    main = durations[0]                     # the quarter, or the year to date where the quarter is filed as zeros
    missing = [c for c in ("pbt", "tax", "pat") if c not in main.values]
    if missing:
        raise StatementError("figures", f"the {main.period_kind}'s {', '.join(missing)} isn't in the file")
    for r in rows:
        if r.period_kind in DURATION_KINDS and {"pbt", "tax", "pat"} <= r.values.keys():
            _pat_tie(r, per, sec, fmt)
        if fmt in ("ind_as", "nbfc") and r.period_kind in DURATION_KINDS:
            v = r.values
            if {"revenue", "other_income", "total_income"} <= v.keys() and not _close(
                    v["total_income"], v["revenue"] + v["other_income"]):
                raise StatementError("total_income", f"{r.period_kind}: total income {v['total_income']} ≠ revenue "
                                                     f"{v['revenue']} + other income {v['other_income']}")
        if r.period_kind == "instant":
            _balance_sheet(r, per, sec, fmt)
        s = r.values.get("shares_outstanding")
        if s is not None and s <= 0:
            raise StatementError("shares", f"{r.period_kind}: shares outstanding {s} (paid-up "
                                           f"{r.values.get('paid_up_capital')} ÷ face value {r.values.get('face_value')})")
    if "shares_outstanding" not in main.values:
        raise StatementError("shares", "the file gives no paid-up capital or no face value other than 0 (both are "
                                        "needed for the shares outstanding)")


def _pat_tie(r: Row, per: _Periods, sec: dict, fmt: str) -> None:
    """PAT = PBT − tax, counting the lines the format places between tax and PAT, each as filed (0 when not filed):
    Ind AS and NBFC: the regulatory-deferral movement, discontinued operations after tax and the share of associates
    and joint ventures (the results format puts it below tax: RELIANCE FY2026, PBT − tax = profit from continuing
    operations ₹95,610 cr, + associates ₹144 cr = PAT ₹95,754 cr); banks and insurers: extraordinary items."""
    v = r.values
    kind = "ytd" if r.period_kind != "quarter" else "quarter"
    expected = v["pbt"] - v["tax"]
    parts = []
    if fmt in ("ind_as", "nbfc"):
        for key in ("regulatory_deferral", "discontinued"):
            got = per.get(kind, sec["tie"][key], key)
            if got is not None:
                expected += got[1]
                parts.append(got[0].name)
        if "share_of_associates" in v:
            expected += v["share_of_associates"]
            parts.append("share of associates")
    else:
        got = per.get(kind, sec["tie"]["extraordinary"], "extraordinary")
        if got is not None:
            expected += got[1]
            parts.append(got[0].name)
    if not _close(v["pat"], expected):
        raise StatementError("pat", f"{r.period_kind}: PAT {v['pat']} ≠ PBT {v['pbt']} − tax {v['tax']}"
                                    + (f" + {', '.join(parts)}" if parts else "") + f" = {expected}")


def _balance_sheet(r: Row, per: _Periods, sec: dict, fmt: str) -> None:
    v = r.values
    if fmt in INSURERS:
        if "assets_net" not in v:
            return
        other = per.get("instant", sec["tie"]["sources_of_funds"], "sources_of_funds")
        if other is None:
            return                              # a quarter without the balance sheet: Assets alone, nothing to tie
        assets, label = v["assets_net"], f"sources of funds {other[1]}"
        rhs = other[1]
    elif "total_assets" not in v:
        return
    elif fmt == "bank":
        other = per.get("instant", sec["tie"]["capital_and_liabilities"], "capital_and_liabilities")
        if other is None:
            raise StatementError("balance_sheet", "total assets are filed but capital and liabilities aren't")
        filed = per.get("instant", sec["instant"]["total_assets"], "total_assets")
        assets = filed[1] if filed else per.unitless("instant", sec["instant"]["total_assets"])
        if assets is None:
            raise StatementError("balance_sheet", "capital and liabilities are filed but total assets aren't")
        rhs, label = other[1], f"capital and liabilities {other[1]}"
    else:
        liab = per.get("instant", sec["tie"]["liabilities"], "liabilities")
        if "total_equity" in v and liab is not None:
            rhs, label = v["total_equity"] + liab[1], f"equity {v['total_equity']} + liabilities {liab[1]}"
        else:
            el = per.get("instant", sec["tie"]["equity_and_liabilities"], "equity_and_liabilities")
            if el is None:
                raise StatementError("balance_sheet", "total assets are filed but equity and liabilities aren't")
            rhs, label = el[1], f"equity and liabilities {el[1]}"
        assets = v["total_assets"]
    if abs(assets - rhs) > abs(assets) * TIE:
        raise StatementError("balance_sheet", f"total assets {assets} ≠ {label}")


# ---------------------------------------------------------------------------------------- X1's reference values
# reference_values.csv column -> (period kind, equity.financials column): X1's FY2026 figures (Data Audit 02 §4.2;
# the kit's `_reference_row`), read from the year's column of the Q4 filing and its balance sheet
REFERENCE = {
    "revenue": ("year", "revenue"), "net_income_attrib": ("year", "pat_owners"), "net_income": ("year", "pat"),
    "total_equity": ("instant", "total_equity"), "equity_owners": ("instant", "equity_owners"),
    "total_assets": ("instant", "total_assets"), "ocf": ("year", "cfo"), "shares": ("year", "shares_outstanding"),
}
REFERENCE_COLUMNS = tuple(dict.fromkeys(col for _, col in REFERENCE.values()))


@dataclass(frozen=True)
class Reference:
    symbol: str
    basis: str
    period_end: date
    file_name: str               # the filing's instance, from the row's source URL
    values: dict[str, Decimal | None]


def load_reference(path: Path) -> list[Reference]:
    """The audit's reference file (data/fixtures/equity/audit/reference_values.csv, private: NSE-derived figures)."""
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out.append(Reference(r["symbol"], r["basis"], date.fromisoformat(r["period_end"]),
                                 r["source_url"].rstrip("/").rsplit("/", 1)[-1],
                                 {k: (Decimal(r[k]) if r[k] else None) for k in REFERENCE}))
    return out


def reference_differences(ref: Reference, rows: dict[str, dict[str, Decimal | None]],
                          tags: dict[str, dict[str, str]] | None = None) -> list[str]:
    """Each reference figure that the stored rows (period kind -> column -> value; `tags` the same for the elements)
    don't equal exactly, naming the element and both values. A blank reference figure must be stored as null."""
    out = []
    for key, (kind, col) in REFERENCE.items():
        want, got = ref.values[key], rows.get(kind, {}).get(col)
        if want != got:
            element = (tags or {}).get(kind, {}).get(col) or "no element"
            out.append(f"{key} ({kind} {col}, {element}): reference {want}, stored {got}")
    return out


# ---------------------------------------------------------------------------------------- lease payments (2A-11)
# Results filings tag no lease liabilities, so `equity.metrics` holds EV-based figures back where lease payments are
# material against EBIT (Anand, 30 Oct 2026, on #117). No column of equity.financials holds the payments, so the
# metrics task reads them from the stored instance on demand, through the same reader and re-dating as the parse.
LEASE_PAYMENTS = ("PaymentsOfLeaseLiabilitiesClassifiedAsFinancingActivities",)


def lease_payments(body: bytes, filing: Filing) -> tuple[Decimal, str] | None:
    """The year to date's lease payments (financing cash flow) as a positive amount with its element, or None when the
    filing doesn't tag them. A file the parse would refuse raises StatementError the same way."""
    try:
        inst = xbrl.parse(body)
    except xbrl.XbrlError as e:
        raise StatementError("unreadable", str(e)) from None
    pe = xbrl.to_date(inst.header.get("period_end"))
    if inst.basis != filing.basis or pe != filing.period_end:
        raise StatementError("period", "the file isn't the filing's basis and period")
    fs = xbrl.to_date(inst.header.get("fy_start")) or filing.fy_start
    xbrl.date_columns(inst, pe, fs)
    per = _Periods(inst, filing.basis)
    got = None
    for kind in (["ytd"] if per.ytd is not None else []) + ["quarter"]:
        # Sep 2020 to Mar 2021 files put the half-year's cash flow in the quarter column (see _rows); results carry no
        # quarterly cash flow, so the quarter column's figure is the year to date's
        if kind == "quarter" and per.ytd is not None and per.ytd != per.quarter \
                and per.get("ytd", section(filing.format)["cash_flow"]["cfo"], "cfo") is not None:
            break
        got = per.get(kind, list(LEASE_PAYMENTS), "lease_payments")
        if got is not None:
            break
    if got is None:
        return None
    f, v = got
    if (f.unit or "").upper() != "INR":
        raise StatementError("unit", f"{f.name} is in {f.unit or 'no unit'}, not INR")
    return abs(v), f.name
