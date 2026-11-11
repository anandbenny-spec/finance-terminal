"""The yfinance probe from GitHub's runners (Spec 02A.1 §5.3 step 1; job US-03, #402): does Yahoo's chart API answer
stock tickers from a runner, and does it behave as `ft.equity.us_prices` assumes?

Run by the manual workflow `us-probe` (`python -m ft.equity.us_probe --tickers AAPL,JPM,GOOGL`). Every request goes
through the shared HTTP client (pacing, retries, Chrome's fingerprint for Yahoo) with Yahoo's crumb, as
`policy.market_series` does. No database, no storage, no secret. The log shows counts and yes/no answers only: this
repository is public, so no price is printed.

Two kinds of answer:
1. **Reach:** each ticker's last five NYSE sessions: the HTTP status, the bars returned against the sessions asked for,
   and whether the chart names the ticker, in USD, on which exchange.
2. **Behaviour** (the assumptions `parse_chart` rests on), on splits whose traded prices are on record: Apple's 4:1 of
   31 Aug 2020 (close as traded on 28 Aug 2020: 499.23; dividend with ex-date 7 Aug 2020: 0.82 a share before the
   split) and Nvidia's 10:1 of 10 Jun 2024 (close as traded on 7 Jun 2024: 1,208.88). For each: is the split listed on
   its ex-date with its ratio, does Yahoo return the earlier close and dividend divided by the ratio, and does
   `parse_chart` multiply them back to the traded values.
Exit status 1 when a ticker can't be read, returns no bar, or a behaviour answer is no.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from ft import calendars
from ft.equity import us_prices as yp
from ft.jobs.fetch import FetchError, HttpClient, utcnow
from ft.jobs.tasks.equity_us_prices import CALENDAR, close_at, last_closed_session
from ft.jobs.tasks.market_series import yahoo_crumb

TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
# Traded values on record, each with the split after it (ticker, window, split ex-date and ratio, the day before,
# its traded close, and a dividend's ex-date and amount as declared, where one falls in the window).
KNOWN = (
    ("AAPL", date(2020, 8, 3), date(2020, 9, 4), date(2020, 8, 31), Decimal(4), date(2020, 8, 28), Decimal("499.23"),
     date(2020, 8, 7), Decimal("0.82")),
    ("NVDA", date(2024, 6, 3), date(2024, 6, 14), date(2024, 6, 10), Decimal(10), date(2024, 6, 7), Decimal("1208.88"),
     None, None),
)


def _get(http, counts, ticker: str, start: date, end: date, crumb: str | None):
    params = yp.chart_params(start, end)
    record = yp.yahoo_url(ticker) + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return http.get(yp.yahoo_url(ticker), record_url=record, params={**params, **({"crumb": crumb} if crumb else {})},
                    stats=counts)


def _yes(b: bool) -> str:
    return "yes" if b else "NO"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ft.equity.us_probe")
    p.add_argument("--tickers", default="AAPL,JPM,GOOGL")
    args = p.parse_args(argv)
    tickers = [t.strip().upper() for t in (args.tickers or "AAPL,JPM,GOOGL").split(",") if t.strip()]
    if not tickers or any(not TICKER.match(t) for t in tickers):
        print("the tickers input must be comma-separated tickers (letters, digits, dot or hyphen)")
        return 2
    http = HttpClient()
    counts: Counter = Counter()
    crumb, session = yahoo_crumb(SimpleNamespace(http=http, counts=counts))
    print(f"Yahoo session: {session}")
    ok = True

    now = utcnow()
    end = last_closed_session(now)
    want = calendars.sessions(CALENDAR, end - timedelta(days=14), end)[-5:]
    print(f"Reach: the last {len(want)} NYSE sessions to {end}")
    for t in tickers:
        try:
            r = _get(http, counts, t, want[0], end, crumb)
        except FetchError as e:
            print(f"  {t}: no answer ({str(e)[:120]})")
            ok = False
            continue
        if r.status != 200:
            print(f"  {t}: HTTP {r.status}")
            ok = False
            continue
        try:
            chart = yp.parse_chart(r.body, t, fetched_at=r.fetched_at, close_at=close_at)
        except yp.SourceError as e:
            print(f"  {t}: HTTP 200, unreadable ({str(e)[:120]})")
            ok = False
            continue
        days = {b.day for b in chart.bars}
        print(f"  {t}: HTTP 200, {len(chart.bars)} bars for {len(want)} sessions (every session has its bar: "
              f"{_yes(all(d in days for d in want))}), symbol {_yes(chart.symbol == t)}, USD {_yes(chart.currency == 'USD')},"
              f" exchange {chart.price_exchange or chart.exchange or 'unknown'}")
        ok = ok and bool(chart.bars)

    print("Behaviour: splits whose traded prices are on record")
    for t, start, stop, ex, ratio, before, traded, div_day, declared in KNOWN:
        try:
            r = _get(http, counts, t, start, stop, crumb)
            doc = json.loads(r.body) if r.status == 200 else None
            chart = yp.parse_chart(r.body, t, fetched_at=r.fetched_at, close_at=close_at) if doc else None
        except (FetchError, ValueError, yp.SourceError) as e:
            print(f"  {t}: no answer ({str(e)[:120]})")
            ok = False
            continue
        if chart is None:
            print(f"  {t}: HTTP {r.status}")
            ok = False
            continue
        split = next((s for s in chart.splits if s.ex_date == ex), None)
        listed = split is not None and split.ratio == ratio
        res = doc["chart"]["result"][0]
        raw_close = None
        for ts, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0].get("close") or [], strict=False):
            if c is not None and yp._day(ts, yp.NEW_YORK, t) == before:
                raw_close = Decimal(repr(float(c)))
        scaled = raw_close is not None and abs(raw_close * ratio - traded) <= Decimal("0.01")
        bar = next((b for b in chart.bars if b.day == before), None)
        back = bar is not None and bar.close == traded
        line = (f"  {t}: split {_yes(listed)} listed on {ex} as {ratio}:1; the close of {before} comes divided by it: "
                f"{_yes(scaled)}; multiplied back to the traded close: {_yes(back)}")
        ok = ok and listed and scaled and back
        if div_day is not None:
            raw_div = None
            for ev in ((res.get("events") or {}).get("dividends") or {}).values():
                if yp._day(ev.get("date"), yp.NEW_YORK, t) == div_day:
                    raw_div = Decimal(repr(float(ev["amount"])))
            div = next((d for d in chart.dividends if d.ex_date == div_day), None)
            d_scaled = raw_div is not None and abs(raw_div * ratio - declared) <= Decimal("0.0001")
            d_back = div is not None and div.amount == declared
            line += (f"; the {div_day} dividend comes divided by it: {_yes(d_scaled)}; multiplied back to the declared "
                     f"amount: {_yes(d_back)}")
            ok = ok and d_scaled and d_back
        print(line)
    print(f"HTTP: {dict(sorted(counts.items()))}")
    print("probe passed" if ok else "probe FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
