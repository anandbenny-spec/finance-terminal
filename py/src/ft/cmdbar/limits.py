"""The command bar's limits (Spec 07 §4 step 2, §8, N7; Architecture 03 §1.8): the visitor hash and the two counting windows.

- **The visitor hash (N7):** HMAC-SHA256 of the IP address and the user agent, keyed by HMAC-SHA256(`CMDBAR_SALT`, the IST
  date). The key changes every IST day, so the hash rotates daily, and without the secret it can't be reversed or linked
  across days. Neither the IP address nor the user agent is stored anywhere (no cookie either): only this hash, in
  `ops.cmdbar_usage`'s bucket and `ops.cmdbar_log`.
- **Per visitor:** every ask that passed the switch counts, cached or not, in the bucket `visitor:<hash>` for the current
  hour (`cmdbar_per_visitor_hourly`, 10).
- **Overall:** only a question that reaches the router counts, in the bucket `global` for the current IST day
  (`cmdbar_daily_cap`, 100: Anand, 15 Nov 2026).

Both counts are incremented atomically by the database (`insert … on conflict … do update … returning count`); this module
only names the bucket and the window. An ask is over a limit when its count (its own included) is above the limit.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
GLOBAL = "global"


def ist_date(now: datetime) -> date:
    return now.astimezone(IST).date()


def visitor_hash(salt: str, ip: str, user_agent: str, now: datetime) -> str:
    """N7: hex HMAC-SHA256(ip + newline + user agent), keyed by HMAC-SHA256(salt, the IST date). `salt` must be non-empty."""
    if not salt:
        raise ValueError("CMDBAR_SALT is empty")
    day_key = hmac.new(salt.encode(), ist_date(now).isoformat().encode(), hashlib.sha256).digest()
    return hmac.new(day_key, f"{ip or ''}\n{user_agent or ''}".encode(), hashlib.sha256).hexdigest()


def visitor_bucket(vhash: str) -> str:
    return f"visitor:{vhash}"


def hour_start(now: datetime) -> datetime:
    """The start of the current hour, in UTC (the per-visitor window)."""
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def ist_day_start(now: datetime) -> datetime:
    """IST midnight of the current IST day, as an instant (the overall window)."""
    return datetime.combine(ist_date(now), time(0), IST)


def over(count: int, limit: int) -> bool:
    """The ask whose increment returned `count` is over `limit` (its own ask included). A limit below 1 allows nothing."""
    return count > limit
