"""equity.statements (job 2A-09): the XBRL reader and the statements mapping on the saved filings (every format and both
NSE eras), the per-filing checks, X1's reference values, and the task through the runner.

The files are in data/fixtures/equity (the private fixtures repo; the tests skip without it, and CI checks it out):
`xbrl/<SYMBOL>/` (1,375 instances, 26 companies), `audit/reference_values.csv` (X1's FY2026 figures) and
`universe/EQUITY_L_*.csv` (insurers' face value). No NSE data is kept in this repo (CLAUDE.md §4)."""
import csv
import gzip
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
import psycopg
import pytest
from conftest import dsn
from lxml import etree
from moto.server import ThreadedMotoServer

from ft.equity import filings as fl
from ft.equity import statements as st
from ft.equity import universe as u
from ft.equity import xbrl
from ft.jobs import config
from ft.jobs.fetch import HttpClient
from ft.jobs.runner import run
from ft.jobs.tasks import equity_statements as es
from ft.jobs.tasks import registry
from ft.jobs.tasks.equity_filings import EquityFilings
from ft.jobs.tasks.equity_statements import EquityStatements

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "data" / "fixtures" / "equity"
XBRL, AUDIT = FIX / "xbrl", FIX / "audit"
EQUITY_L = FIX / "universe" / "EQUITY_L_20261019.csv"
needs_fixtures = pytest.mark.skipif(not XBRL.exists(), reason="the saved files aren't checked out (data/fixtures)")
D = date


def raw(symbol: str, name: str) -> bytes:
    with gzip.open(XBRL / symbol / f"{name}.gz") as f:
        return f.read()


def face_values() -> dict[str, Decimal]:
    """EQUITY_L's face value by NSE symbol (insurers file none)."""
    return {r.symbol: Decimal(r.face_value) for r in u.parse_equity_l(EQUITY_L.read_bytes()).values()}


def filing_of(symbol: str, name: str, data: bytes | None = None, **override) -> st.Filing:
    """The filing as equity.filings registers it: its format from the file, its basis and period from the listing
    (here: the file's own header, which the listing matches for every saved filing), fy_start from the March year."""
    data = data if data is not None else raw(symbol, name)
    inst = xbrl.parse(data)
    pe = xbrl.to_date(inst.header["period_end"])
    f = {"file_name": name, "basis": inst.basis, "format": fl.format_of(name, fl.read_instance(data)),
         "period_end": pe, "fy_start": fl.fy_of(pe, 3)[0]}
    return st.Filing(**{**f, **override})


def parse(symbol: str, name: str, data: bytes | None = None, *, face: Decimal | None = None, **override) -> st.Parsed:
    data = data if data is not None else raw(symbol, name)
    return st.parse_filing(data, filing_of(symbol, name, data, **override),
                           face_value=face if face is not None else face_values().get(symbol))


def by_kind(p: st.Parsed) -> dict[str, st.Row]:
    return {r.period_kind: r for r in p.rows}


def edited(data: bytes, element: str, context: str, *, text: str | None = None, add: int | None = None,
           unit: str | None = None) -> bytes:
    """The instance with one fact changed (the single `element` in `context`)."""
    root = etree.fromstring(data, xbrl.PARSER)
    hits = [el for el in root if isinstance(el.tag, str) and etree.QName(el).localname == element
            and el.get("contextRef") == context]
    assert len(hits) == 1, (element, context, len(hits))
    if add is not None:
        text = str(Decimal(hits[0].text) + add)
    if text is not None:
        hits[0].text = text
    if unit is not None:
        hits[0].set("unitRef", unit)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


RELIANCE_FY24 = "INDAS_104634_1106038_22042024074704.xml"                     # Q4 FY24, consolidated, pre-2025 file
RELIANCE_FY26 = "INTEGRATED_FILING_INDAS_1658776_24042026105714_WEB.xml"      # Q4 FY26, consolidated, Integrated Filing
HDFCBANK_DEC18 = "BANKING_41595_75813_21012019115230_WEB.xml"                 # Q3 FY19, standalone, no main contexts


# ---------------------------------------------------------------------------------------------- the reader
@needs_fixtures
def test_a_pre_2025_file_dates_the_year_to_date_like_the_quarter_and_the_reader_re_dates_it():
    inst = xbrl.parse(raw("RELIANCE", RELIANCE_FY24))
    four = inst.contexts["FourD"]
    assert (four.start, four.end) == (D(2024, 1, 1), D(2024, 3, 31))           # as filed: the quarter's dates
    d = xbrl.date_columns(inst, D(2024, 3, 31), D(2023, 4, 1))
    assert (four.start, four.end) == (D(2023, 4, 1), D(2024, 3, 31))
    assert d.changed == {"Four": 15} and d.synthesized == []
    assert d.quarter == (D(2024, 1, 1), D(2024, 3, 31)) and d.year_to_date == (D(2023, 4, 1), D(2024, 3, 31))
    one = inst.contexts["OneD"]
    assert (one.start, one.end) == (D(2024, 1, 1), D(2024, 3, 31))           # the quarter column is left as filed


@needs_fixtures
def test_an_integrated_filing_file_is_dated_as_filed():
    inst = xbrl.parse(raw("RELIANCE", RELIANCE_FY26))
    d = xbrl.date_columns(inst, D(2026, 3, 31), D(2025, 4, 1))
    assert d.changed == {} and d.synthesized == []
    assert (inst.contexts["FourD"].start, inst.contexts["FourD"].end) == (D(2025, 4, 1), D(2026, 3, 31))
    assert inst.basis == "consolidated" and inst.header["rounding"] == "Crores"


@needs_fixtures
def test_old_files_reference_their_main_contexts_without_defining_them_and_the_reader_dates_them_by_column():
    """Files to about early 2023 point facts at OneD, FourD and OneI but define only the dimensioned contexts (650 of
    the saved files); the old bank files also lack the reporting period's start, so the quarter comes from the period
    end."""
    inst = xbrl.parse(raw("HDFCBANK", HDFCBANK_DEC18))
    assert set(inst.undefined) == {"OneD", "FourD", "OneI"} and "period_start" not in inst.header
    d = xbrl.date_columns(inst, D(2018, 12, 31), D(2018, 4, 1))
    assert d.synthesized == ["FourD", "OneD", "OneI"] and inst.undefined == {}
    c = inst.contexts
    assert (c["OneD"].start, c["OneD"].end) == (D(2018, 10, 1), D(2018, 12, 31))
    assert (c["FourD"].start, c["FourD"].end) == (D(2018, 4, 1), D(2018, 12, 31))
    assert c["OneI"].instant == D(2018, 12, 31)


def test_the_reader_refuses_what_isnt_an_instance_and_keeps_values_as_filed():
    with pytest.raises(xbrl.XbrlError, match="not XML"):
        xbrl.parse(b"<html><body>Access Denied")
    with pytest.raises(xbrl.XbrlError, match="not an XBRL instance"):
        xbrl.parse(b"<html xmlns='http://www.w3.org/1999/xhtml'/>")
    with pytest.raises(xbrl.XbrlError, match="no facts"):
        xbrl.parse(b"<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance'/>")
    inst = xbrl.parse(b"<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance' xmlns:a='urn:a'>"
                      b"<a:Revenue contextRef='OneD' unitRef='INR' decimals='-7'>10756750000000.10</a:Revenue>"
                      b"<a:Note contextRef='OneD'>text</a:Note></xbrli:xbrl>")
    assert inst.facts[0].value == Decimal("10756750000000.10") and inst.facts[1].value is None
    assert xbrl.quarter_of(D(2026, 3, 31)) == (D(2026, 1, 1), D(2026, 3, 31))
    assert xbrl.quarter_of(D(2026, 1, 31)) == (D(2025, 11, 1), D(2026, 1, 31))
    assert [xbrl.period_kind(D(2025, 4, 1), e) for e in (D(2025, 6, 30), D(2025, 9, 30), D(2025, 12, 31),
                                                          D(2026, 3, 31))] == ["quarter", "half_year", "nine_months",
                                                                               "year"]
    assert xbrl.period_kind(D(2025, 4, 1), D(2025, 5, 31)) is None


# ---------------------------------------------------------------------------------------------- the mapping
@needs_fixtures
def test_a_pre_2025_q4_filing_gives_its_quarter_its_full_year_and_its_balance_sheet():
    p = by_kind(parse("RELIANCE", RELIANCE_FY24))
    assert list(p) == ["quarter", "year", "instant"]
    q, y, i = p["quarter"], p["year"], p["instant"]
    assert (q.period_start, y.period_start, i.period_start) == (D(2024, 1, 1), D(2023, 4, 1), None)
    assert q.period_end == y.period_end == i.period_end == D(2024, 3, 31)
    assert q.values["revenue"] == Decimal(2407150000000)
    assert y.values["revenue"] == Decimal(9144720000000)                   # ₹9,14,472 cr: FY24, not the quarter
    assert (y.values["pat"], y.values["pat_owners"]) == (Decimal(790200000000), Decimal(696210000000))
    assert y.values["cfo"] == Decimal(1587880000000) and "cfo" not in q.values
    assert y.values["shares_outstanding"] == Decimal(6766000000)           # before the Oct 2024 1:1 bonus
    assert i.values["total_assets"] == Decimal(17559860000000) and i.tags["total_assets"] == "Assets"
    assert y.tags["revenue"] == "RevenueFromOperations" and set(y.tags) == set(y.values)


@needs_fixtures
def test_an_integrated_filing_q4_gives_the_same_three_rows():
    p = parse("RELIANCE", RELIANCE_FY26)
    assert [r.period_kind for r in p.rows] == ["quarter", "year", "instant"]
    assert "columns dated as filed" in p.note
    y = by_kind(p)["year"]
    # PAT = PBT − tax + the share of associates, which the format puts below tax (₹1,23,162 − ₹27,552 + ₹144 cr)
    assert (y.values["pbt"], y.values["tax"], y.values["share_of_associates"], y.values["pat"]) == (
        Decimal(1231620000000), Decimal(275520000000), Decimal(1440000000), Decimal(957540000000))


@needs_fixtures
def test_a_bank_s_ratios_are_read_from_the_standalone_filing_only():
    sa = by_kind(parse("SBIN", "INTEGRATED_FILING_BANKING_1709644_07082026025925_WEB.xml"))["quarter"]
    assert {k: sa.values[k] for k in ("roa_filed", "gnpa_ratio", "nnpa_ratio", "cet1_ratio", "at1_ratio")} == {
        "roa_filed": Decimal("0.0111"), "gnpa_ratio": Decimal("0.0147"), "nnpa_ratio": Decimal("0.0038"),
        "cet1_ratio": Decimal("0.1289"), "at1_ratio": Decimal("0.0101")}       # SBI files ROA annualised (Brief §6.3)
    assert sa.values["revenue"] == sa.values["total_income"] == Decimal(1438191500000) and sa.tags["revenue"] == "Income"
    cons = by_kind(parse("SBIN", "INTEGRATED_FILING_BANKING_1709692_07082026034917_WEB.xml"))["quarter"]
    assert not {"roa_filed", "gnpa_ratio", "nnpa_ratio", "cet1_ratio", "at1_ratio"} & cons.values.keys()
    hdfc = by_kind(parse("HDFCBANK", "INTEGRATED_FILING_BANKING_1740912_17102026054806_WEB.xml"))
    assert (hdfc["quarter"].values["roa_filed"], hdfc["half_year"].values["roa_filed"]) == (Decimal("0.0045"),
                                                                                          Decimal("0.0091"))
    assert hdfc["quarter"].values["at1_ratio"] == 0                          # no AT1 capital: a true 0 is kept
    inst = hdfc["instant"]
    assert inst.values["total_equity"] == Decimal(5791464600000) and inst.tags["total_equity"] == (
        "Capital + ReservesAndSurplus")
    assert inst.values["total_assets"] == inst.values["segment_assets_total"] == Decimal(45777597700000)
    assert inst.values["deposits"] == Decimal(33274489300000)


@needs_fixtures
def test_an_old_bank_file_dates_its_columns_from_the_header_and_keeps_its_own_face_value():
    """HDFC Bank's Dec 2018 file: no main contexts, no reporting-period start; face value ₹2 as filed (it split to ₹1
    in Sep 2019, so today's EQUITY_L value would double the shares); the net NPA ratio filed without a unit."""
    p = parse("HDFCBANK", HDFCBANK_DEC18)
    assert "dated from the header FourD, OneD, OneI" in p.note
    r = by_kind(p)
    assert list(r) == ["quarter", "nine_months", "instant"]
    q = r["quarter"]
    assert (q.values["face_value"], q.tags["face_value"]) == (Decimal("2.00"), "FaceValueOfEquityShareCapital")
    assert q.values["shares_outstanding"] == Decimal(2719500000)
    assert q.values["nnpa_ratio"] == Decimal("0.0042") and q.values["gnpa_ratio"] == Decimal("0.0138")
    assert (q.values["roa_filed"], r["nine_months"].values["roa_filed"]) == (Decimal("0.0049"), Decimal("0.0139"))
    assert r["instant"].values["segment_assets_total"] == Decimal(11685563100000)
    assert "filed in the quarter's duration context" in r["instant"].tags["segment_assets_total"]


@needs_fixtures
def test_an_old_bank_file_with_assets_filed_without_a_unit_takes_capital_and_liabilities():
    i = by_kind(parse("HDFCBANK", "BANKING_104623_1105106_21042024122701.xml"))["instant"]
    assert i.values["total_assets"] == Decimal(36176230600000)
    assert i.tags["total_assets"].startswith("CapitalAndLiabilities (Assets is filed without a unit")


@needs_fixtures
def test_an_nbfc_files_in_the_ind_as_layout_with_its_interest_and_deposits():
    name = "INTEGRATED_FILING_NBFC_INDAS_1742040_19102026075918_WEB.xml"
    assert filing_of("BAJFINANCE", name).format == "nbfc"
    r = by_kind(parse("BAJFINANCE", name))
    assert r["quarter"].values["revenue"] == Decimal(245463700000)
    assert r["quarter"].values["interest_earned"] == Decimal(219363900000)
    assert r["instant"].values["deposits"] == Decimal(697537000000)
    assert r["instant"].values["total_equity"] == Decimal(1263652600000)


@needs_fixtures
def test_a_life_insurer_s_gross_assets_equity_tax_and_face_value():
    r = by_kind(parse("SBILIFE", "INTEGRATED_FILING_LI_1657299_22042026060213_WEB.xml"))
    i, y = r["instant"], r["year"]
    assert i.values["assets_net"] + i.values["current_liabilities_provisions"] == i.values["total_assets"] == Decimal(
        5000459215000)                                                   # the gross figure (finding 16)
    assert i.values["total_equity"] == Decimal(190861446000) and i.tags["total_equity"] == "ShareCapital + ReservesAndSurplus"
    # the shareholders' account's tax sits between its PBT and PAT (ProvisionForTax is the policyholders')
    assert (y.values["pbt"], y.values["tax"], y.values["pat"]) == (Decimal(25373995000), Decimal(670997000),
                                                                   Decimal(24702998000))
    assert y.tags["tax"] == "ProvisionsForTaxes"
    assert y.values["solvency_ratio"] == Decimal("1.900") and "flagged" in y.tags["solvency_ratio"]
    assert y.values["face_value"] == 10 and y.tags["face_value"] == "EQUITY_L FACE VALUE (not in the filing)"
    assert y.values["shares_outstanding"] == Decimal(1003092100)
    assert y.values["cfo"] == Decimal(345226720000) and not {"capex", "dividends_paid"} & y.values.keys()
    assert "revenue" not in y.values                                       # X1's reference has none for an insurer
    with pytest.raises(st.StatementError, match="shares"):                 # an insurer without EQUITY_L's face value
        st.parse_filing(raw("SBILIFE", "INTEGRATED_FILING_LI_1657299_22042026060213_WEB.xml"),
                        filing_of("SBILIFE", "INTEGRATED_FILING_LI_1657299_22042026060213_WEB.xml"), face_value=None)


@needs_fixtures
def test_a_general_insurer_s_shares_are_not_the_promoters_number_of_shares():
    r = by_kind(parse("ICICIGI", "INTEGRATED_FILING_GI_1740432_16102026073544_WEB.xml"))
    q, i = r["quarter"], r["instant"]
    assert q.values["shares_outstanding"] == Decimal(499610000)          # paid-up ÷ ₹10, not NumberOfShares (244.1 mn)
    assert q.values["pat"] == Decimal(9464000000) and q.values["gross_premium"] == Decimal(77020900000)
    assert q.values["total_income"] == Decimal(82523500000) and q.tags["total_income"].endswith("(derived)")
    assert i.values["total_assets"] == Decimal(842356100000) == Decimal(208915200000) + Decimal(633440900000)
    assert q.values["solvency_ratio"] == Decimal("2.75")


@needs_fixtures
def test_a_cash_flow_filed_in_the_quarter_column_is_the_year_s():
    """Files of Sep 2020 to Mar 2021 put the year's cash flow in the quarter column (TCS FY21 standalone: CFO
    ₹33,822 cr); results carry no quarterly cash flow, so it's the year to date's and never the quarter's."""
    r = by_kind(parse("TCS", "INDAS_69325_430703_12042021110202_WEB.xml"))
    assert r["year"].values["cfo"] == Decimal(338220000000) and "filed in the quarter column" in r["year"].tags["cfo"]
    assert not {"cfo", "cfi", "cff", "capex"} & r["quarter"].values.keys()


@needs_fixtures
def test_the_pre_ind_as_layout_files_pat_after_minority_interest():
    r = by_kind(parse("LT", "INTEGRATED_FILING_INDAS_1434675_09052025014028_WEB.xml"))
    y, i = r["year"], r["instant"]
    assert (y.values["pat"], y.values["pat_owners"], y.values["pat_nci"]) == (
        Decimal(176733300000), Decimal(150371100000), Decimal(26362200000))
    assert y.values["pat"] == y.values["pat_owners"] + y.values["pat_nci"] and "(derived" in y.tags["pat"]
    assert (i.values["equity_owners"], i.values["nci"], i.values["total_equity"]) == (
        Decimal(976556000000), Decimal(177480800000), Decimal(1154036800000))


@needs_fixtures
def test_a_quarter_column_filed_as_zeros_is_not_stored():
    """Hindustan Unilever's Q4 FY19 consolidated file: the year only (consolidated results were then annual), the
    quarter column all zeros; and the quarter column's paid-up capital of 0 gives way to the year's."""
    p = parse("HINDUNILVR", "INDAS_43797_100479_03052019063834_WEB.xml")
    assert [r.period_kind for r in p.rows] == ["year"] and "the quarter column is filed as zeros" in p.note
    y = p.rows[0]
    assert y.values["revenue"] == Decimal(393100000000) and y.values["shares_outstanding"] == Decimal(2160000000)


# ---------------------------------------------------------------------------------------------- the checks
@needs_fixtures
@pytest.mark.parametrize("element, context, add, check", [
    ("ProfitLossForPeriod", "FourD", 10**10, "pat: year"),
    ("ProfitLossForPeriod", "OneD", 10**10, "pat: quarter"),
    ("Income", "FourD", 10**11, "total_income: year"),          # ₹10,000 cr: beyond 0.1% of ₹11,04,637 cr
    ("Assets", "OneI", 10**11, "balance_sheet"),
])
def test_a_filing_whose_figures_dont_tie_fails(element, context, add, check):
    data = edited(raw("RELIANCE", RELIANCE_FY26), element, context, add=add)
    with pytest.raises(st.StatementError) as e:
        parse("RELIANCE", RELIANCE_FY26, data)
    assert str(e.value).startswith(check), e.value


@needs_fixtures
def test_a_tie_within_its_tolerance_passes():
    data = edited(raw("RELIANCE", RELIANCE_FY26), "ProfitLossForPeriod", "FourD", add=90_000)    # under ₹1 lakh
    assert parse("RELIANCE", RELIANCE_FY26, data).rows


@needs_fixtures
def test_a_unit_basis_or_period_that_doesnt_match_fails():
    data = edited(raw("RELIANCE", RELIANCE_FY26), "RevenueFromOperations", "OneD", unit="USD")
    with pytest.raises(st.StatementError, match="^unit: RevenueFromOperations"):
        parse("RELIANCE", RELIANCE_FY26, data)
    with pytest.raises(st.StatementError, match="^basis: the file is consolidated, the listing says standalone"):
        parse("RELIANCE", RELIANCE_FY26, basis="standalone")
    with pytest.raises(st.StatementError, match="^period: the file's period ends 2026-03-31"):
        parse("RELIANCE", RELIANCE_FY26, period_end=D(2025, 12, 31))
    with pytest.raises(st.StatementError, match="^period: the file's financial year starts 2025-04-01"):
        parse("RELIANCE", RELIANCE_FY26, fy_start=D(2025, 1, 1))


@needs_fixtures
def test_an_integrated_filing_file_that_would_need_re_dating_fails():
    root = etree.fromstring(raw("RELIANCE", RELIANCE_FY26), xbrl.PARSER)
    four = root.find(f"{{{xbrl.XBRLI}}}context[@id='FourD']")
    four.find(f".//{{{xbrl.XBRLI}}}startDate").text = "2026-01-01"             # dated like the quarter
    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    with pytest.raises(st.StatementError, match="^period: an Integrated Filing file needed re-dating"):
        parse("RELIANCE", RELIANCE_FY26, data)


# ---------------------------------------------------------------------------------------------- X1's reference values
def universe_symbols() -> set[str]:
    """The universe's companies by NSE symbol (companies.yaml is keyed by slug)."""
    slugs = set(u.load_companies(u.load_peer_groups()))
    nifty = [r for r in csv.DictReader((FIX / "universe" / "nifty50_20261019.csv").open(encoding="utf-8"))]
    return {r["Symbol"].strip() for r in nifty if u.slugify(r["Symbol"].strip()) in slugs}


@needs_fixtures
def test_x1_s_fy2026_reference_values_equal_the_parsed_filings_exactly():
    """Spec 02A §5.4: for every company in both reference_values.csv and the universe, FY2026 consolidated and
    standalone revenue, PAT to owners, PAT, total equity, equity to owners, total assets, CFO and shares equal X1's
    reference exactly (same filings, same parse). Any difference is a stop, not a tolerance."""
    refs = st.load_reference(AUDIT / "reference_values.csv")
    universe = universe_symbols()
    checked, diffs = set(), []
    for ref in refs:
        path = XBRL / ref.symbol / f"{ref.file_name}.gz"
        if ref.symbol not in universe and not path.exists():
            continue
        assert path.exists(), f"{ref.symbol} {ref.basis}: {ref.file_name} isn't saved"
        p = parse(ref.symbol, ref.file_name)
        assert all(r.period_end == ref.period_end for r in p.rows)
        rows = {r.period_kind: r.values for r in p.rows}
        tags = {r.period_kind: r.tags for r in p.rows}
        diffs += [f"{ref.symbol} {ref.basis} {d}" for d in st.reference_differences(ref, rows, tags)]
        checked.add((ref.symbol, ref.basis))
    assert diffs == []
    in_universe = {s for s, _ in checked if s in universe}
    assert in_universe == {"RELIANCE", "TCS", "INFY", "HINDUNILVR", "LT", "HDFCBANK", "SBIN", "BAJFINANCE", "NTPC",
                           "TMPV", "SBILIFE"}
    assert len([c for c in checked if c[0] in universe]) == 21               # both bases, SBI Life standalone only
    assert ("FEDERALBNK", "consolidated") in checked                         # and the saved one outside the universe


def test_a_reference_difference_names_the_element_and_both_values():
    ref = st.Reference("X", "consolidated", D(2026, 3, 31), "F.xml",
                       {k: None for k in st.REFERENCE} | {"revenue": Decimal("100.0")})
    out = st.reference_differences(ref, {"year": {"revenue": Decimal(101)}}, {"year": {"revenue": "RevenueFromOperations"}})
    assert out == ["revenue (year revenue, RevenueFromOperations): reference 100.0, stored 101"]
    assert st.reference_differences(ref, {"year": {"revenue": Decimal(100)}}) == []       # numerically equal


# ---------------------------------------------------------------------------------------------- every saved filing
# The three saved filings that fail, all on shares: the file gives a paid-up capital or face value of 0 in every
# column (the filer's error; nothing to divide by). M&M's Q4 FY26 standalone is the only FY2026 one.
EXPECTED_FAILURES = {("GRASIM", "INDAS_100768_987746_13112023085050.xml"),
                     ("M&M", "INDAS_46891_128671_07082019073845_WEB.xml"),
                     ("M&M", "INTEGRATED_FILING_INDAS_1663643_05052026042738_WEB.xml")}


@needs_fixtures
def test_every_saved_filing_parses_or_fails_with_its_reason():
    failed, kinds, faces = {}, {}, face_values()
    for path in sorted(XBRL.glob("*/*.xml.gz")):
        symbol, name = path.parent.name, path.name[:-3]
        if name.startswith("NONINDAS_"):
            continue                                    # before the Dec 2018 quarter; none of the five formats
        data = raw(symbol, name)
        try:
            p = st.parse_filing(data, filing_of(symbol, name, data), face_value=faces.get(symbol))
        except st.StatementError as e:
            failed[(symbol, name)] = e
            continue
        if st.is_integrated(name):
            assert "columns dated as filed" in p.note, (symbol, name)
        for r in p.rows:
            assert set(r.tags) == set(r.values) and set(r.values) <= set(st.COLUMNS), (symbol, name)
            kinds[r.period_kind] = kinds.get(r.period_kind, 0) + 1
    assert set(failed) == EXPECTED_FAILURES and {e.check for e in failed.values()} == {"shares"}
    assert kinds == {"quarter": 1362, "half_year": 317, "nine_months": 345, "year": 363, "instant": 652}


# ---------------------------------------------------------------------------------------------- config and schema
def test_the_tag_map_names_only_columns_of_equity_financials():
    sql = (REPO / "supabase" / "migrations" / "20261028001900_equity_schema.sql").read_text()
    body = re.search(r"create table equity\.financials \((.*?)\n\);", sql, re.DOTALL).group(1)
    cols = set(re.findall(r"\b([a-z_0-9]+) numeric\b", body))
    assert set(st.COLUMNS) == cols
    for name, sec in st.TAGS.items():
        if name.startswith("_") or name == "shares":
            continue
        for part, mapping in sec.items():
            if part in ("tie", "minority_layout", "zero_is_not_filed") or part.startswith("_"):
                continue
            assert set(mapping) <= cols, (name, part, set(mapping) - cols)
    assert set(st.TAGS["shares"]) - {"_comment"} <= cols
    assert isinstance(st.PARSER_VERSION, int) and st.PARSER_VERSION >= 1


def test_the_task_is_wired_in_tasks_yaml():
    t = config.load()["equity.statements"]
    assert t.rules == {"weekly": "every 6 days"} and t.run_after == ("equity.filings",)
    assert t.overdue_after == timedelta(days=10) and t.uses_nse is False
    assert registry()["equity.statements"] is EquityStatements


# ---------------------------------------------------------------------------------------------- through the runner
JOB, ADMIN = dsn("job_writer"), dsn("postgres")
PORT, BUCKET = 5595, "equity-statements-raw"
ENDPOINT = f"http://127.0.0.1:{PORT}"
FKEY, SKEY = "test.equity_statements_filings", "test.equity_statements"
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
ISIN = {"RELIANCE": "INE002A01018", "SBILIFE": "INE123W01016"}


@pytest.fixture(scope="module")
def s3():
    with psycopg.connect(JOB, autocommit=True) as c:
        for key in (FKEY, SKEY):
            c.execute("""insert into ops.task (key, module, description, workflows, min_interval, overdue_after, enabled)
                         values (%s, 'test', 'equity statements test', '{weekly}', '6 days', '10 days', false)
                         on conflict (key) do nothing""", (key,))
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


def wipe():
    with psycopg.connect(ADMIN, autocommit=True) as c:
        ids = "(select id from core.entity where slug like 'zs-%')"
        c.execute(f"delete from equity.financials where entity_id in {ids}")
        c.execute(f"delete from equity.filing where entity_id in {ids}")
        c.execute(f"delete from core.document where entity_id in {ids}")
        c.execute(f"delete from core.event where entity_id in {ids}")
        c.execute(f"delete from equity.company where entity_id in {ids}")
        c.execute(f"delete from core.entity_identifier where entity_id in {ids}")
        c.execute("delete from core.entity where slug like 'zs-%'")
        c.execute("delete from ops.alert where task_key = any(%s)", ([FKEY, SKEY],))


@pytest.fixture(autouse=True)
def clean(request):
    if "db" not in request.keywords:
        yield
        return
    wipe()
    yield
    wipe()


def filings_task() -> EquityFilings:
    class T(EquityFilings):
        key = FKEY
    t = T()
    t.workflows = ("weekly", "backfill")
    return t


def statements_task(version: int | None = None, requires: str | None = None) -> EquityStatements:
    class T(EquityStatements):
        key = SKEY
    if version is not None:
        T.version = version
    if requires is not None:
        T.requires = requires
    t = T()
    t.workflows = ("weekly", "refresh")
    return t


def register(symbols, files: dict[str, bytes] | None = None):
    """The companies, their filings registered by equity.filings from the saved listings and instances (`files`
    overrides an instance), and EQUITY_L stored as the universe task stores it (insurers' face value)."""
    from test_equity_filings import Nse  # the saved NSE replies as a mock transport (2A-07's)
    with psycopg.connect(ADMIN, autocommit=True) as c:
        for s in symbols:
            eid = c.execute("""insert into core.entity (kind, slug, name, market, currency, is_published)
                               values ('company', %s, %s, 'IN', 'INR', false) returning id""",
                            (f"zs-{s.lower()}", f"{s} test")).fetchone()[0]
            c.execute("""insert into equity.company (entity_id, isin, nse_symbol, fy_end_month)
                         values (%s, %s, %s, 3)""", (eid, ISIN[s], s))
    ex = Nse()
    ex.files.update(files or {})
    http = HttpClient(transport=httpx.MockTransport(ex), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, "backfill", [filings_task()], only=FKEY, force=True, http=http, storage=storage,
               clock=lambda: NOW, since=D(2019, 1, 1), until=D(2026, 10, 27))
    assert code == 0, q("select status, error from ops.task_run where task_key = %s order by id desc limit 1", FKEY)
    stored = storage().put(EQUITY_L.read_bytes(), source="nse", media_type="text/csv", ext="csv")
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("""insert into core.raw_file (object_key, source_id, source_url, sha256, bytes, media_type, fetched_at,
                                                task_run_id)
                     values (%s, (select id from core.source where key = 'nse'), %s, %s, %s, 'text/csv',
                             now(), (select max(id) from ops.task_run))
                     on conflict (source_id, sha256) do update set fetched_at = excluded.fetched_at""",
                  (stored.object_key, u.EQUITY_L_URL, stored.sha256, stored.bytes))


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"equity.statements fetched {request.url}")


def go(task: EquityStatements | None = None, workflow: str = "refresh"):
    http = HttpClient(transport=httpx.MockTransport(refuse), sleep=lambda s: None, clock=lambda: NOW)
    code = run(JOB, workflow, [task or statements_task()], only=SKEY, force=True, http=http, storage=storage,
               clock=lambda: NOW)
    tr = q("""select status, rows_written, checks, error from ops.task_run where task_key = %s
              order by id desc limit 1""", SKEY)[0]
    return code, tr


def checks(tr):
    return {c["name"]: c for c in tr[2]}


def registered():
    """(file name, symbol, basis, format, period end, fy start, status, note, version) of the test companies' filings."""
    return q("""select d.external_key, c.nse_symbol, f.basis, f.format, f.period_end, f.fy_start, f.parse_status,
                       f.parse_note, f.parser_version
                  from equity.filing f join core.document d on d.id = f.document_id
                  join equity.company c on c.entity_id = f.entity_id
                 where f.entity_id in (select id from core.entity where slug like 'zs-%%') order by 1""")


def stored_rows(name: str | None = None):
    return q("""select d.external_key, x.period_kind, x.period_start, x.period_end, x.revenue, x.pat, x.total_assets,
                       x.face_value, x.shares_outstanding, x.tags
                  from equity.financials x join core.document d on d.id = x.document_id
                 where x.entity_id in (select id from core.entity where slug like 'zs-%%')
                   and (%s::text is null or d.external_key = %s) order by 1, 2""", name, name)


def expected_rows() -> int:
    """The rows the registered filings parse to, here (the same parser on the same files, the same face values)."""
    faces, n = face_values(), 0
    for name, symbol, basis, fmt, pe, fs, *_ in registered():
        try:
            n += len(st.parse_filing(raw(symbol, name), st.Filing(name, basis, fmt, pe, fs),
                                     face_value=faces.get(symbol)).rows)
        except st.StatementError:
            pass
    return n


@pytest.mark.db
@needs_fixtures
def test_a_refresh_parses_every_pending_filing_into_its_rows(s3):
    register(("RELIANCE", "SBILIFE"))
    assert {r[6] for r in registered()} == {"pending"} and len(registered()) == 67
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert {n: (v["passed"], v["blocking"]) for n, v in c.items()} == {
        "filings_failed": (True, False), "failed_share": (True, True), "latest_quarters": (True, True),
        "reference": (True, True), "parsed": (True, False)}
    # RELIANCE's FY2026 pair and SBI Life's FY2026 standalone are registered, and equal X1's figures exactly
    assert c["reference"]["detail"].startswith("3 reference filings equal X1's FY2026 figures exactly"), c["reference"]
    assert c["parsed"]["detail"].startswith("67 of 67 filings parsed") and "face value from EQUITY_L" in c["parsed"]["detail"]
    got = registered()
    assert {(r[6], r[8]) for r in got} == {("parsed", str(st.PARSER_VERSION))}
    assert all(r[7].startswith(f"v{st.PARSER_VERSION}: ") for r in got)
    n = expected_rows()
    assert len(stored_rows()) == n == tr[1] and n > 67
    year = {r[1]: r for r in stored_rows(RELIANCE_FY26)}
    assert set(year) == {"quarter", "year", "instant"}
    assert year["year"][4] == Decimal(10756750000000) and year["year"][9]["revenue"] == "RevenueFromOperations"
    assert (year["year"][2], year["year"][3]) == (D(2025, 4, 1), D(2026, 3, 31))
    life = {r[1]: r for r in stored_rows("INTEGRATED_FILING_LI_1657299_22042026060213_WEB.xml")}
    assert life["instant"][6] == Decimal(5000459215000) and life["instant"][7] == 10
    assert life["instant"][9]["face_value"] == "EQUITY_L FACE VALUE (not in the filing)"


@pytest.mark.db
@needs_fixtures
def test_a_second_run_parses_nothing_and_changes_nothing(s3):
    register(("SBILIFE",))
    go()
    before = stored_rows()
    code, tr = go()
    assert code == 0 and tr[0] == "success" and tr[1] == 0, tr
    assert checks(tr)["parsed"]["detail"].startswith("0 of 0 filings parsed")
    assert stored_rows() == before


@pytest.mark.db
@needs_fixtures
def test_a_parser_version_bump_parses_every_filing_again_and_replaces_its_rows(s3):
    register(("RELIANCE",))
    go()
    n = len(stored_rows())
    with psycopg.connect(ADMIN, autocommit=True) as c:                    # a stale row the new parse must remove
        c.execute("""insert into equity.financials (document_id, entity_id, basis, period_end, period_kind, revenue)
                     select f.document_id, f.entity_id, f.basis, f.period_end, 'nine_months', 1
                       from equity.filing f
                      where f.entity_id in (select id from core.entity where slug like 'zs-%')
                        and extract(month from f.period_end) = 6
                      order by f.period_end limit 1""")
    assert len(stored_rows()) == n + 1
    code, tr = go(statements_task(version=st.PARSER_VERSION + 1))
    assert code == 0 and tr[0] == "success", tr
    assert tr[1] == n and len(stored_rows()) == n
    assert checks(tr)["parsed"]["detail"].startswith("61 of 61 filings parsed")
    assert {r[8] for r in registered()} == {str(st.PARSER_VERSION + 1)}
    assert all(r[7].startswith(f"v{st.PARSER_VERSION + 1}: ") for r in registered())
    code, tr = go()                                       # the older version doesn't parse them back
    assert tr[1] == 0 and {r[8] for r in registered()} == {str(st.PARSER_VERSION + 1)}


@pytest.mark.db
@needs_fixtures
def test_a_filing_with_a_broken_tie_fails_alone(s3):
    bad = "INDAS_117298_1348254_16012025082021.xml"                     # Dec 2024 standalone: not the latest quarter
    register(("RELIANCE",), {bad: edited(raw("RELIANCE", bad), "ProfitLossForPeriod", "OneD", add=10**10)})
    code, tr = go()
    assert code == 0 and tr[0] == "success", tr
    c = checks(tr)
    assert not c["filings_failed"]["passed"] and not c["filings_failed"]["blocking"]
    assert bad in c["filings_failed"]["detail"] and "pat: quarter: PAT" in c["filings_failed"]["detail"]
    assert c["failed_share"]["passed"] and c["latest_quarters"]["passed"]
    by = {r[0]: r for r in registered()}
    assert by[bad][6] == "failed" and by[bad][7].startswith(f"v{st.PARSER_VERSION}: pat: quarter: PAT")
    assert stored_rows(bad) == []
    assert sum(1 for r in by.values() if r[6] == "parsed") == 60
    assert q("select count(*) from ops.alert where dedupe_key = %s and resolved_at is null",
             f"check:{SKEY}:filings_failed") == [(1,)]


@pytest.mark.db
@needs_fixtures
def test_more_than_five_percent_failed_stops_the_load(s3):
    bad = "INTEGRATED_FILING_LI_1424277_24042025082603_WEB.xml"          # SBI Life's oldest: 1 of 6 is 17%
    register(("SBILIFE",), {bad: edited(raw("SBILIFE", bad), "ProfitLossAfterTaxAndExtraordinaryItems", "OneD",
                                        add=10**9)})
    code, tr = go()
    assert code != 0 and tr[0] == "checks_failed" and "failed_share" in tr[3], tr
    assert checks(tr)["failed_share"]["detail"].startswith("1 of 6 filings failed")
    assert checks(tr)["latest_quarters"]["passed"]
    assert {r[6] for r in registered()} == {"pending"} and stored_rows() == []      # rolled back: nothing stored


@pytest.mark.db
@needs_fixtures
def test_a_latest_quarter_that_fails_stops_the_load(s3):
    bad = "INTEGRATED_FILING_INDAS_1695741_17072026075004_WEB.xml"        # RELIANCE's Jun 2026 consolidated, its latest
    register(("RELIANCE",), {bad: edited(raw("RELIANCE", bad), "ProfitLossForPeriod", "OneD", add=10**10)})
    code, tr = go()
    assert code != 0 and tr[0] == "checks_failed" and "latest_quarters" in tr[3], tr
    c = checks(tr)
    assert c["failed_share"]["passed"]                                              # 1 of 61
    assert "zs-reliance 2026-06-30 consolidated" in c["latest_quarters"]["detail"]
    assert {r[6] for r in registered()} == {"pending"} and stored_rows() == []


@pytest.mark.db
@needs_fixtures
def test_a_reference_value_that_differs_stops_the_load_and_names_the_filing_element_and_values(s3, tmp_path,
                                                                                                monkeypatch):
    text = (AUDIT / "reference_values.csv").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("RELIANCE,consolidated,"))
    assert ",10756750000000.0," in line
    ref = tmp_path / "reference_values.csv"
    ref.write_text(text.replace(line, line.replace(",10756750000000.0,", ",10756750000001.0,", 1)), encoding="utf-8")
    monkeypatch.setenv(es.REFERENCE_ENV, str(ref))
    register(("RELIANCE",))
    code, tr = go()
    assert code != 0 and tr[0] == "checks_failed" and tr[3] == "reference", tr
    d = checks(tr)["reference"]["detail"]
    assert f"RELIANCE consolidated ({RELIANCE_FY26}) revenue (year revenue, RevenueFromOperations): " \
           "reference 10756750000001.0, stored 10756750000000" in d
    assert {r[6] for r in registered()} == {"pending"}


@pytest.mark.db
def test_the_production_guard_holds_a_scheduled_first_run_and_a_missing_table_but_not_a_refresh():
    class Never(EquityStatements):
        key = "test.equity_statements_never_run"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        weekly, refresh = Never().due(c, "weekly", NOW), Never().due(c, "refresh", NOW)
        missing = statements_task(requires="equity.not_a_table").due(c, "refresh", NOW)
    assert weekly.due is False and "waiting for its first recorded run" in weekly.reason
    assert refresh.due is True
    assert missing.due is False and missing.reason.startswith("not set up")
