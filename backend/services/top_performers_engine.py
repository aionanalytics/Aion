# backend/services/top_performers_engine.py
"""
Top Performers Engine

Responsibilities:
- Maintain rolling Top-50 prediction history per horizon (1w / 2w / 4w).
- Append today's Top-50 from insights_builder outputs.
- Prune entries older than their horizon (in calendar days).
- Compute mean predicted price per symbol over its rolling window.
- Compute current price vs mean predicted price to find top performers.
- Produce frozen daily top-3 lists per horizon for dashboard stability.

Rolling files (per horizon):
    PATHS["insights"] / "top50_rolling_1w.json.gz"
    PATHS["insights"] / "top50_rolling_2w.json.gz"
    PATHS["insights"] / "top50_rolling_4w.json.gz"

Daily Top-50 source files (overwritten nightly by insights_builder):
    PATHS["insights"] / "top50_1w.json"
    PATHS["insights"] / "top50_2w.json"
    PATHS["insights"] / "top50_4w.json"

Frozen daily top-3 outputs (for dashboard):
    PATHS["insights"] / "top3_1w_today.json"
    PATHS["insights"] / "top3_4w_today.json"

NOTE:
- This engine intentionally does NOT look at bot trades.
- It is strictly model-centric: predictions + prices.
"""

from __future__ import annotations

import json
import gzip
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Tuple

from backend.core.config import PATHS
from utils.logger import log

# Horizon definitions in days
HORIZON_DAYS: Dict[str, int] = {
    "1w": 7,
    "2w": 14,
    "4w": 28,
}

# Mapping from horizon to daily Top-50 filename
TOP50_DAILY_FILES: Dict[str, str] = {
    "1w": "top50_1w.json",
    "2w": "top50_2w.json",
    "4w": "top50_4w.json",
}

# Rolling file names
TOP50_ROLLING_FILES: Dict[str, str] = {
    "1w": "top50_rolling_1w.json.gz",
    "2w": "top50_rolling_2w.json.gz",
    "4w": "top50_rolling_4w.json.gz",
}

# Frozen top-3 daily outputs (for dashboard)
TOP3_DAILY_FILES: Dict[str, str] = {
    "1w": "top3_1w_today.json",
    "4w": "top3_4w_today.json",
}


INSIGHTS_DIR: Path = PATHS["insights"]


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"[top_performers] ⚠️ Failed to read JSON {path}: {e}")
        return None


def _load_json_gz(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        log(f"[top_performers] ⚠️ Failed to read gz JSON {path}: {e}")
        return []


def _save_json_gz(path: Path, data: List[Dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log(f"[top_performers] ⚠️ Failed to write gz JSON {path}: {e}")


def _parse_date(d: str) -> Optional[date]:
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None


def _today() -> date:
    return datetime.utcnow().date()


def _extract_predicted_price(rec: Dict[str, Any]) -> Optional[float]:
    """
    Try multiple common keys to find the model's target price.
    Adjust if your Top-50 JSON uses a different field.
    """
    candidates = [
        "predicted_price",
        "target_price",
        "price_target",
        "prediction_price",
    ]
    for key in candidates:
        if key in rec and rec[key] is not None:
            try:
                return float(rec[key])
            except Exception:
                continue

    # Some structures might nest prediction info:
    pred = rec.get("prediction") or {}
    for key in candidates:
        if key in pred and pred[key] is not None:
            try:
                return float(pred[key])
            except Exception:
                continue
    return None


def append_today_top50_to_rolling(as_of: Optional[date] = None) -> None:
    """
    Appends today's Top-50 predictions (from insights_builder outputs) into
    horizon-specific rolling files, then prunes entries older than horizon.
    """
    as_of = as_of or _today()
    as_of_str = as_of.strftime("%Y-%m-%d")

    for horizon, filename in TOP50_DAILY_FILES.items():
        horizon_days = HORIZON_DAYS[horizon]
        daily_file = INSIGHTS_DIR / filename
        rolling_file = INSIGHTS_DIR / TOP50_ROLLING_FILES[horizon]

        top50 = _load_json(daily_file) or []
        if not isinstance(top50, list) or not top50:
            log(f"[top_performers] ℹ️ No Top-50 for horizon={horizon} at {daily_file}")
            continue

        rolling_rows = _load_json_gz(rolling_file)

        new_rows: List[Dict[str, Any]] = []
        for rec in top50:
            sym = rec.get("symbol") or rec.get("ticker")
            if not sym:
                continue
            pred_price = _extract_predicted_price(rec)
            if pred_price is None:
                continue

            new_rows.append(
                {
                    "date": as_of_str,
                    "symbol": sym,
                    "predicted_price": pred_price,
                    "confidence": rec.get("confidence"),
                    "score": rec.get("score"),
                    "horizon": horizon,
                }
            )

        combined = rolling_rows + new_rows

        # prune by horizon window
        cutoff = as_of - timedelta(days=horizon_days)
        pruned: List[Dict[str, Any]] = []
        for row in combined:
            d = _parse_date(str(row.get("date", "")))
            if not d:
                continue
            if d >= cutoff:
                pruned.append(row)

        _save_json_gz(rolling_file, pruned)
        log(
            f"[top_performers] ✅ Updated rolling Top-50 for {horizon}: "
            f"{len(rolling_rows)} → {len(pruned)} rows (added {len(new_rows)})"
        )


# --- Helpers for computing top performers ------------------------------------


def _group_mean_predicted(rolling_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Compute mean predicted_price per symbol in a rolling window.
    """
    sums: Dict[str, float] = {}
    counts: Dict[str, int] = {}

    for row in rolling_rows:
        sym = row.get("symbol")
        pp = row.get("predicted_price")
        if not sym or pp is None:
            continue
        try:
            val = float(pp)
        except Exception:
            continue
        sums[sym] = sums.get(sym, 0.0) + val
        counts[sym] = counts.get(sym, 0) + 1

    out: Dict[str, float] = {}
    for sym, total in sums.items():
        c = counts.get(sym, 0)
        if c > 0:
            out[sym] = total / c
    return out


# NOTE:
# For fully correct “live price” you may want to plug in Alpaca or dt_backend
# intraday bars. For now, we provide a hook. metrics_router can pass in its
# own price lookup function.


def compute_top_performers_for_horizon(
    horizon: str,
    price_lookup: Optional[callable] = None,
    as_of: Optional[date] = None,
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """
    Compute the top performers for a given horizon using rolling Top-50 history.

    - horizon: "1w", "2w", or "4w"
    - price_lookup: optional function(symbols: List[str]) -> Dict[str, float]
      If not provided, the caller is expected to fill in live prices later.

    Returns a list of dicts with:
        symbol, mean_predicted_price, live_price (if available),
        pct_diff, horizon
    """
    as_of = as_of or _today()
    rolling_file = INSIGHTS_DIR / TOP50_ROLLING_FILES[horizon]
    rows = _load_json_gz(rolling_file)
    if not rows:
        return []

    mean_pred = _group_mean_predicted(rows)
    symbols = sorted(mean_pred.keys())
    live_prices: Dict[str, float] = {}

    if price_lookup and symbols:
        try:
            live_prices = price_lookup(symbols) or {}
        except Exception as e:
            log(f"[top_performers] ⚠️ price_lookup failed: {e}")
            live_prices = {}

    out_rows: List[Tuple[str, float, Optional[float], float]] = []
    for sym, m_pred in mean_pred.items():
        live = live_prices.get(sym)
        if live is None:
            # If no live price, skip from ranking but keep predicted for debugging
            continue
        if m_pred <= 0:
            continue
        pct_diff = (live - m_pred) / m_pred
        out_rows.append((sym, m_pred, live, pct_diff))

    # sort by pct_diff descending
    out_rows.sort(key=lambda x: x[3], reverse=True)
    top = out_rows[:top_n]

    return [
        {
            "symbol": sym,
            "horizon": horizon,
            "mean_predicted_price": m_pred,
            "live_price": live,
            "pct_diff": pct_diff,
        }
        for (sym, m_pred, live, pct_diff) in top
    ]


def freeze_daily_top3(as_of: Optional[date] = None, price_lookup: Optional[callable] = None) -> None:
    """
    Compute and freeze top-3 performers for 1w and 4w horizons.
    Writes JSON files in PATHS["insights"]:

        top3_1w_today.json
        top3_4w_today.json

    These are meant to be stable for the trading day and used by the dashboard.
    """
    as_of = as_of or _today()
    for horizon in ("1w", "4w"):
        top3 = compute_top_performers_for_horizon(
            horizon=horizon,
            price_lookup=price_lookup,
            as_of=as_of,
            top_n=3,
        )
        out_path = INSIGHTS_DIR / TOP3_DAILY_FILES[horizon]
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "as_of": as_of.strftime("%Y-%m-%d"),
                        "horizon": horizon,
                        "top3": top3,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            log(f"[top_performers] ✅ Frozen daily top-3 for {horizon} → {out_path}")
        except Exception as e:
            log(f"[top_performers] ⚠️ Failed to write top-3 for {horizon}: {e}")


def get_frozen_top3() -> Dict[str, Any]:
    """
    Read the frozen daily top-3 JSON files for 1w and 4w, if present.
    Returns a dict keyed by horizon (e.g. {"1w": {...}, "4w": {...}})
    """
    out: Dict[str, Any] = {}
    for horizon, fname in TOP3_DAILY_FILES.items():
        path = INSIGHTS_DIR / fname
        data = _load_json(path)
        if data:
            out[horizon] = data
    return out
