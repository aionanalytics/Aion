# dt_backend/ml_data_builder_intraday.py — v1.0 (Intraday dataset builder)
# Builds a compact intraday feature set for fast training & scoring.
# Outputs: ml_data_dt/training_data_intraday.parquet

from __future__ import annotations
import os, sys
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Import safety so dt_backend can find backend
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Reuse your logger
try:
    from backend.data_pipeline import log, _read_rolling  # type: ignore
except Exception:
    def log(msg: str): print(msg, flush=True)
    def _read_rolling() -> dict: return {}
from dt_backend.dt_logger import dt_log as log
from dt_backend.config_dt import DT_PATHS  # DT paths (all *_dt)
from dt_backend.momentum_detector import compute_momentum_features
from dt_backend.orderflow_analyzer import build_orderflow_features
from dt_backend.sentiment_live import load_recent_sentiment

# Optional intraday provider (if you already have one)
# Expected signature: get_intraday_bars_bulk(symbols, interval='1m', lookback_minutes=390) -> Dict[str, DataFrame]
_INTRADAY_PROVIDER_AVAILABLE = False
def _get_intraday_bars_bulk(symbols: List[str], interval: str = "1m", lookback_minutes: int = 390) -> Dict[str, pd.DataFrame]:
    global _INTRADAY_PROVIDER_AVAILABLE
    try:
        from backend.live_prices_router import get_intraday_bars_bulk  # type: ignore
        _INTRADAY_PROVIDER_AVAILABLE = True
        return get_intraday_bars_bulk(symbols, interval=interval, lookback_minutes=lookback_minutes) or {}
    except Exception:
        _INTRADAY_PROVIDER_AVAILABLE = False
        return {}

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _now_ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _ensure_dirs():
    DT_PATHS["dtml_data"].mkdir(parents=True, exist_ok=True)
    (DT_PATHS["dtml_data"] / "signals").mkdir(parents=True, exist_ok=True)
    (DT_PATHS["dtmodels"]).mkdir(parents=True, exist_ok=True)

def _pick_symbols_for_dt(dt_rolling: dict | None) -> List[str]:
    """
    Choose a working intraday symbol set.
    Priority:
      1) DT rolling keys (if present)
      2) Fallback to top liquid names from backend rolling by marketCap
    """
    if isinstance(dt_rolling, dict) and dt_rolling:
        return list(dt_rolling.keys())

    # fallback: take top 500 by market cap from backend rolling
    base = _read_rolling() or {}
    rows = []
    for s, node in base.items():
        try:
            mc = float((node or {}).get("marketCap") or 0.0)
        except Exception:
            mc = 0.0
        rows.append((s, mc))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:500]]  # keep it sane for intraday

def _coerce_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["timestamp","open","high","low","close","volume"]
    out = df.copy()
    for c in ["open","high","low","close","volume"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    if "timestamp" in out.columns:
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out.dropna(subset=["close"]).reset_index(drop=True)
    if not all(col in out.columns for col in cols):
        missing = [c for c in cols if c not in out.columns]
        raise ValueError(f"Intraday bars missing columns: {missing}")
    return out

def _label_from_future_return(df: pd.DataFrame, horizon: int = 15, up=0.25, dn=-0.25) -> pd.Series:
    """
    Create BUY/SELL/HOLD labels from future % return over N bars (default 15 minutes).
    Thresholds in percent (e.g., +0.25% BUY, -0.25% SELL).
    """
    c = df["close"].astype(float)
    fut = c.shift(-horizon)
    ret = (fut - c) / c * 100.0
    label = pd.Series("HOLD", index=df.index, dtype=object)
    label[ret >= up] = "BUY"
    label[ret <= dn] = "SELL"
    return label

# ---------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------
def build_intraday_dataset(
    *,
    dt_rolling: Optional[dict] = None,
    interval: str = "1m",
    lookback_minutes: int = 390,    # full regular session
    max_symbols: int = 300,         # keep lightweight
    label_horizon: int = 15,        # 15-min ahead label
    buy_thr: float = 0.25,          # +0.25% → BUY
    sell_thr: float = -0.25,        # -0.25% → SELL
    include_sentiment: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Builds the intraday training frame and writes it to:
      ml_data_dt/training_data_intraday.parquet
    Returns the DataFrame (or None on failure).
    """
    _ensure_dirs()

    # 0) Symbol universe
    symbols = _pick_symbols_for_dt(dt_rolling)
    if not symbols:
        log("[ml_data_builder_intraday] ⚠️ No symbols available for intraday dataset.")
        return None
    if max_symbols and len(symbols) > max_symbols:
        symbols = symbols[:max_symbols]

    log(f"[ml_data_builder_intraday] 🚀 Building intraday dataset for {len(symbols)} symbols ({interval}, lookback={lookback_minutes}m).")

    # 1) Fetch intraday bars in bulk (provider optional)
    bars_map: Dict[str, pd.DataFrame] = _get_intraday_bars_bulk(symbols, interval=interval, lookback_minutes=lookback_minutes)

    # 2) Sentiment pulse (optional)
    senti_map = load_recent_sentiment() if include_sentiment else {}

    # 3) Transform each symbol → feature frame
    frames: List[pd.DataFrame] = []
    for sym in symbols:
        try:
            df = bars_map.get(sym)
            if df is None or df.empty:
                continue
            df = _coerce_ohlcv(df)

            # momentum & orderflow features
            f1 = compute_momentum_features(df)
            f2 = build_orderflow_features(f1)
            feat = f2

            # labels for training (optional use in classifier)
            feat["target_label_15m"] = _label_from_future_return(feat, horizon=label_horizon, up=buy_thr, dn=sell_thr)
            feat["target_ret_15m"] = (feat["close"].shift(-label_horizon) - feat["close"]) / feat["close"] * 100.0

            # enrich with symbol, sentiment
            feat["symbol"] = sym
            if include_sentiment:
                feat["sentiment_live"] = float(senti_map.get(sym.upper(), 0.0))

            # final cleaning
            feat = feat.dropna(subset=["close"]).reset_index(drop=True)

            frames.append(feat)
        except Exception as e:
            log(f"[ml_data_builder_intraday] ⚠️ {sym} feature build failed: {e}")

    if not frames:
        log("[ml_data_builder_intraday] ⚠️ No intraday frames built (provider missing or no data).")
        return None

    ddf = pd.concat(frames, axis=0, ignore_index=True)

    # 4) Basic column ordering (symbol, timestamp, label → features)
    cols_front = [c for c in ["symbol","timestamp","target_label_15m","target_ret_15m"] if c in ddf.columns]
    other = [c for c in ddf.columns if c not in cols_front]
    ddf = ddf[cols_front + other]

    # 5) Persist parquet
    out_path = DT_PATHS["dtml_data"] / "training_data_intraday.parquet"
    try:
        ddf.to_parquet(out_path, index=False)
        log(f"[ml_data_builder_intraday] ✅ Saved → {out_path} ({len(ddf):,} rows).")
    except Exception as e:
        log(f"[ml_data_builder_intraday] ⚠️ Failed to save parquet: {e}")

    # 6) Summary
    n_syms = ddf["symbol"].nunique() if "symbol" in ddf.columns else 0
    log(f"[ml_data_builder_intraday] 📊 Built {len(ddf):,} rows across {n_syms} symbols. Provider={'ON' if _INTRADAY_PROVIDER_AVAILABLE else 'OFF'}.")

    return ddf
