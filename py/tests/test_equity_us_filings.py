"""us.filings and the EDGAR adapter (job US-02, #401; Spec 02A.1 §5.2; Data Audit 02a finding 14).

The adapter's rows are checked against SEC's company facts saved in data/fixtures/equity/edgar (the private fixtures
repo: the audit kit's 10 E1 names) and against E1's own results (`equity/audit/E1_latest.json`: the kit's annual and
quarterly figures, its derived fourth quarters and differenced cash flows). The task runs through the runner on the
test database with a mock SEC (the saved files; a made inline XBRL cover page for Alphabet) and a mock object store.
"""
import copy
import functools
import gzip
import itertools
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import equity_edgar as helper
import httpx
import psycopg
import pytest
from conftest import dsn
from moto.server import ThreadedMotoServer

from ft.equity import edgar as E
from ft.equity import us_universe as us
from ft.jobs import config
from ft.jobs.fetch import HttpClient, site_for
from ft.jobs.runner import run
from ft.jobs.tasks import equity_us_filings as task_module
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_us_filings import UsFilings, companyfacts_url, document_url, parse_recent

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
EDGAR = FIX / "edgar"
needs_fixtures = pytest.mark.skipif(not EDGAR.exists(), reason="the saved files aren't checked out (data/fixtures)")
CIK = {"AAPL": 320193, "MSFT": 789019, "NVDA": 1045810, "WMT": 104169, "JPM": 19617, "BRK-B": 1067983,
       "GOOGL": 1652044, "O": 726728, "GEV": 1996810, "XOM": 2115436}
PRED = {"XOM": [34088]}
DOW = ("AAPL", "MSFT", "NVDA", "WMT", "JPM", "GOOGL")          # the E1 names in the Dow
START = date(2019, 1, 1)
D = date


@functools.cache
def read(cik: int) -> dict:
    """A saved company facts file (read once per session: callers that change it copy it first)."""
    return json.loads(gzip.decompress((EDGAR / f"companyfacts_CIK{cik:010d}.json.gz").read_bytes()))


@functools.cache
def statements(ticker: str) -> E.Statements:
    return E.Statements([(c, read(c)) for c in [CIK[ticker], *PRED.get(ticker, [])]], E.load_tags())


def window(s: E.Statements) -> list[E.Filing]:
    return sorted((f for f in s.filings.values() if f.period_end and f.period_end >= START), key=lambda f: (f.filed, f.accn))


def book(s: E.Statements) -> dict:
    """The rows of every filing, the latest filed winning per period (as E1 read company facts)."""
    out = {}
    for f in sorted(s.filings.values(), key=lambda f: (f.filed, f.accn)):
        b = s.build(f)
        for r in (b.rows if b else []):
            out[(r.period_kind, r.period_end)] = r
    return out


E1 = {"revenue": "revenue", "net_income": "pat_owners", "operating_income": "operating_income",
      "total_assets": "total_assets", "total_equity": "equity_owners", "ocf": "cfo", "capex": "capex", "cash": "cash"}
INSTANT = {"total_assets", "equity_owners", "cash"}


def e1_differences(ticker: str) -> list[tuple]:
    e1 = json.loads((FIX / "audit" / "E1_latest.json").read_text())["info"]["per_name"][ticker]["fields"]
    rows = book(statements(ticker))
    out = []
    for field, col in E1.items():
        for when, kind in (("annual", "year"), ("quarters", "quarter")):
            for x in (e1.get(field) or {}).get(when, []):
                end = D.fromisoformat(x["end"])
                r = rows.get(("instant" if col in INSTANT else kind, end))
                v = r.values.get(col) if r else None
                if v is None or v != Decimal(str(x["value"])).quantize(Decimal(1)):
                    out.append((field, when, x["end"]))
    return out


# ---------------------------------------------------------------------------------------------- the rules moved here
def test_the_helper_imports_its_rules_from_the_adapter():
    assert helper.pick_tags is E.pick_tags and helper.load_facts is E.load_facts and helper.F is E.F
    assert helper.fiscal_year_ends is E.fiscal_year_ends and helper.cover_shares is E.cover_shares
    assert helper.PERIODIC_FORMS == E.PERIODIC_FORMS


def test_the_column_map_loads_and_refuses_a_broken_file(tmp_path):
    t = E.load_tags()
    assert t.start == D(2019, 1, 1) and t.tolerance == Decimal("0.005")
    assert [e.tag for e in t.columns["pat_owners"].chain] == [
        "NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]               # no ProfitLoss (that is pat)
    assert t.columns["total_equity"].chain[1] == E.Entry("StockholdersEquity", 1)
    assert t.columns["equity_owners"].chain[1].nci == -1
    assert "InterestPaidNet" not in [e.tag for e in t.columns["finance_cost"].chain]
    debt = t.columns["borrowings_current"]
    assert debt.parts[:3] == ("LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper")
    assert debt.sum == (("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"),
                        ("ShortTermBorrowings", "CommercialPaper"))                 # never both of one group (#455)
    assert debt.included == {"ShortTermBorrowings": ("OtherShortTermBorrowings",)} and debt.not_summed == {}
    # #455's questions 1 to 3 (Anand, 12 Nov 2026; #470)
    assert debt.added_to == {"CommercialPaper": ("OtherShortTermBorrowings",)}
    assert debt.tie == {"ShortTermBorrowings": ("CommercialPaper", "OtherShortTermBorrowings")}
    ibm = debt.filed_total["IBM"]
    assert (ibm.tag, ibm.label, ibm.never_below) == (
        "ShortTermBorrowings", "short-term debt as filed, including current maturities of long-term debt",
        ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"))
    assert ibm.source.startswith("IBM's 10-K: the balance sheet's \"Short-term debt\" line")
    assert t.columns["pbt"].all_parts == ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
                                          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign")
    assert t.columns["pbt"].all_parts_label == "the sum of the filed domestic and foreign parts"
    assert sorted(t.share_classes) == ["GOOGL", "NKE", "V"]
    assert set(t.columns) <= set(task_module.columns(t)) and "shares_outstanding" in task_module.columns(t)
    text = E.TAGS_FILE.read_text()
    for bad, message in ((text.replace("kind: flow", "kind: stream", 1), "kind 'stream'"),
                         (text.replace("from: 2019-01-01", "from: soon"), "needs `from`"),
                         (text.replace("CommonClassBMember: 1, CapitalClassCMember: 1", "CommonClassBMember: 0, CapitalClassCMember: 1"),
                          "positive number"),
                         (text.replace("included: {ShortTermBorrowings: [OtherShortTermBorrowings]}",
                                       "included: {ShortTermBorrowings: [NotesPayableCurrent]}"), "NotesPayableCurrent isn't one"),
                         (text.replace("effective: 2026-09-18", "effective: soon", 1), "effective is a date"),
                         (text.replace("review_days: 120", "review_days: -1"), "review_days"),
                         (text.replace("added_to: {CommercialPaper: [OtherShortTermBorrowings]}",
                                       "added_to: {CommercialPaper: [NotesPayableCurrent]}"), "NotesPayableCurrent isn't one"),
                         (text.replace("never_below: [LongTermDebtCurrent,", "never_below: [DebtCurrent,"),
                          "DebtCurrent isn't one"),
                         (text.replace("        label: short-term debt as filed", "        lable: short-term debt as filed"),
                          "filed_total IBM needs its tag, label and source"),
                         (text.replace("not_summed: {}", "not_summed: {IBM: why}"), "IBM is in both"),
                         (text.replace("    all_parts_label: the sum of the filed domestic and foreign parts\n", ""),
                          "all_parts is for a flow column")):
        (tmp_path / "t.yaml").write_text(bad)
        with pytest.raises(E.EdgarError, match=message):
            E.load_tags(tmp_path / "t.yaml")


# ---------------------------------------------------------------------------------------------- against E1
@needs_fixtures
@pytest.mark.parametrize("ticker", [*DOW, "BRK-B", "XOM"])
def test_the_rows_equal_e1s_figures(ticker):
    """Every annual and quarterly figure E1 built (its fourth quarters from the year less nine months, its quarterly
    cash flow differenced) equals the adapter's rows, to the dollar."""
    assert e1_differences(ticker) == []


@needs_fixtures
def test_where_the_rows_differ_from_e1_by_design():
    # Realty Income: its capex tag (capital improvements) isn't in the map. Its 2026 net income is the fallback,
    # NetIncomeLossAvailableToCommonStockholdersBasic, as E1 read it
    assert {x[0] for x in e1_differences("O")} == {"capex"}
    s = statements("O")
    q = next(r for r in s.build(window(s)[-1]).rows if r.period_kind == "quarter")
    assert q.tags["pat_owners"] == "NetIncomeLossAvailableToCommonStockholdersBasic"
    # GE Vernova: its FY2023 figures are only comparatives in its first 10-K (it was spun off in April 2024), and a
    # row is a filing's own period
    assert {x[2] for x in e1_differences("GEV")} == {"2023-12-31"}


# ---------------------------------------------------------------------------------------------- the rules
@needs_fixtures
def test_apples_year_ends_in_september_and_its_fourth_quarter_is_derived():
    s = statements("AAPL")
    fy = next(f for f in window(s) if f.form == "10-K" and f.period_end == D(2025, 9, 27))
    b = s.build(fy)
    assert (fy.accn, b.fy_start) == ("0000320193-25-000079", D(2024, 9, 29))
    assert fy.link() == "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"
    q, y, bs = b.rows
    assert (q.period_kind, q.period_start, q.period_end) == ("quarter", D(2025, 6, 29), D(2025, 9, 27))
    assert q.values["revenue"] == 102466000000 and q.tags["revenue"].startswith(
        "derived: RevenueFromContractWithCustomerExcludingAssessedTax FY − 9M filed 2025-08-01")
    assert "eps_basic" not in q.values                                    # a per-share figure is never derived
    assert (y.period_kind, y.values["revenue"], y.values["cfo"]) == ("year", 416161000000, 111482000000)
    assert (bs.period_kind, bs.period_start, bs.values["total_assets"]) == ("instant", None, 359241000000)
    assert b.balance == (True, "assets 359241000000 vs liabilities 285508000000 + temporary equity 0 + equity 73733000000")
    q2 = s.build(next(f for f in window(s) if f.period_end == D(2026, 3, 28)))
    assert [(r.period_kind, r.period_start) for r in q2.rows] == [("quarter", D(2025, 12, 28)), ("half_year", D(2025, 9, 28)),
                                                                  ("instant", None)]
    assert (q2.rows[0].values["cfo"], q2.rows[0].tags["cfo"]) == (
        28702000000, "derived: NetCashProvidedByUsedInOperatingActivities 6M − Q filed 2026-01-30")


@needs_fixtures
def test_jpmorgan_files_no_operating_income():
    s = statements("JPM")
    rows = [r for f in window(s) for r in s.build(f).rows]
    assert len(rows) > 80 and not any("operating_income" in r.values for r in rows)
    assert all(r.values.get("revenue") for r in rows if r.period_kind in ("quarter", "year"))


@needs_fixtures
def test_exxons_history_stays_under_its_predecessors_cik():
    s = statements("XOM")
    w = window(s)
    assert [(f.accn, f.cik, f.form) for f in w[-3:]] == [("0000034088-26-000045", 34088, "10-K"),
                                                          ("0000034088-26-000067", 34088, "10-Q"),
                                                          ("0000034088-26-000093", 2115436, "10-Q")]
    assert w[-1].link().startswith("https://www.sec.gov/Archives/edgar/data/2115436/")
    assert len({f.period_end for f in w if f.annual}) == 7               # 2019 to 2025, across both CIKs


@needs_fixtures
@pytest.mark.parametrize("ticker", list(CIK))
def test_every_year_end_balance_sheet_ties(ticker):
    s = statements(ticker)
    got = [s.build(f).balance for f in window(s) if f.annual]
    assert got and all(ok for ok, _ in got), [x for x in got if not x[0]]
    if ticker == "WMT":                                                   # files no total-liabilities line
        assert all("no total-liabilities line" in how for _, how in got)


@needs_fixtures
@pytest.mark.parametrize("ticker", DOW)
def test_the_period_end_is_secs_report_date(ticker):
    s = statements(ticker)
    listed = parse_recent(gzip.decompress((EDGAR / f"submissions_CIK{CIK[ticker]:010d}.json.gz").read_bytes()))
    both = [f for f in s.filings.values() if f.accn in listed]
    assert len(both) >= 4
    assert [(f.accn, f.period_end, f.form) for f in both if (f.period_end, f.form) !=
            (listed[f.accn].report_date, listed[f.accn].form)] == []


@needs_fixtures
def test_a_forward_looking_line_never_sets_the_period():
    """Dev run 3 (11 Nov 2026): P&G's FY2019 10-K tags a few figures for the year to 30 Jun 2020. The fixtures hold the
    same shape: Nvidia's FY2014 10-K (one fact for the quarter to 27 Apr 2014) and Walmart's 2011 and 2012 10-Ks."""
    nvda = statements("NVDA").filings["0001045810-14-000030"]
    assert (nvda.form, nvda.period_end) == ("10-K", D(2014, 1, 26))
    wmt = statements("WMT").filings
    assert (wmt["0001193125-11-083157"].period_end, wmt["0001193125-12-134679"].period_end) == (D(2011, 1, 31), D(2012, 1, 31))


@needs_fixtures
def test_current_debt_is_a_filed_total_one_line_or_its_filed_captions_summed():
    """#401's question 1, option A (Anand, 12 Nov 2026; #455)."""
    def latest(ticker):
        s = statements(ticker)
        f = window(s)[-1]
        r = next(r for r in s.build(f).rows if r.period_kind == "instant")
        return r.values.get("borrowings_current"), r.tags.get("borrowings_current"), f
    assert latest("NVDA")[:2] == (1000000000, "DebtCurrent")              # a filed total
    assert latest("MSFT")[:2] == (9227000000, "LongTermDebtCurrent")      # the one line it files
    assert latest("AAPL")[:2] == (11007000000 + 1997000000,
                                  "LongTermDebtCurrent+CommercialPaper (the sum of the filed captions; no total is filed)")
    v, how, f = latest("WMT")                                             # Walmart: plus short-term borrowings
    parts = [next(iter(f.get(t, E.MONEY, None, f.period_end))) for t in ("LongTermDebtCurrent", "ShortTermBorrowings")]
    assert parts[1] == 10479000000 and v == sum(parts)
    assert how == "LongTermDebtCurrent+ShortTermBorrowings (the sum of the filed captions; no total is filed)"
    # Apple's 2020 quarters filed OtherShortTermBorrowings with no ShortTermBorrowings line: outside the decided sum
    s = statements("AAPL")
    q = next(r for r in s.build(next(f for f in window(s) if f.period_end == D(2020, 3, 28))).rows if r.period_kind == "instant")
    assert "borrowings_current" not in q.values
    assert q.tags["borrowings_current"].endswith("no total is filed; OtherShortTermBorrowings outside the decided sum")


def debt(parts: dict[str, int], ticker: str | None = None) -> tuple:
    cf = facts({t: {"USD": [fact(v, "2026-06-30")]} for t, v in parts.items()} |
               {"NetIncomeLoss": {"USD": [fact(1, "2026-06-30", "2026-04-01")]}, "Assets": {"USD": [fact(99, "2026-06-30")]}})
    s = E.Statements([(1, cf)], E.load_tags(), ticker)
    r = s.build(next(iter(s.filings.values()))).rows[-1]
    return r.values.get("borrowings_current"), r.tags.get("borrowings_current")


def test_current_debt_in_parts_takes_one_caption_of_each_group_never_both():
    lt, lcl, stb, cp, other = ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "ShortTermBorrowings",
                               "CommercialPaper", "OtherShortTermBorrowings")
    assert debt({"DebtCurrent": 9, lt: 5, cp: 4}) == (9, "DebtCurrent")                # a filed total wins
    assert debt({lt: 5, cp: 0}) == (5, lt)                                            # one non-zero caption
    assert debt({lt: 0, cp: 0}) == (0, f"{lt}+{cp} (filed as zero)")
    # Home Depot: commercial paper and current installments with finance leases
    assert debt({cp: 4248, lcl: 4697}) == (8945, f"{lcl}+{cp} (the sum of the filed captions; no total is filed)")
    # short-term borrowings include commercial paper and other short-term borrowings (Coca-Cola 2020-22: 2,373 =
    # 2,146 + 227): never both
    assert debt({lt: 399, stb: 2373, cp: 2146, other: 227}) == (2772, (
        f"{lt}+{stb} (the sum of the filed captions; no total is filed; not added: {cp} (never with {stb}), "
        f"{other} (within {stb}))"))
    # one current-part line only: the one without finance leases first, as borrowings_noncurrent's chain
    assert debt({lt: 5, lcl: 6, cp: 1})[0] == 6
    # other short-term borrowings beside commercial paper, with no short-term borrowings line: added to it (#455's
    # question 2; Anand, 12 Nov 2026; #470). Coca-Cola's year ends from 2023 file these three (2025's, $ millions)
    assert debt({lcl: 1960, cp: 4209, other: 348}, "KO") == (1960 + 4209 + 348, (
        f"{lcl}+{cp}+{other} (the sum of the filed captions; no total is filed)"))
    assert debt({cp: 4209, other: 348}) == (4557, f"{cp}+{other} (the sum of the filed captions; no total is filed)")
    # ... where a short-term borrowings line is filed, nothing changes: it holds both (Coca-Cola 2020-22, above)
    assert debt({lcl: 399, stb: 2373, cp: 2146, other: 227}, "KO")[0] == 399 + 2373
    # ... other short-term borrowings with neither commercial paper nor a short-term borrowings line still hold (Apple's
    # 2020 quarters, which file no commercial paper beside them)
    v, how = debt({lt: 10392, other: 10029})
    assert v is None and how == (f"held: filed in parts ({lt} 10392, {other} 10029) and no total is filed; "
                                 f"{other} outside the decided sum")
    assert debt({lt: 10392, cp: 0, other: 10029})[0] is None                          # commercial paper filed as zero
    # IBM (#455's question 1; #470): its short-term borrowings are its whole current debt, current maturities within
    ibm = "ShortTermBorrowings (short-term debt as filed, including current maturities of long-term debt)"
    assert debt({stb: 7013, lcl: 7009}, "IBM") == (7013, ibm)                         # 30 Sep 2026: nothing added
    assert debt({stb: 8530, cp: 2988, lt: 5147}, "IBM") == (8530, ibm)                # 30 Jun 2019
    assert debt({stb: 8530}, "IBM") == (8530, ibm)
    assert debt({"DebtCurrent": 9, stb: 8}, "IBM") == (9, "DebtCurrent")              # a filed DebtCurrent still wins
    # ... a date where the current part it includes is above it is held, naming both (no values)
    v, how = debt({stb: 7000, lt: 7009, lcl: 1}, "IBM")
    assert v is None and how == (f"held: {stb} (IBM's short-term debt as filed, including current maturities of long-term "
                                 f"debt) is below its {lt} at this date, which it includes")
    assert debt({stb: 7000, lt: 0, lcl: 7009}, "IBM")[0] is None                       # the first non-zero part
    # ... a date with captions but no short-term borrowings line: held, a part is never taken for the whole
    assert debt({lt: 5147, cp: 2988}, "IBM") == (None, (
        f"held: {stb}, IBM's whole current debt as filed, isn't filed for this date (filed: {lt}, {cp})"))
    assert debt({stb: 7013, lcl: 7009}, "SHW")[0] == 14022                            # other companies: option A


def test_where_short_term_borrowings_and_their_parts_dont_tie_the_added_part_is_held():
    """parts_tie (#470): a company whose ShortTermBorrowings differ from CommercialPaper + OtherShortTermBorrowings by more
    than $1 million in a period that files all three has the addition switched off; within it, it stays on."""
    lcl, stb, cp, other = ("LongTermDebtAndCapitalLeaseObligationsCurrent", "ShortTermBorrowings", "CommercialPaper",
                           "OtherShortTermBorrowings")
    m = 1_000_000
    k22, k23 = "0000000005-23-000001", "0000000005-24-000001"
    at = lambda v, end, accn, filed: fact(v, end, accn=accn, form="10-K", filed=filed)

    def ko(stb_2022: int, since: date | None = None) -> E.Statements:
        old = {lcl: 399 * m, stb: stb_2022, cp: 2146 * m, other: 227 * m}
        new = {lcl: 1960 * m, cp: 4209 * m, other: 348 * m}
        cf = facts({t: {"USD": ([at(old[t], "2022-12-31", k22, "2023-02-20")] if t in old else [])
                        + ([at(new[t], "2023-12-31", k23, "2024-02-20")] if t in new else [])}
                    for t in (lcl, stb, cp, other)}
                   | {"NetIncomeLoss": {"USD": [fact(1, "2022-12-31", "2022-01-01", accn=k22, form="10-K", filed="2023-02-20"),
                                                fact(1, "2023-12-31", "2023-01-01", accn=k23, form="10-K", filed="2024-02-20")]},
                      "Assets": {"USD": [at(9, "2022-12-31", k22, "2023-02-20"), at(9, "2023-12-31", k23, "2024-02-20")]}})
        return E.Statements([(1, cf)], E.load_tags(), "KO", since=since)

    def latest(s):
        r = next(r for r in s.build(s.filings[k23]).rows if r.period_kind == "instant")
        return r.values.get("borrowings_current"), r.tags.get("borrowings_current")

    s = ko(2373 * m)                                          # 2,146 + 227 = 2,373: ties, so the rule is on
    assert [(t.column, t.period, t.total, t.ties) for t in s.ties] == [("borrowings_current", "2022-12-31", stb, True)]
    assert s.untied == set() and latest(s)[0] == (1960 + 4209 + 348) * m
    s = ko(2373 * m + m)                                      # exactly $1 million apart: still ties
    assert s.untied == set() and latest(s)[0] == (1960 + 4209 + 348) * m
    s = ko(2373 * m + m + 1)                                  # more than $1 million apart: off, held
    assert s.untied == {"borrowings_current"}
    v, how = latest(s)
    assert v is None and how.endswith(
        f"{other} outside the decided sum (not added to {cp}: {cp} + {other} didn't tie with the filed {stb} for this "
        "company at 2022-12-31 (parts_tie), so the rule is off until Anand rules)")
    # only the window's filings are compared (the task's `since`): a mismatch in a filing before it switches nothing off
    s = ko(2373 * m + 2 * m, since=D(2023, 1, 1))
    assert s.ties == [] and s.untied == set() and latest(s)[0] == (1960 + 4209 + 348) * m


DOM, FOR = ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign")
PBT = "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
K21, Q22, K22 = "0000000006-22-000001", "0000000006-22-000002", "0000000006-23-000001"


def mcd(pbt_2021: int | None = 7_000, *, domestic_2022: bool = True, nine_months: bool = True) -> E.Statements:
    """McDonald's shape (#455's question 3): its FY2021 10-K files the total and its tax note's two parts; from 2022 its
    10-Ks file only the parts. Its Q3 2022 10-Q files the nine months' parts (and the total, as a 10-Q does)."""
    yr = lambda v, y, accn, filed: fact(v, f"{y}-12-31", f"{y}-01-01", accn=accn, form="10-K", filed=filed)
    nine = lambda v: fact(v, "2022-09-30", "2022-01-01", accn=Q22, filed="2022-10-27")
    us_gaap = {
        DOM: {"USD": [yr(2_000, 2021, K21, "2022-02-24")] + ([yr(2_600, 2022, K22, "2023-02-23")] if domestic_2022 else [])
              + ([nine(1_900)] if nine_months else [])},
        FOR: {"USD": [yr(5_000, 2021, K21, "2022-02-24"), yr(5_300, 2022, K22, "2023-02-23")]
              + ([nine(3_950)] if nine_months else [])},
        "NetIncomeLoss": {"USD": [yr(1, 2021, K21, "2022-02-24"), yr(1, 2022, K22, "2023-02-23"), nine(1)]},
    }
    if pbt_2021 is not None:
        us_gaap[PBT] = {"USD": [yr(pbt_2021, 2021, K21, "2022-02-24"), nine(5_850)]}
    return E.Statements([(1, facts(us_gaap))], E.load_tags(), "MCD")


def rows(s: E.Statements, accn: str) -> dict:
    return {r.period_kind: r for r in s.build(s.filings[accn]).rows}


def test_pre_tax_income_filed_only_in_its_domestic_and_foreign_parts_is_their_sum():
    """#455's question 3 (Anand, 12 Nov 2026; #470): where no tag of pbt's chain is filed for a period (same end and
    duration) and both parts are, their sum; one part alone leaves it empty; a filed total is used where there is one."""
    s = mcd()
    assert [(t.column, t.period, t.total, t.ties) for t in s.ties] == [("pbt", "2021-01-01 to 2021-12-31", PBT, True),
                                                                      ("pbt", "2022-01-01 to 2022-09-30", PBT, True)]
    y22 = rows(s, K22)
    assert (y22["year"].values["pbt"], y22["year"].tags["pbt"]) == (
        7_900, f"{DOM}+{FOR} (the sum of the filed domestic and foreign parts; no total is filed)")
    # the fourth quarter: the year's parts less the nine months' parts as the 10-Q filed them (the total isn't used for
    # one side and the parts for the other)
    assert (y22["quarter"].values["pbt"], y22["quarter"].tags["pbt"]) == (
        7_900 - 5_850, f"derived: {DOM}+{FOR} FY − 9M filed 2022-10-27 (the sum of the filed domestic and foreign parts)")
    assert y22["quarter"].period_start == D(2022, 10, 1)
    assert (rows(s, K21)["year"].values["pbt"], rows(s, K21)["year"].tags["pbt"]) == (7_000, PBT)   # a filed total
    assert rows(s, Q22)["nine_months"].tags["pbt"] == PBT
    # one part alone: empty (no tag), as before; the gaps warning still names the part filed
    s = mcd(domestic_2022=False)
    assert "pbt" not in rows(s, K22)["year"].values and "pbt" not in rows(s, K22)["year"].tags
    assert "pbt" not in rows(s, K22)["quarter"].values
    # no nine months' parts filed: the fourth quarter isn't derived (the year's total isn't mixed with them)
    s = mcd(nine_months=False)
    assert rows(s, K22)["year"].values["pbt"] == 7_900 and "pbt" not in rows(s, K22)["quarter"].values
    # parts_tie: the 2021 total more than $1 million away from its parts switches the rule off for the company
    s = mcd(pbt_2021=7_000 + 1_000_001)
    assert s.untied == {"pbt"}
    y22 = rows(s, K22)
    assert "pbt" not in y22["year"].values and y22["year"].tags["pbt"] == (
        f"held: filed only as {DOM}+{FOR}; {DOM} + {FOR} didn't tie with the filed {PBT} for this company at 2021-01-01 to "
        "2021-12-31 (parts_tie), so the rule is off until Anand rules")
    assert "pbt" not in y22["quarter"].values
    assert rows(s, K21)["year"].values["pbt"] == 7_000 + 1_000_001          # a filed total is still the total


def us_company(ticker: str) -> task_module.Company:
    return task_module.Company(next(c for c in us.load_us_config().companies if c.ticker == ticker), 1)


def test_the_parts_tie_warning_names_companies_and_periods_never_values():
    x, y = us_company("MCD"), us_company("KO")
    x.stmts, y.stmts = mcd(pbt_2021=7_000 + 1_000_001), mcd()
    got = UsFilings._parts_tie([x, y])
    assert not got.passed and not got.blocking
    assert got.detail == (f"more than $1 million apart: MCD pbt ({PBT} against {DOM} + {FOR}) at 2021-01-01 to 2021-12-31 "
                          "(1 of 2 periods): its parts rule is off until Anand rules; tied (company, periods): pbt: KO 2")
    assert not any(str(v) in got.detail for v in (7_000, 1_007_001, 2_000, 5_000, 1_900, 3_950, 5_850))
    x.stmts = mcd()
    got = UsFilings._parts_tie([x, y])
    assert got.passed and got.detail == ("every filed total ties with its parts within $1 million (company, periods): "
                                         "pbt: KO 2, MCD 2")
    x.stmts = y.stmts = mcd(pbt_2021=None)                                   # no total filed anywhere: nothing compared
    assert UsFilings._parts_tie([x, y]).detail == "no period files a total with all of its parts"


def test_the_debt_held_warning_names_every_period_ibms_filed_total_is_held():
    """IBM's guard (#470): a date where its current part of long-term debt is above its short-term debt is held and
    named, every such date in the window, not only the latest; other companies: the latest, as before."""
    stb, lt = "ShortTermBorrowings", "LongTermDebtCurrent"
    q = lambda end, accn, filed, v: fact(v, end, accn=accn, filed=filed)
    dates = (("2026-03-31", "0000000007-26-000001", "2026-04-28", 7_000, 7_009),
             ("2026-06-30", "0000000007-26-000002", "2026-07-28", 7_013, 7_009))
    cf = facts({stb: {"USD": [q(e, a, f, s) for e, a, f, s, _ in dates]},
                lt: {"USD": [q(e, a, f, c) for e, a, f, _, c in dates]},
                "NetIncomeLoss": {"USD": [fact(1, e, e[:5] + ("01-01" if e.endswith("03-31") else "04-01"), accn=a, filed=f)
                                          for e, a, f, _, _ in dates]},
                "Assets": {"USD": [q(e, a, f, 9) for e, a, f, _, _ in dates]}})
    tags = E.load_tags()
    x = us_company("IBM")
    x.stmts = E.Statements([(1, cf)], tags, "IBM")
    x.built = [x.stmts.build(fl) for fl in x.stmts.filings.values()]
    got = UsFilings._debt_held(task_module.Fetched(START, tags), [x])
    assert not got.passed and got.detail == (
        f"current borrowings held: IBM 2026-03-31: {stb} (IBM's short-term debt as filed, including current maturities of "
        f"long-term debt) is below its {lt} at this date, which it includes")
    assert "7000" not in got.detail and "7009" not in got.detail
    last = next(r for r in x.built[-1].rows if r.period_kind == "instant")
    assert last.values["borrowings_current"] == 7_013
    y = us_company("KO")                                                     # the latest only, as before
    y.stmts = E.Statements([(1, cf)], tags, "KO")
    y.built = [y.stmts.build(fl) for fl in y.stmts.filings.values()]
    assert UsFilings._debt_held(task_module.Fetched(START, tags), [y]).passed


def test_a_row_that_lacks_a_figure_never_displaces_one_that_has_it():
    """Disney's FY2023 10-K/A (Part III: net income only) would have replaced its 10-K's year, quarter and balance sheet
    rows for US-04's reader, which takes the latest filed row per period (#403's dev runs, 12 Nov 2026; #455)."""
    k, ka, full = "0000000003-23-000001", "0000000003-24-000001", "0000000003-24-000002"
    yr = lambda v, accn, form, filed: fact(v, "2023-09-30", "2022-10-02", accn=accn, form=form, filed=filed)
    nine = lambda v: fact(v, "2023-07-01", "2022-10-02", accn="0000000003-23-000000", filed="2023-08-09")
    cf = facts({
        "Revenues": {"USD": [nine(75), yr(100, k, "10-K", "2023-11-21"), yr(101, full, "10-K/A", "2024-03-01")]},
        "NetIncomeLoss": {"USD": [nine(30), yr(40, k, "10-K", "2023-11-21"), yr(40, ka, "10-K/A", "2024-01-24"),
                                  yr(41, full, "10-K/A", "2024-03-01")]},
        "Assets": {"USD": [fact(500, "2023-09-30", accn=k, form="10-K", filed="2023-11-21"),
                           fact(501, "2023-09-30", accn=full, form="10-K/A", filed="2024-03-01")]},
    }, dei=[{"val": 7, "end": "2024-01-17", "accn": ka, "form": "10-K/A", "filed": "2024-01-24"}])
    s = E.Statements([(1, cf)], E.load_tags())
    built = [s.build(s.filings[a]) for a in (k, ka)]
    assert {r.period_kind for r in built[1].rows} == {"quarter", "year", "instant"}      # as built: it would displace
    E.never_displace(built)
    assert [r.period_kind for r in built[0].rows] == ["quarter", "year", "instant"]
    assert built[1].rows == [] and built[1].dropped == ["quarter", "year", "instant"]
    assert built[1].notes[1] == ("its year row to 2023-09-30 isn't stored: it lacks revenue, which 10-K "
                                 f"{k} files for that period")
    assert next(r for r in built[0].rows if r.period_kind == "quarter").values == {"revenue": 25, "pat_owners": 10}
    # a later amendment with every figure (restated statements) replaces the 10-K's rows, as before
    built = [s.build(s.filings[a]) for a in (full, k, ka)]                # in filing order whatever the list's order
    E.never_displace(built)
    assert [b.dropped for b in built] == [[], [], ["quarter", "year", "instant"]]
    assert next(r for r in built[0].rows if r.period_kind == "year").values == {"revenue": 101, "pat_owners": 41}


@needs_fixtures
def test_single_class_shares_come_from_the_filings_cover_page():
    s = statements("MSFT")
    r = next(r for r in s.build(window(s)[-1]).rows if r.period_kind == "instant")
    assert (r.values["shares_outstanding"], r.tags["shares_outstanding"]) == (
        7425545491, "dei:EntityCommonStockSharesOutstanding (cover page, as of 2026-07-23)")


@needs_fixtures
def test_tag_switches_are_named():
    s = statements("JPM")
    used: dict[str, set[str]] = {}
    for f in window(s):
        for r in s.build(f).rows:
            for k, v in r.tags.items():
                used.setdefault(k, set()).add(v)
    assert s.switched(used) == ["revenue: Revenues primary, also RevenuesNetOfInterestExpense"]


# ---------------------------------------------------------------------------------------------- made-up facts
def fact(val, end, start=None, accn="0000000001-26-000001", form="10-Q", filed="2026-08-01"):
    e = {"val": val, "end": end, "accn": accn, "form": form, "filed": filed}
    if start:
        e["start"] = start
    return e


def facts(us_gaap: dict, dei: list | None = None) -> dict:
    out = {"cik": 1, "entityName": "Test", "facts": {"us-gaap": {t: {"units": u} for t, u in us_gaap.items()}}}
    if dei is not None:
        out["facts"]["dei"] = {E.SHARES_TAG: {"units": {"shares": dei}}}
    return out


def test_money_in_another_currency_is_never_read_and_equity_fills_adjust_for_nci():
    cf = facts({
        "Revenues": {"EUR": [fact(100, "2026-06-30", "2026-04-01")]},
        "NetIncomeLoss": {"USD": [fact(10, "2026-06-30", "2026-04-01"),
                                  fact(9, "2026-06-30", "2026-04-01", accn="0000000001-26-000002"),
                                  fact(8, "2026-06-30", "2026-04-01", accn="0000000001-26-000003")]},
        "StockholdersEquity": {"USD": [fact(50, "2026-06-30"), fact(40, "2026-06-30", accn="0000000001-26-000002"),
                                       fact(30, "2026-06-30", accn="0000000001-26-000003")]},
        "MinorityInterest": {"USD": [fact(5, "2026-06-30"), fact(4, "2025-12-31", accn="0000000001-26-000002")]},
    })
    s = E.Statements([(1, cf)], E.load_tags())
    b = s.build(s.filings["0000000001-26-000001"])
    q_row, bs = b.rows
    assert "revenue" not in q_row.values and q_row.values["pat_owners"] == 10          # EUR is never read
    assert (bs.values["total_equity"], bs.tags["total_equity"]) == (55, "StockholdersEquity+MinorityInterest")
    assert (bs.values["equity_owners"], bs.values["nci"]) == (50, 5)
    other = s.build(s.filings["0000000001-26-000002"]).rows[-1]           # NCI presented, not at its date: no fill
    assert "total_equity" not in other.values and other.values["equity_owners"] == 40
    # a filing that presents no NCI (Salesforce and Cisco, which had some years before: dev run 1, 11 Nov 2026)
    none = s.build(s.filings["0000000001-26-000003"]).rows[-1]
    assert (none.values["total_equity"], none.tags["total_equity"], none.values["equity_owners"]) == (30, "StockholdersEquity", 30)


def test_the_year_end_check_names_what_it_cant_read():
    """Dev run 2 (11 Nov 2026): Disney's FY2023 and P&G's FY2020 year ends had no single total-assets figure."""
    y = lambda v, accn="0000000002-26-000001", form="10-K": fact(v, "2026-06-30", "2025-07-01", accn=accn, form=form)
    i = lambda v, accn="0000000002-26-000001", form="10-K", end="2026-06-30": fact(v, end, accn=accn, form=form)
    cf = facts({
        "NetIncomeLoss": {"USD": [y(5), y(5, "0000000002-26-000002"), y(5, "0000000002-26-000003", "10-K/A"),
                                  y(5, "0000000002-26-000004", "10-K/A")]},
        "Assets": {"USD": [i(100), i(120), i(100, "0000000002-26-000002"), i(90, "0000000002-26-000004", "10-K/A", "2025-06-30")]},
        "Liabilities": {"USD": [i(60), i(60, "0000000002-26-000002")]},
        "StockholdersEquity": {"USD": [i(40), i(30, "0000000002-26-000002")]},
    })
    s = E.Statements([(1, cf)], E.load_tags())
    two = s.build(s.filings["0000000002-26-000001"])           # 100 and 120: only 100 ties with 60 + 40
    assert two.balance[0] is True and "Assets has 2 values at the date in 10-K 0000000002-26-000001: 100, 120" in two.balance[1]
    assert "total_assets" not in two.rows[-1].values                        # the column still holds the field
    assert s.build(s.filings["0000000002-26-000002"]).balance == (
        False, "assets 100 vs liabilities 60 + temporary equity 0 + equity 30")
    assert s.build(s.filings["0000000002-26-000003"]).balance == (
        None, "10-K/A 0000000002-26-000003 presents no balance sheet (no total assets): not checked")
    assert s.build(s.filings["0000000002-26-000004"]).balance == (
        False, "no total-assets figure at the year end (10-K/A 0000000002-26-000004; Assets filed at 2025-06-30)")


def test_two_values_for_one_period_in_one_filing_are_held():
    cf = facts({"NetIncomeLoss": {"USD": [fact(10, "2026-06-30", "2026-04-01"), fact(11, "2026-06-30", "2026-04-01")]},
                "IncomeTaxExpenseBenefit": {"USD": [fact(3, "2026-06-30", "2026-04-01")]}})
    s = E.Statements([(1, cf)], E.load_tags())
    r = s.build(next(iter(s.filings.values()))).rows[0]
    assert r.values == {"tax": 3} and r.tags["pat_owners"].startswith("held: NetIncomeLoss has 2 different values")


def test_an_ifrs_filer_is_refused():
    cf = facts({"NetIncomeLoss": {"USD": [fact(1, "2026-06-30", "2026-04-01")]}})
    cf["facts"]["ifrs-full"] = {"Revenue": {"units": {"USD": []}}}
    with pytest.raises(E.EdgarError, match="IFRS"):
        E.Statements([(1, cf)], E.load_tags())


# ---------------------------------------------------------------------------------------------- cover pages
def cover_page(end: str, classes: dict[str, str], *, scale: str | None = "6", cik: str = "0001652044") -> bytes:
    ctx, figs = [], []
    for i, (member, text) in enumerate(classes.items()):
        seg = ("" if member == "total" else
               '<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
               f"us-gaap:{member}</xbrldi:explicitMember></xbrli:segment>")
        ctx.append(f'<xbrli:context id="c-{i}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}'
                   f"</xbrli:identifier>{seg}</xbrli:entity><xbrli:period><xbrli:instant>{end}</xbrli:instant>"
                   "</xbrli:period></xbrli:context>")
        sc = f' scale="{scale}"' if scale else ""
        figs.append(f'<p>{member}: <ix:nonFraction unitRef="shares" contextRef="c-{i}" decimals="-6"{sc} '
                    f'name="dei:EntityCommonStockSharesOutstanding" format="ixt:num-dot-decimal">{text}</ix:nonFraction></p>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:dei="http://xbrl.sec.gov/dei/2025" '
            'xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">'
            '<head><title>10-Q</title></head><body><div style="display:none"><ix:header><ix:resources>'
            + "".join(ctx) + "</ix:resources></ix:header></div>" + "".join(figs) + "</body></html>").encode()


GOOGL_COVER = {"CommonClassAMember": "5,868", "CommonClassBMember": "835", "CapitalClassCMember": "5,527"}


def test_a_multi_class_cover_page_is_summed_by_its_curated_ratios():
    cov = E.read_cover(cover_page("2026-07-15", GOOGL_COVER))
    assert cov == E.Cover(D(2026, 7, 15), {"CommonClassAMember": Decimal(5868000000), "CommonClassBMember": Decimal(835000000),
                                          "CapitalClassCMember": Decimal(5527000000)})
    total, how = E.cover_total(cov, E.load_tags().share_classes["GOOGL"])
    assert total == 12230000000                                                   # E1's cover-page total
    assert how.startswith("cover page, as of 2026-07-15: CapitalClassCMember 5527000000 × 1 + CommonClassAMember")
    # a class with no curated conversion ratio holds the field, naming it
    v = E.read_cover(cover_page("2026-10-30", {"CommonClassAMember": "1,700", "CommonClassB4Member": "4"}))
    total, how = E.cover_total(v, E.load_tags().share_classes["V"])
    assert total is None and how.startswith("held: no curated conversion ratio for CommonClassB4Member")
    assert E.cover_total(v, None)[0] is None
    assert E.cover_total(E.read_cover(cover_page("2026-10-30", {"total": "7,425,545,491"}, scale=None)), None) == (
        Decimal(7425545491), "cover page, as of 2026-10-30")


VISA_COVER = {"CommonClassAMember": "1,724,473,215", "CommonClassB1Member": "2,180,148", "CommonClassB2Member": "486,669",
              "CommonClassB3Member": "60,589,871", "CommonClassCMember": "9,080,220"}


def test_visas_classes_and_preferred_stock_count_as_converted():
    """#401's question 2 (Anand, 12 Nov 2026; #455): the rates of Visa's FY2026 10-K, Note 15, and its preferred stock as
    converted (6, 1 and 2 million, rounded to millions as the note gives them)."""
    v = E.load_tags().share_classes["V"]
    assert {m: str(r) for m, r in v.ratios.items()} == {"CommonClassAMember": "1", "CommonClassB1Member": "1.5400",
                                                        "CommonClassB2Member": "1.4924", "CommonClassB3Member": "1.4773",
                                                        "CommonClassCMember": "4.0000"}
    assert {m: r.effective for m, r in v.rates.items()} == {"CommonClassB1Member": D(2026, 9, 18), "CommonClassB2Member":
                                                            D(2026, 9, 18), "CommonClassB3Member": D(2026, 9, 18),
                                                            "CommonClassCMember": None}
    assert all("0001403161-26-000134" in r.source for r in v.rates.values()) and v.rates["CommonClassCMember"].as_of == D(2026, 9, 30)
    cover = E.read_cover(cover_page("2026-10-29", VISA_COVER, scale=None, cik="0001403161"))
    total, how = E.cover_total(cover, v)
    # 1,724,473,215 + 2,180,148 × 1.54 + 486,669 × 1.4924 + 60,589,871 × 1.4773 + 9,080,220 × 4 + 9,000,000
    exact = (Decimal(1724473215) + Decimal(2180148) * Decimal("1.54") + Decimal(486669) * Decimal("1.4924")
             + Decimal(60589871) * Decimal("1.4773") + Decimal(9080220) * 4 + 9000000)
    assert total == exact.quantize(Decimal(1)) == 1863387244
    # the note's total at 30 Sep 2026 is 1,867 million, when class A was 1,728 million
    assert round((total - 1724473215 + 1728000000) / Decimal(10) ** 6) == 1867
    assert how.startswith("cover page, as of 2026-10-29: CommonClassAMember 1724473215 × 1 + CommonClassB1Member 2180148 × 1.5400")
    assert "+ 9000000 (series A, B and C preferred stock as converted into class A at 30 Sep 2026: 6, 1 and 2 million, " \
           "rounded to millions as Note 15 states them)" in how
    assert "; rates: CommonClassB1Member 1.5400 in effect from 2026-09-18" in how
    # the B rates fall with each litigation-escrow deposit: a warning once the cover page is 120 days past them
    assert v.stale(D(2027, 1, 16)) == [] and v.stale(D(2027, 1, 17)) == [
        "CommonClassB1Member", "CommonClassB2Member", "CommonClassB3Member"]
    assert E.load_tags().share_classes["GOOGL"].stale(D(2030, 1, 1)) == []


class Rows:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, args=None):
        return self

    def fetchone(self):
        return self.row


def test_the_share_rates_warning_reads_the_stored_cover_pages_date():
    x = task_module.Company(us.load_us_config().companies[0], 1)
    x.c = next(c for c in us.load_us_config().companies if c.ticker == "V")
    f = task_module.Fetched(START, E.load_tags())
    stored = "cover page, as of {}: CommonClassAMember 1 × 1; rates: … (note)"
    assert UsFilings._share_rates(Rows((stored.format("2026-10-29"),)), f, [x]).passed
    late = UsFilings._share_rates(Rows((stored.format("2027-02-01"),)), f, [x])
    assert not late.passed and not late.blocking
    assert late.detail.startswith("V: CommonClassB1Member, CommonClassB2Member, CommonClassB3Member took effect 2026-09-18, "
                                  "more than 120 days before its cover page of 2027-02-01: update them")
    assert UsFilings._share_rates(Rows(None), f, [x]).passed                     # no share count stored yet


def test_the_gaps_warning_names_the_tags_filed_for_an_empty_line():
    """Where to look before a tag joins a chain: tag names only, never a value (#455)."""
    y = lambda t, v: {t: {"USD": [fact(v, "2026-06-30", "2025-07-01", form="10-K"),
                                  fact(v - 1, "2026-03-31", "2025-07-01", accn="0000000001-26-000000", filed="2026-05-01")]}}
    cf = facts(y("Revenues", 7101) | y("NetIncomeLoss", 7102) | y("IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic", 7103)
               | y("IncomeTaxExpenseBenefit", 7104) | y("OperatingIncomeLoss", 7105) | y("InterestExpense", 7106)
               | {"Assets": {"USD": [fact(7777, "2026-06-30", form="10-K")]}})
    s = E.Statements([(1, cf)], E.load_tags())
    x = task_module.Company(next(c for c in us.load_us_config().companies if c.valuation_family == "operating"), 1)
    x.stmts, x.built = s, [s.build(next(iter(s.filings.values())))]
    got = UsFilings._gaps([x])
    assert not got.passed and not got.blocking
    assert f"{x.c.ticker} pbt empty in 2 of its periods (year 2026-06-30; quarter 2026-06-30); filed for them: " \
           "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic" in got.detail
    assert f"{x.c.ticker} cash empty in 1 of its periods (instant 2026-06-30); filed for them: no tag holding Cash" in got.detail
    assert not any(str(v) in got.detail for v in (7101, 7102, 7103, 7104, 7105, 7106, 7777))
    assert " revenue " not in got.detail and " operating_income " not in got.detail          # filed: no gap


def test_a_cover_page_without_a_share_figure_or_with_an_entity_is_refused():
    with pytest.raises(E.EdgarError, match="no cover-page share figure"):
        E.read_cover(cover_page("2026-07-15", {}))
    evil = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            b'<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>&e;</body></html>')
    with pytest.raises(E.EdgarError):
        E.read_cover(evil)


def test_the_task_is_wired_as_module_us_after_the_universe():
    t = config.load()["us.filings"]
    assert t.module == "us" and t.rules == {"weekly": "every 6 days"} and not t.uses_nse
    assert t.run_after == ("us.universe",)
    assert registry()["us.filings"] is UsFilings
    assert site_for(companyfacts_url(320193)).key == "sec"
    assert site_for(document_url(1652044, "0001652044-26-000071", "goog-20260630.htm")).key == "sec"


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5641, "us-filings-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
KEY = "test.us_filings"
NOW = datetime(2026, 11, 12, 1, 0, tzinfo=UTC)
UA = "Finance Terminal test sec-ua@example.invalid"
GOOGL_DOC = "/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm"


FULL_CONFIG = us.load_us_config


def dow_config() -> us.UsConfig:
    full = FULL_CONFIG()
    return us.UsConfig(full.index, full.as_of, [c for c in full.companies if c.ticker in DOW], full.groups)


class Sec:
    """data.sec.gov and www.sec.gov as a mock transport: the saved submissions and company facts of the six Dow names,
    and Alphabet's latest filing as a made inline XBRL cover page."""

    def __init__(self, change=None):
        self.files: dict[str, bytes] = {GOOGL_DOC: cover_page("2026-07-15", GOOGL_COVER)}
        for t in DOW:
            cik = CIK[t]
            self.files[f"/submissions/CIK{cik:010d}.json"] = gzip.decompress(
                (EDGAR / f"submissions_CIK{cik:010d}.json.gz").read_bytes())
            cf = read(cik)
            if change is not None:
                cf = change(t, cf)
            self.files[f"/api/xbrl/companyfacts/CIK{cik:010d}.json"] = json.dumps(cf).encode()
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        body = self.files.get(request.url.path)
        kind = "text/html" if request.url.path.endswith(".htm") else "application/json"
        return httpx.Response(404) if body is None else httpx.Response(200, content=body, headers={"content-type": kind})


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                     values (%s, 'test', 'us filings test', '{weekly}', '6 days', '10 days', false)
                     on conflict (key) do nothing""", (KEY,))
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=PORT)
    srv.start()
    boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1", aws_access_key_id="test",
                 aws_secret_access_key="test").create_bucket(Bucket=BUCKET)
    yield
    srv.stop()


def storage():
    from ft.storage import Storage
    return Storage(ENDPOINT, BUCKET, "test", "test", region="us-east-1")


def q(sql, *a):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        return c.execute(sql, a or None).fetchall()


US_IDS = "(select id from core.entity where kind = 'company' and market = 'US' and slug like 'us-%')"


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for sql in (f"delete from equity.financials where entity_id in {US_IDS}",
                    f"delete from equity.filing where entity_id in {US_IDS}",
                    f"delete from core.document where entity_id in {US_IDS}",
                    f"delete from core.entity_identifier where entity_id in {US_IDS}",
                    f"delete from core.entity where id in {US_IDS}"):
            c.execute(sql)


def master():
    """The six Dow names in the master, as us.universe leaves them: unpublished, with their CIKs."""
    with psycopg.connect(ADMIN, autocommit=True) as c:
        sec = c.execute("select id from core.source where key = 'sec'").fetchone()[0]
        for x in dow_config().companies:
            eid = c.execute("""insert into core.entity (kind, slug, name, short_name, market, currency, is_published)
                               values ('company', %s, %s, %s, 'US', 'USD', false) returning id""",
                            (x.slug, x.name, x.short_name)).fetchone()[0]
            c.execute("""insert into core.entity_identifier (entity_id, scheme, value, valid, source_id)
                         values (%s, 'cik', %s, daterange(null, null, '[)'), %s)""", (eid, str(x.cik), sec))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    master()
    yield
    wipe()


_runs = itertools.count(1)


def go(sec, monkeypatch, ua=UA, version=None):
    monkeypatch.setenv("SEC_USER_AGENT", ua)
    monkeypatch.setattr(task_module.us, "load_us_config", dow_config)

    class T(UsFilings):
        key = KEY
    if version is not None:
        T.version = version
    t = T()
    t.workflows = ("weekly",)
    http = HttpClient(transport=httpx.MockTransport(sec), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "weekly", [t], only=KEY, force=True, http=http, storage=storage, clock=lambda: NOW)
    tr = q("select status, rows_written, checks, error from ops.task_run where task_key = %s order by id desc limit 1", KEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: (c["passed"], c["blocking"]) for c in tr[2] or []}


def detail(tr, name):
    return next(c["detail"] for c in tr[2] if c["name"] == name)


def counts():
    return q(f"""select (select count(*) from equity.filing where entity_id in {US_IDS}),
                        (select count(*) from equity.financials where entity_id in {US_IDS}),
                        (select count(*) from core.document where entity_id in {US_IDS}),
                        (select count(*) from core.document where entity_id in {US_IDS} and is_published),
                        (select count(*) from core.entity where id in {US_IDS} and is_published)""")[0]


def snapshot():
    return q(f"""select d.external_key, fn.period_kind, fn.period_end, fn.period_start, fn.revenue, fn.pat_owners,
                        fn.cfo, fn.total_equity, fn.shares_outstanding, fn.tags
                   from equity.financials fn join core.document d on d.id = fn.document_id
                  where fn.entity_id in {US_IDS} order by 1, 2, 3""")


FILINGS = {"AAPL": 30, "MSFT": 30, "NVDA": 31, "WMT": 31, "JPM": 30, "GOOGL": 30}


@pytest.mark.db
@needs_fixtures
def test_the_first_load_registers_every_filing_since_2019_unpublished(s3, monkeypatch):
    sec = Sec()
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr) == {"company_facts": (True, True), "period_end": (True, True), "balance_sheet": (True, True),
                          "coverage": (True, True), "shares": (True, True), "shares_held": (True, False),
                          "tag_switch": (False, False), "debt_held": (True, False), "share_rates": (True, False),
                          "parts_tie": (True, False), "kept_fuller": (True, False), "gaps": (False, False),
                          "loaded": (True, False)}
    # every E1 name's pre-tax income ties with its domestic and foreign parts wherever it files all three (#470)
    assert detail(tr, "parts_tie") == ("every filed total ties with its parts within $1 million (company, periods): "
                                       "pbt: GOOGL 9, JPM 9, MSFT 10, NVDA 10, WMT 10")
    assert detail(tr, "gaps").startswith("AAPL finance_cost empty in 10 of its periods")       # tag names only
    assert q("""select fn.borrowings_current, fn.tags->>'borrowings_current' from equity.financials fn
                  join core.document d on d.id = fn.document_id
                 where d.external_key = '0000320193-25-000079' and fn.period_kind = 'instant'""") == [
        (20329000000, "LongTermDebtCurrent+CommercialPaper (the sum of the filed captions; no total is filed)")]
    assert "WMT" in detail(tr, "balance_sheet") and "no total-liabilities line" in detail(tr, "balance_sheet")
    n_filings = sum(FILINGS.values())
    assert counts()[0] == n_filings and counts()[2:] == (n_filings, 0, 0)     # nothing published
    assert detail(tr, "loaded").startswith(f"{n_filings} filings registered, 0 rebuilt")
    by = dict(q(f"""select e.slug, count(*) from equity.filing fl join core.entity e on e.id = fl.entity_id
                     where fl.entity_id in {US_IDS} group by 1"""))
    assert by == {f"us-{t.lower()}": n for t, n in FILINGS.items()}
    # Apple's FY2025 10-K: the filing, its document and its rows
    assert q("""select d.doc_type, s.key, d.source_url, d.title, d.published_at, f.source_url
                  from core.document d join core.source s on s.id = d.source_id join core.raw_file f on f.id = d.raw_file_id
                 where d.external_key = '0000320193-25-000079'""") == [(
        "results_xbrl", "sec", "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm",
        "Apple Inc. 10-K, period ended 2025-09-27", datetime(2025, 10, 31, 14, 1, 26, tzinfo=UTC),
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")]
    assert q("""select fl.exchange, fl.format, fl.basis, fl.period_end, fl.fy_start, fl.fy_end, fl.audited, fl.parse_status,
                       fl.parser_version
                  from equity.filing fl join core.document d on d.id = fl.document_id
                 where d.external_key = '0000320193-25-000079'""") == [
        ("SEC", "us_gaap", "consolidated", D(2025, 9, 27), D(2024, 9, 29), D(2025, 9, 27), True, "parsed",
         str(E.PARSER_VERSION))]
    assert q("""select fn.period_kind, fn.period_start, fn.currency, fn.revenue, fn.operating_income, fn.pat_owners,
                       fn.tags->>'revenue'
                  from equity.financials fn join core.document d on d.id = fn.document_id
                 where d.external_key = '0000320193-25-000079' and fn.period_kind in ('quarter', 'year') order by 1""") == [
        ("quarter", D(2025, 6, 29), "USD", 102466000000, 32427000000, 27466000000,
         "derived: RevenueFromContractWithCustomerExcludingAssessedTax FY − 9M filed 2025-08-01"),
        ("year", D(2024, 9, 29), "USD", 416161000000, 133050000000, 112010000000,
         "RevenueFromContractWithCustomerExcludingAssessedTax")]
    # Alphabet's latest filing: its cover page's three classes; its earlier filings hold the field with the reason
    googl = q("""select fn.shares_outstanding, fn.tags->>'shares_outstanding' from equity.financials fn
                   join core.document d on d.id = fn.document_id
                  where d.external_key = '0001652044-26-000071' and fn.period_kind = 'instant'""")
    assert googl[0][0] == 12230000000 and "CapitalClassCMember 5527000000 × 1" in googl[0][1]
    assert q("""select count(*) from equity.financials fn join core.entity e on e.id = fn.entity_id
                  where e.slug = 'us-googl' and fn.period_kind = 'instant' and fn.shares_outstanding is null
                    and fn.tags->>'shares_outstanding' like 'held: a multi-class%%'""")[0][0] == FILINGS["GOOGL"] - 1
    # every SEC request declared the secret's User-Agent; each reply is a sec raw file
    assert {r.headers["user-agent"] for r in sec.seen} == {UA}
    assert sorted({r.url.host for r in sec.seen}) == ["data.sec.gov", "www.sec.gov"]
    assert len(sec.seen) == 2 * len(DOW) + 1
    assert q("""select count(*) from core.raw_file f join core.source s on s.id = f.source_id
                 where s.key = 'sec' and f.source_url like 'https://data.sec.gov/api/xbrl/companyfacts/%'""")[0][0] >= 6


@pytest.mark.db
@needs_fixtures
def test_a_second_identical_load_writes_nothing(s3, monkeypatch):
    sec = Sec()
    go(sec, monkeypatch)
    before, rows = counts(), snapshot()
    sec.seen.clear()
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert counts() == before and snapshot() == rows
    assert len(sec.seen) == 2 * len(DOW)                         # Alphabet's latest cover page isn't read again


@pytest.mark.db
@needs_fixtures
def test_a_newer_parser_version_rebuilds_the_rows_and_keeps_the_cover_pages_shares(s3, monkeypatch):
    sec = Sec()
    go(sec, monkeypatch)
    rows = snapshot()
    code, tr = go(sec, monkeypatch, version=E.PARSER_VERSION + 1)
    assert code == 0 and tr[0] == "success", tr
    assert detail(tr, "loaded").startswith(f"0 filings registered, {sum(FILINGS.values())} rebuilt")
    assert snapshot() == rows
    assert q(f"select distinct parser_version from equity.filing where entity_id in {US_IDS}") == [(str(E.PARSER_VERSION + 1),)]


@pytest.mark.db
@needs_fixtures
def test_a_year_end_that_doesnt_tie_fails_the_load_and_writes_nothing(s3, monkeypatch):
    def change(t, cf):
        if t != "AAPL":
            return cf
        cf = copy.deepcopy(cf)
        for e in cf["facts"]["us-gaap"]["Assets"]["units"]["USD"]:
            if e["accn"] == "0000320193-25-000079" and e["end"] == "2025-09-27":
                e["val"] = int(e["val"] * 1.02)
        return cf
    _code, tr = go(Sec(change), monkeypatch)
    assert tr[0] == "checks_failed" and checks(tr)["balance_sheet"] == (False, True), tr
    assert "AAPL 2025-09-27: assets" in detail(tr, "balance_sheet")
    assert counts()[:3] == (0, 0, 0)


@pytest.mark.db
@needs_fixtures
def test_a_company_missing_from_the_master_fails_the_load(s3, monkeypatch):
    q("delete from core.entity_identifier where value = '19617' and scheme = 'cik' returning entity_id")
    _code, tr = go(Sec(), monkeypatch)
    assert tr[0] == "checks_failed" and checks(tr)["company_facts"] == (False, True), tr
    assert "JPM (not in the company master" in detail(tr, "company_facts") and counts()[0] == 0


@pytest.mark.db
def test_the_due_rule_waits_for_migration_0039_and_for_the_first_recorded_run(s3):
    t = UsFilings()
    t.rules = {"weekly": "every 6 days"}
    with psycopg.connect(JOB, autocommit=True) as c:
        d = t.due(c, "weekly", NOW)
        assert not d.due and "first recorded run" in d.reason
        assert t.due(c, "refresh", NOW).due
    with psycopg.connect(ADMIN) as c:
        c.execute("update core.source set key = 'secgone' where key = 'sec'")
        d = t.due(c, "refresh", NOW)
        c.rollback()
    assert not d.due and "0039" in d.reason


APPLE_10KA = "0000320193-26-000999"     # a made Part III amendment of Apple's FY2025 10-K: net income only


def with_part_iii_amendment(t, cf):
    """Apple's FY2025 10-K/A, as Disney's FY2023 one: it tags only the year's net income (#455)."""
    if t != "AAPL":
        return cf
    cf = copy.deepcopy(cf)
    cf["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"].append(
        {"start": "2024-09-29", "end": "2025-09-27", "val": 112010000000, "accn": APPLE_10KA, "fy": 2025, "fp": "FY",
         "form": "10-K/A", "filed": "2026-01-20"})
    return cf


@pytest.mark.db
@needs_fixtures
def test_a_part_iii_amendment_is_registered_and_never_displaces_the_10ks_rows(s3, monkeypatch):
    code, tr = go(Sec(with_part_iii_amendment), monkeypatch)
    assert code == 0 and tr[0] == "success", tr
    assert checks(tr)["kept_fuller"] == (False, False)
    assert detail(tr, "kept_fuller").endswith(f"AAPL 10-K/A {APPLE_10KA} (quarter, year)")
    (status, note, n), = q("""select fl.parse_status, fl.parse_note,
                                     (select count(*) from equity.financials where document_id = fl.document_id)
                                from equity.filing fl join core.document d on d.id = fl.document_id
                               where d.external_key = %s""", APPLE_10KA)
    assert (status, n) == ("parsed", 0)
    assert note.startswith(f"v{E.PARSER_VERSION}: 10-K/A filed 2026-01-20 (its acceptance time isn't in SEC's recent "
                           "filings list); 0 rows from company facts (CIK 320193); its quarter row to 2025-09-27 isn't "
                           "stored: it lacks revenue, operating_income, ")
    assert "; its year row to 2025-09-27 isn't stored: it lacks revenue, " in note
    assert note.endswith("which 10-K 0000320193-25-000079 files for that period")
    # Apple's 10-K's rows stay the latest filed for the year
    assert q("""select count(*) from equity.financials fn join core.document d on d.id = fn.document_id
                 where d.external_key = '0000320193-25-000079' and fn.revenue is not null""") == [(2,)]
    assert counts()[0] == sum(FILINGS.values()) + 1


@pytest.mark.db
@needs_fixtures
def test_a_stored_row_that_displaced_a_fuller_one_is_removed_by_the_rebuild(s3, monkeypatch):
    """Dev's Disney: v1 stored its FY2023 10-K/A's one-figure rows; the v2 rebuild leaves the filing with none."""
    sec = Sec(with_part_iii_amendment)
    go(sec, monkeypatch)
    doc = q("select id from core.document where external_key = %s", APPLE_10KA)[0][0]
    q("update equity.filing set parser_version = '1' where document_id = %s returning document_id", doc)
    q("""insert into equity.financials (document_id, entity_id, basis, period_start, period_end, period_kind, currency,
                                        pat_owners, tags)
         select %s, entity_id, 'consolidated', '2024-09-29', '2025-09-27', 'year', 'USD', 112010000000,
                '{"pat_owners": "NetIncomeLoss"}' from core.document where id = %s returning document_id""", doc, doc)
    code, tr = go(sec, monkeypatch)
    assert code == 0 and tr[0] == "success", tr
    assert detail(tr, "loaded").startswith("0 filings registered, 1 rebuilt")
    assert q("select count(*) from equity.financials where document_id = %s", doc) == [(0,)]
    assert q("select parse_status, parser_version from equity.filing where document_id = %s", doc) == [
        ("parsed", str(E.PARSER_VERSION))]


def test_the_gaps_warning_reads_the_first_years_opening_balance_sheet_too():
    """A valuation's first year opens with the balance sheet of the year end before it (#403: 3M's cash)."""
    acc = lambda y: f"0000000004-{y % 100:02d}-000001"
    yr = lambda y, v: fact(v, f"{y}-12-31", f"{y}-01-01", accn=acc(y), form="10-K", filed=f"{y + 1}-02-10")
    at = lambda y, v: fact(v, f"{y}-12-31", accn=acc(y), form="10-K", filed=f"{y + 1}-02-10")
    years = (2022, 2023, 2024, 2025)
    cf = facts({"Revenues": {"USD": [yr(y, 9001) for y in years]}, "NetIncomeLoss": {"USD": [yr(y, 9002) for y in years]},
                "Assets": {"USD": [at(y, 9003) for y in years]},
                "CashAndCashEquivalentsAtCarryingValue": {"USD": [at(y, 9004) for y in years[1:]]},
                "CashAndDueFromBanks": {"USD": [at(2022, 9005)]}})
    s = E.Statements([(1, cf)], E.load_tags())
    x = task_module.Company(next(c for c in us.load_us_config().companies if c.valuation_family == "operating"), 1)
    x.stmts, x.built = s, [s.build(s.filings[acc(y)]) for y in years]
    got = UsFilings._gaps([x]).detail
    assert f"{x.c.ticker} cash empty in 1 of its periods (instant 2022-12-31); filed for them: CashAndDueFromBanks" in got
