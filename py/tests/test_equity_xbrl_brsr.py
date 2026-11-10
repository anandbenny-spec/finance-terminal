"""The XBRL reader's third use (job VC-01, the value chain card; Spec 02B §12.4): typed-dimension rows, the BRSR's
current-year and prior-year contexts, header facts and the entry point. 2A-09's and 2A-10's tests are unchanged.

The synthetic tests build small instances and always run. The saved files are in data/fixtures/equity (the private
fixtures repo; those tests skip without it, and CI checks it out): `brsr/` (VC-03) and `xbrl/<SYMBOL>/` (the results
filings)."""
import gzip
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ft.equity import xbrl as x

FIX = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "equity"
BRSR, RES = FIX / "brsr", FIX / "xbrl"
needs_fixtures = pytest.mark.skipif(not BRSR.exists() or not RES.exists(),
                                    reason="the saved files aren't checked out (data/fixtures)")
D = date


def load(path: Path) -> x.Instance:
    with gzip.open(path) as f:
        return x.parse(f.read())


def brsr(rel: str) -> x.BrsrView:
    return x.brsr_view(load(BRSR / rel))


NS = ('xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
      'xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:c="http://example.org/c"')


def ctx(cid, start=None, end=None, instant=None, typed=None, explicit=None):
    per = f"<xbrli:instant>{instant}</xbrli:instant>" if instant else \
        f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
    scen = ""
    for axis, m in (typed or {}).items():
        scen += f'<xbrldi:typedMember dimension="c:{axis}"><c:Dom>{m}</c:Dom></xbrldi:typedMember>'
    for axis, m in (explicit or {}).items():
        scen += f'<xbrldi:explicitMember dimension="c:{axis}">c:{m}</xbrldi:explicitMember>'
    scen = f"<xbrli:scenario>{scen}</xbrli:scenario>" if scen else ""
    return f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="s">1</xbrli:identifier></xbrli:entity>' \
           f'<xbrli:period>{per}</xbrli:period>{scen}</xbrli:context>'


def num(name, cid, v, decimals="INF"):
    return f'<c:{name} contextRef="{cid}" unitRef="pure" decimals="{decimals}">{v}</c:{name}>'


def txt(name, cid, v):
    return f'<c:{name} contextRef="{cid}">{v}</c:{name}>'


def instance(*parts, href="in-capmkt-ent-2024-04-30.xsd") -> x.Instance:
    body = f'<?xml version="1.0"?><xbrli:xbrl {NS}><link:schemaRef xlink:type="simple" xlink:href="{href}"/>' \
           + "".join(parts) + "</xbrli:xbrl>"
    return x.parse(body.encode())


def small_brsr(**extra) -> x.Instance:
    return instance(
        ctx("DCY", "2023-04-01", "2024-03-31"), ctx("DPY", "2022-04-01", "2023-03-31"), ctx("ICY", instant="2024-03-31"),
        txt("NameOfTheCompany", "ICY", "Acme Ltd"), txt("DateOfStartOfFinancialYear", "DCY", "2023-04-01"),
        txt("DateOfEndOfFinancialYear", "DCY", "2024-03-31"), txt("ReportingBoundary", "DCY", "Standalone basis"),
        num("Share", "DCY", "0.7933"), num("Share", "DPY", "0.5"), num("Count", "DCY", "740", "0"),
        txt("Note", "DCY", "as filed"), **extra)


# ---- synthetic ---------------------------------------------------------------------------------------------------

def test_typed_members_are_kept_apart_from_explicit_ones():
    i = instance(ctx("a", "2024-01-01", "2024-03-31", typed={"Ax": "Dom1"}),
                 ctx("b", "2024-01-01", "2024-03-31", explicit={"Ax2": "Mem"}),
                 num("V", "a", "1"), num("V", "b", "2"))
    assert i.contexts["a"].typed == {"Ax": "Dom1"} and i.contexts["a"].dims == {"Ax": "Dom1"}
    assert i.contexts["b"].typed == {} and i.contexts["b"].dims == {"Ax2": "Mem"}


def test_rows_are_keyed_by_member_and_period():
    i = instance(
        ctx("d1", "2025-07-01", "2025-09-30", typed={"RPT": "Dom1"}), ctx("i1", instant="2025-09-30", typed={"RPT": "Dom1"}),
        ctx("p1", instant="2025-06-30", typed={"RPT": "Dom1"}), ctx("d2", "2025-07-01", "2025-09-30", typed={"RPT": "Dom2"}),
        ctx("d10", "2025-07-01", "2025-09-30", typed={"RPT": "Dom10"}),
        ctx("other", "2025-07-01", "2025-09-30", typed={"RPT": "Dom1"}, explicit={"Seg": "S1"}),
        num("Amount", "d1", "5", "-6"), num("Amount", "i1", "7"), num("Amount", "p1", "0"), num("Amount", "d2", "9"),
        num("Amount", "d10", "1"), txt("Remarks", "d1", "NA"), num("Amount", "other", "99"))
    rows = x.typed_rows(i, "RPT")
    assert [(r.member, r.period[0]) for r in rows] == [("Dom1", "duration"), ("Dom1", "instant"), ("Dom1", "instant"),
                                                       ("Dom2", "duration"), ("Dom10", "duration")]
    first = rows[0]
    assert first.facts["Amount"].value == Decimal(5) and first.facts["Amount"].decimals == "-6"
    assert first.facts["Remarks"].text == "NA" and first.contexts == ["d1"]
    assert [r.facts["Amount"].value for r in rows[1:3]] == [Decimal(0), Decimal(7)]   # 30 Jun before 30 Sep
    assert x.typed_rows(i, "Seg") == []                  # an explicit axis isn't a typed one


def test_a_member_with_two_values_in_one_period_is_reported():
    i = instance(ctx("a", "2024-01-01", "2024-03-31", typed={"Ax": "D1"}),
                 ctx("b", "2024-01-01", "2024-03-31", typed={"Ax": "D1"}),
                 num("V", "a", "1"), num("V", "b", "2"), num("W", "a", "3"), num("W", "b", "3"))
    (row,) = x.typed_rows(i, "Ax")
    assert "V" not in row.facts and [f.value for f in row.conflicts["V"]] == [Decimal(1), Decimal(2)]
    assert row.facts["W"].value == Decimal(3) and "W" not in row.conflicts      # the same value twice isn't a conflict


def test_brsr_current_and_prior_year_and_header():
    v = x.brsr_view(small_brsr())
    assert v.fy == (D(2023, 4, 1), D(2024, 3, 31)) and v.entry_point == "2024-04-30"
    assert v.header["name"] == "Acme Ltd" and v.header["boundary"] == "Standalone basis"
    assert v.header["fy_start"] == "2023-04-01" and v.current_contexts == ["DCY"] and v.prior_contexts == ["DPY"]
    assert v.current["Share"].value == Decimal("0.7933") and v.current["Count"].decimals == "0"
    assert v.current["Note"].text == "as filed" and v.prior["Share"].value == Decimal("0.5")
    assert v.conflicts == {} and v.prior_conflicts == {}


def test_brsr_fraction_element_holding_a_percentage_comes_back_as_filed():
    i = instance(ctx("DCY", "2024-04-01", "2025-03-31"), txt("DateOfStartOfFinancialYear", "DCY", "2024-04-01"),
                 txt("DateOfEndOfFinancialYear", "DCY", "2025-03-31"), num("Share", "DCY", "59.62"))
    assert x.brsr_view(i).current["Share"].value == Decimal("59.62")


def test_brsr_two_values_for_one_element_in_one_year_are_reported_not_resolved():
    i = instance(ctx("DCY", "2024-04-01", "2025-03-31"), ctx("DCY2", "2024-04-01", "2025-03-31"),
                 txt("DateOfStartOfFinancialYear", "DCY", "2024-04-01"), txt("DateOfEndOfFinancialYear", "DCY", "2025-03-31"),
                 num("Share", "DCY", "0.2"), num("Share", "DCY2", "0.3"), num("Other", "DCY", "1"))
    v = x.brsr_view(i)
    assert "Share" not in v.current and [f.value for f in v.conflicts["Share"]] == [Decimal("0.2"), Decimal("0.3")]
    assert v.current["Other"].value == Decimal(1)


def test_brsr_dimensioned_and_other_year_facts_stay_out_of_the_current_year():
    i = instance(ctx("DCY", "2024-04-01", "2025-03-31"), ctx("DIM", "2024-04-01", "2025-03-31", typed={"Ax": "D1"}),
                 ctx("OLD", "2021-04-01", "2022-03-31"), txt("DateOfStartOfFinancialYear", "DCY", "2024-04-01"),
                 txt("DateOfEndOfFinancialYear", "DCY", "2025-03-31"), num("S", "DIM", "1"), num("S", "OLD", "2"))
    v = x.brsr_view(i)
    assert v.current == {k: f for k, f in v.current.items() if k.startswith("Date")} and v.prior == {}


def test_a_filing_with_no_financial_year_is_not_a_brsr():
    with pytest.raises(x.XbrlError):
        x.brsr_view(instance(ctx("a", "2024-01-01", "2024-03-31"), num("V", "a", "1")))


def test_entry_point_is_none_without_a_dated_schemaref():
    assert instance(ctx("a", "2024-01-01", "2024-03-31"), num("V", "a", "1"), href="other.xsd").entry_point is None


# ---- the saved files -------------------------------------------------------------------------------------------------

def el(v: x.BrsrView, name: str) -> Decimal | None:
    f = v.current.get(name)
    return f.value if f else None


@needs_fixtures
def test_mm_fy2023_24_known_case():
    v = brsr("bse/M&M/BRSR_500520_2962024195928.xml.gz")
    assert el(v, "PercentageOfSalesToDealersOrDistributorsInTotalSales") == Decimal("0.7933")
    assert el(v, "NumberOfDealersOrDistributorsToWhomSalesAreMade") == Decimal(740)
    assert el(v, "PercentageOfSalesToTopTenDealersOrDistributorsInTotalSalesToDealersOrDistributors") == Decimal("0.1296")
    assert el(v, "PercentageOfSourcedDirectlyFromWithinTheDistrictAndNeighbouringDistricts") == Decimal("0.84")
    assert el(v, "PercentageOfDirectlySourcedFromMSMEsOrSmallProducers") == Decimal("0.10")
    assert v.header["boundary"] == "Standalone basis" and v.entry_point == "2024-04-30"
    assert v.fy == (D(2023, 4, 1), D(2024, 3, 31)) and v.conflicts == {}


@needs_fixtures
def test_mm_fy2024_25_products_and_group_companies_as_rows():
    inst = load(BRSR / "bse/M&M/BRSR_500520_286202523432.xml.gz")
    axis = "ProductsOrServicesSoldByTheEntityAccountingForNinetyPercentOfTheTurnoverAxis"
    prod = x.typed_rows(inst, axis)
    got = [(r.facts["ProductOrServiceSoldByTheEntity"].text, r.facts["PercentageOfTotalTurnoverForProductOrServiceSold"].value)
           for r in prod]
    assert got == [("Automotive (29101, 29102)", Decimal("0.6951")), ("Farm Equipment", Decimal("0.2166"))]
    assert prod[0].facts["NICCodeOfProductOrServiceSoldByTheEntity"].text == "29101"
    groups = x.typed_rows(inst, "HoldingSubsidiaryAssociateCompaniesAndJointVenturesAxis")
    assert len(groups) == 13 and all(r.period[0] == "duration" and not r.conflicts for r in groups)
    assert groups[0].facts["NameOfTheHoldingOrSubsidiaryAssociateCompaniesOrJointVentures"].text == "Mahindra Steel Service Centre Limited"
    assert groups[0].facts["PercentageOfSharesHeldByListedEntity"].value == Decimal("0.61")
    assert groups[0].facts["CategoryOfCompany"].text == "Subsidiary"


@needs_fixtures
def test_fy2025_26_header_carries_symbol_and_scrip_code():
    v = brsr("nse/M&M/BRSR_500520_472026182143_BRSR_WebXMLFile_20260704_182159464.xml.gz")
    assert v.header["nse_symbol"] == "M&M" and v.header["scrip_code"] == "500520" and v.entry_point == "2026-02-28"
    assert v.header["name"] and v.header["boundary"]


@needs_fixtures
def test_fy2022_23_entry_point_is_the_2021_format():
    v = brsr("nse/M&M/BRSR_872798_03072023012828_WEB.xml.gz")
    assert v.entry_point in {"2021-09-30", "2023-06-30"}
    assert "nse_symbol" not in v.header and v.fy[1] == D(2023, 3, 31)


@needs_fixtures
def test_coal_india_fraction_element_returns_59_62_as_filed():
    v = brsr("nse/COALINDIA/BRSR_1501398_04082025110417_WEB.xml.gz")
    f = v.current["PercentageOfDirectlySourcedFromMSMEsOrSmallProducers"]
    assert f.value == Decimal("59.62") and f.decimals == "INF" and f.unit == "pure"
    assert v.prior["PercentageOfDirectlySourcedFromMSMEsOrSmallProducers"].value == Decimal("0.5844")


@needs_fixtures
def test_nestle_fy2023_24_current_year_context_is_15_months():
    v = brsr("nse/NESTLEIND/BRSR_1158818_15062024115331_WEB.xml.gz")
    assert v.fy == (D(2023, 1, 1), D(2024, 3, 31)) and v.header["fy_start"] == "2023-01-01"
    assert v.current_contexts == ["DCYMain"] and v.prior_contexts == ["DPYMain"]


def related_party(path: str) -> list[x.Row]:
    return x.typed_rows(load(RES / path), "RelatedPartyTransactionAxis")


@needs_fixtures
def test_mm_h1_fy2025_26_related_party_rows():
    rows = related_party("M&M/INTEGRATED_FILING_INDAS_1565188_04112025075712_WEB.xml.gz")
    members = {r.member for r in rows}
    assert len(members) == 1953 and len(rows) == 3 * 1953
    assert all(len([r for r in rows if r.member == m]) == 3 for m in list(members)[:50])
    kinds = {}
    for r in rows:
        kinds[r.period[0]] = kinds.get(r.period[0], 0) + 1
    assert kinds == {"duration": 1953, "instant": 2 * 1953}
    first = [r for r in rows if r.member == "RelatedPartyTransactionDomain1"]
    assert {r.period for r in first} == {("duration", D(2025, 7, 1), D(2025, 9, 30)), ("instant", D(2025, 9, 30), D(2025, 9, 30)),
                                         ("instant", D(2025, 6, 30), D(2025, 6, 30))}
    assert "AmountOfRelatedPartyTransaction" in next(r for r in first if r.period[0] == "instant").facts


@needs_fixtures
def test_maruti_h1_fy2025_26_related_party_rows():
    rows = related_party("MARUTI/INTEGRATED_FILING_INDAS_1562963_31102025080837_WEB.xml.gz")
    assert len({r.member for r in rows}) == 249 and len(rows) == 3 * 249


@needs_fixtures
def test_mm_segments_read_through_the_explicit_axis_lookup():
    """No change was needed for segments: 2A's lookup by dimension member already reads them."""
    inst = load(RES / "M&M/INTEGRATED_FILING_INDAS_1565188_04112025075712_WEB.xml.gz")
    axes = {a for c in inst.contexts.values() for a in c.dims}
    assert "ReportableSegmentsAxis" in axes
    members = sorted({c.dims["ReportableSegmentsAxis"] for c in inst.contexts.values()
                      if set(c.dims) == {"ReportableSegmentsAxis"}})
    assert members and members[0].startswith("ReportableSegments")
    assert members == [f"ReportableSegments{n}Member" for n in range(1, 6)]
    found = x.facts_on(inst, "SegmentRevenue", "ReportableSegmentsAxis", tuple(members))
    assert len(found) == 10 and found[0].value == Decimal(249290700000) and found[0].unit == "INR"
