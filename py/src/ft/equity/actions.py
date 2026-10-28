"""Corporate actions (Spec 02A §5.2; job 2A-08): the exchanges' free-text subjects parsed into a kind and its numbers,
each kind's price factor, the plausibility check a factor passes before it is applied, and the reviewed releases in
`py/config/equity/corporate_action_review.yaml`. The subject parser is ported from the audit kit's `kit/nse.py`
`classify_action` (read-only reference; not imported) and extended to every subject in the saved replies. No database
and no network here.

Factors (Data Audit 00 §6, 22 Oct 2026, F13; Spec 02A §5.2), as `price_factor` = adjusted ÷ raw for trade dates
before the ex-date (below 1 lowers earlier prices):
- split, face value X → Y: Y ÷ X; consolidation (Y > X): the same formula, the inverse of a split;
- bonus A:B (A new shares for every B held): B ÷ (A + B);
- demerger: the ex-date's special pre-open price (the bhavcopy's OPEN on the ex-date) ÷ the previous session's close;
- rights M:N (M new shares for every N held) at issue price R: TERP ÷ the previous close, with
  TERP = (P × N + R × M) ÷ (N + M) and P the previous close;
- dividend, merger, buyback, other: no factor (2A's returns are price returns: Spec 02A §10 decision 10).
Several actions on one ex-date keep a factor each; the plausibility check tests their product.

Factors are stored to 12 decimal places: exact for every split and for bonuses such as 1:1 and 4:1, and within 5e-13 of
the ratio for the ones that don't terminate (NTPC's 1:5 bonus is 5/6). Decided by Claude (standing delegation, 29 Oct
2026): equity.price_adjusted rounds the product of a company's factors to 12 places, so 12 is the finest that keeps
its result exact for one factor; 6 places (the view comment's assumption) would put a 1:5 bonus 4e-7 off its ratio.

The check (Spec 02A §5.2): with c0 the previous session's close and c1 the ex-date's close, the adjusted move
ln(c1 ÷ (c0 × factor)) is within ± ln 1.2 (NSE's widest price band), and for a factor below 0.8 it is also closer to
zero than the unadjusted move ln(c1 ÷ c0).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "equity"
REVIEW_FILE = CONFIG_DIR / "corporate_action_review.yaml"

KINDS = ("split", "bonus", "consolidation", "dividend", "rights", "demerger", "merger", "buyback", "other")
PRICE_KINDS = ("split", "bonus", "consolidation", "demerger", "rights")    # the kinds with a factor
RATIO_KINDS = ("split", "bonus", "consolidation")                          # factor from the record's own numbers
PLACES = Decimal("1e-12")
BAND = Decimal("1.2").ln()                         # NSE's widest band, ±20%, as a log move: 0.1823…
CLOSER_BELOW = Decimal("0.8")
SLUG_RE = re.compile(r"^[a-z0-9-]+$")
EVENT_SLUG_RE = re.compile(r"^[a-z0-9-]+-(?:split|bonus|consolidation|dividend|rights|demerger|merger|buyback|other)"
                           r"-(\d{4}-\d{2}-\d{2})(?:-\d+)?$")

_CUR = r"(?:rs|re|inr|₹)"
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_MONEY = re.compile(_CUR + r"\.?\s*-?\s*" + _NUM, re.IGNORECASE)
_FROM_TO = re.compile(r"from\s*" + _CUR + r"?\.?\s*(\d+(?:\.\d+)?).*?\bto\b\s*" + _CUR + r"?\.?\s*(\d+(?:\.\d+)?)",
                      re.IGNORECASE | re.DOTALL)
_FV_TO_FV = re.compile(_CUR + r"\.?\s*(\d+(?:\.\d+)?)[^0-9]*?\bto\b\s*" + _CUR + r"\.?\s*(\d+(?:\.\d+)?)", re.IGNORECASE | re.DOTALL)
_BONUS_RATIO = re.compile(r"bonus\D{0,30}?(\d+)\s*:\s*(\d+)", re.IGNORECASE)
_RIGHTS_RATIO = re.compile(r"rights?\D{0,30}?(\d+)\s*:\s*(\d+)", re.IGNORECASE)
_PREMIUM = re.compile(r"premium\s*(?:of\s*)?" + _CUR + r"?\.?\s*-?\s*" + _NUM, re.IGNORECASE)
_AT_PRICE = re.compile(r"@\s*" + _CUR + r"\.?\s*" + _NUM, re.IGNORECASE)
_AT_PAR = re.compile(r"@\s*par\b|\bat\s+par\b", re.IGNORECASE)

# The kinds that change a price, in the order a subject's kind is read (the first found names it).
_PRICE_MARKS = (
    ("split", re.compile(r"\bsplit\b|sub[- ]?division", re.IGNORECASE)),
    ("consolidation", re.compile(r"consolidat", re.IGNORECASE)),
    ("bonus", re.compile(r"\bbonus\b", re.IGNORECASE)),
    ("rights", re.compile(r"\brights?\b", re.IGNORECASE)),
    ("demerger", re.compile(r"de-?merger|spin[- ]?off", re.IGNORECASE)),
)
_MERGER = re.compile(r"amalgamation|(?<!de)(?<!de-)merger", re.IGNORECASE)
_BUYBACK = re.compile(r"buy[- ]?back", re.IGNORECASE)
_DIVIDEND = re.compile(r"dividend|\bdiv\b", re.IGNORECASE)
# Entries in the record that change no price: meetings and the like.
_NOTICE = re.compile(r"general meeting|\bagm\b|\begm\b|book closure|interest payment|redemption", re.IGNORECASE)
# Entries that may change a price in a way none of the methods covers (a scheme of arrangement with cash, a capital
# reduction): held for a reviewed method (2A-08's stop condition).
_RESTRUCTURING = re.compile(r"arrangement|reduction|return of capital|capital repayment|restructur|reorgani[sz]|"
                            r"hive[- ]?off|distribution", re.IGNORECASE)


@dataclass(frozen=True)
class Parsed:
    """A subject read into the schema's kind (`equity.corporate_action.kind`) and the numbers it states."""
    kind: str
    ratio_new: Decimal | None = None          # bonus A, rights M: new shares
    ratio_old: Decimal | None = None          # bonus B, rights N: shares held
    face_value_old: Decimal | None = None
    face_value_new: Decimal | None = None
    dividend_per_share: Decimal | None = None
    rights_price: Decimal | None = None       # the issue price per share (face value plus premium)
    problem: str | None = None                # why the numbers this kind needs can't be read
    restructuring: bool = False               # kind "other" that may change a price: held for a reviewed method
    notice: bool = False                      # kind "other" that is a meeting or similar: no price effect
    several: tuple[str, ...] = ()             # more than one price-changing kind named in one subject

    @property
    def understood(self) -> bool:
        """Read in full: a known kind with every number it needs (an "other" only if it's a notice)."""
        if self.problem:
            return False
        return self.kind != "other" or self.notice


def clean(subject: str) -> str:
    """The subject with its whitespace collapsed: the event's title and the key two replies are compared on."""
    return " ".join((subject or "").split())


def _dec(s: str) -> Decimal | None:
    try:
        v = Decimal(s.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    return v if v.is_finite() else None


def _amounts(s: str) -> list[Decimal]:
    """Every rupee amount per share in a dividend subject (an interim plus a special dividend are two), leaving out
    face values and premiums."""
    out = []
    for m in _MONEY.finditer(s):
        before = s[max(0, m.start() - 16):m.start()].lower()
        if "face value" in before or "premium" in before or "fv" in before.split():
            continue
        v = _dec(m.group(1))
        if v is not None and v > 0:
            out.append(v)
    return out


def parse_subject(subject: str, face_value: Decimal | None = None) -> Parsed:
    """Read a subject from NSE's or BSE's record. `face_value` is the record's own face value field (NSE's `faceVal`):
    a rights issue "@ Premium Rs P" is issued at the face value plus P."""
    s = clean(subject)
    marks = [k for k, rx in _PRICE_MARKS if rx.search(s)]
    if marks:
        kinds = sorted(set(marks), key=marks.index)
        if "split" in kinds and "consolidation" in kinds:          # "consolidation ... split" wording: one action
            kinds.remove("split")
        kind = kinds[0]
        several = tuple(kinds) if len(kinds) > 1 else ()
        multi = (f"the subject holds more than one price-changing action ({' and '.join(kinds)})"
                 if several else None)
        dps = None
        if _DIVIDEND.search(s):
            amounts = _amounts(s)
            dps = sum(amounts) if amounts else None
        if kind in ("split", "consolidation"):
            p = _face_values(s, kind)
            return Parsed(p.kind, face_value_old=p.face_value_old, face_value_new=p.face_value_new,
                          dividend_per_share=dps, problem=multi or p.problem, several=several)
        if kind == "bonus":
            m = _BONUS_RATIO.search(s)
            a, b = (_dec(m.group(1)), _dec(m.group(2))) if m else (None, None)
            problem = None if a and b and a > 0 and b > 0 else "no bonus ratio A:B in the subject"
            return Parsed("bonus", ratio_new=a if not problem else None, ratio_old=b if not problem else None,
                          dividend_per_share=dps, problem=multi or problem, several=several)
        if kind == "rights":
            m = _RIGHTS_RATIO.search(s)
            new, held = (_dec(m.group(1)), _dec(m.group(2))) if m else (None, None)
            if not (new and held and new > 0 and held > 0):
                return Parsed("rights", dividend_per_share=dps, problem=multi or "no rights ratio M:N in the subject",
                              several=several)
            price, problem = _rights_price(s, face_value)
            return Parsed("rights", ratio_new=new, ratio_old=held, rights_price=price, dividend_per_share=dps,
                          problem=multi or problem, several=several)
        return Parsed("demerger", dividend_per_share=dps, problem=multi, several=several)
    if _MERGER.search(s):
        return Parsed("merger")
    if _BUYBACK.search(s):
        return Parsed("buyback")
    if _DIVIDEND.search(s):
        amounts = _amounts(s)
        if not amounts:
            return Parsed("dividend", problem="no amount per share in the subject")
        return Parsed("dividend", dividend_per_share=sum(amounts))
    if _RESTRUCTURING.search(s):
        return Parsed("other", restructuring=True)
    return Parsed("other", notice=bool(_NOTICE.search(s)))


def _face_values(s: str, kind: str) -> Parsed:
    m = _FROM_TO.search(s) or _FV_TO_FV.search(s)
    old, new = (_dec(m.group(1)), _dec(m.group(2))) if m else (None, None)
    if not (old and new and old > 0 and new > 0):
        return Parsed(kind, problem="no face values (from X to Y) in the subject")
    if old == new:
        return Parsed(kind, face_value_old=old, face_value_new=new, problem="the face value doesn't change")
    if kind == "consolidation" and new < old:
        return Parsed(kind, face_value_old=old, face_value_new=new,
                      problem="a consolidation whose face value falls: the subject contradicts itself")
    return Parsed("consolidation" if new > old else "split", face_value_old=old, face_value_new=new)


def _rights_price(s: str, face_value: Decimal | None) -> tuple[Decimal | None, str | None]:
    m = _PREMIUM.search(s)
    if m:
        premium = _dec(m.group(1))
        if premium is None:
            return None, "the rights premium can't be read"
        if not face_value or face_value <= 0:
            return None, "a rights issue at a premium, and the record has no face value to add it to"
        return face_value + premium, None
    m = _AT_PRICE.search(s)
    if m and _dec(m.group(1)):
        return _dec(m.group(1)), None
    if _AT_PAR.search(s):
        if not face_value or face_value <= 0:
            return None, "a rights issue at par, and the record has no face value"
        return face_value, None
    return None, "no rights price in the subject"


# ---------------------------------------------------------------------------------------------- factors
def q(x: Decimal) -> Decimal:
    """A factor as stored: 12 decimal places (half-up)."""
    return x.quantize(PLACES, rounding="ROUND_HALF_UP")


def ratio_factor(kind: str, *, ratio_new=None, ratio_old=None, face_value_old=None, face_value_new=None) -> Decimal:
    """The factor of a split, consolidation or bonus from the record's own numbers, as stored."""
    with localcontext() as c:
        c.prec = 50
        if kind in ("split", "consolidation"):
            return q(Decimal(face_value_new) / Decimal(face_value_old))
        if kind == "bonus":
            return q(Decimal(ratio_old) / (Decimal(ratio_new) + Decimal(ratio_old)))
    raise ValueError(f"no ratio factor for {kind}")


def preopen_factor(open1: Decimal, c0: Decimal) -> Decimal:
    """A demerger's factor: the ex-date's pre-open price over the previous session's close."""
    with localcontext() as c:
        c.prec = 50
        return q(Decimal(open1) / Decimal(c0))


def terp(c0: Decimal, new: Decimal, held: Decimal, price: Decimal) -> Decimal:
    """The theoretical ex-rights price for M new shares per N held at `price`, from the cum-rights close c0."""
    with localcontext() as c:
        c.prec = 50
        return (Decimal(c0) * Decimal(held) + Decimal(price) * Decimal(new)) / (Decimal(held) + Decimal(new))


def terp_factor(c0: Decimal, new: Decimal, held: Decimal, price: Decimal) -> Decimal:
    with localcontext() as c:
        c.prec = 50
        return q(terp(c0, new, held, price) / Decimal(c0))


def product(factors) -> Decimal:
    out = Decimal(1)
    with localcontext() as c:
        c.prec = 50
        for f in factors:
            out *= Decimal(f)
    return out


@dataclass(frozen=True)
class Plausibility:
    passed: bool
    adjusted_move: Decimal          # ln(c1 / (c0 × factor))
    unadjusted_move: Decimal        # ln(c1 / c0)
    reason: str | None = None


def plausible(c0: Decimal, c1: Decimal, factor: Decimal) -> Plausibility:
    """Spec 02A §5.2's check of a factor (or of the product of one ex-date's factors) against the closes either side."""
    with localcontext() as c:
        c.prec = 40
        adjusted = (Decimal(c1) / (Decimal(c0) * Decimal(factor))).ln()
        unadjusted = (Decimal(c1) / Decimal(c0)).ln()
    if abs(adjusted) > BAND:
        return Plausibility(False, adjusted, unadjusted,
                            f"the adjusted move {pct(adjusted)} is outside NSE's ±20% band "
                            f"(±{float(BAND) * 100:.2f}% as a log move)")
    if Decimal(factor) < CLOSER_BELOW and not abs(adjusted) < abs(unadjusted):
        return Plausibility(False, adjusted, unadjusted,
                            f"a factor below 0.8 should bring the move closer to zero, but the adjusted move "
                            f"{pct(adjusted)} isn't closer than the unadjusted {pct(unadjusted)}")
    return Plausibility(True, adjusted, unadjusted)


def pct(log_move: Decimal) -> str:
    """A log move as a signed percentage with two decimals (for reasons and check details)."""
    v = float(log_move) * 100
    return f"{v:+.2f}%"


def num(x: Decimal | None) -> str | None:
    """A Decimal for a jsonb evidence field: a string, so a re-run compares equal."""
    return None if x is None else format(Decimal(x).normalize(), "f")


def move(x: Decimal) -> float:
    return round(float(x), 6)


# ---------------------------------------------------------------------------------------------- reviews
@dataclass(frozen=True)
class Release:
    """A held factor released by review: applied with method `reviewed` and this factor."""
    event: str
    factor: Decimal
    evidence: str
    reviewer: str
    reviewed_on: date


@dataclass(frozen=True)
class JumpReview:
    """A session whose close moved more than 15% with no applied factor, reviewed as a real move."""
    company: str
    date: date
    verdict: str
    note: str
    reviewer: str
    reviewed_on: date


@dataclass(frozen=True)
class Reviews:
    releases: dict[str, Release] = field(default_factory=dict)
    jumps: dict[tuple[str, date], JumpReview] = field(default_factory=dict)


def event_date(slug: str) -> date | None:
    """The ex-date an event slug ends with (`<company>-<kind>-<yyyy-mm-dd>[-n]`)."""
    m = EVENT_SLUG_RE.match(slug)
    return date.fromisoformat(m.group(1)) if m else None


def _as_date(v, where: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise ValueError(f"{where}: {v!r} isn't a date (yyyy-mm-dd)") from None


def _text(entry: dict, key: str, where: str) -> str:
    v = entry.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{where}: {key} is missing")
    return v.strip()


def load_reviews(path: Path | None = None) -> Reviews:
    """The reviewed releases and jumps. A malformed entry fails the run: a review that silently didn't apply would
    leave a company's prices held back with nobody knowing why."""
    path = path or REVIEW_FILE
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict) or set(raw) - {"releases", "jumps"}:
        raise ValueError(f"{path.name}: only `releases` and `jumps` are allowed at the top")
    releases: dict[str, Release] = {}
    for i, e in enumerate(raw.get("releases") or []):
        where = f"{path.name} releases[{i}]"
        if not isinstance(e, dict) or set(e) - {"event", "factor", "evidence", "reviewer", "reviewed_on"}:
            raise ValueError(f"{where}: keys are event, factor, evidence, reviewer, reviewed_on")
        slug = _text(e, "event", where)
        if event_date(slug) is None:
            raise ValueError(f"{where}: {slug!r} isn't an event slug (<company>-<kind>-<yyyy-mm-dd>)")
        factor = _dec(str(e.get("factor")))
        if factor is None or factor <= 0:
            raise ValueError(f"{where}: factor must be a number above 0")
        if slug in releases:
            raise ValueError(f"{where}: {slug} is released twice")
        releases[slug] = Release(slug, q(factor), _text(e, "evidence", where), _text(e, "reviewer", where),
                                 _as_date(e.get("reviewed_on"), where))
    jumps: dict[tuple[str, date], JumpReview] = {}
    for i, e in enumerate(raw.get("jumps") or []):
        where = f"{path.name} jumps[{i}]"
        if not isinstance(e, dict) or set(e) - {"company", "date", "verdict", "note", "reviewer", "reviewed_on"}:
            raise ValueError(f"{where}: keys are company, date, verdict, note, reviewer, reviewed_on")
        company = _text(e, "company", where)
        if not SLUG_RE.match(company):
            raise ValueError(f"{where}: {company!r} isn't a company slug")
        verdict = _text(e, "verdict", where)
        if verdict != "real move":
            raise ValueError(f"{where}: the only verdict recorded here is 'real move' (a missing action is a stop "
                             f"for Anand, not a review entry)")
        d = _as_date(e.get("date"), where)
        if (company, d) in jumps:
            raise ValueError(f"{where}: {company} on {d} is reviewed twice")
        jumps[(company, d)] = JumpReview(company, d, verdict, _text(e, "note", where), _text(e, "reviewer", where),
                                         _as_date(e.get("reviewed_on"), where))
    return Reviews(releases, jumps)
