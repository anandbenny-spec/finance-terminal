"""equity.corporate_actions: every universe company's corporate actions from 1 Jan 2019 as dated events, each
price-changing action's adjustment factor computed by its method, checked against the prices either side of its
ex-date and applied, or held with its reason (Spec 02A §5.2; job 2A-08). `equity.price_adjusted` multiplies the applied
factors and nulls a company's earlier adjusted closes while one of its factors is held.

Fetch, per universe company: NSE's corporate-actions API (`/api/corporates-corporateActions`, through the impersonating
client, the homepage first for its cookies), each reply stored as a raw file. A weekly run reads ex-dates from 400 days
back to 90 days ahead; a backfill reads `--since` to `--until` (floored at 1 Jan 2019; with no `--until`, 90 days
ahead). BSE's endpoint (`DefaultData/w`, windows of 360 days or less) is asked only for a company NSE returns nothing
for and has never served (MEESHO's case, Data Audit 02a finding 12): BSE fills in where NSE has nothing, and a company
NSE serves is never sent to BSE.

Load (one transaction):
1. **Events.** One `core.event` per action (type `corporate_action`, published, source `nse` or `bse`; slug
   `<company-slug>-<kind>-<ex-date>`, with `-2`, `-3` … for a second action of one kind on one ex-date; title the
   subject; `starts_on` the ex-date) and its `equity.corporate_action` row (the subject verbatim; ratios, face values,
   dividend per share and the rights price read from it: `ft/equity/actions.py`). An action is matched to a stored one
   by company, exchange, ex-date, kind and subject (whitespace and case aside), so a re-run writes nothing. Status:
   `announced` before the ex-date, `effective` from it, `withdrawn` for an announced action the exchange no longer
   lists in a window this run read.
2. **Factors**, for every effective split, bonus, consolidation, demerger and rights issue of the universe (and a
   restructuring no method covers, which is always held): computed by its method once the company's prices reach the
   ex-date, checked (Spec 02A §5.2: the product of one ex-date's factors), and written as `applied` with the evidence,
   or `held` with the reason and the evidence. A release in `py/config/equity/corporate_action_review.yaml` applies a
   held one with `method = reviewed`. Every run recomputes every factor and writes only what changed (held factors are
   re-checked as prices arrive; a review takes effect on the next run). Dividends, mergers and buybacks get no factor.

Checks, B blocking, W warning:
- (B) `lists_read`: every company's list was read (a reply that isn't JSON, or refused after the retries, fails);
- (B) `ratios_exact`: every split, bonus and consolidation factor equals its ratio exactly (12 places);
- (B) `applied_checked`: no factor is applied without passing the plausibility check or a review entry;
- (W) `not_applied`: the held factors with their reasons; the window's mergers, buybacks and unrecognised entries;
  the actions waiting for their ex-date's prices; dividends and meeting notices counted;
- (W) `unexplained_jumps`: every session since 2019 where a universe company's |ln(close ÷ previous close)| > 0.15
  with no applied factor that day, unless the review file records it as a real move;
- (W) `undated_rows`: a listed row without a readable ex-date, named;
- (W) `reviews_matched`: a release in the review file whose event isn't stored (or has no factor), named.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ft import calendars
from ft.equity import actions as ac
from ft.equity import bhavcopy as bh
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register

INDIA = ZoneInfo("Asia/Kolkata")
CALENDAR = "xnse"
FLOOR = date(2019, 1, 1)
BACK, AHEAD = timedelta(days=400), timedelta(days=90)
BSE_CHUNK = timedelta(days=360)                 # BSE's APIs refuse windows over 12 months (Data Audit 02a finding 24)
JUMP = Decimal("0.15")
SHOWN = 12
SHOWN_JUMPS = 60

NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/corporates-corporateActions"
NSE_HEADERS = {"Referer": NSE_HOME, "Accept": "application/json, text/plain, */*"}
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"
BSE_HEADERS = {**bh.BSE_ORIGIN_HEADERS, "Accept": "application/json, text/plain, */*"}

METHOD = {"split": "ratio", "bonus": "ratio", "consolidation": "ratio", "demerger": "special_preopen",
          "rights": "terp", "other": "special_preopen"}


def _names(items: list[str], shown: int = SHOWN) -> str:
    return "; ".join(items[:shown]) + (f"; and {len(items) - shown} more" if len(items) > shown else "")


def parse_day(s) -> date | None:
    """An exchange date: NSE's 20-Jul-2023, BSE's 20 Jul 2023, 20230720, 2023-07-20T00:00:00 and the like."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s == "-":
        return None
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y%m%d", "%d-%b-%y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    for cut in ("T", " "):
        if cut in s:
            head = s.split(cut, 1)[0]
            if head != s:
                d = parse_day(head)
                if d:
                    return d
    return None


def _face_value(v) -> Decimal | None:
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


@dataclass
class Company:
    entity_id: int
    slug: str
    nse_symbol: str | None
    bse_code: str | None
    price_exchange: str
    nse_served: bool                    # an NSE-sourced action is stored for it


@dataclass(frozen=True)
class Row:
    """One action as the exchange lists it."""
    entity_id: int
    exchange: str                       # NSE or BSE
    ex_date: date
    record_date: date | None
    subject: str                        # verbatim
    face_value: Decimal | None
    raw_file_id: int


@dataclass
class Fetched:
    window: tuple[date, date]
    today: date
    companies: dict[int, Company]
    rows: list[Row]
    read: dict[int, set[str]]           # entity_id -> exchanges whose list this run read in full
    failed: list[str]
    undated: list[str]
    reviews: ac.Reviews
    note: str = ""


@dataclass
class Want:
    """A factor row as this run computes it."""
    event_id: int
    entity_id: int
    ex_date: date
    price_factor: Decimal | None
    method: str
    status: str
    reason: str | None
    evidence: dict = field(default_factory=dict)

    def key(self) -> tuple:
        return (self.entity_id, self.ex_date, self.price_factor, self.method, self.status, self.reason,
                json.loads(json.dumps(self.evidence)))


@register
class EquityCorporateActions(Task):
    key = "equity.corporate_actions"

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.corporate_action")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f, db = ctx.fetcher, ctx.fetcher.db
        reviews = ac.load_reviews()
        today = ctx.now.astimezone(INDIA).date()
        start = max(ctx.since, FLOOR) if ctx.since else max(today - BACK, FLOOR)
        end = ctx.until or (today + AHEAD)
        companies = {r[0]: Company(*r) for r in db.execute(
            """select c.entity_id, e.slug, c.nse_symbol, c.bse_code, c.price_exchange,
                      exists (select 1 from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                               where ev.entity_id = c.entity_id and ca.exchange = 'NSE')
                 from equity.company c join core.entity e on e.id = c.entity_id order by e.slug""").fetchall()}
        out = Fetched((start, end), today, companies, [], {}, [], [], reviews)
        if start > end:
            out.note = f"an empty window ({start} to {end})"
            return out
        self._warm(f)
        for c in companies.values():
            seen: set[tuple[date, str]] = set()
            got_nse = None
            if c.nse_symbol:
                try:
                    got_nse, raw_id = self._nse(f, c.nse_symbol, start, end)
                except (FetchError, ValueError) as e:
                    out.failed.append(f"{c.slug} (NSE): {str(e)[:120]}")
                    continue
                out.read.setdefault(c.entity_id, set()).add("NSE")
                for r in got_nse:
                    self._keep(out, c, "NSE", raw_id, r.get("exDate"), r.get("recDate"), r.get("subject"),
                               _face_value(r.get("faceVal")), seen)
            if (not got_nse) and not c.nse_served and c.bse_code:
                try:
                    for rows, raw_id in self._bse(f, c.bse_code, start, end):
                        for r in rows:
                            g = _getter(r)
                            self._keep(out, c, "BSE", raw_id, g("Ex_date", "Ex_Date", "exdate"),
                                       g("RD_Date", "record_date", "RecordDate"), g("Purpose", "purpose", "subject"),
                                       _face_value(g("Face_Value", "FaceValue", "face_value")), seen)
                except (FetchError, ValueError) as e:
                    out.failed.append(f"{c.slug} (BSE): {str(e)[:120]}")
                    continue
                out.read.setdefault(c.entity_id, set()).add("BSE")
            elif not c.nse_symbol:
                out.failed.append(f"{c.slug}: no NSE symbol, and BSE can't be asked (no scrip code, or NSE has "
                                  f"served it before)")
        return out

    @staticmethod
    def _keep(out: Fetched, c: Company, exchange: str, raw_id: int, ex, rec, subject, fv, seen) -> None:
        subject = "" if subject is None else str(subject)
        ex_date = parse_day(ex)
        if ex_date is None:
            out.undated.append(f"{c.slug} ({exchange}): {ac.clean(subject) or 'no subject'} (ex-date {ex!r})")
            return
        start, end = out.window
        if not (start <= ex_date <= end) or ex_date < FLOOR:
            return
        k = (ex_date, ac.clean(subject).lower())
        if k in seen:                                   # the same entry listed twice (another series, say)
            return
        seen.add(k)
        out.rows.append(Row(c.entity_id, exchange, ex_date, parse_day(rec), subject, fv, raw_id))

    @staticmethod
    def _warm(f) -> None:
        """NSE's API wants the cookies its homepage sets (the page itself isn't stored: it's not data we keep)."""
        try:
            f.http.get(NSE_HOME, record_url=NSE_HOME, stats=f.counts)
        except FetchError:
            pass                                        # the list calls will say if the cookies were needed

    def _nse(self, f, symbol: str, start: date, end: date) -> tuple[list[dict], int]:
        params = {"index": "equities", "symbol": symbol, "from_date": start.strftime("%d-%m-%Y"),
                  "to_date": end.strftime("%d-%m-%Y")}
        for attempt in (1, 2):
            try:
                got = f.get(NSE_API, source="nse", params=params, headers=NSE_HEADERS, conditional=False,
                            media_type="application/json", ext="json")
                body = got.body if got.body is not None else f.read(got.raw_file_id)
                return _rows(json.loads(body), "NSE"), got.raw_file_id
            except FetchError as e:
                if attempt == 2 or e.status not in (401, 403):
                    raise
            except ValueError:
                if attempt == 2:
                    raise ValueError("the reply isn't a JSON list of actions") from None
            self._warm(f)
        raise AssertionError("unreachable")

    @staticmethod
    def _bse(f, code: str, start: date, end: date):
        lo = start
        while lo <= end:
            hi = min(end, lo + BSE_CHUNK)
            params = {"Fdate": lo.strftime("%Y%m%d"), "Purposecode": "", "TDate": hi.strftime("%Y%m%d"),
                      "ddlcategorys": "E", "ddlindustrys": "", "scripcode": code, "segment": "0", "strSearch": "D"}
            got = f.get(BSE_API, source="bse", params=params, headers=BSE_HEADERS, conditional=False,
                        media_type="application/json", ext="json")
            body = got.body if got.body is not None else f.read(got.raw_file_id)
            try:
                data = json.loads(body)
            except ValueError:
                raise ValueError("BSE's reply isn't JSON (a page served with 200?)") from None
            yield _rows(data, "BSE"), got.raw_file_id
            lo = hi + timedelta(days=1)

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        r = self.result = {"events": [], "effective": [], "withdrawn": [], "factors_written": 0, "factors_deleted": 0,
                           "waiting": [], "window_kinds": {}, "window_unrecognised": [], "window_no_factor": [],
                           "window_no_dps": []}
        ids = list(f.companies)
        source = dict(tx.execute("select key, id from core.source where key in ('nse', 'bse')").fetchall())
        stored = tx.execute(
            """select ev.id, ev.entity_id, ev.slug, ev.status, ca.exchange, ca.ex_date, ca.kind, ca.subject
                 from core.event ev join equity.corporate_action ca on ca.event_id = ev.id
                where ev.entity_id = any(%s)""", (ids,)).fetchall()
        slugs = {s for (s,) in tx.execute("select slug from core.event where event_type = 'corporate_action'")}

        # 1. match this run's rows to stored actions; insert the new ones
        by_key: dict[tuple, list] = {}
        for row in stored:
            by_key.setdefault((row[1], row[4], row[5], row[6]), []).append(row)
        parsed = {row: ac.parse_subject(row.subject, row.face_value) for row in f.rows}
        matched: dict[int, Row] = {}                 # event id -> this run's row
        unmatched: list[Row] = []
        for row in f.rows:
            k = (row.entity_id, row.exchange, row.ex_date, parsed[row].kind)
            hit = next((s for s in by_key.get(k, []) if s[0] not in matched
                        and ac.clean(s[7]).lower() == ac.clean(row.subject).lower()), None)
            if hit:
                matched[hit[0]] = row
            else:
                unmatched.append(row)
        new: list[Row] = []
        for row in unmatched:                        # the same action with its subject reworded: one each side
            k = (row.entity_id, row.exchange, row.ex_date, parsed[row].kind)
            left = [s for s in by_key.get(k, []) if s[0] not in matched]
            mine = [x for x in unmatched if (x.entity_id, x.exchange, x.ex_date, parsed[x].kind) == k]
            if len(left) == 1 and len(mine) == 1:
                matched[left[0][0]] = row
            else:
                new.append(row)
        for row in sorted(new, key=lambda x: (f.companies[x.entity_id].slug, x.ex_date, ac.clean(x.subject))):
            p = parsed[row]
            base = f"{f.companies[row.entity_id].slug}-{p.kind}-{row.ex_date.isoformat()}"
            slug, n = base, 1
            while slug in slugs:
                n += 1
                slug = f"{base}-{n}"
            slugs.add(slug)
            status = "announced" if row.ex_date > f.today else "effective"
            title = ac.clean(row.subject) or p.kind.capitalize()
            event_id = tx.execute(
                """insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id,
                                           is_published)
                   values ('corporate_action', %s, %s, %s, %s, %s, %s, true) returning id""",
                (row.entity_id, slug, title, row.ex_date, status, source[row.exchange.lower()])).fetchone()[0]
            tx.execute(
                """insert into equity.corporate_action (event_id, kind, ex_date, record_date, subject, ratio_new,
                       ratio_old, face_value_old, face_value_new, dividend_per_share, rights_price, exchange,
                       raw_file_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (event_id, p.kind, row.ex_date, row.record_date, row.subject, p.ratio_new, p.ratio_old,
                 p.face_value_old, p.face_value_new, p.dividend_per_share, p.rights_price, row.exchange,
                 row.raw_file_id))
            r["events"].append(slug)

        # 2. statuses: announced -> effective from the ex-date; withdrawn when a read window no longer lists it
        start, end = f.window
        for event_id, entity_id, slug, status, exchange, ex_date, _, _ in stored:
            if status != "announced":
                continue
            listed = event_id in matched
            in_window = start <= ex_date <= end and exchange in f.read.get(entity_id, set())
            if in_window and not listed:
                tx.execute("update core.event set status = 'withdrawn' where id = %s", (event_id,))
                r["withdrawn"].append(slug)
            elif ex_date <= f.today:
                tx.execute("update core.event set status = 'effective' where id = %s", (event_id,))
                r["effective"].append(slug)

        # what the window holds, for the not_applied check
        for row in f.rows:
            p = parsed[row]
            r["window_kinds"][p.kind] = r["window_kinds"].get(p.kind, 0) + 1
            name = f"{f.companies[row.entity_id].slug} {row.ex_date} ({ac.clean(row.subject)})"
            if p.kind in ("merger", "buyback"):
                r["window_no_factor"].append(f"{p.kind}: {name}")
            elif p.kind == "other" and not p.notice and not p.restructuring:
                r["window_unrecognised"].append(name)
            elif p.kind == "dividend" and p.dividend_per_share is None:
                r["window_no_dps"].append(name)

        # 3. factors
        r["factors_written"], r["factors_deleted"] = self._factors(tx, f, ids)
        return len(r["events"]) + len(r["effective"]) + len(r["withdrawn"]) + r["factors_written"] \
            + r["factors_deleted"]

    def _factors(self, tx, f: Fetched, ids: list[int]) -> tuple[int, int]:
        actions = tx.execute(
            """select ca.event_id, ev.entity_id, ev.slug, ca.kind, ca.ex_date, ca.subject, ca.ratio_new, ca.ratio_old,
                      ca.face_value_old, ca.face_value_new, ca.rights_price
                 from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                where ev.entity_id = any(%s) and ev.status = 'effective'
                order by ev.entity_id, ca.ex_date, ev.slug""", (ids,)).fetchall()
        groups: dict[tuple[int, date], list] = {}
        for a in actions:
            p = ac.parse_subject(a[5])
            if a[3] in ac.PRICE_KINDS or (a[3] == "other" and p.restructuring):
                groups.setdefault((a[1], a[4]), []).append(a)
        latest = dict(tx.execute(
            """select p.entity_id, max(p.trade_date) from equity.price_daily p
                 join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
                where p.entity_id = any(%s) group by 1""", (ids,)).fetchall())
        days = {d for (_, ex) in groups for d in (ex, _previous(ex)) if d is not None}
        prices = {(e, d): (o, c) for e, d, o, c in tx.execute(
            """select p.entity_id, p.trade_date, p.open, p.close from equity.price_daily p
                 join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
                where p.entity_id = any(%s) and p.trade_date = any(%s)""", (ids, list(days))).fetchall()} \
            if groups else {}

        want: dict[int, Want] = {}
        waiting: set[int] = set()
        for (entity_id, ex), members in groups.items():
            if latest.get(entity_id) is None or latest[entity_id] < ex:
                waiting.update(m[0] for m in members)
                self.result["waiting"] += [m[2] for m in members]
                continue
            for w in self._group(entity_id, ex, members, prices, f.reviews.releases):
                want[w.event_id] = w

        have = {row[0]: Want(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7] or {}) for row in tx.execute(
            """select event_id, entity_id, ex_date, price_factor, method, status, reason, evidence
                 from equity.adjustment_factor where entity_id = any(%s)""", (ids,)).fetchall()}
        written = deleted = 0
        for event_id, w in want.items():
            old = have.get(event_id)
            if old is not None and old.key() == w.key():
                continue
            args = (w.entity_id, w.ex_date, w.price_factor, w.method, w.status, w.reason, Jsonb(w.evidence))
            if old is None:
                tx.execute("""insert into equity.adjustment_factor (entity_id, ex_date, price_factor, method, status,
                                  reason, evidence, event_id) values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                           (*args, event_id))
            else:
                tx.execute("""update equity.adjustment_factor set entity_id = %s, ex_date = %s, price_factor = %s,
                                  method = %s, status = %s, reason = %s, evidence = %s where event_id = %s""",
                           (*args, event_id))
            written += 1
        for event_id in set(have) - set(want) - waiting:
            tx.execute("delete from equity.adjustment_factor where event_id = %s", (event_id,))
            deleted += 1
        return written, deleted

    @staticmethod
    def _group(entity_id: int, ex: date, members: list, prices: dict, releases: dict[str, ac.Release]) -> list[Want]:
        """The factor rows of one company's ex-date: each member's factor by its method, then the plausibility check on
        their product (Spec 02A §5.2). A member with a problem holds the whole ex-date: their product can't be checked."""
        prev = _previous(ex)
        p0, p1 = prices.get((entity_id, prev)), prices.get((entity_id, ex))
        c0 = p0[1] if p0 else None
        c1, open1 = (p1[1], p1[0]) if p1 else (None, None)
        base = {"c0": ac.num(c0), "c0_date": prev.isoformat() if prev else None, "c1": ac.num(c1),
                "c1_date": ex.isoformat(), "open1": ac.num(open1), "group": [m[2] for m in members]}
        if c0 and open1:
            base["preopen_implied"] = ac.num(ac.preopen_factor(open1, c0))
        price_problem = None
        if prev is None:
            price_problem = f"the trading calendar doesn't reach the session before {ex}"
        elif not calendars.is_session(CALENDAR, ex):
            price_problem = f"the ex-date {ex} isn't a trading session"
        elif c0 is None:
            price_problem = f"no close on the previous session ({prev})"
        elif c1 is None:
            price_problem = f"no close on the ex-date ({ex})"

        rows: list[tuple] = []                       # (member, factor, problem, extra evidence)
        for event_id, _, slug, kind, _, subject, r_new, r_old, fv_old, fv_new, r_price in members:
            p = ac.parse_subject(subject)
            extra: dict = {}
            factor, problem = None, None
            if p.several:
                problem = p.problem
            elif kind in ac.RATIO_KINDS:
                if kind == "bonus" and r_new and r_old:
                    factor = ac.ratio_factor(kind, ratio_new=r_new, ratio_old=r_old)
                elif kind in ("split", "consolidation") and fv_old and fv_new:
                    factor = ac.ratio_factor(kind, face_value_old=fv_old, face_value_new=fv_new)
                else:
                    problem = p.problem or "the ratio can't be read from the subject"
            elif kind == "demerger":
                if open1 is None or not open1 or open1 <= 0:
                    problem = "no pre-open price in the ex-date's bhavcopy"
                elif c0 is not None:
                    if open1 >= c0:
                        problem = (f"the ex-date's open ({ac.num(open1)}) isn't below the previous close "
                                   f"({ac.num(c0)}): no sign of the demerger in the pre-open price")
                    else:
                        factor = ac.preopen_factor(open1, c0)
            elif kind == "rights":
                if not (r_new and r_old and r_price):
                    problem = p.problem or "the rights ratio or price can't be read from the subject"
                elif c0 is not None:
                    extra["rights"] = {"new": ac.num(r_new), "held": ac.num(r_old), "price": ac.num(r_price)}
                    if r_price >= c0:
                        problem = (f"the rights price ({ac.num(r_price)}) is at or above the previous close "
                                   f"({ac.num(c0)}): no price effect to adjust for")
                    else:
                        extra["terp"] = ac.num(ac.q(ac.terp(c0, r_new, r_old, r_price)))
                        factor = ac.terp_factor(c0, r_new, r_old, r_price)
            else:                                    # a restructuring none of the methods covers
                problem = ("a restructuring none of the methods covers (2A-08's stop condition): held for a reviewed "
                           "method")
            rows.append((event_id, slug, kind, factor, problem, extra))

        reviewed = {slug: releases[slug] for _, slug, *_ in rows if slug in releases}
        open_rows = [x for x in rows if x[1] not in reviewed]
        blocker = next((x for x in open_rows if x[4]), None)
        check = None
        factors = [reviewed[x[1]].factor if x[1] in reviewed else x[3] for x in rows]
        if not price_problem and all(v is not None for v in factors):
            group_factor = ac.product(factors)
            check = ac.plausible(c0, c1, group_factor)
            base.update({"group_factor": ac.num(ac.q(group_factor)), "adjusted_move": ac.move(check.adjusted_move),
                         "unadjusted_move": ac.move(check.unadjusted_move), "band": ac.move(ac.BAND)})

        out: list[Want] = []
        for event_id, slug, kind, factor, problem, extra in rows:
            ev = {**base, **extra, "factor": ac.num(factor)}
            if slug in reviewed:
                rel = reviewed[slug]
                ev["review"] = {"factor": ac.num(rel.factor), "evidence": rel.evidence, "reviewer": rel.reviewer,
                                "reviewed_on": rel.reviewed_on.isoformat()}
                ev["passed"] = bool(check and check.passed)
                out.append(Want(event_id, entity_id, ex, rel.factor, "reviewed", "applied",
                                f"released by review ({rel.reviewer}, {rel.reviewed_on.isoformat()})", ev))
                continue
            if problem:
                reason = problem
            elif blocker is not None:
                reason = f"another action on the same ex-date is held: {blocker[1]} ({blocker[4]})"
            elif price_problem:
                reason = price_problem
            elif check is None:
                reason = "the factor can't be computed"
            elif not check.passed:
                reason = f"the plausibility check failed: {check.reason}"
            else:
                reason = None
            ev["passed"] = reason is None
            out.append(Want(event_id, entity_id, ex, factor, METHOD[kind], "held" if reason else "applied", reason, ev))
        return out

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        ids = list(f.companies)
        out: list[Check] = []
        n = len(f.read)
        out.append(Check("lists_read", not f.failed,
                         ("not read: " + _names(f.failed)) if f.failed
                         else (f"{n} companies' lists read for {f.window[0]} to {f.window[1]}: {len(f.rows)} actions, "
                               f"{len(r['events'])} new" if n else f.note or "no company to read")))

        bad_ratio = []
        for slug, kind, factor, r_new, r_old, fv_old, fv_new in tx.execute(
                """select ev.slug, ca.kind, a.price_factor, ca.ratio_new, ca.ratio_old, ca.face_value_old,
                          ca.face_value_new
                     from equity.adjustment_factor a join equity.corporate_action ca on ca.event_id = a.event_id
                     join core.event ev on ev.id = a.event_id
                    where a.entity_id = any(%s) and a.method = 'ratio' and a.price_factor is not null""",
                (ids,)).fetchall():
            try:
                expected = ac.ratio_factor(kind, ratio_new=r_new, ratio_old=r_old, face_value_old=fv_old,
                                           face_value_new=fv_new)
            except (ValueError, TypeError, ArithmeticError):
                expected = None
            if expected is None or factor != expected:
                bad_ratio.append(f"{slug}: stored {ac.num(factor)}, ratio {ac.num(expected)}")
        out.append(Check("ratios_exact", not bad_ratio,
                         _names(bad_ratio) if bad_ratio else "every split, bonus and consolidation factor equals its ratio"))

        unchecked = []
        applied = 0
        for slug, method, passed in tx.execute(
                """select ev.slug, a.method, a.evidence ->> 'passed' from equity.adjustment_factor a
                     join core.event ev on ev.id = a.event_id where a.entity_id = any(%s) and a.status = 'applied'""",
                (ids,)).fetchall():
            applied += 1
            if not ((method == "reviewed" and slug in f.reviews.releases) or (method != "reviewed" and passed == "true")):
                unchecked.append(f"{slug} ({method})")
        out.append(Check("applied_checked", not unchecked,
                         ("applied without a passed check or a review entry: " + _names(unchecked)) if unchecked
                         else f"{applied} applied factors, each checked or reviewed"))

        held = [f"{slug}: {reason}" for slug, reason in tx.execute(
            """select ev.slug, a.reason from equity.adjustment_factor a join core.event ev on ev.id = a.event_id
                where a.entity_id = any(%s) and a.status = 'held' order by a.ex_date, ev.slug""", (ids,)).fetchall()]
        parts = []
        if held:
            parts.append(f"held ({len(held)}): " + _names(held))
        if r["window_unrecognised"]:
            parts.append("unrecognised entries: " + _names(r["window_unrecognised"]))
        if r["waiting"]:
            parts.append("waiting for the ex-date's prices: " + _names(sorted(set(r["waiting"]))))
        if r["window_no_factor"]:
            parts.append("no factor by method: " + _names(r["window_no_factor"]))
        if r["window_no_dps"]:
            parts.append("dividends without an amount per share: " + _names(r["window_no_dps"]))
        k = r["window_kinds"]
        parts.append(f"in the window: {k.get('dividend', 0)} dividends, "
                     f"{sum(1 for x in f.rows if ac.parse_subject(x.subject).notice)} meeting notices")
        out.append(Check("not_applied", not held and not r["window_unrecognised"], "; ".join(parts), blocking=False))

        jumps = []
        for slug, d, prev_close, close, lnmove in tx.execute(
                """with p as (
                     select p.entity_id, e.slug, p.trade_date, p.close,
                            lag(p.close) over (partition by p.entity_id order by p.trade_date) as prev_close
                       from equity.price_daily p
                       join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
                       join core.entity e on e.id = p.entity_id
                      where p.entity_id = any(%s))
                   select slug, trade_date, prev_close, close, ln(close / prev_close) from p
                    where prev_close is not null and abs(ln(close / prev_close)) > %s
                      and not exists (select 1 from equity.adjustment_factor a where a.entity_id = p.entity_id
                                         and a.ex_date = p.trade_date and a.status = 'applied')
                    order by trade_date, slug""", (ids, JUMP)).fetchall():
            if (slug, d) in f.reviews.jumps:
                continue
            jumps.append(f"{slug} {d} ({ac.pct(lnmove)}: {ac.num(prev_close)} to {ac.num(close)})")
        out.append(Check("unexplained_jumps", not jumps,
                         ("no applied factor and no review for: " + _names(jumps, SHOWN_JUMPS)) if jumps
                         else f"no unreviewed move over 15% ({len(f.reviews.jumps)} reviewed as real moves)",
                         blocking=False))

        out.append(Check("undated_rows", not f.undated,
                         _names(f.undated) if f.undated else "every listed row has its ex-date", blocking=False))

        known = {slug for (slug,) in tx.execute(
            """select ev.slug from equity.adjustment_factor a join core.event ev on ev.id = a.event_id
                where a.entity_id = any(%s)""", (ids,)).fetchall()}
        stray = [s for s, rel in f.reviews.releases.items()
                 if s not in known and (ac.event_date(s) or date.min) <= min(f.window[1], f.today)]
        out.append(Check("reviews_matched", not stray,
                         ("released in the review file, but no stored factor row: " + _names(stray)) if stray
                         else f"{len(f.reviews.releases)} releases in the review file", blocking=False))
        return out


def _previous(d: date) -> date | None:
    """The session before `d`, or None where the calendar doesn't reach (the session before 1 Jan 2019)."""
    try:
        return calendars.previous_session(CALENDAR, d)
    except calendars.CalendarYearError:
        return None


def _getter(row: dict):
    lk = {k.lower(): k for k in row}

    def g(*names):
        for name in names:
            k = lk.get(name.lower())
            if k is not None and row[k] not in (None, "", "-"):
                return row[k]
        return None
    return g


def _rows(data, exchange: str) -> list[dict]:
    """The action rows of a reply: NSE answers a JSON list; BSE a list or {"Table": [...]}."""
    rows = None
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = next((data[k] for k in ("data", "Table", "Table1", "rows") if isinstance(data.get(k), list)), None)
    if rows is None or not all(isinstance(x, dict) for x in rows):
        raise ValueError(f"{exchange}'s reply isn't a list of actions")
    return rows
