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
from typing import Any, Dict, List, Optional

from dt_backend.core import load_universe
from dt_backend.core.logger_dt import log, warn
from dt_backend.services.intraday_bars_fetcher import update_rolling_with_live_bars
from dt_backend.core.config_dt import DT_PATHS

try:
    from utils.time_utils import is_market_open, next_market_open, now_ny  # type: ignore
except Exception:  # pragma: no cover
    is_market_open = None  # type: ignore
    next_market_open = None  # type: ignore
    now_ny = None  # type: ignore


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

    # IMPORTANT: live market loop writes to the market-only rolling file.
    # This avoids Windows file-lock contention/gzip corruption when the
    # dt job runs concurrently and writes dt state to its own rolling.
    try:
        market_path = DT_PATHS.get("rolling_market_intraday_file")
        if market_path:
            os.environ["DT_ROLLING_PATH"] = str(market_path)
        os.environ["DT_USE_LOCK"] = "0"
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
        # Market-hours gating when available.
        if callable(is_market_open) and callable(next_market_open):
            if not is_market_open():
                nxt = next_market_open()
                now = now_ny() if callable(now_ny) else None
                if now is not None:
                    sleep_s = max(30, int((nxt - now).total_seconds()))
                    log(f"[live_bars] market closed; sleeping ~{sleep_s}s until next open")
                else:
                    sleep_s = 600
                    log("[live_bars] market closed; sleeping 10m")
                time.sleep(sleep_s)
                continue

        _cycle()
        time.sleep(max(1, int(interval_sec)))
if __name__ == "__main__":
    run_live_market_data_loop()

