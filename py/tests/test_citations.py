"""Citation mapping and number grounding."""
from ft.ai.citations import locate_quote, map_api_citation, numbers_grounded

DOC = "The Committee decided to lower the target range by 1/4 point.\n\nInflation remains   somewhat elevated. Inflation remains somewhat elevated."
ROWS = [{"bank": "RBI", "meeting_date": "2026-10-01", "policy_rate_pct": 5.250, "policy_rate_change_bps": -25},
        {"bank": "RBI", "meeting_date": "2026-08-06", "policy_rate_pct": 5.500, "policy_rate_change_bps": 0}]


def test_quote_found_once_maps_to_offsets():
    assert locate_quote(DOC, "decided to lower the target").start == DOC.index("decided")


def test_duplicate_quote_is_not_evidence():
    assert not locate_quote(DOC, "Inflation remains somewhat elevated").verified


def test_whitespace_differences_map_back_to_original():
    sp = locate_quote(DOC, "target range by 1/4 point. Inflation remains somewhat")
    assert sp.verified and DOC[sp.start:sp.end] == "target range by 1/4 point.\n\nInflation remains   somewhat"


def test_invented_quote_rejected():
    assert not locate_quote(DOC, "the Committee raised rates").verified


def test_native_citation_offsets_and_fallback():
    assert map_api_citation(DOC, 0, 4, 13, "Committee").how == "offsets"
    assert map_api_citation(DOC, 10, 4, 13, "Committee").how == "search"


def test_numbers_grounded():
    assert numbers_grounded("RBI cut the repo rate by 25 bps to 5.25% on 1 Oct 2026, after holding at 5.5% in August.", ROWS)[0]
    ok, bad = numbers_grounded("RBI cut the repo rate by 50 bps to 5.25%.", ROWS)
    assert not ok and bad == ["50"]
    assert numbers_grounded("2 meetings returned.", ROWS)[0]


def test_indian_and_western_grouping_parse_whole():
    # Indian grouping used to split into 1 and 23,456; Western millions must still parse whole (Review 01, F4)
    assert numbers_grounded("Net profit was ₹1,23,456 cr.", [{"pat_cr": 123456}])[0]
    assert numbers_grounded("Revenue was 12,34,567.5 lakh.", [{"rev": 1234567.5}])[0]
    assert numbers_grounded("Revenue was $12,345,678 and 1,234,567 shares.", [{"rev": 12345678, "sh": 1234567}])[0]
    assert not numbers_grounded("Net profit was ₹1,23,456 cr.", [{"pat_cr": 23456}])[0]


def test_sign_is_compared_when_written():
    assert not numbers_grounded("The repo rate changed by +25 bps.", ROWS)[0]
    assert numbers_grounded("The repo rate changed by −25 bps.", ROWS)[0]
    assert numbers_grounded("RBI cut the repo rate by 25 bps.", ROWS)[0]      # unsigned: either sign


def test_query_literals_and_tenors_are_allowed():
    sql = "select * from policy_meetings where bank = 'RBI' and meeting_date >= '2025-01-01'"
    assert numbers_grounded("Since 2025, RBI cut the repo rate by 25 bps.", ROWS, sql)[0]
    assert numbers_grounded("Since 2025, RBI cut the repo rate by 25 bps.", ROWS) == (False, ["2025"])
    assert numbers_grounded("The 10-year yield rose after RBI cut by 25 bps.", ROWS)[0]
