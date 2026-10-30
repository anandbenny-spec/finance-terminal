"""Promoter holding: NSE's shareholding-pattern listing and the pattern XBRL (Spec 02A §5.5; job 2A-10; ported from the
audit kit's `kit/nse.py` `shareholding_filings` and `kit/xbrl.py` `promoter_pct`, `_is_promoter_group_member` and
`PROMOTER_PCT_TAGS`). The reader is `ft/equity/xbrl.py`, here in its **second use**: a pattern file is read by element
and dimension member (`xbrl.facts_on`), not by the results columns.

Reading what the stored rows mean (for 2A-15, the screener's promoter filter):

- **One row per stored document.** A revised pattern for the same quarter is a new document and a new row; the first is
  kept. **Readers take the latest filed per company and quarter**: for each `(entity_id, as_of)`, the row with the
  greatest `filed_at` (ties: the greatest `document_id`).
- `promoter_pct` is the filed figure (`ShareholdingAsAPercentageOfTotalNumberOfShares` on the promoter-and-promoter-group
  member, a fraction, stored × 100). It is never recomputed from the share counts: the filing's own denominator can
  differ from the total row (Infosys, Reliance and SBI file a percentage that is not promoter shares ÷ total shares), so
  `promoter_shares` and `total_shares` are the filed share counts of the same two rows, shown for what they are.
- A pattern with category rows but no promoter-group row (L&T, HDFC Bank, Federal Bank: no promoter) gives **0%**, with
  no promoter share count. A file with no category rows at all is not a shareholding pattern: it is refused, never 0%.

What the files hold (Data Audit 02a finding 8; the 20 saved patterns in the fixtures repo, read 30 Oct 2026): a plain
instance whose contexts carry `CategoryOfShareholdersAxis`; `MainI` is the quarter end; the header fact `DateOfReport`
says the same date; the percentages are fractions with four decimals (`0.7177`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from ft.equity import filings as fl
from ft.equity import xbrl as x

AXIS = "CategoryOfShareholdersAxis"
PROMOTER_MEMBER = "ShareholdingOfPromoterAndPromoterGroupMember"
TOTAL_MEMBER = "ShareholdingPatternMember"
PCT_TAGS = ("ShareholdingAsAPercentageOfTotalNumberOfShares",
            "ShareholdingAsAPercentageOfTotalNoOfSharesCalculatedAsPerSCRR1957AsAPercentageOfAPlusBPlusC2")
SHARE_TAGS = ("NumberOfShares", "NumberOfFullyPaidUpEquityShares")
LIST_PATH = "/api/corporate-share-holdings-master"
NO_PROMOTER = "no promoter-group rows in a shareholding-pattern filing (0%)"
FILE_STAMP = re.compile(r"_(\d{14})(?:_|\.|$)")


class ShareholdingError(ValueError):
    """A listing row or a file that can't be read as a shareholding pattern; the message says why. `kind` is `invalid`
    (not a pattern, or unreadable) or `range` (a promoter figure outside 0–100)."""

    def __init__(self, msg: str, kind: str = "invalid"):
        super().__init__(msg)
        self.kind = kind


@dataclass(frozen=True)
class Pattern:
    as_of: date
    promoter_pct: Decimal                  # 0–100
    promoter_shares: Decimal | None
    total_shares: Decimal | None
    found_by: str


@dataclass(frozen=True)
class Listed:
    """One pattern, as a listing row describes it."""
    symbol: str
    url: str
    file_name: str
    as_of: date
    filed_at: datetime                     # the submission time (India time), else the broadcast time, else the file name's
    nse_pct: Decimal | None                # NSE's own promoter figure (`pr_and_prgrp`), kept only for the cross-check


@dataclass
class Listing:
    patterns: list[Listed]
    no_link: int = 0                       # rows without an XBRL link: counted, not fetched
    unreadable: list[str] = field(default_factory=list)


def is_pattern(inst: x.Instance) -> bool:
    return any(AXIS in c.dims for c in inst.contexts.values())


def _is_promoter_group(member: str) -> bool:
    """The kit's rule for a promoter-group member that isn't the exact one."""
    m = member.lower()
    return "promoterandpromotergroup" in m and "nonpromoter" not in m and "relative" not in m and "government" not in m


def _promoter_fact(inst: x.Instance) -> x.Fact | None:
    """The promoter figure: the exact member under the first tag; else the kit's fallback (the second tag, or any member
    that is a promoter group), the fewest dimensions first. Two different figures at the best rank are refused."""
    best: list[tuple[tuple, x.Fact]] = []
    for f in inst.facts:
        if f.value is None or f.name not in PCT_TAGS:
            continue
        c = inst.contexts.get(f.context)
        if c is None or not any(_is_promoter_group(m) for m in c.dims.values()):
            continue
        exact = c.dims.get(AXIS) == PROMOTER_MEMBER and len(c.dims) == 1
        best.append(((0 if exact and f.name == PCT_TAGS[0] else 1, PCT_TAGS.index(f.name), len(c.dims)), f))
    if not best:
        return None
    best.sort(key=lambda t: t[0])
    top = [f for k, f in best if k == best[0][0]]
    if len({f.value for f in top}) > 1:
        raise ShareholdingError(f"the promoter figure is ambiguous: {sorted({str(f.value) for f in top})}")
    return top[0]


def _shares(inst: x.Instance, member: str) -> Decimal | None:
    for tag in SHARE_TAGS:
        found = {f.value for f in x.facts_on(inst, tag, AXIS, member)}
        if len(found) == 1:
            return found.pop()
    return None


def as_of_of(inst: x.Instance) -> date | None:
    """The quarter end: the header's `DateOfReport`, else the instant of `MainI`."""
    for f in inst.facts:
        if f.name == "DateOfReport":
            d = x.to_date(f.text)
            if d:
                return d
    c = inst.contexts.get("MainI")
    return c.instant if c is not None else None


def parse_pattern(body: bytes) -> Pattern:
    """A pattern file's quarter end, promoter % and share counts. Refuses a file that isn't an XBRL instance, one with
    no category rows (not a shareholding pattern: never a guessed 0%), one with no quarter end, and a promoter figure
    outside 0–100 (kind `range`)."""
    try:
        inst = x.parse(body)
    except x.XbrlError as e:
        raise ShareholdingError(str(e)) from None
    if not is_pattern(inst):
        raise ShareholdingError(f"not a shareholding pattern (no {AXIS} contexts)")
    as_of = as_of_of(inst)
    if as_of is None:
        raise ShareholdingError("the quarter end isn't in the file (no DateOfReport, no MainI)")
    f = _promoter_fact(inst)
    if f is None:
        pct, found_by, p_shares = Decimal(0), NO_PROMOTER, None
    else:
        pct, found_by, p_shares = f.value * 100, f.name, _shares(inst, PROMOTER_MEMBER)
    if not Decimal(0) <= pct <= Decimal(100):
        raise ShareholdingError(f"promoter holding {pct:f}% is outside 0–100", "range")
    return Pattern(as_of, pct, p_shares, _shares(inst, TOTAL_MEMBER), found_by)


# ---------------------------------------------------------------------------------------- the listing
def _time(raw) -> datetime | None:
    """`16-Jul-2026 07:24:34` (India time), or a date alone (midnight)."""
    t = fl.parse_time(raw)
    if t is not None:
        return t
    d = fl.parse_date(raw)
    return datetime(d.year, d.month, d.day, tzinfo=fl.INDIA) if d else None


def _stamp(file_name: str) -> datetime | None:
    """The time in a file name `SHP_1694620_16072026072434_WEB.xml` (ddmmyyyyHHMMSS, India time)."""
    m = FILE_STAMP.search(file_name)
    try:
        return datetime.strptime(m.group(1), "%d%m%Y%H%M%S").replace(tzinfo=fl.INDIA) if m else None
    except ValueError:
        return None


def _pct(raw) -> Decimal | None:
    try:
        return Decimal(str(raw).replace("%", "").replace(",", "").strip())
    except Exception:       # noqa: BLE001 - NSE's field is free text: anything unreadable is simply absent
        return None


def parse_master(rows: list[dict], symbol: str) -> Listing:
    """NSE's `corporate-share-holdings-master` rows: each row's XBRL link, quarter end (`date`), submission time and
    NSE's own promoter figure (`pr_and_prgrp`). A row without a link is counted; a row with no readable date or an
    unusable link is named; the same file listed twice is one pattern."""
    out, seen = Listing([]), set()
    for r in rows:
        link = r.get("xbrl")
        if not isinstance(link, str) or not link.strip() or link.strip() in ("-", "NA"):
            out.no_link += 1
            continue
        link = link.strip()
        name = fl._file_name(link)
        if not name.lower().endswith(".xml"):
            out.unreadable.append(f"{symbol}: {link[-60:]} (not an .xml link)")
            continue
        as_of = fl.parse_date(r.get("date"))
        if as_of is None:
            out.unreadable.append(f"{symbol}: {name} (quarter end unreadable: {str(r.get('date'))[:20]!r})")
            continue
        filed = _time(r.get("submissionDate")) or _time(r.get("broadcastDate")) or _stamp(name)
        if filed is None:
            out.unreadable.append(f"{symbol}: {name} (no submission time)")
            continue
        if name in seen:
            continue
        seen.add(name)
        out.patterns.append(Listed(symbol, link, name, as_of, filed, _pct(r.get("pr_and_prgrp"))))
    return out


def quarter_ends(lo: date, hi: date) -> list[date]:
    """Calendar quarter ends in `lo..hi` (SEBI's patterns are for 31 Mar, 30 Jun, 30 Sep and 31 Dec)."""
    out = []
    for y in range(lo.year, hi.year + 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(y, m, d)
            if lo <= q <= hi:
                out.append(q)
    return out


LATE = timedelta(days=45)
