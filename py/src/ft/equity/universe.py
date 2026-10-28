"""The company master's sources and rules (Spec 02A §4; job 2A-04): parsers for the NSE and BSE reference files, the
slug and identifier-history rules, and the curated YAML (universe, peer groups, companies) with its validator.
Ported from the audit kit's `kit/universe.py` (read-only reference; not imported, CLAUDE.md §8). No database here:
`ft.jobs.tasks.equity_universe` calls these and writes the rows.

Files (all weekly, under the NSE posture, CLAUDE.md §1):
- the five Nifty lists on nsearchives (`Company Name, Industry, Symbol, Series, ISIN Code`; the file carries no date,
  so each fetch is a dated snapshot);
- `EQUITY_L.csv` (column names have leading spaces: stripped; Data Audit 02a §8);
- `symbolchange.csv` (a header row, then company, old symbol, new symbol, effective date);
- BSE's scrip list (JSON, browser headers; `SCRIP_CD` and `ISIN_NUMBER`).
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "equity"

NSE_ARCHIVE = "https://nsearchives.nseindia.com"
EQUITY_L_URL = f"{NSE_ARCHIVE}/content/equities/EQUITY_L.csv"
SYMBOLCHANGE_URL = f"{NSE_ARCHIVE}/content/equities/symbolchange.csv"
BSE_SCRIPS_URL = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
BSE_SCRIPS_PARAMS = {"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"}
# BSE's API wants a browser User-Agent (the shared client sends one) plus these two (Data Audit 02a §8).
BSE_HEADERS = {"Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/",
               "Accept": "application/json, text/plain, */*"}
NSE_HEADERS = {"Referer": "https://www.nseindia.com/"}

VALUATION_FAMILIES = ("operating", "lender", "insurer", "mixed")
ISIN = re.compile(r"^[A-Z0-9]{12}$")


class UniverseError(ValueError):
    """A reference file or a curated YAML that isn't in the shape the rules expect. The message says what."""


def nifty_url(file: str) -> str:
    return f"{NSE_ARCHIVE}/content/indices/{file}"


# ------------------------------------------------------------------------------------------------ the files
@dataclass(frozen=True)
class NiftyRow:
    name: str
    industry: str
    symbol: str
    series: str
    isin: str


@dataclass(frozen=True)
class EquityL:
    symbol: str
    name: str
    isin: str
    listing_date: date | None
    face_value: str


@dataclass(frozen=True)
class SymbolChange:
    company: str
    old: str
    new: str
    effective: date


@dataclass(frozen=True)
class BseScrip:
    code: str
    name: str
    isin: str
    group: str


def _text(body: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("latin-1")


def _looks_html(body: bytes) -> bool:
    head = body[:400].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html")) or b"<html" in head


def _dmy(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def parse_nifty_list(body: bytes) -> list[NiftyRow]:
    if _looks_html(body):
        raise UniverseError("an HTML page, not a Nifty list")
    reader = csv.DictReader(io.StringIO(_text(body)))
    reader.fieldnames = [(c or "").strip() for c in reader.fieldnames or []]
    need = {"Company Name", "Industry", "Symbol", "Series", "ISIN Code"}
    if not need <= set(reader.fieldnames):
        raise UniverseError(f"Nifty list columns are {reader.fieldnames}, expected {sorted(need)}")
    rows = []
    for r in reader:
        r = {k: (v or "").strip() for k, v in r.items()}
        if not r["Symbol"]:
            continue
        if not ISIN.match(r["ISIN Code"]):
            raise UniverseError(f"{r['Symbol']}: ISIN {r['ISIN Code']!r} isn't 12 letters and digits")
        rows.append(NiftyRow(r["Company Name"], r["Industry"], r["Symbol"], r["Series"], r["ISIN Code"]))
    return rows


def parse_equity_l(body: bytes) -> dict[str, EquityL]:
    """EQUITY_L by ISIN. Column names have leading spaces (` ISIN NUMBER`): stripped."""
    if _looks_html(body):
        raise UniverseError("an HTML page, not EQUITY_L")
    reader = csv.DictReader(io.StringIO(_text(body)))
    reader.fieldnames = [(c or "").strip() for c in reader.fieldnames or []]
    if not {"SYMBOL", "NAME OF COMPANY", "ISIN NUMBER"} <= set(reader.fieldnames):
        raise UniverseError(f"EQUITY_L columns are {reader.fieldnames}")
    out: dict[str, EquityL] = {}
    for r in reader:
        r = {k: (v or "").strip() for k, v in r.items()}
        if r["ISIN NUMBER"]:
            out[r["ISIN NUMBER"]] = EquityL(r["SYMBOL"], r["NAME OF COMPANY"], r["ISIN NUMBER"],
                                            _dmy(r.get("DATE OF LISTING", "")), r.get("FACE VALUE", ""))
    return out


def parse_symbolchange(body: bytes) -> list[SymbolChange]:
    """symbolchange.csv: company, old symbol, new symbol, effective date, with a header row. A row whose date doesn't
    parse is dropped (it can't place a range)."""
    if _looks_html(body):
        raise UniverseError("an HTML page, not symbolchange.csv")
    rows = list(csv.reader(io.StringIO(_text(body))))
    if rows and any("SYMBOL" in c.upper() for c in rows[0]):
        rows = rows[1:]
    out = []
    for r in rows:
        if len(r) < 4:
            continue
        company, old, new, when = (c.strip() for c in r[:4])
        d = _dmy(when)
        if old and new and d is not None:
            out.append(SymbolChange(company, old, new, d))
    return out


def parse_bse_scrips(body: bytes) -> tuple[dict[str, BseScrip], dict[str, list[str]]]:
    """BSE's active equity scrips by ISIN, and the ISINs that have more than one code (the lowest code is kept)."""
    try:
        data = json.loads(_text(body))
    except ValueError as e:
        raise UniverseError(f"BSE's scrip list isn't JSON: {e}") from None
    rows = data if isinstance(data, list) else (data.get("Table") or data.get("data") or [] if isinstance(data, dict) else [])
    if not rows:
        raise UniverseError("BSE's scrip list is empty or has an unexpected shape")
    by_isin: dict[str, list[BseScrip]] = {}
    for r in rows:
        isin = str(r.get("ISIN_NUMBER") or "").strip().upper()
        code = str(r.get("SCRIP_CD") or "").strip()
        if isin and code:
            by_isin.setdefault(isin, []).append(BseScrip(code, str(r.get("Scrip_Name") or "").strip(), isin,
                                                         str(r.get("GROUP") or "").strip()))
    out, dup = {}, {}
    for isin, scrips in by_isin.items():
        scrips.sort(key=lambda s: (len(s.code), s.code))
        out[isin] = scrips[0]
        if len(scrips) > 1:
            dup[isin] = [s.code for s in scrips]
    return out, dup


# ------------------------------------------------------------------------------------------------ slugs and history
def slugify(symbol: str) -> str:
    """The company's slug: the NSE symbol at creation, lower-cased, `&` as `-and-`, other characters as `-`
    (`M&M` becomes `m-and-m`). It doesn't change when the symbol does."""
    s = re.sub(r"[^a-z0-9]+", "-", symbol.lower().replace("&", "-and-")).strip("-")
    if not s:
        raise UniverseError(f"no slug for symbol {symbol!r}")
    return s


@dataclass(frozen=True)
class IdRange:
    """One owner of an identifier value over [start, end): a bound of None is open."""
    value: str
    start: date | None
    end: date | None


def symbol_history(symbol: str, changes: list[SymbolChange]) -> list[IdRange]:
    """The NSE symbols a company has had, oldest first, from symbolchange.csv: walks back from the current symbol
    through the changes whose new symbol it is. The earliest range starts open (the file says nothing before its
    first row); the current one ends open. A symbol's range ends where the next one starts."""
    out: list[IdRange] = []
    cur, end, seen = symbol, None, {symbol}
    while True:
        before = [c for c in changes if c.new == cur and c.old != cur and (end is None or c.effective <= end)]
        if not before:
            out.append(IdRange(cur, None, end))
            break
        step = max(before, key=lambda c: c.effective)
        out.append(IdRange(cur, step.effective, end))
        if step.old in seen:                         # a loop in the file: stop rather than follow it round
            break
        cur, end = step.old, step.effective
        seen.add(cur)
    return out[::-1]


# ------------------------------------------------------------------------------------------------ curated YAML
@dataclass(frozen=True)
class IndexFile:
    key: str
    file: str
    rows: int


@dataclass(frozen=True)
class UniverseConfig:
    index: str
    indices: dict[str, IndexFile]
    bse_not_listed: frozenset[str]


@dataclass(frozen=True)
class PeerGroup:
    key: str
    label: str
    damodaran_industry: str
    source_note: str


@dataclass(frozen=True)
class Curation:
    slug: str
    peer_group: str
    valuation_family: str
    family_note: str
    fy_end_month: int
    price_exchange: str


def _yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not data:
        raise UniverseError(f"{path.name}: expected a mapping")
    return data


def load_universe_config(path: Path | None = None) -> UniverseConfig:
    path = path or CONFIG_DIR / "universe.yaml"
    raw = _yaml(path)
    problems = []
    indices = {}
    for key, v in (raw.get("snapshot_indices") or {}).items():
        if not isinstance(v, dict) or not isinstance(v.get("file"), str) or not isinstance(v.get("rows"), int) \
                or v["rows"] <= 0:
            problems.append(f"snapshot_indices.{key}: needs a file and a positive row count")
        else:
            indices[key] = IndexFile(key, v["file"], v["rows"])
    index = raw.get("index")
    if index not in indices:
        problems.append(f"index {index!r} isn't one of snapshot_indices ({', '.join(indices)})")
    not_listed = raw.get("bse_not_listed") or []
    if not isinstance(not_listed, list) or not all(isinstance(s, str) for s in not_listed):
        problems.append("bse_not_listed must be a list of NSE symbols")
    if problems:
        raise UniverseError(f"{path.name}: " + "; ".join(problems))
    return UniverseConfig(index, indices, frozenset(not_listed))


def _text_field(raw: object, name: str, where: str, problems: list[str]) -> str:
    if not isinstance(raw, str) or not raw.strip():
        problems.append(f"{where}: needs {name}")
        return ""
    return raw.strip()


def load_peer_groups(path: Path | None = None) -> dict[str, PeerGroup]:
    path = path or CONFIG_DIR / "peer_groups.yaml"
    problems, out = [], {}
    for key, v in _yaml(path).items():
        where = f"peer group {key}"
        if not re.match(r"^[a-z0-9_-]+$", str(key)):
            problems.append(f"{where}: key must be lower case letters, digits, - or _")
        if not isinstance(v, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        out[key] = PeerGroup(key, _text_field(v.get("label"), "a label", where, problems),
                             _text_field(v.get("damodaran_industry"), "a Damodaran industry", where, problems),
                             _text_field(v.get("source_note"), "a source note", where, problems))
    if problems:
        raise UniverseError(f"{path.name}: " + "; ".join(problems))
    return out


def load_companies(peer_groups: dict[str, PeerGroup], path: Path | None = None) -> dict[str, Curation]:
    path = path or CONFIG_DIR / "companies.yaml"
    problems, out = [], {}
    for slug, v in _yaml(path).items():
        where = f"company {slug}"
        if not isinstance(v, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        group = v.get("peer_group")
        if group not in peer_groups:
            problems.append(f"{where}: peer_group {group!r} isn't in peer_groups.yaml")
        fam = v.get("valuation_family")
        if fam not in VALUATION_FAMILIES:
            problems.append(f"{where}: valuation_family must be one of {', '.join(VALUATION_FAMILIES)}, not {fam!r}")
        note = _text_field(v.get("family_note"), "a family_note", where, problems)
        month = v.get("fy_end_month", 3)
        if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
            problems.append(f"{where}: fy_end_month must be 1 to 12")
        exch = v.get("price_exchange", "NSE")
        if exch not in ("NSE", "BSE"):
            problems.append(f"{where}: price_exchange must be NSE or BSE")
        out[str(slug)] = Curation(str(slug), group, fam, note, month, exch)
    if problems:
        raise UniverseError(f"{path.name}: " + "; ".join(problems))
    return out
