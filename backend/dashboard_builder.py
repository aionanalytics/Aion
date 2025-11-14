# backend/dashboard_builder.py
"""
Dashboard Intelligence v1.5.0
- Weighted accuracy badge (rolling 30 days, ±10% tolerance with your bucketed scoring)
- Top performer score cards (1w, 1m): freeze tickers + predicted prices nightly
"""

from __future__ import annotations
import os, json, glob, math, datetime as dt
from typing import Dict, List, Tuple, Any, Optional

DASHBOARD_CACHE_DIR = "dashboard_cache"
INSIGHTS_LATEST = "ml_data/daily_insights_{h}.json"
INSIGHTS_HISTORY_DIR = "ml_data/insights_history/{h}"  # optional, if you keep daily snapshots


def _ensure_cache_dir():
    os.makedirs(DASHBOARD_CACHE_DIR, exist_ok=True)


def _utcnow_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _load_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _list_history_files(horizon: str, days: int) -> List[str]:
    """List history files within the last N days, if you store them."""
    dir_ = INSIGHTS_HISTORY_DIR.format(h=horizon)
    if not os.path.isdir(dir_):
        return []
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
    files = []
    for p in sorted(glob.glob(os.path.join(dir_, "*.json"))):
        # Try to parse yyyymmdd from filename if present
        base = os.path.basename(p)
        stamp = None
        for token in base.split("_"):
            t = token.replace(".json", "")
            if len(t) == 8 and t.isdigit():
                try:
                    stamp = dt.datetime.strptime(t, "%Y%m%d")
                except Exception:
                    pass
        if stamp is None:
            # include if we can't parse a date (best-effort)
            files.append(p)
        else:
            if stamp >= cutoff:
                files.append(p)
    return files


def _gather_predictions(days: int = 30, horizons: Tuple[str, ...] = ("1w", "1m")) -> Dict[str, List[float]]:
    """
    Gather predicted prices for tickers across horizons within a rolling window.
    Returns a mapping: ticker -> [predicted_price1, predicted_price2, ...]
    """
    pred_map: Dict[str, List[float]] = {}

    def absorb_from_rows(rows: List[dict]):
        for r in rows:
            t = r.get("ticker")
            p = r.get("predictedPrice") or r.get("pred_price") or r.get("predicted_price")
            # If not present, try to derive from expectedReturnPct + currentPrice
            if p is None:
                cur = r.get("currentPrice")
                er = r.get("expectedReturnPct")
                try:
                    if isinstance(cur, (int, float)) and isinstance(er, (int, float)):
                        p = cur * (1.0 + er / 100.0)
                except Exception:
                    p = None
            if isinstance(t, str) and isinstance(p, (int, float)) and p > 0:
                pred_map.setdefault(t, []).append(float(p))

    for h in horizons:
        # include history if available
        for path in _list_history_files(h, days):
            data = _load_json(path)
            if isinstance(data, list):
                absorb_from_rows(data[:50])
            elif isinstance(data, dict) and isinstance(data.get("picks"), list):
                absorb_from_rows(data["picks"][:50])

        # include latest
        latest_path = INSIGHTS_LATEST.format(h=h)
        data = _load_json(latest_path)
        if isinstance(data, list):
            absorb_from_rows(data[:50])
        elif isinstance(data, dict) and isinstance(data.get("picks"), list):
            absorb_from_rows(data["picks"][:50])

    return pred_map


def _latest_actual_price_map() -> Dict[str, float]:
    """Get latest price per ticker from data_pipeline cache (best-effort)."""
    out: Dict[str, float] = {}
    try:
        from data_pipeline import STOCK_CACHE, load_all_cached_stocks
        load_all_cached_stocks()
        for t, rec in (STOCK_CACHE or {}).items():
            if isinstance(rec, dict):
                price = rec.get("price") or rec.get("last") or rec.get("close")
                if isinstance(price, (int, float)):
                    out[t] = float(price)
    except Exception:
        pass
    return out


def _weighted_score(pred: float, actual: float, tolerance: float = 10.0) -> float:
    """
    Your exact scoring:
    - If actual >= predicted: 1.0 (full point)
    - Else graded buckets by how far below predicted (capped at 10%)
      .01–2%: 0.8
      2.01–4%: 0.65
      4.01–6%: 0.5
      6.01–8%: 0.3
      8.01–10%: 0.1
      > 10% miss: 0
    """
    if not (isinstance(pred, (int, float)) and isinstance(actual, (int, float)) and pred > 0 and actual >= 0):
        return 0.0
    if actual >= pred:
        return 1.0
    miss_pct = (pred - actual) / pred * 100.0
    if miss_pct <= 0.0:
        return 1.0
    if miss_pct <= 2.0:
        return 0.8
    if miss_pct <= 4.0:
        return 0.65
    if miss_pct <= 6.0:
        return 0.5
    if miss_pct <= 8.0:
        return 0.3
    if miss_pct <= tolerance:
        return 0.1
    return 0.0


def compute_accuracy(days: int = 30, tolerance: float = 10.0, horizons: Tuple[str, ...] = ("1w", "1m")) -> Dict[str, Any]:
    """
    Builds the weighted accuracy badge over a rolling window.
    - Averages predicted prices for tickers appearing multiple times (across horizons if provided).
    - Compares to latest actual prices.
    """
    _ensure_cache_dir()
    pred_map = _gather_predictions(days=days, horizons=horizons)
    actual_map = _latest_actual_price_map()

    scores: List[float] = []
    abs_errors: List[float] = []

    for t, preds in pred_map.items():
        if not preds:
            continue
        mean_pred = sum(preds) / len(preds)
        actual = actual_map.get(t)
        if isinstance(actual, (int, float)):
            scores.append(_weighted_score(mean_pred, actual, tolerance))
            abs_errors.append(abs(mean_pred - actual) / mean_pred)

    out = {
        "accuracy_30d": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "avg_error": round(sum(abs_errors) / len(abs_errors), 4) if abs_errors else None,
        "tolerance": tolerance,
        "sample_size": len(scores),
        "last_updated": _utcnow_iso(),
        "summary": f"✅ Weighted accuracy {round((sum(scores)/len(scores))*100,1) if scores else 0.0}% within ±{int(tolerance)}%" if scores else "No data",
    }

    # cache to disk
    with open(os.path.join(DASHBOARD_CACHE_DIR, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _window_days_for(horizon: str) -> int:
    return 7 if horizon == "1w" else 30


def _freeze_top_tickers(horizon: str) -> List[Dict[str, Any]]:
    """
    Build the frozen list for a horizon:
    - For the window (7d for 1w, 30d for 1m), aggregate predicted prices per ticker (mean).
    - Rank by (current - mean_pred)/mean_pred % and keep top 3 tickers.
    - Store only ticker, pred_price (mean), frozen_on (today).
    """
    days = _window_days_for(horizon)
    pred_map = _gather_predictions(days=days, horizons=(horizon,))
    actual_map = _latest_actual_price_map()

    rows: List[Tuple[str, float, float]] = []  # (ticker, mean_pred, gain_pct_now)
    for t, preds in pred_map.items():
        if not preds:
            continue
        mean_pred = sum(preds) / len(preds)
        cur = actual_map.get(t)
        if isinstance(cur, (int, float)) and mean_pred > 0:
            gain_pct = (cur - mean_pred) / mean_pred * 100.0
            rows.append((t, mean_pred, gain_pct))

    rows.sort(key=lambda x: x[2], reverse=True)
    top = rows[:3]
    today = dt.datetime.utcnow().date().isoformat()
    frozen = [
        {"ticker": t, "pred_price": round(p, 4), "frozen_on": today}
        for (t, p, _g) in top
    ]
    return frozen


def compute_top_performers(horizon: str) -> Dict[str, Any]:
    """
    Freeze top performers for the requested horizon (store tickers + pred_price only).
    """
    _ensure_cache_dir()
    # Read current cache to preserve the other horizon if present
    cache_path = os.path.join(DASHBOARD_CACHE_DIR, "top_performers.json")
    base = _load_json(cache_path) or {}

    frozen = _freeze_top_tickers(horizon)
    base[horizon] = frozen
    base["last_updated"] = _utcnow_iso()

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)

    return {"horizon": horizon, "tickers": [r["ticker"] for r in frozen], "summary": f"✅ Top performers frozen ({horizon})"}
