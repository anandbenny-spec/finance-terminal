"""us.corporate_actions: the Dow 30's splits and dividends from Yahoo Finance, each split's price adjustment factor
checked against the stored prices before it is applied (Spec 02A.1 §5.3; job US-03, #402).

Module `us` (decided on #400). Weekly, after `us.prices`; its first run on a project is a backfill (`equity_due`).

Fetch, per US company: Yahoo's chart API with its events (`ft.equity.us_prices`), from 400 days back to today on a
weekly run, or `--since` (floored at 1 Jan 2019) to `--until` on a backfill. Each response is stored as a raw file,
fetched whole every run (`conditional=False`), so the task never reads the raw bucket back. Yahoo scales a dividend
before a later split as it scales prices; the window always runs to today, so every later split is in the response and
`parse_chart` multiplies the amount back: each dividend is the amount per share on its own ex-date.

Load (one transaction):
1. **Events.** One `core.event` per action (type `corporate_action`, source `yfinance`, slug
   `<company-slug>-<kind>-<ex-date>`, `starts_on` the ex-date, `effective`: Yahoo lists an action once its ex-date has
   come) and its `equity.corporate_action` row (exchange the company's listing exchange). Published like India's: a US
   action and its factor are visible only once the company is (migration 0039's policies), so the go-live flip of the
   companies is the whole gate. Kinds: `split` (numerator above denominator; `ratio_new` the numerator, `ratio_old`
   the denominator), `consolidation` (a reverse split), `other` (a split Yahoo lists with a ratio that isn't two whole
   numbers, as it carries some spin-offs), `dividend` (`dividend_per_share`). An action is matched to a stored one by
   company, ex-date and kind, so a re-run writes nothing; a re-read that differs is named, never written over.
2. **Factors**, for every split, reverse split and `other` once the company's stored prices reach its ex-date:
   - ex-date on or before the company's first stored session: factor 1, `method` `source_adjusted` (Spec 02A.1 §5.3:
     no stored price is before it, so there is nothing to adjust);
   - after it: the ratio factor (denominator ÷ numerator) and Spec 02A §5.2's plausibility check on the stored closes
     either side (`ft.equity.actions.plausible`): `applied` with the evidence, or `held` with the reason;
   - `other`: held for a reviewed factor.
   A release in `config/equity/us_corporate_action_review.yaml` applies a held factor with `method` `reviewed`. Every
   run recomputes every factor and writes only what changed. Dividends get no factor (price returns only, as India).

Checks, B blocking, W warning:
- (B) `charts_read`: every company's chart read and parsed;
- (B) `responses`: each chart is the ticker asked for, in USD;
- (B) `ratios_exact`: every split and reverse split factor by ratio equals denominator ÷ numerator exactly (12 places);
- (B) `applied_checked`: no factor is applied without a passed check, a review entry, or its ex-date on or before
  the first stored session;
- (W) `not_applied`: held factors with their reasons, and the actions waiting for their ex-date's prices;
- (W) `changed_actions`: a stored action whose re-read differs, named with both values;
- (W) `unexplained_jumps`: every session since 2019 where a US company's |ln(close ÷ previous close)| > 0.15 with no
  applied factor that day, unless the review file records it as a real move;
- (W) `actions`: what the window held, by kind.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from ft import calendars
from ft.equity import actions as ac
from ft.equity import us_prices as yp
from ft.equity.guard import equity_due
from ft.jobs.due import Due
from ft.jobs.fetch import FetchError
from ft.jobs.runner import Check, Context, Task
from ft.jobs.tasks import register
from ft.jobs.tasks.equity_us_prices import CALENDAR, close_at
from ft.jobs.tasks.market_series import yahoo_crumb

FLOOR = date(2019, 1, 1)
BACK = timedelta(days=400)
JUMP = Decimal("0.15")
SHOWN = 12
SHOWN_JUMPS = 60
REVIEW_FILE = ac.CONFIG_DIR / "us_corporate_action_review.yaml"
FACTOR_KINDS = ("split", "consolidation", "other")


def _names(items: list[str], shown: int = SHOWN) -> str:
    return "; ".join(items[:shown]) + (f"; and {len(items) - shown} more" if len(items) > shown else "")


@dataclass
class Company:
    entity_id: int
    slug: str
    ticker: str | None
    exchange: str


@dataclass(frozen=True)
class Action:
    entity_id: int
    exchange: str
    ex_date: date
    kind: str
    subject: str
    title: str
    ratio_new: Decimal | None
    ratio_old: Decimal | None
    dividend_per_share: Decimal | None
    raw_file_id: int


def split_kind(s: yp.Split) -> str:
    if not s.whole or s.numerator == s.denominator:
        return "other"
    return "split" if s.numerator > s.denominator else "consolidation"


def _n(x: Decimal) -> str:
    return ac.num(x) or "0"


def actions_of(c: Company, chart: yp.Chart, raw_file_id: int) -> list[Action]:
    """The chart's splits and dividends as action rows (subject: what Yahoo lists, in words; numbers in their columns)."""
    out = []
    for s in chart.splits:
        kind = split_kind(s)
        if kind == "split":
            title = f"Stock split {_n(s.numerator)}:{_n(s.denominator)}"
        elif kind == "consolidation":
            title = f"Reverse stock split {_n(s.numerator)}:{_n(s.denominator)}"
        else:
            title = f"Split of {s.label} as Yahoo Finance lists it (not a whole-number ratio)"
        out.append(Action(c.entity_id, c.exchange, s.ex_date, kind, f"{title} (Yahoo Finance splitRatio {s.label})",
                          title, s.numerator, s.denominator, None, raw_file_id))
    for d in chart.dividends:
        out.append(Action(c.entity_id, c.exchange, d.ex_date, "dividend", "Cash dividend (Yahoo Finance)",
                          "Cash dividend", None, None, d.amount, raw_file_id))
    return out


@dataclass
class Fetched:
    window: tuple[date, date]
    today: date
    companies: dict[int, Company]
    actions: list[Action] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)       # responses that aren't the ticker, or not in USD
    reviews: ac.Reviews = field(default_factory=ac.Reviews)
    yahoo_session: str | None = None


@dataclass
class Want:
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


def _previous(d: date) -> date | None:
    try:
        return calendars.previous_session(CALENDAR, d)
    except calendars.CalendarYearError:
        return None


def ratio_factor(numerator: Decimal, denominator: Decimal) -> Decimal:
    """adjusted ÷ raw before the ex-date: a 4:1 split is 0.25, a 1:10 reverse split 10 (12 places, as India's)."""
    return ac.q(Decimal(denominator) / Decimal(numerator))


@register
class UsCorporateActions(Task):
    key = "us.corporate_actions"
    slug_like = "us-%"                  # the US master's slugs; tests point this at their own companies

    def __init__(self):
        self.result: dict = {}

    def due(self, db, workflow: str, now=None) -> Due:
        if not db.execute("select exists (select 1 from core.source where key = 'sec')").fetchone()[0]:
            return Due(False, "not set up: migration 0039 (us_market) isn't pushed to this project")
        return equity_due(db, self.key, workflow, super().due(db, workflow, now), requires="equity.corporate_action")

    # ------------------------------------------------------------------ fetch (no transaction)
    def fetch(self, ctx: Context) -> Fetched:
        f, db = ctx.fetcher, ctx.fetcher.db
        today = ctx.now.astimezone(yp.NEW_YORK).date()
        start = max(ctx.since, FLOOR) if ctx.since else max(today - BACK, FLOOR)
        end = min(ctx.until or today, today)
        companies = {r[0]: Company(*r) for r in db.execute(
            """select c.entity_id, e.slug,
                      (select i.value from core.entity_identifier i
                        where i.entity_id = c.entity_id and i.scheme = 'ticker' and upper_inf(i.valid)
                        order by lower(i.valid) desc nulls last limit 1),
                      c.price_exchange
                 from equity.company c join core.entity e on e.id = c.entity_id
                where e.kind = 'company' and e.market = 'US' and e.slug like %s order by e.slug""",
            (self.slug_like,)).fetchall()}
        if not companies:
            raise RuntimeError("no US company in the master: us.universe runs first")
        out = Fetched((start, end), today, companies, reviews=ac.load_reviews(REVIEW_FILE))
        if start > end:
            return out
        crumb = None
        for c in companies.values():
            if not c.ticker:
                out.failed.append(f"{c.slug}: no current ticker in core.entity_identifier")
                continue
            if out.yahoo_session is None:
                crumb, out.yahoo_session = yahoo_crumb(f)
            try:
                got = f.get(yp.yahoo_url(c.ticker), source="yfinance", params=yp.chart_params(start, end),
                            secret_params={"crumb": crumb} if crumb else None, media_type="application/json",
                            conditional=False, content_date=end)
                chart = yp.parse_chart(got.body, c.ticker, fetched_at=got.fetched_at, close_at=close_at)
            except (FetchError, yp.SourceError) as e:
                out.failed.append(f"{c.ticker}: {str(e)[:160]}")
                continue
            if chart.symbol != c.ticker:
                out.wrong.append(f"{c.ticker}: symbol {chart.symbol!r}")
            if chart.currency != "USD":
                out.wrong.append(f"{c.ticker}: currency {chart.currency!r}")
            out.actions += [a for a in actions_of(c, chart, got.raw_file_id) if start <= a.ex_date <= end]
        return out

    # ------------------------------------------------------------------ load (one transaction)
    def load(self, tx, f: Fetched, task_run_id) -> int:
        r = self.result = {"events": [], "changed": [], "waiting": [], "written": 0, "deleted": 0, "kinds": {}}
        ids = list(f.companies)
        source = tx.execute("select id from core.source where key = 'yfinance'").fetchone()[0]
        stored = {(row[1], row[2], row[3]): row for row in tx.execute(
            """select ev.id, ev.entity_id, ca.ex_date, ca.kind, ev.slug, ca.ratio_new, ca.ratio_old,
                      ca.dividend_per_share
                 from core.event ev join equity.corporate_action ca on ca.event_id = ev.id
                where ev.entity_id = any(%s)""", (ids,)).fetchall()}
        slugs = {s for (s,) in tx.execute("select slug from core.event where event_type = 'corporate_action'")}
        for a in sorted(f.actions, key=lambda x: (f.companies[x.entity_id].slug, x.ex_date, x.kind)):
            r["kinds"][a.kind] = r["kinds"].get(a.kind, 0) + 1
            old = stored.get((a.entity_id, a.ex_date, a.kind))
            if old is not None:
                now, then = (a.ratio_new, a.ratio_old, a.dividend_per_share), tuple(old[5:8])
                if now != then:
                    r["changed"].append(f"{old[4]}: stored {_fmt(then)}, read {_fmt(now)}")
                continue
            base = f"{f.companies[a.entity_id].slug}-{a.kind}-{a.ex_date.isoformat()}"
            slug, n = base, 1
            while slug in slugs:
                n += 1
                slug = f"{base}-{n}"
            slugs.add(slug)
            event_id = tx.execute(
                """insert into core.event (event_type, entity_id, slug, title, starts_on, status, source_id,
                                           is_published)
                   values ('corporate_action', %s, %s, %s, %s, 'effective', %s, true) returning id""",
                (a.entity_id, slug, a.title, a.ex_date, source)).fetchone()[0]
            tx.execute(
                """insert into equity.corporate_action (event_id, kind, ex_date, subject, ratio_new, ratio_old,
                       dividend_per_share, exchange, raw_file_id)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (event_id, a.kind, a.ex_date, a.subject, a.ratio_new, a.ratio_old, a.dividend_per_share, a.exchange,
                 a.raw_file_id))
            r["events"].append(slug)
        r["written"], r["deleted"] = self._factors(tx, f, ids)
        return len(r["events"]) + r["written"] + r["deleted"]

    def _factors(self, tx, f: Fetched, ids: list[int]) -> tuple[int, int]:
        actions = tx.execute(
            """select ca.event_id, ev.entity_id, ev.slug, ca.kind, ca.ex_date, ca.ratio_new, ca.ratio_old
                 from equity.corporate_action ca join core.event ev on ev.id = ca.event_id
                where ev.entity_id = any(%s) and ev.status = 'effective' and ca.kind = any(%s)
                order by ev.entity_id, ca.ex_date, ev.slug""", (ids, list(FACTOR_KINDS))).fetchall()
        span = {e: (lo, hi) for e, lo, hi in tx.execute(
            """select p.entity_id, min(p.trade_date), max(p.trade_date) from equity.price_daily p
                 join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
                where p.entity_id = any(%s) group by 1""", (ids,)).fetchall()}
        days = {d for a in actions for d in (a[4], _previous(a[4])) if d is not None}
        closes = {(e, d): c for e, d, c in tx.execute(
            """select p.entity_id, p.trade_date, p.close from equity.price_daily p
                 join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
                where p.entity_id = any(%s) and p.trade_date = any(%s)""", (ids, list(days))).fetchall()} \
            if actions else {}

        by_date: dict[tuple[int, date], list] = {}
        for a in actions:
            by_date.setdefault((a[1], a[4]), []).append(a)
        want: dict[int, Want] = {}
        waiting: set[int] = set()
        for (entity_id, ex), members in by_date.items():
            lo_hi = span.get(entity_id)
            if lo_hi is None or lo_hi[1] < ex:
                waiting.update(m[0] for m in members)
                self.result["waiting"] += [m[2] for m in members]
                continue
            for w in self._group(entity_id, ex, members, lo_hi[0], closes, f.reviews.releases):
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
    def _group(entity_id: int, ex: date, members: list, first: date, closes: dict,
               releases: dict[str, ac.Release]) -> list[Want]:
        """The factor rows of one company's ex-date (Yahoo lists one split a day, so normally one member)."""
        prev = _previous(ex)
        c0, c1 = closes.get((entity_id, prev)) if prev else None, closes.get((entity_id, ex))
        base = {"first_session": first.isoformat(), "c0": ac.num(c0), "c0_date": prev.isoformat() if prev else None,
                "c1": ac.num(c1), "c1_date": ex.isoformat(), "group": [m[2] for m in members]}
        out: list[Want] = []
        if ex <= first:
            for event_id, _, slug, kind, _, r_new, r_old in members:
                ev = {**base, "ratio": f"{ac.num(r_new)}:{ac.num(r_old)}", "passed": True,
                      "note": "no stored price is before the ex-date, so there is nothing to adjust"}
                out.append(Want(event_id, entity_id, ex, Decimal(1), "source_adjusted", "applied", None, ev))
            return out

        price_problem = None
        if prev is None:
            price_problem = f"the trading calendar doesn't reach the session before {ex}"
        elif not calendars.is_session(CALENDAR, ex):
            price_problem = f"the ex-date {ex} isn't a NYSE session"
        elif c0 is None:
            price_problem = f"no stored close on the previous session ({prev})"
        elif c1 is None:
            price_problem = f"no stored close on the ex-date ({ex})"

        rows = []                                    # (event_id, slug, kind, factor, problem)
        for event_id, _, slug, kind, _, r_new, r_old in members:
            if kind == "other":
                why = (f"Yahoo lists a split of {ac.num(r_new)}:{ac.num(r_old)}, not a whole-number ratio (a spin-off "
                       f"carried as a split?): held for a reviewed factor")
                rows.append((event_id, slug, kind, None, why))
            elif not r_new or not r_old:
                rows.append((event_id, slug, kind, None, "the split's ratio isn't stored"))
            else:
                rows.append((event_id, slug, kind, ratio_factor(r_new, r_old), None))
        reviewed = {slug: releases[slug] for _, slug, *_ in rows if slug in releases}
        factors = [reviewed[x[1]].factor if x[1] in reviewed else x[3] for x in rows]
        blocker = next((x for x in rows if x[4] and x[1] not in reviewed), None)
        check = None
        if not price_problem and all(v is not None for v in factors):
            group_factor = ac.product(factors)
            check = ac.plausible(c0, c1, group_factor)
            base.update({"group_factor": ac.num(ac.q(group_factor)), "adjusted_move": ac.move(check.adjusted_move),
                         "unadjusted_move": ac.move(check.unadjusted_move), "band": ac.move(ac.BAND)})
        for event_id, slug, kind, factor, problem in rows:
            ev = {**base, "factor": ac.num(factor)}
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
            out.append(Want(event_id, entity_id, ex, factor, "ratio", "held" if reason else "applied", reason, ev))
        return out

    # ------------------------------------------------------------------ checks (same transaction)
    def checks(self, tx, f: Fetched, task_run_id) -> list[Check]:
        r = self.result
        ids = list(f.companies)
        out: list[Check] = []
        read = len(f.companies) - len(f.failed)
        out.append(Check("charts_read", not f.failed, ("not read: " + _names(f.failed)) if f.failed else
                         f"{read} companies' actions read for {f.window[0]} to {f.window[1]}: {len(f.actions)} "
                         f"actions, {len(r['events'])} new"))
        out.append(Check("responses", not f.wrong, _names(f.wrong) if f.wrong else "each chart its ticker, in USD"))

        bad_ratio = []
        for slug, factor, r_new, r_old in tx.execute(
                """select ev.slug, a.price_factor, ca.ratio_new, ca.ratio_old
                     from equity.adjustment_factor a join equity.corporate_action ca on ca.event_id = a.event_id
                     join core.event ev on ev.id = a.event_id
                    where a.entity_id = any(%s) and a.method = 'ratio' and a.price_factor is not null""",
                (ids,)).fetchall():
            expected = ratio_factor(r_new, r_old) if r_new and r_old else None
            if expected is None or factor != expected:
                bad_ratio.append(f"{slug}: stored {ac.num(factor)}, ratio {ac.num(expected)}")
        out.append(Check("ratios_exact", not bad_ratio, _names(bad_ratio) if bad_ratio
                         else "every split factor by ratio equals its ratio"))

        unchecked, applied = [], 0
        for slug, method, passed, ex, first in tx.execute(
                """select ev.slug, a.method, a.evidence ->> 'passed', a.ex_date,
                          (select min(p.trade_date) from equity.price_daily p
                             join equity.company c on c.entity_id = p.entity_id and c.price_exchange = p.exchange
                            where p.entity_id = a.entity_id)
                     from equity.adjustment_factor a join core.event ev on ev.id = a.event_id
                    where a.entity_id = any(%s) and a.status = 'applied'""", (ids,)).fetchall():
            applied += 1
            ok = ((method == "reviewed" and slug in f.reviews.releases)
                  or (method == "source_adjusted" and first is not None and ex <= first)
                  or (method == "ratio" and passed == "true"))
            if not ok:
                unchecked.append(f"{slug} ({method})")
        out.append(Check("applied_checked", not unchecked,
                         ("applied without a passed check, a review entry or an ex-date before the prices: "
                          + _names(unchecked)) if unchecked else f"{applied} applied factors, each checked"))

        held = [f"{slug}: {reason}" for slug, reason in tx.execute(
            """select ev.slug, a.reason from equity.adjustment_factor a join core.event ev on ev.id = a.event_id
                where a.entity_id = any(%s) and a.status = 'held' order by a.ex_date, ev.slug""", (ids,)).fetchall()]
        parts = [f"held ({len(held)}): " + _names(held)] if held else []
        if r["waiting"]:
            parts.append("waiting for the ex-date's prices: " + _names(sorted(set(r["waiting"]))))
        out.append(Check("not_applied", not held and not r["waiting"], "; ".join(parts) or "every split's factor applied",
                         blocking=False))
        out.append(Check("changed_actions", not r["changed"], _names(r["changed"]) if r["changed"]
                         else "no stored action differs from its re-read", blocking=False))

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
        k = r["kinds"]
        out.append(Check("actions", True, ", ".join(f"{n} {kind}" for kind, n in sorted(k.items())) or "none in the window",
                         blocking=False))
        if f.yahoo_session is not None:
            out.append(Check("yahoo_session", f.yahoo_session == "crumb received", f.yahoo_session, blocking=False))
        return out


def _fmt(vals: tuple) -> str:
    return "(" + ", ".join("-" if v is None else ac.num(v) for v in vals) + ")"
