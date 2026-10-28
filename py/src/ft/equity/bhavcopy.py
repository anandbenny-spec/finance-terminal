"""Exchange bhavcopies (Spec 02A §5.1; job 2A-06): URL builders and parsers for the four formats, ported from the audit
kit's `kit/bhavcopy.py` (read-only reference; not imported). No database and no network here.

NSE   UDiFF from 8 Jul 2024: nsearchives .../content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
      before that:            nsearchives .../content/historical/EQUITIES/YYYY/MMM/cmDDMMMYYYYbhav.csv.zip
BSE   UDiFF from 8 Jul 2024: www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV
      before that:            .../EQ_ISINCODE_DDMMYY.zip (a CSV inside)

Every format parses to the same `Bhav`: its exchange, the trade date found inside the file and one `PriceRow` per
equity row. A file counts only if it unzips, parses and carries one trade date (the caller then checks it is the date
asked for): BSE answers a missing day with HTTP 200 and a page, and an archive can serve the previous day's file for
a holiday, so the status code and the file name prove nothing (Data Audit 00 §6, 21 Oct 2026; 02a §8).
"""
from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

UDIFF_START = date(2024, 7, 8)
NSE_ARCHIVE = "https://nsearchives.nseindia.com"
BSE_DOWNLOAD = "https://www.bseindia.com/download/BhavCopy/Equity"
NSE_SERIES = ("EQ", "BE", "BZ")                 # main-board equity series kept (Spec 02A §5.1); EQ wins a tie
BSE_ORIGIN_HEADERS = {"Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/"}


class BhavError(ValueError):
    """A file that isn't a usable bhavcopy: a page, a changed layout, no trade date, two trade dates."""


@dataclass(frozen=True)
class PriceRow:
    symbol: str
    isin: str                  # "" where the format has none
    series: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    prev_close: Decimal | None
    volume: int | None
    traded_value: Decimal | None     # rupees
    trades: int | None
    code: str = ""             # BSE's scrip code (BSE files only): the key BSE rows are matched on


@dataclass(frozen=True)
class Bhav:
    exchange: str              # NSE or BSE, from the file itself
    fmt: str                   # nse_udiff, nse_old, bse_udiff, bse_old
    trade_date: date
    rows: list[PriceRow]


def url_for(exchange: str, d: date) -> tuple[str, str]:
    """(format, url) for the file of session `d`."""
    new = d >= UDIFF_START
    if exchange == "NSE":
        if new:
            return "nse_udiff", f"{NSE_ARCHIVE}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
        return "nse_old", (f"{NSE_ARCHIVE}/content/historical/EQUITIES/{d:%Y}/{d.strftime('%b').upper()}"
                           f"/cm{d.strftime('%d%b%Y').upper()}bhav.csv.zip")
    if exchange == "BSE":
        if new:
            return "bse_udiff", f"{BSE_DOWNLOAD}/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV"
        return "bse_old", f"{BSE_DOWNLOAD}/EQ_ISINCODE_{d:%d%m%y}.zip"
    raise ValueError(f"unknown exchange {exchange!r}")


def _dec(text: str | None) -> Decimal | None:
    t = (text or "").replace(",", "").strip()
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _int(text: str | None) -> int | None:
    v = _dec(text)
    return int(v) if v is not None else None


def _unwrap(body: bytes) -> str:
    head = body[:400].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        raise BhavError("an HTML page, not a bhavcopy")
    if body[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".csv")] or z.namelist()
                if not names:
                    raise BhavError("an empty zip")
                body = z.read(names[0])
        except zipfile.BadZipFile:
            raise BhavError("a damaged zip") from None
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("latin-1")


def _date(text: str, *formats: str) -> date | None:
    for f in formats:
        try:
            return datetime.strptime(text.strip(), f).date()
        except ValueError:
            continue
    return None


def parse_bhavcopy(body: bytes) -> Bhav:
    """Detect the format from the header and normalise. Raises BhavError for anything else."""
    text = _unwrap(body)
    reader = csv.reader(io.StringIO(text))
    try:
        header = [c.strip().upper() for c in next(reader)]
    except StopIteration:
        raise BhavError("an empty file") from None
    ix = {c: i for i, c in enumerate(header) if c}

    def get(row: list[str], name: str) -> str:
        i = ix.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    if {"TRADDT", "CLSPRIC", "SCTYSRS"} <= ix.keys():
        fmt_exchange = None
        parse_date = lambda r: _date(get(r, "TRADDT"), "%Y-%m-%d")   
        cols = {"sym": "TCKRSYMB", "isin": "ISIN", "series": "SCTYSRS", "o": "OPNPRIC", "h": "HGHPRIC", "lo": "LWPRIC", "c": "CLSPRIC",
                    "pc": "PRVSCLSGPRIC", "vol": "TTLTRADGVOL", "val": "TTLTRFVAL", "trades": "TTLNBOFTXSEXCTD",
                    "code": "FININSTRMID"}
    elif {"TIMESTAMP", "TOTTRDQTY", "SERIES"} <= ix.keys():
        fmt_exchange = "NSE"
        parse_date = lambda r: _date(get(r, "TIMESTAMP"), "%d-%b-%Y")
        cols = {"sym": "SYMBOL", "isin": "ISIN", "series": "SERIES", "o": "OPEN", "h": "HIGH", "lo": "LOW", "c": "CLOSE",
                    "pc": "PREVCLOSE", "vol": "TOTTRDQTY", "val": "TOTTRDVAL", "trades": "TOTALTRADES", "code": ""}
    elif {"SC_CODE", "ISIN_CODE", "SC_GROUP"} <= ix.keys():
        fmt_exchange = "BSE"
        parse_date = lambda r: _date(get(r, "TRADING_DATE"), "%d-%b-%y", "%d-%b-%Y") 
        cols = {"sym": "SC_NAME", "isin": "ISIN_CODE", "series": "SC_GROUP", "o": "OPEN", "h": "HIGH", "lo": "LOW", "c": "CLOSE",
                    "pc": "PREVCLOSE", "vol": "NO_OF_SHRS", "val": "NET_TURNOV", "trades": "NO_TRADES", "code": "SC_CODE"}
    else:
        raise BhavError(f"an unknown bhavcopy layout: {header[:10]}")

    rows, dates, exchange = [], set(), fmt_exchange
    for r in reader:
        if not r or not any(c.strip() for c in r):
            continue
        if fmt_exchange is None:                               # UDiFF: the file says whose it is, and keeps only cash
            exchange = exchange or get(r, "SRC").upper()
            if get(r, "SGMT").upper() not in ("", "CM"):
                continue
        d = parse_date(r)
        if d is None:
            raise BhavError("a row without a readable trade date")
        dates.add(d)
        close = _dec(get(r, cols["c"]))
        if close is None:
            continue
        rows.append(PriceRow(get(r, cols["sym"]), get(r, cols["isin"]).upper(), get(r, cols["series"]).upper(),
                             _dec(get(r, cols["o"])), _dec(get(r, cols["h"])), _dec(get(r, cols["lo"])), close,
                             _dec(get(r, cols["pc"])), _int(get(r, cols["vol"])), _dec(get(r, cols["val"])),
                             _int(get(r, cols["trades"])), get(r, cols["code"]) if cols["code"] else ""))
    if not dates:
        raise BhavError("no rows")
    if len(dates) > 1:
        raise BhavError(f"{len(dates)} different trade dates in one file ({min(dates)} to {max(dates)})")
    if exchange not in ("NSE", "BSE"):
        raise BhavError(f"unknown exchange {exchange!r} in the file")
    fmt = ("nse" if exchange == "NSE" else "bse") + ("_udiff" if fmt_exchange is None else "_old")
    return Bhav(exchange, fmt, dates.pop(), rows)


def pick_rows(bhav: Bhav) -> dict[str, PriceRow]:
    """The row to keep for each key. NSE: `isin or symbol`, only series EQ, BE and BZ (EQ first). BSE: the scrip code (one
    ISIN can have several codes, such as a test scrip beside the real one); every group is kept. Two rows of the same
    series for one key are a BhavError."""
    out: dict[str, PriceRow] = {}
    for r in bhav.rows:
        if bhav.exchange == "NSE":
            if r.series not in NSE_SERIES:
                continue
            key = r.isin or r.symbol
        else:
            key = r.code
            if not key:
                raise BhavError("a BSE row without a scrip code")
        old = out.get(key)
        if old is None:
            out[key] = r
        elif old.series == r.series:
            raise BhavError(f"{key} has two {r.series} rows")
        elif bhav.exchange == "NSE" and NSE_SERIES.index(r.series) < NSE_SERIES.index(old.series):
            out[key] = r
    return out
