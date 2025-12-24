# utils/time_utils.py
"""
Time utilities used across backend + dt_backend:
- UTC <-> local conversions
- Market open/close detection
- Trading day helpers
- Timestamps for logs and filenames
"""

from __future__ import annotations

from datetime import datetime, timedelta, time, timezone
import pytz

# U.S. Equity markets (default)
NY_TZ = pytz.timezone("America/New_York")
UTC = pytz.UTC

# Market hours (regular session)
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def now_utc() -> datetime:
    return datetime.utcnow().replace(tzinfo=UTC)


def now_ny() -> datetime:
    return now_utc().astimezone(NY_TZ)


def is_weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5  # 5=Saturday, 6=Sunday


def is_market_open(dt: datetime | None = None) -> bool:
    dt = dt or now_ny()
    if is_weekend(dt):
        return False
    t = dt.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def next_market_open(dt: datetime | None = None) -> datetime:
    dt = dt or now_ny()

    # If before open today
    if dt.time() < MARKET_OPEN and dt.weekday() < 5:
        return dt.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0)

    # Otherwise next weekday morning
    d = dt
    while True:
        d += timedelta(days=1)
        if d.weekday() < 5:
            return d.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0)


def previous_trading_day(dt: datetime | None = None) -> datetime:
    dt = dt or now_ny()
    d = dt
    while True:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            return d


def today_str() -> str:
    return now_ny().strftime("%Y-%m-%d")


def ts() -> str:
    """For logs: 2025-02-15T13:32:00Z"""
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_file() -> str:
    """For filenames: 2025-02-15_133200"""
    return now_utc().strftime("%Y-%m-%d_%H%M%S")
