"""The Dow 30's company master: the curated YAML and SEC's submissions (Spec 02A.1 §5.1; job US-01, issue #400).

- `config/equity/us_companies.yaml` names the 30 companies (ticker, CIK and predecessor CIKs, listing exchange,
  fiscal-year-end month, SIC, peer group, valuation family with its note, multi-class flag); `us_peer_groups.yaml`
  their curated groups, each with its Damodaran US industry. `load_us_config` validates both and refuses a file that
  breaks a rule, naming it.
- SEC's submissions API (`submissions_url`) gives each CIK's tickers, exchanges, SIC, fiscal year end and recent
  filings; `parse_submissions` reads it and `compare` checks a company against it. The ticker and the form type (10-K
  filers only: a 20-F or 40-F filer is outside what the slice reads) are blocking; the exchange, SIC and fiscal year
  end are warnings naming the company.
- SEC's fair-access rules: every request carries the declared User-Agent from the `SEC_USER_AGENT` secret
  (`sec_user_agent`), never written anywhere, and the shared client paces the `sec` site under 10 requests a second.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from ft.equity.universe import CONFIG_DIR, VALUATION_FAMILIES, UniverseError

COMPANIES_FILE = CONFIG_DIR / "us_companies.yaml"
PEER_GROUPS_FILE = CONFIG_DIR / "us_peer_groups.yaml"
EXCHANGES = ("NYSE", "NASDAQ")
SEC_EXCHANGE = {"NYSE": "NYSE", "Nasdaq": "NASDAQ", "NASDAQ": "NASDAQ"}   # SEC's spelling -> equity.company.price_exchange
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
KEY = re.compile(r"^us-[a-z0-9-]+$")
EXPECTED_COMPANIES = 30
PERIODIC = ("10-K", "10-K/A", "10-Q", "10-Q/A")
FOREIGN = ("20-F", "20-F/A", "40-F", "40-F/A")
RECENT_DAYS = 200                 # a quarterly filer files a 10-Q or 10-K at least this often


@dataclass(frozen=True)
class UsPeerGroup:
    key: str
    label: str
    damodaran_industry: str
    source_note: str


@dataclass(frozen=True)
class UsCompany:
    ticker: str
    name: str
    short_name: str
    cik: int
    exchange: str
    fy_end_month: int
    sic: int
    peer_group: str
    valuation_family: str
    family_note: str | None = None
    predecessor_ciks: tuple[int, ...] = ()
    multi_class: bool = False

    @property
    def slug(self) -> str:
        return slug(self.ticker)


@dataclass(frozen=True)
class UsConfig:
    index: str
    as_of: date
    companies: list[UsCompany]
    groups: dict[str, UsPeerGroup]


def slug(ticker: str) -> str:
    """`us-` and the lower-cased ticker, any other character a hyphen (BRK.B -> us-brk-b): never an Indian slug, which
    has no `us-` prefix (Spec 02A.1 §5.1). Fixed at creation, like India's."""
    return "us-" + re.sub(r"[^a-z0-9]+", "-", ticker.lower()).strip("-")


def load_us_config(companies_file: Path | None = None, groups_file: Path | None = None) -> UsConfig:
    companies_file, groups_file = companies_file or COMPANIES_FILE, groups_file or PEER_GROUPS_FILE
    groups_raw = yaml.safe_load(groups_file.read_text()) or {}
    groups: dict[str, UsPeerGroup] = {}
    for key, g in (groups_raw.get("groups") or {}).items():
        if not KEY.match(key):
            raise UniverseError(f"US peer group {key!r}: keys are us- plus lower-case letters, digits and hyphens")
        missing = [f for f in ("label", "damodaran_industry", "source_note") if not (g or {}).get(f)]
        if missing:
            raise UniverseError(f"US peer group {key}: no {', '.join(missing)}")
        groups[key] = UsPeerGroup(key, g["label"], g["damodaran_industry"], g["source_note"])
    raw = yaml.safe_load(companies_file.read_text()) or {}
    index, as_of = raw.get("index"), raw.get("as_of")
    if index != "dow-jones":
        raise UniverseError(f"us_companies.yaml names the index {index!r}; the US slice is the Dow (dow-jones)")
    if not isinstance(as_of, date):
        raise UniverseError("us_companies.yaml needs as_of, the date the list was confirmed (YYYY-MM-DD)")
    companies: list[UsCompany] = []
    for row in raw.get("companies") or []:
        companies.append(_company(row, groups))
    tickers, ciks = [c.ticker for c in companies], [c.cik for c in companies]
    for what, values in (("ticker", tickers), ("CIK", ciks), ("slug", [c.slug for c in companies])):
        dup = sorted({str(v) for v in values if values.count(v) > 1})
        if dup:
            raise UniverseError(f"us_companies.yaml: the {what} {', '.join(dup)} appears twice")
    if len(companies) != EXPECTED_COMPANIES:
        raise UniverseError(f"us_companies.yaml lists {len(companies)} companies; the Dow has {EXPECTED_COMPANIES}")
    unused = sorted(set(groups) - {c.peer_group for c in companies})
    if unused:
        raise UniverseError(f"us_peer_groups.yaml: no company is in {', '.join(unused)}")
    return UsConfig(index, as_of, companies, groups)


def _company(row: dict, groups: dict[str, UsPeerGroup]) -> UsCompany:
    ticker = str(row.get("ticker") or "")
    if not TICKER.match(ticker):
        raise UniverseError(f"us_companies.yaml: {ticker!r} isn't a ticker")
    for f in ("name", "short_name", "cik", "exchange", "fy_end_month", "sic", "peer_group", "valuation_family"):
        if row.get(f) in (None, ""):
            raise UniverseError(f"{ticker}: no {f} in us_companies.yaml")
    if row["exchange"] not in EXCHANGES:
        raise UniverseError(f"{ticker}: exchange {row['exchange']!r} isn't one of {', '.join(EXCHANGES)}")
    if not (isinstance(row["fy_end_month"], int) and 1 <= row["fy_end_month"] <= 12):
        raise UniverseError(f"{ticker}: fy_end_month must be 1 to 12")
    if not (isinstance(row["sic"], int) and 100 <= row["sic"] <= 9999):
        raise UniverseError(f"{ticker}: sic must be SEC's code (up to four digits)")
    if row["peer_group"] not in groups:
        raise UniverseError(f"{ticker}: peer group {row['peer_group']!r} isn't in us_peer_groups.yaml")
    family = row["valuation_family"]
    if family not in VALUATION_FAMILIES:
        raise UniverseError(f"{ticker}: valuation family {family!r} isn't one of {', '.join(VALUATION_FAMILIES)}")
    note = row.get("family_note")
    if family != "operating" and not note:
        raise UniverseError(f"{ticker}: a {family} needs its family_note (why it isn't valued as an operating company)")
    preds = tuple(int(c) for c in row.get("predecessor_ciks") or ())
    cik = int(row["cik"])
    if cik in preds or cik <= 0 or any(p <= 0 for p in preds):
        raise UniverseError(f"{ticker}: a CIK is a positive number, and a predecessor isn't the company's own CIK")
    return UsCompany(ticker, row["name"], row["short_name"], cik, row["exchange"], row["fy_end_month"], row["sic"],
                     row["peer_group"], family, note, preds, bool(row.get("multi_class", False)))


# ------------------------------------------------------------------------------------------------ SEC
def sec_user_agent(env=os.environ) -> str:
    """SEC's fair-access rules ask every request to declare who is asking, with a contact. The value is the secret
    SEC_USER_AGENT (Anand's: `gh secret set SEC_USER_AGENT`), never written in the repo, a log or a stored file."""
    ua = (env.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        raise UniverseError("SEC_USER_AGENT is empty: SEC's fair-access rules need a declared User-Agent with a contact. "
                            "Set the repository secret SEC_USER_AGENT (gh secret set SEC_USER_AGENT)")
    return ua


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


@dataclass(frozen=True)
class Submission:
    cik: int
    name: str
    sic: int | None
    sic_description: str
    fiscal_year_end: str | None            # SEC's MMDD
    listings: dict[str, str]               # ticker -> exchange as SEC spells it
    forms: list[tuple[str, date]] = field(default_factory=list)   # recent filings: form type and filing date, newest first


def parse_submissions(body: bytes) -> Submission:
    try:
        d = json.loads(body)
    except ValueError as e:
        raise UniverseError(f"SEC's submissions reply isn't JSON ({e.__class__.__name__})") from None
    if not isinstance(d, dict) or "cik" not in d or "filings" not in d:
        raise UniverseError("SEC's submissions reply has no cik or filings")
    tickers, exchanges = d.get("tickers") or [], d.get("exchanges") or []
    recent = (d.get("filings") or {}).get("recent") or {}
    forms = [(f, date.fromisoformat(t)) for f, t in zip(recent.get("form") or [], recent.get("filingDate") or [],
                                                         strict=False) if f and t]
    sic = str(d.get("sic") or "").strip()
    return Submission(int(d["cik"]), str(d.get("name") or ""), int(sic) if sic.isdigit() else None,
                      str(d.get("sicDescription") or ""), (str(d.get("fiscalYearEnd") or "") or None),
                      {str(t): str(x or "") for t, x in zip(tickers, exchanges, strict=False)}, forms)


def fy_end_month(mmdd: str | None) -> int | None:
    """The month a fiscal year ends, from SEC's MMDD. A 52/53-week year that ends in a month's first seven days
    (Disney's 1003, Home Depot's 0201, J&J's 0103) belongs to the month before."""
    if not mmdd or not re.fullmatch(r"\d{4}", mmdd):
        return None
    month, day = int(mmdd[:2]), int(mmdd[2:])
    if not 1 <= month <= 12:
        return None
    return (month - 2) % 12 + 1 if day <= 7 else month


@dataclass
class Findings:
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def compare(c: UsCompany, s: Submission, today: date) -> Findings:
    """One company against SEC's record of its CIK."""
    out = Findings()
    if s.cik != c.cik:
        out.blocking.append(f"{c.ticker}: SEC answered for CIK {s.cik}, not {c.cik}")
    if c.ticker not in s.listings:
        out.blocking.append(f"{c.ticker}: not among CIK {c.cik}'s tickers on SEC ({', '.join(s.listings) or 'none'})")
    foreign = sorted({f for f, _ in s.forms if f in FOREIGN})
    if foreign:
        out.blocking.append(f"{c.ticker}: files {', '.join(foreign)} (the slice reads 10-K filers only)")
    if not any(f in ("10-K", "10-K/A") for f, _ in s.forms):
        out.blocking.append(f"{c.ticker}: no 10-K among its recent SEC filings")
    sec_exchange = SEC_EXCHANGE.get(s.listings.get(c.ticker, ""), s.listings.get(c.ticker) or "none")
    if c.ticker in s.listings and sec_exchange != c.exchange:
        out.warnings.append(f"{c.ticker}: listed on {sec_exchange} at SEC, {c.exchange} in the YAML")
    if s.sic != c.sic:
        out.warnings.append(f"{c.ticker}: SIC {s.sic} ({s.sic_description}) at SEC, {c.sic} in the YAML")
    month = fy_end_month(s.fiscal_year_end)
    if month != c.fy_end_month:
        out.warnings.append(f"{c.ticker}: fiscal year ends {s.fiscal_year_end} at SEC (month {month}), "
                            f"month {c.fy_end_month} in the YAML")
    latest = max((d for f, d in s.forms if f in PERIODIC), default=None)
    if latest is None or latest < today - timedelta(days=RECENT_DAYS):
        out.warnings.append(f"{c.ticker}: no 10-K or 10-Q filed under CIK {c.cik} since "
                            f"{(today - timedelta(days=RECENT_DAYS)).isoformat()} (a new holding company's CIK?)")
    return out
