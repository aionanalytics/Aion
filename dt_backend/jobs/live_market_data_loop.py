"""dt_backend/jobs/live_market_data_loop.py

Live market-data refresher for dt_backend.

This is the missing "heartbeat" for the day-trading system:
it keeps `rolling[sym]["bars_intraday"]` (1m) and optionally
`rolling[sym]["bars_intraday_5m"]` (5m) refreshed during market hours.

Design notes
------------
* Best-effort: never crashes the whole process on a bad symbol/batch.
* Market-hour aware: sleeps until next open when closed.
* Does not modify backend nightly-job code.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dt_backend.core import load_universe
from dt_backend.core.logger_dt import log, warn
from dt_backend.services.intraday_bars_fetcher import update_rolling_with_live_bars

try:
    from utils.time_utils import is_market_open, next_market_open, now_ny  # type: ignore
except Exception:  # pragma: no cover
    is_market_open = None  # type: ignore
    next_market_open = None  # type: ignore
    now_ny = None  # type: ignore

try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def _now_ny_fallback() -> datetime:
    if callable(now_ny):
        try:
            return now_ny()  # type: ignore[misc]
        except Exception:
            pass
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo("America/New_York"))  # type: ignore[misc]
    return datetime.now(timezone.utc)


def _is_market_open_fallback() -> bool:
    # Prefer utils.time_utils if it exists.
    if callable(is_market_open):
        try:
            return bool(is_market_open())  # type: ignore[call-arg]
        except Exception:
            pass

    # Simple NY schedule (no holiday calendar).
    n = _now_ny_fallback()
    wd = int(n.weekday())  # 0=Mon
    if wd >= 5:
        return False
    hm = n.hour * 60 + n.minute
    open_min = 9 * 60 + 30
    close_min = 16 * 60
    return (hm >= open_min) and (hm < close_min)


def _sleep_until_next_open() -> None:
    # If we have a real calendar util, use it.
    if callable(next_market_open):
        try:
            nxt = next_market_open()  # type: ignore[call-arg]
            now = _now_ny_fallback()

            # Defensive: normalize tz
            try:
                if getattr(nxt, "tzinfo", None) is None:
                    # assume NY local if naive
                    if ZoneInfo is not None:
                        nxt = nxt.replace(tzinfo=ZoneInfo("America/New_York"))  # type: ignore[misc]
                    else:
                        nxt = nxt.replace(tzinfo=timezone.utc)
                # compute delta in same zone
                sleep_s = max(30, int((nxt - now).total_seconds()))
            except Exception:
                sleep_s = 600

            log(f"[live_bars] market closed; sleeping ~{sleep_s}s until next open")
            time.sleep(sleep_s)
            return
        except Exception:
            pass

    # Fallback: just sleep 10 minutes.
    log("[live_bars] market closed; sleeping 10m (fallback)")
    time.sleep(600)


def fetch_live_bars_once(
    *,
    symbols: Optional[List[str]] = None,
    max_symbols: Optional[int] = None,
    fetch_1m: bool = True,
    fetch_5m: bool = True,
    lookback_minutes_1m: int = 90,
    lookback_minutes_5m: int = 240,
    max_len_1m: int = 600,
    max_len_5m: int = 300,
) -> Dict[str, Any]:
    syms = symbols or load_universe()
    syms = [s.strip().upper() for s in (syms or []) if str(s).strip()]
    syms = sorted(set(syms))
    if max_symbols is not None:
        syms = syms[: max(0, int(max_symbols))]

    if not syms:
        warn("[live_bars] no symbols to fetch")
        return {"status": "no_symbols"}

    out: Dict[str, Any] = {"status": "ok", "symbols": len(syms), "results": {}}

    if fetch_1m:
        out["results"]["1Min"] = update_rolling_with_live_bars(
            symbols=syms,
            timeframe="1Min",
            lookback_minutes=lookback_minutes_1m,
            max_len=max_len_1m,
        )

    if fetch_5m:
        out["results"]["5Min"] = update_rolling_with_live_bars(
            symbols=syms,
            timeframe="5Min",
            lookback_minutes=lookback_minutes_5m,
            max_len=max_len_5m,
        )

    return out


def run_live_market_data_loop(
    *,
    interval_sec: int = 60,
    max_symbols: Optional[int] = None,
    fetch_1m: bool = True,
    fetch_5m: bool = True,
    once: bool = False,
) -> Dict[str, Any]:
    """Continuously refresh live bars while the market is open."""

    try:
        os.environ.pop("DT_ROLLING_PATH", None)
        os.environ.setdefault("DT_USE_LOCK", "0")
    except Exception:
        pass

    log(
        f"[live_bars] loop start interval={interval_sec}s once={once} "
        f"fetch_1m={fetch_1m} fetch_5m={fetch_5m} max_symbols={max_symbols}"
    )

    def _cycle() -> Dict[str, Any]:
        return fetch_live_bars_once(
            max_symbols=max_symbols,
            fetch_1m=fetch_1m,
            fetch_5m=fetch_5m,
        )

    if once:
        return _cycle()

    while True:
        if not _is_market_open_fallback():
            _sleep_until_next_open()
            continue

        _cycle()
        time.sleep(max(1, int(interval_sec)))


if __name__ == "__main__":
    run_live_market_data_loop()
