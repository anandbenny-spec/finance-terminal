"""A test helper: EDGAR companyfacts → the valuation engine's inputs, in USD (job 2A-13; Spec 02A §5.7, Plan 02 §2.1:
EDGAR is the engine's test set from the start).

Ported minimally from the audit kit's `kit/edgar.py` (Stage C test E1; ported, never imported, `CLAUDE.md` §8), with
only the rules the engine needs (Data Audit 02 rows D1–D3, red flag 10, 02a finding 14):
- facts from periodic reports only (10-K, 10-Q and their variants: a proxy statement's rounded figures lost to them in
  E1's 19 Oct run), one per period, the latest filed winning;
- durations classed by day count, never by the fiscal-period labels;
- a fallback chain of tags per field, the primary the first still current (a tag abandoned for over 400 days gives way:
  the revenue tag changes of red flag 10), the rest filling only the periods it lacks; an equity fill from the other
  equity concept adjusted by non-controlling interests at the same date;
- year-end balances chosen by date (the fiscal year ends that at least five 12-month 10-K facts share), not by the
  form of the latest filing;
- CIK succession: ExxonMobil Holdings Corp (CIK 2115436) with Exxon Mobil Corp's history (CIK 34088), the successor's
  facts winning;
- shares from the cover page's `dei:EntityCommonStockSharesOutstanding`, summed across classes; for multi-class
  issuers, whose current per-class shares companyfacts doesn't hold, the totals E1 read from the filings' cover pages
  (`equity/audit/E1_latest.json`).

**Decided by Claude (standing delegation, 1 Nov 2026, #119), each only for this test:** cash and investments is the
kit's cash line alone (E1's fields; counting marketable securities too takes Apple's invested capital below zero, so
the engine's rule would make it N/A); the cost of debt comes from the newest of the three financial years that tags
interest expense, with that year's opening and closing debt (Apple stopped tagging it after FY2023), and interest paid
(cash flow) is the interest chain's last fallback (GE Vernova tags no interest expense); TTM = the latest FY + the year
to date − the same period a year earlier. **Test assumptions, not market data:** a beta of 1 and a market
capitalisation of 20 × TTM net income, used only for the WACC's weights. So the US values check the engine (finite,
in USD, reproducible); they aren't valuations. The US rates are fixed with their dates (Data Audit 02 E8: the 10-year
Treasury, the mature-market ERP, a 25% marginal rate).

When the US slice arrives (its second use), this moves into `ft/equity/` (`CLAUDE.md` §6, reuse rule 2).
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from ft.equity.valuation.model import Company, Input, Market

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
EDGAR = FIX / "edgar"
CIK = {"AAPL": 320193, "MSFT": 789019, "NVDA": 1045810, "WMT": 104169, "JPM": 19617, "BRK-B": 1067983,
       "GOOGL": 1652044, "O": 726728, "GEV": 1996810, "XOM": 2115436}
PREDECESSORS = {"XOM": [34088]}
FAMILY = {"JPM": "lender", "BRK-B": "insurer"}        # financials: N/A in the engine (Spec 02A §5.7)

# The US parameters, fixed with their dates (Data Audit 02 E8; Brief §6.3: the 10-year Treasury yield and the
# mature-market ERP). Both from Damodaran's ERPbymonth.xlsx, sheet "Historical ERP", row 1 Oct 2026: "T.Bond Rate"
# 5.29% and "ERP (T12 m with sustainable payout)" 4.20%; the saved file is in the fixtures (equity/damodaran/).
US_10Y = Decimal("0.0529")
US_ERP = Decimal("0.0420")
US_MONTH = dt.date(2026, 10, 1)
US_TAX = Decimal("0.25")
BETA = Decimal(1)
PE = Decimal(20)

PERIODIC_FORMS = {"10-K", "10-Q", "10-KT", "10-QT", "20-F", "40-F"}
STALE_DAYS = 400
FIELD_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "RevenuesNetOfInterestExpense",
                "SalesRevenueGoodsNet", "OperatingLeasesIncomeStatementLeaseRevenue", "RealEstateRevenueNet",
                "InterestAndDividendIncomeOperating"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss",
                   "NetIncomeLossAttributableToParent"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestPaidNet"],
    "total_equity_incl_nci": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                              "StockholdersEquity"],
    "nci": ["MinorityInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "Cash"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
    "st_debt": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings",
                "LongTermDebtAndCapitalLeaseObligationsCurrent", "CommercialPaper"],
}
FLOW = {"revenue", "operating_income", "net_income", "income_tax", "pretax_income", "interest_expense"}
NCI_ADJUST = {("total_equity_incl_nci", "StockholdersEquity"): +1}
SOURCE = "SEC EDGAR companyfacts (10-K and 10-Q)"


@dataclass(frozen=True)
class F:
    tag: str
    start: dt.date | None
    end: dt.date
    val: Decimal
    filed: dt.date
    form: str

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


def companyfacts(ticker: str) -> dict:
    def read(cik: int) -> dict:
        with gzip.open(EDGAR / f"companyfacts_CIK{cik:010d}.json.gz", "rt", encoding="utf-8") as fh:
            return json.load(fh)
    primary, preds = read(CIK[ticker]), [read(c) for c in PREDECESSORS.get(ticker, [])]
    if not preds:
        return primary
    out = {"cik": primary.get("cik"), "entityName": primary.get("entityName"), "facts": {}}
    for doc in [primary] + preds:                      # the successor first: its facts win for the same period
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
        for entries in body.get("units", {}).values():
            best: dict[tuple, F] = {}
            for e in entries:
                if (e.get("form") or "").split("/")[0] not in PERIODIC_FORMS:
                    continue
                try:
                    f = F(tag, _d(e.get("start")), _d(e["end"]), Decimal(str(e["val"])), _d(e["filed"]),
                          e.get("form", ""))
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


def merged(facts: dict[str, list[F]], field: str) -> list[F]:
    tags = pick_tags(facts, FIELD_TAGS[field], field in FLOW)
    out: dict[tuple, F] = {}
    nci = None
    for i, t in enumerate(tags):
        sign = NCI_ADJUST.get((field, t)) if i > 0 else None
        if sign and nci is None:
            nci = {f.end: f.val for f in facts.get("MinorityInterest", []) if f.start is None}
        for f in facts.get(t, []):
            k = (f.start, f.end)
            if k in out:
                continue
            if sign:
                if f.end in nci:
                    f = replace(f, val=f.val + sign * nci[f.end], tag=f"{t}+MinorityInterest")
                elif "MinorityInterest" in facts:
                    continue
            out[k] = f
    return list(out.values())


def fiscal_year_ends(facts: dict[str, list[F]], min_tags: int = 5) -> list[dt.date]:
    count: dict[dt.date, int] = defaultdict(int)
    n_tags = 0
    for fs in facts.values():
        ends = {f.end for f in fs if f.start is not None and f.kind == "FY" and f.form.startswith("10-K")}
        n_tags += bool(ends)
        for e in ends:
            count[e] += 1
    need = min(min_tags, max(1, n_tags // 2))
    return sorted(e for e, n in count.items() if n >= need)


def recorded_shares(ticker: str) -> tuple[Decimal, dt.date, str] | None:
    """E1's cover-page total for a multi-class issuer (companyfacts has no current per-class shares)."""
    e1 = json.loads((FIX / "audit" / "E1_latest.json").read_text("utf-8"))
    sh = e1["info"]["per_name"][ticker]["shares"]
    if not sh.get("multi_class_expected"):
        return None
    latest = sh["latest"]
    return Decimal(str(latest["total"])), dt.date.fromisoformat(latest["end"]), latest["source"]


def cover_shares(cf: dict) -> tuple[Decimal, dt.date] | None:
    entries = cf.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}) \
        .get("units", {}).get("shares", [])
    groups: dict[tuple, list] = defaultdict(list)
    filed: dict[str, str] = {}
    for e in entries:
        groups[(e["accn"], e["end"])].append(Decimal(str(e["val"])))
        filed[e["accn"]] = e.get("filed") or ""
    if not groups:
        return None
    accn, end = max(groups, key=lambda k: (k[1], filed[k[0]]))
    return sum(groups[(accn, end)], Decimal(0)), dt.date.fromisoformat(end)


class Filer:
    """One company's facts, by field."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.cf = companyfacts(ticker)
        self.facts = load_facts(self.cf)
        self.fy_ends = fiscal_year_ends(self.facts)
        self.fields = {k: merged(self.facts, k) for k in FIELD_TAGS}

    def annual(self, field: str, end: dt.date) -> F | None:
        return next((f for f in self.fields[field] if f.end == end and f.kind == "FY"), None)

    def instant(self, field: str, end: dt.date) -> F | None:
        return next((f for f in self.fields[field] if f.start is None and f.end == end), None)

    def latest_instant(self) -> dt.date:
        return max(f.end for f in self.fields["total_equity_incl_nci"] if f.start is None)

    def ttm(self, field: str) -> tuple[Decimal, dt.date, dict] | None:
        """The latest FY plus the year to date since it, less the same period a year earlier."""
        fy_end = self.fy_ends[-1]
        fy = self.annual(field, fy_end)
        if fy is None:
            return None
        start = fy_end + dt.timedelta(days=1)
        ytd = [f for f in self.fields[field] if f.start == start and f.kind in ("Q", "6M", "9M")]
        if not ytd:
            return fy.val, fy_end, {"fy": str(fy.val)}
        cur = max(ytd, key=lambda f: f.end)
        prior = next((f for f in self.fields[field] if f.start is not None and f.kind == cur.kind
                      and abs((f.start - fy.start).days) <= 7 and abs((f.end - (cur.end - dt.timedelta(days=364))).days) <= 7),
                     None) if fy.start else None
        if prior is None:
            return None
        return fy.val + cur.val - prior.val, cur.end, {"fy": str(fy.val), "ytd": str(cur.val), "ytd_prior": str(prior.val)}


def _held(why: str, unit: str = "money", when: dt.date | None = None) -> Input:
    return Input.withheld(why, unit, SOURCE, when)


def _val(f: F | None, field: str, *, absent_zero: bool = False) -> Decimal | None:
    if f is None:
        return Decimal(0) if absent_zero else None
    return f.val


def market() -> Market:
    def fixed(v, src):
        return Input.of(v, "fraction", US_MONTH, src)
    erpfile = "Aswath Damodaran, NYU Stern, ERPbymonth.xlsx, 1 Oct 2026"
    return Market("US", "USD", fixed(US_10Y, f"10-year US Treasury yield ({erpfile}: T.Bond Rate)"),
                  fixed(US_10Y, f"10-year US Treasury yield ({erpfile}: T.Bond Rate)"),
                  fixed(US_ERP, f"implied ERP, mature market ({erpfile})"),
                  Input.of(US_TAX, "fraction", None, "US marginal rate, federal 21% plus state (Data Audit 02 E8)"))


def company(ticker: str) -> Company:
    f = Filer(ticker)
    family = FAMILY.get(ticker, "operating")
    fy = f.fy_ends[-4:]
    last3 = fy[-3:]
    t_end = f.latest_instant()

    def flow3(field, why):
        rows = [f.annual(field, e) for e in last3]
        if len(last3) < 3 or any(r is None for r in rows):
            return _held(why, when=last3[-1] if last3 else None)
        return Input.of(sum((r.val for r in rows), Decimal(0)), "money", last3[-1], f"{SOURCE}: {rows[0].tag}",
                        years=[e.isoformat() for e in last3])

    def debt_at(end):
        lt, st_ = f.instant("lt_debt", end), f.instant("st_debt", end)
        if lt is None and st_ is None:
            return None
        return _val(lt, "lt_debt", absent_zero=True) + _val(st_, "st_debt", absent_zero=True)

    def cash_at(end):
        c = f.instant("cash", end)
        return None if c is None else c.val

    def ic_at(end):
        eq, d, c = f.instant("total_equity_incl_nci", end), debt_at(end), cash_at(end)
        if eq is None or c is None:
            return None
        return eq.val + (d or Decimal(0)) - c

    opening = [fy[i - 1] for i in range(1, len(fy))][-3:] if len(fy) >= 4 else []
    ics = [ic_at(e) for e in opening]
    cap3 = (_held("Fewer than three year-end balance sheets are filed.", when=last3[-1] if last3 else None)
            if len(opening) < 3 or any(v is None for v in ics) else
            _held("Opening invested capital is zero or negative.") if any(v <= 0 for v in ics) else
            Input.of(sum(ics, Decimal(0)), "money", last3[-1], SOURCE, openings=[e.isoformat() for e in opening]))

    def ttm(field, why):
        got = f.ttm(field)
        if got is None:
            return _held(why)
        v, end, parts = got
        return Input.of(v, "money", end, SOURCE, **parts)

    cagr = _held("Fewer than four financial years are filed.", "fraction")
    if len(fy) >= 4:
        a, b = f.annual("revenue", fy[-1]), f.annual("revenue", fy[-4])
        if a is not None and b is not None and a.val > 0 and b.val > 0:
            cagr = Input.of((a.val / b.val) ** (Decimal(1) / 3) - 1, "fraction", fy[-1], SOURCE)

    # the cost of debt's year: the newest of the three that tags interest expense
    kd_year = next((i for i in range(len(fy) - 1, max(len(fy) - 4, 0), -1)
                    if f.annual("interest_expense", fy[i]) is not None), None)
    interest = f.annual("interest_expense", fy[kd_year]) if kd_year is not None else None
    debt_now, cash_now = debt_at(t_end), cash_at(t_end)
    nci = f.instant("nci", t_end)
    shares = recorded_shares(ticker)
    if shares is None:
        got = cover_shares(f.cf)
        shares = (got[0], got[1], "cover page (companyfacts dei)") if got else None
    ni = f.ttm("net_income")
    return Company(
        ticker, "US", "USD", family,
        ttm("revenue", "TTM revenue can't be built."),
        ttm("operating_income", "No operating-income line is filed."),
        flow3("revenue", "Fewer than three financial years of revenue are filed."),
        flow3("operating_income", "No operating-income line is filed."),
        flow3("income_tax", "Fewer than three financial years of tax expense are filed."),
        flow3("pretax_income", "Fewer than three financial years of profit before tax are filed."),
        cap3, cagr,
        Input.of(interest.val, "money", fy[kd_year], f"{SOURCE}: {interest.tag}") if interest else
        _held("No interest expense is filed."),
        Input.of(debt_at(fy[kd_year - 1]) or 0, "money", fy[kd_year - 1], SOURCE) if interest else
        _held("No interest expense is filed."),
        Input.of(debt_at(fy[kd_year]) or 0, "money", fy[kd_year], SOURCE) if interest else
        _held("No interest expense is filed."),
        Input.of(debt_now or 0, "money", t_end, SOURCE),
        Input.of(cash_now, "money", t_end, SOURCE) if cash_now is not None else _held("No cash line is filed."),
        Input.of(nci.val if nci else 0, "money", t_end, SOURCE),
        Input.of(shares[0], "shares", shares[1], shares[2]) if shares else _held("No shares are filed.", "shares"),
        Input.of(PE * ni[0], "money", ni[1], "test assumption, not market data: 20 × TTM net income")
        if ni and ni[0] > 0 else _held("TTM net income isn't positive."),
        Input.of(BETA, "beta", None, "test assumption, not market data: the market's beta"),
    )
