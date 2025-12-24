# ===============================================================
# ml_data_builder.py — v2.7.0 (Windows-safe atomic parquet + price sanity + parquet integrity gates)
# backend/services/ml_data_builder
#
# Key upgrades in v2.7.0 (fixes “FINAL parquet corrupt / WinError 32 / target explosions”):
#   ✅ Atomic parquet writes on Windows (write → .tmp → validate → os.replace)
#   ✅ Raw parquet is also written atomically (prevents partial/corrupt RAW on crash)
#   ✅ Parquet integrity validation (schema + at least 1 batch) before publishing FINAL
#   ✅ Price sanity filter before targets:
#       - drop rows where close <= 0
#       - drop/NaN targets where close < MIN_VALID_CLOSE (default 1.0)
#     This kills the “+377x return from $0.80 close” label poison.
#   ✅ Robust Windows file-lock handling (retry/backoff on delete/replace)
#   ✅ latest_features snapshot no longer includes "name" (avoids object-dtype parquet memory spikes)
#   ✅ Explicit numeric coercion + float32 for ALL features/targets in artifacts
#
# Kept from v2.6.6:
#   ✅ Trading-day aligned targets (HORIZON_STEPS)
#   ✅ Protected prefixes from filtering
#   ✅ Target debug stats from sample rows
#   ✅ Streaming build, pyarrow parquet or CSV fallback
# ===============================================================

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Literal

from datetime import datetime
from multiprocessing import Pool, cpu_count

import numpy as np
import pandas as pd

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import (
    _read_rolling,
    _read_brain,
    safe_float,
    log,
)

from utils.progress_bar import progress_bar

# ---------------------------------------------------------------
# Platform flags
# ---------------------------------------------------------------
WINDOWS = platform.system().lower().startswith("win")

# ---------------------------------------------------------------
# Paths
# ---------------------------------------------------------------
ML_ROOT: Path = Path(PATHS.get("ml_data", "ml_data"))
DATASET_DIR: Path = ML_ROOT / "nightly" / "dataset"
DATASET_DIR.mkdir(parents=True, exist_ok=True)

DATASET_FILE = DATASET_DIR / "training_data_daily.parquet"
RAW_DATASET_FILE = DATASET_DIR / "training_data_daily.raw.parquet"
FEATURE_LIST_FILE = DATASET_DIR / "feature_list_daily.json"

LATEST_FEATURES_FILE = DATASET_DIR / "latest_features_daily.parquet"
LATEST_FEATURES_CSV = DATASET_DIR / "latest_features_daily.csv"

MACRO_STATE_FILE = Path(PATHS.get("macro_state", ML_ROOT / "macro_state.json"))
NEWS_FEATURES_DIR: Path = ML_ROOT / "news_features"

CSV_CHUNKS_DIR: Path = DATASET_DIR / "_csv_chunks"
CSV_CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

_GLOBAL: Dict[str, Any] = {}

MAX_RETURN_ROWS = int(os.getenv("ML_BUILDER_MAX_RETURN_ROWS", "200000") or "200000")
SAMPLE_RETURN_ROWS = int(os.getenv("ML_BUILDER_SAMPLE_RETURN_ROWS", "50000") or "50000")

# ---------------------------------------------------------------
# New: sanity knobs
# ---------------------------------------------------------------
MIN_VALID_CLOSE: float = float(os.getenv("AION_MIN_VALID_CLOSE", "1.0") or "1.0")  # kills penny/near-zero divide explosions
MAX_ABS_TARGET_RET: float = float(os.getenv("AION_MAX_ABS_TARGET_RET", "20.0") or "20.0")  # hard safety clamp for extreme label errors

# ===============================================================
# PyArrow helpers (streaming parquet)
# ===============================================================
def _try_import_pyarrow():
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
        import pyarrow.dataset as ds  # type: ignore
        return pa, pq, ds
    except Exception as e:
        log(f"[ml_data_builder] ⚠️ pyarrow not available: {e}")
        return None, None, None


# ===============================================================
# Windows-safe filesystem helpers
# ===============================================================
def _win_retry(op_name: str, fn, *, attempts: int = 8, base_sleep: float = 0.15):
    """
    Windows loves holding file handles briefly after closing.
    Retry common ops (unlink/replace) with backoff + jitter.
    """
    last_err = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if not WINDOWS:
                break
            sleep = base_sleep * (1.7 ** i) + (0.03 * (i + 1))
            time.sleep(min(sleep, 1.25))
    raise last_err  # type: ignore


def _safe_unlink(p: Path) -> None:
    if not p.exists():
        return
    def _do():
        try:
            p.unlink()
        except FileNotFoundError:
            return
    try:
        _win_retry(f"unlink:{p.name}", _do)
    except Exception:
        pass


def _atomic_replace(src: Path, dst: Path) -> None:
    """
    Atomic publish: os.replace is atomic on Windows and POSIX.
    Retries on WinError 32.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    def _do():
        os.replace(str(src), str(dst))
    _win_retry(f"replace:{src.name}->{dst.name}", _do)


def _parquet_preflight_or_raise(parquet_path: Path, *, columns: Optional[List[str]] = None) -> None:
    """
    Validate parquet integrity:
      - exists, non-empty
      - pyarrow can open schema
      - scanning yields at least 1 non-empty batch
    """
    if not parquet_path.exists():
        raise FileNotFoundError(f"[DATA PREFLIGHT] Missing parquet: {parquet_path}")
    if parquet_path.stat().st_size <= 0:
        raise RuntimeError(f"[DATA PREFLIGHT] Empty parquet file: {parquet_path}")

    pa, pq, ds = _try_import_pyarrow()
    if pa is None or pq is None or ds is None:
        raise RuntimeError("[DATA PREFLIGHT] pyarrow required for parquet preflight but unavailable")

    try:
        dset = ds.dataset(str(parquet_path), format="parquet")
        # schema access (forces footer read)
        _ = dset.schema

        cols = columns if columns is not None else []
        scanner = dset.scanner(columns=cols, batch_size=10_000)
        got = False
        for b in scanner.to_batches():
            if b is not None and b.num_rows > 0:
                got = True
                break
        if not got:
            raise RuntimeError("[DATA PREFLIGHT] Parquet readable but yielded no rows.")
    except Exception as e:
        raise RuntimeError(f"[DATA PREFLIGHT] Failed parquet scan: {e}")


# ===============================================================
# Helpers
# ===============================================================
def _load_macro() -> Dict[str, float]:
    try:
        if not MACRO_STATE_FILE.exists():
            log("[ml_data_builder] ℹ️ No macro_state.json — macro features empty.")
            return {}
        raw = json.loads(MACRO_STATE_FILE.read_text(encoding="utf-8"))
        out = {f"macro_{k}": safe_float(v) for k, v in raw.items()}
        log(f"[ml_data_builder] ✅ Loaded {len(out)} macro features.")
        return out
    except Exception as e:
        log(f"[ml_data_builder] ⚠️ Failed loading macro state: {e}")
        return {}


def _flatten_numeric(prefix: str, d: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        out[f"{prefix}{k}"] = safe_float(v)
    return out


# ===============================================================
# News features
# ===============================================================
def _latest_news_features_file() -> Optional[Path]:
    try:
        if not NEWS_FEATURES_DIR.exists():
            return None
        candidates = sorted(NEWS_FEATURES_DIR.glob("news_features_*.json"))
        if not candidates:
            return None
        return candidates[-1]
    except Exception as e:
        log(f"[ml_data_builder] ⚠️ Failed scanning news_features dir: {e}")
        return None


def _load_news_features() -> Dict[str, Dict[str, Any]]:
    path = _latest_news_features_file()
    if not path or not path.exists():
        log("[ml_data_builder] ℹ️ No news_features_*.json found — news features disabled for this build.")
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        symbols = raw.get("symbols") or {}
        if not isinstance(symbols, dict):
            log("[ml_data_builder] ⚠️ news_features file symbols not dict; ignoring.")
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        for sym, node in symbols.items():
            if not isinstance(node, dict):
                continue
            lh = node.get("long_horizon") or {}
            bz = node.get("buzz") or {}

            feats: Dict[str, Any] = {
                "news_sentiment_mean": safe_float(lh.get("sentiment_mean")),
                "news_sentiment_weighted": safe_float(lh.get("sentiment_weighted")),
                "news_sentiment_max": safe_float(lh.get("sentiment_max")),
                "news_article_count": safe_float(lh.get("article_count")),
                "news_recency_weight_sum": safe_float(lh.get("recency_weight_sum")),
                "news_buzz_count": safe_float(bz.get("buzz_count")),
                "news_buzz_score": safe_float(bz.get("buzz_score")),
            }
            out[str(sym).upper()] = feats

        log(f"[ml_data_builder] ✅ Loaded news features for {len(out)} symbols from {path.name}")
        return out

    except Exception as e:
        log(f"[ml_data_builder] ⚠️ Failed loading news_features: {e}")
        return {}


def _build_news_feature_block(
    sym: str,
    node: Dict[str, Any],
    news_intel: Dict[str, Dict[str, Any]],
) -> Dict[str, float]:
    sym_u = sym.upper()
    out: Dict[str, float] = {}

    intel = news_intel.get(sym_u)
    if intel:
        for k, v in intel.items():
            out[str(k)] = safe_float(v)
        return out

    news = node.get("news") or {}
    if isinstance(news, dict) and (safe_float(news.get("buzz", 0)) != 0 or news):
        out.update(_flatten_numeric("news_", news))

    return out


# ===============================================================
# Forward returns targets (TRADING-DAY STEP BASED)
# ===============================================================
# Must match accuracy_engine.HORIZON_STEPS
HORIZON_STEPS: Dict[str, int] = {
    "1d": 1,
    "3d": 3,
    "1w": 5,
    "2w": 10,
    "4w": 20,
    "13w": 65,
    "26w": 130,
    "52w": 260,
}


def _add_forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Trading-day aligned targets:
      target_ret_h = (close[t+steps] - close[t]) / close[t]

    Safety:
      - if close <= 0 → drop earlier; but we still guard
      - if close < MIN_VALID_CLOSE → targets set to NaN (prevents divide explosions)
      - clamp absurd targets to NaN if |ret| > MAX_ABS_TARGET_RET (dataset corruption guard)
    """
    if df.empty or "close" not in df.columns:
        return df

    close = pd.to_numeric(df["close"], errors="coerce").replace([np.inf, -np.inf], np.nan).astype(float)

    # close sanity
    close = close.where(close > 0.0, np.nan)

    # known-bad denominator region
    denom_ok = close >= float(MIN_VALID_CLOSE)

    for label, steps in HORIZON_STEPS.items():
        fut = close.shift(-int(steps))
        tgt = (fut - close) / close
        # if denom not ok, blank out target
        tgt = tgt.where(denom_ok, np.nan)
        # if absurd, blank out (poison guard)
        tgt = tgt.where(tgt.abs() <= float(MAX_ABS_TARGET_RET), np.nan)
        df[f"target_ret_{label}"] = tgt.astype(float)

    return df


# ===============================================================
# Rolling/tech stats (vectorized)
# ===============================================================
def _add_vectorized_stats(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    for col in ("close", "high", "low"):
        if col not in df.columns:
            df[col] = np.nan

    ret1 = df["close"].pct_change(1)
    df["ret1"] = ret1
    df["ret5"] = df["close"].pct_change(5)
    df["ret10"] = df["close"].pct_change(10)

    exp = ret1.expanding(min_periods=3)
    df["roll_vol"] = exp.std()
    df["roll_skew"] = exp.skew()
    df["roll_kurt"] = exp.kurt()

    hl_range = (df["high"] - df["low"]).replace([np.inf, -np.inf], np.nan)
    df["roll_atr"] = hl_range.expanding(min_periods=2).mean()

    df["velocity"] = (df["close"] / df["close"].shift(3) - 1.0).replace([np.inf, -np.inf], np.nan)
    df["acceleration"] = ret1 - ret1.shift(2)

    df["tech_ret_1"] = df["ret1"]
    df["tech_ret_5"] = df["ret5"]
    df["tech_ret_10"] = df["ret10"]
    df["tech_volatility_10d"] = ret1.rolling(10, min_periods=2).std()
    df["tech_momentum_5d"] = (df["close"] / df["close"].shift(5) - 1.0).replace([np.inf, -np.inf], np.nan)

    cols = [
        "roll_vol",
        "roll_skew",
        "roll_kurt",
        "roll_atr",
        "velocity",
        "acceleration",
        "tech_ret_1",
        "tech_ret_5",
        "tech_ret_10",
        "tech_volatility_10d",
        "tech_momentum_5d",
    ]
    df[cols] = df[cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return df


# ===============================================================
# Brain / Drift
# ===============================================================
def _extract_brain_features_for_symbol(sym: str, brain: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}

    if not isinstance(brain, dict):
        return out

    bnode = brain.get(sym.upper()) or {}
    perf = bnode.get("horizon_perf") or {}
    if not isinstance(perf, dict):
        return out

    for h, values in perf.items():
        if not isinstance(values, dict):
            continue
        drift_val = values.get("drift_score", values.get("drift", 0.0))
        stats = values.get("short_stats", {}) or {}
        hit = stats.get("hit_ratio", stats.get("hit_ratio_dir", 0.5))
        mae = stats.get("mae", 0.0)

        out[f"drift_{h}"] = safe_float(drift_val)
        out[f"hit_ratio_{h}"] = safe_float(hit)
        out[f"mae_{h}"] = safe_float(mae)

    return out


# ===============================================================
# Sector one-hot (stable schema)
# ===============================================================
def _collect_sectors(rolling: Dict[str, Any]) -> List[str]:
    sectors: set[str] = set()
    for sym, node in (rolling or {}).items():
        if str(sym).startswith("_") or not isinstance(node, dict):
            continue
        sec = node.get("sector") or (node.get("fundamentals") or {}).get("sector") or ""
        if not isinstance(sec, str):
            continue
        sec = sec.upper().strip()
        if sec:
            sectors.add(sec)
    return sorted(sectors)


def _sector_one_hot(sector: str, all_sectors: List[str]) -> Dict[str, float]:
    sec_u = (sector or "").upper().strip()
    out: Dict[str, float] = {}
    for s in all_sectors:
        out[f"sector_{s}"] = 1.0 if s == sec_u else 0.0
    return out


# ===============================================================
# Row Builder
# ===============================================================
def _build_rows_for_symbol(
    sym: str,
    node: Dict[str, Any],
    macro: Dict[str, float],
    brain: Dict[str, Any],
    news_intel: Dict[str, Dict[str, Any]],
    all_sectors: List[str],
    as_of_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    history = node.get("history") or []
    if len(history) < 15:
        return []

    hist_df = pd.DataFrame(history).copy()
    if "date" not in hist_df.columns or "close" not in hist_df.columns:
        return []

    hist_df["date"] = pd.to_datetime(hist_df["date"], errors="coerce")
    hist_df = hist_df.sort_values("date").reset_index(drop=True)

    hist_df["close"] = pd.to_numeric(hist_df["close"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    hist_df["volume"] = pd.to_numeric(hist_df.get("volume", 0.0), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # hard drop invalid closes
    hist_df = hist_df.dropna(subset=["date", "close"])
    hist_df = hist_df[hist_df["close"] > 0.0]

    if len(hist_df) < 15:
        return []

    if "high" not in hist_df.columns:
        hist_df["high"] = hist_df["close"]
    if "low" not in hist_df.columns:
        hist_df["low"] = hist_df["close"]

    hist_df = _add_vectorized_stats(hist_df)
    hist_df = _add_forward_returns(hist_df)

    sector = node.get("sector") or (node.get("fundamentals") or {}).get("sector") or ""
    if not isinstance(sector, str):
        sector = ""

    base_row: Dict[str, Any] = {
        "symbol": sym.upper(),
        "name": node.get("name") or sym.upper(),
    }
    base_row.update(_sector_one_hot(sector, all_sectors))

    base_row.update(_flatten_numeric("fund_", node.get("fundamentals") or {}))
    base_row.update(_flatten_numeric("met_", node.get("metrics") or {}))

    shares = safe_float(node.get("shares_outstanding"))
    public_float = safe_float(node.get("public_float"))
    market_cap = safe_float((node.get("fundamentals") or {}).get("marketCap"))

    base_row["float_ratio"] = 0.0
    base_row["cap_per_share"] = 0.0
    if shares and shares > 0:
        base_row["float_ratio"] = (public_float or 0.0) / shares
        base_row["cap_per_share"] = (market_cap or 0.0) / shares

    base_row.update(_flatten_numeric("ctx_", node.get("context") or {}))
    base_row.update(_build_news_feature_block(sym, node, news_intel))

    social = node.get("social") or {}
    if isinstance(social, dict) and (safe_float(social.get("buzz", 0)) != 0 or social):
        base_row.update(_flatten_numeric("soc_", social))

    base_row.update(macro)
    base_row.update(_extract_brain_features_for_symbol(sym, brain))

    rows: List[Dict[str, Any]] = []
    n = len(hist_df)
    start_idx = 10
    last_date = hist_df["date"].iloc[-1]

    for idx in range(start_idx, n):
        bar = hist_df.iloc[idx]

        close_val = safe_float(bar.get("close"))
        # We keep the row, but if close < MIN_VALID_CLOSE we still compute features; targets were set NaN.
        if close_val is None or close_val <= 0:
            continue

        row = dict(base_row)
        row["asof_date"] = bar["date"].isoformat()
        if as_of_date and row["asof_date"] and str(row["asof_date"]) > str(as_of_date):
            break

        row["close"] = close_val
        row["volume"] = safe_float(bar.get("volume"))

        row["roll_vol"] = float(bar.get("roll_vol", 0.0) or 0.0)
        row["roll_skew"] = float(bar.get("roll_skew", 0.0) or 0.0)
        row["roll_kurt"] = float(bar.get("roll_kurt", 0.0) or 0.0)
        row["roll_atr"] = float(bar.get("roll_atr", 0.0) or 0.0)
        row["velocity"] = float(bar.get("velocity", 0.0) or 0.0)
        row["acceleration"] = float(bar.get("acceleration", 0.0) or 0.0)

        row["tech_ret_1"] = float(bar.get("tech_ret_1", 0.0) or 0.0)
        row["tech_ret_5"] = float(bar.get("tech_ret_5", 0.0) or 0.0)
        row["tech_ret_10"] = float(bar.get("tech_ret_10", 0.0) or 0.0)
        row["tech_volatility_10d"] = float(bar.get("tech_volatility_10d", 0.0) or 0.0)
        row["tech_momentum_5d"] = float(bar.get("tech_momentum_5d", 0.0) or 0.0)

        for label in HORIZON_STEPS.keys():
            col = f"target_ret_{label}"
            v = bar.get(col, np.nan)
            try:
                fv = float(v) if v is not None else float("nan")
            except Exception:
                fv = float("nan")
            row[col] = fv

        rows.append(row)

        # keep last bar per symbol for prediction snapshot efficiency
        if (last_date - bar["date"]).days < 1:
            break

    return rows


# ===============================================================
# Feature Filtering (protected)
# ===============================================================
PROTECTED_FEATURE_PREFIXES = (
    "tech_",
    "sector_",
    "float_ratio",
    "cap_per_share",
    "news_",
    # critical learning prefixes
    "drift_",
    "hit_ratio_",
    "mae_",
    "macro_",
    "ctx_",
    "regime_",
    "risk_",
    "perf_",
)


def _is_protected(col: str) -> bool:
    return col.startswith(PROTECTED_FEATURE_PREFIXES)


class _RunningStats:
    """
    Streaming stats per feature:
      - missing rate
      - variance/std estimate
      - zero rate
    """
    def __init__(self):
        self.total_rows: int = 0
        self.na: Dict[str, int] = {}
        self.zero: Dict[str, int] = {}
        self.n: Dict[str, int] = {}
        self.mean: Dict[str, float] = {}
        self.m2: Dict[str, float] = {}

    def update_from_df(self, df: pd.DataFrame, feature_cols: List[str]) -> None:
        if df is None or df.empty:
            return

        self.total_rows += int(len(df))

        for c in feature_cols:
            if c not in df.columns:
                continue

            s = pd.to_numeric(df[c], errors="coerce").replace([np.inf, -np.inf], np.nan)

            na_cnt = int(s.isna().sum())
            self.na[c] = self.na.get(c, 0) + na_cnt

            v = s.dropna()
            if v.empty:
                continue

            arr = v.values.astype(float, copy=False)

            self.zero[c] = self.zero.get(c, 0) + int((arr == 0.0).sum())

            k0 = self.n.get(c, 0)
            mu0 = self.mean.get(c, 0.0)
            m20 = self.m2.get(c, 0.0)

            k1 = int(arr.size)
            mu1 = float(arr.mean())
            m21 = float(((arr - mu1) ** 2).sum())

            if k0 == 0:
                self.n[c] = k1
                self.mean[c] = mu1
                self.m2[c] = m21
                continue

            k = k0 + k1
            delta = mu1 - mu0

            mu = mu0 + delta * (k1 / k)
            m2 = m20 + m21 + (delta ** 2) * (k0 * k1 / k)

            self.n[c] = k
            self.mean[c] = mu
            self.m2[c] = m2

    def std(self, c: str) -> float:
        k = self.n.get(c, 0)
        if k <= 1:
            return 0.0
        return float(np.sqrt(self.m2.get(c, 0.0) / (k - 1)))

    def missing_rate(self, c: str) -> float:
        if self.total_rows <= 0:
            return 1.0
        return float(self.na.get(c, 0) / max(1, self.total_rows))

    def zero_rate(self, c: str) -> float:
        denom = max(1, self.n.get(c, 0))
        return float(self.zero.get(c, 0) / denom)


def _filter_missing_from_stats(stats: _RunningStats, feats: List[str]) -> List[str]:
    keep: List[str] = []
    for c in feats:
        if _is_protected(c):
            keep.append(c)
            continue
        if stats.missing_rate(c) <= 0.40:
            keep.append(c)
    log(f"[ml_data_builder] Missing filter (stream): {len(keep)}/{len(feats)} kept")
    return keep


def _filter_low_variance_from_stats(stats: _RunningStats, feats: List[str]) -> List[str]:
    keep: List[str] = []
    dropped = 0
    for c in feats:
        if _is_protected(c):
            keep.append(c)
            continue
        std = stats.std(c)
        if std < 1e-9 or stats.zero_rate(c) > 0.98:
            dropped += 1
            continue
        keep.append(c)
    log(f"[ml_data_builder] Low-variance filter (stream): {len(keep)}/{len(feats)} kept (dropped={dropped})")
    return keep


def _filter_correlation_sample(df_sample: pd.DataFrame, feats: List[str], threshold: float = 0.98) -> List[str]:
    FEAT_CAP = 350
    if df_sample is None or df_sample.empty:
        log("[ml_data_builder] Corr filter skipped: sample empty")
        return feats
    if len(feats) > FEAT_CAP:
        log(f"[ml_data_builder] Skipping corr filter: {len(feats)} > {FEAT_CAP}")
        return feats

    use_cols = [c for c in feats if c in df_sample.columns]
    if not use_cols:
        return feats

    corr = df_sample[use_cols].corr().abs()
    drop = set()

    for i in range(len(use_cols)):
        ci = use_cols[i]
        if ci in drop or _is_protected(ci):
            continue
        for j in range(i + 1, len(use_cols)):
            cj = use_cols[j]
            if cj in drop or _is_protected(cj):
                continue
            if corr.iloc[i, j] > threshold:
                drop.add(cj)

    kept = [c for c in feats if c not in drop]
    log(f"[ml_data_builder] Corr filter (sample): {len(kept)}/{len(feats)} kept (dropped={len(drop)})")
    return kept


# ===============================================================
# Schema inference
# ===============================================================
def _infer_feature_keys(
    rolling: Dict[str, Any],
    macro: Dict[str, float],
    brain: Dict[str, Any],
    news_intel: Dict[str, Dict[str, Any]],
) -> List[str]:
    keys: set[str] = set()

    keys.update([
        "close", "volume",
        "roll_vol", "roll_skew", "roll_kurt", "roll_atr",
        "velocity", "acceleration",
        "tech_ret_1", "tech_ret_5", "tech_ret_10", "tech_volatility_10d", "tech_momentum_5d",
        "float_ratio", "cap_per_share",
    ])

    keys.update(macro.keys())

    keys.update([
        "news_sentiment_mean",
        "news_sentiment_weighted",
        "news_sentiment_max",
        "news_article_count",
        "news_recency_weight_sum",
        "news_buzz_count",
        "news_buzz_score",
    ])

    if isinstance(brain, dict):
        for sym, bnode in brain.items():
            if not isinstance(bnode, dict) or str(sym).startswith("_"):
                continue
            perf = bnode.get("horizon_perf") or {}
            if not isinstance(perf, dict):
                continue
            for h in perf.keys():
                keys.add(f"drift_{h}")
                keys.add(f"hit_ratio_{h}")
                keys.add(f"mae_{h}")

    for sym, node in (rolling or {}).items():
        if str(sym).startswith("_") or not isinstance(node, dict):
            continue
        fnd = node.get("fundamentals") or {}
        met = node.get("metrics") or {}
        ctx = node.get("context") or {}
        soc = node.get("social") or {}
        nws = node.get("news") or {}

        if isinstance(fnd, dict):
            keys.update({f"fund_{k}" for k in fnd.keys()})
        if isinstance(met, dict):
            keys.update({f"met_{k}" for k in met.keys()})
        if isinstance(ctx, dict):
            keys.update({f"ctx_{k}" for k in ctx.keys()})
        if isinstance(soc, dict):
            keys.update({f"soc_{k}" for k in soc.keys()})
        if isinstance(nws, dict):
            keys.update({f"news_{k}" for k in nws.keys()})

    return sorted(keys)


def _full_column_plan(
    rolling: Dict[str, Any],
    macro: Dict[str, float],
    brain: Dict[str, Any],
    news_intel: Dict[str, Dict[str, Any]],
    all_sectors: List[str],
) -> Tuple[List[str], List[str], List[str]]:
    id_cols = ["symbol", "name", "asof_date"]
    target_cols = [f"target_ret_{h}" for h in HORIZON_STEPS.keys()]

    feature_cols = _infer_feature_keys(rolling, macro, brain, news_intel)
    for s in all_sectors:
        feature_cols.append(f"sector_{s}")
    feature_cols = sorted(set(feature_cols))
    feature_cols = [c for c in feature_cols if c not in target_cols and c not in id_cols]

    return id_cols, feature_cols, target_cols


def _rows_to_df(rows: List[Dict[str, Any]], id_cols: List[str], feature_cols: List[str], target_cols: List[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=id_cols + feature_cols + target_cols)

    df = pd.DataFrame(rows)
    for c in id_cols + feature_cols + target_cols:
        if c not in df.columns:
            df[c] = np.nan
    return df[id_cols + feature_cols + target_cols]


# ===============================================================
# Multiprocessing (POSIX only)
# ===============================================================
def _init_worker(
    rolling: Dict[str, Any],
    macro: Dict[str, float],
    brain: Dict[str, Any],
    news_intel: Dict[str, Dict[str, Any]],
    all_sectors: List[str],
    debug: bool = False,
) -> None:
    global _GLOBAL
    _GLOBAL["rolling"] = rolling
    _GLOBAL["macro"] = macro
    _GLOBAL["brain"] = brain
    _GLOBAL["news_intel"] = news_intel
    _GLOBAL["all_sectors"] = all_sectors
    _GLOBAL["debug"] = debug


def _worker_build_rows(sym: str) -> List[Dict[str, Any]]:
    try:
        rolling = _GLOBAL.get("rolling") or {}
        macro = _GLOBAL.get("macro") or {}
        brain = _GLOBAL.get("brain") or {}
        news_intel = _GLOBAL.get("news_intel") or {}
        all_sectors = _GLOBAL.get("all_sectors") or []
        node = rolling.get(sym)
        if node is None:
            return []
        return _build_rows_for_symbol(sym, node, macro, brain, news_intel, all_sectors)
    except Exception as e:
        log(f"[ml_data_builder] ⚠️ Worker error for {sym}: {e}")
        return []


# ===============================================================
# PUBLIC — Build Dataset (streaming)
# ===============================================================
ReturnMode = Literal["auto", "full", "sample", "none"]


def _wipe_old_outputs():
    for p in (RAW_DATASET_FILE, DATASET_FILE, LATEST_FEATURES_FILE, LATEST_FEATURES_CSV):
        _safe_unlink(p)
    try:
        if CSV_CHUNKS_DIR.exists():
            for f in CSV_CHUNKS_DIR.glob("chunk_*.csv"):
                _safe_unlink(f)
    except Exception:
        pass


def build_ml_dataset(
    as_of_date: Optional[str] = None,
    strict: bool = False,
    use_multiprocessing: bool = True,
    debug: bool = False,
    max_symbols: Optional[int] = None,
    chunk_symbols: int = 50,
    corr_sample_rows: int = 50_000,
    rewrite_final: bool = True,
    return_dataframe: ReturnMode = "auto",
) -> pd.DataFrame:
    log("=======================================================")
    log(f"[ml_data_builder] 🚀 Starting ML dataset build… v2.7.0 (mp={use_multiprocessing}, debug={debug}, chunk_symbols={chunk_symbols})")

    pa, pq, ds = _try_import_pyarrow()
    have_pyarrow = pa is not None and pq is not None and ds is not None

    if strict:
        # Deterministic-ish replay: avoid multiprocess nondeterminism.
        use_multiprocessing = False

    if WINDOWS and use_multiprocessing:
        log("[ml_data_builder] ⚠ Detected Windows — disabling multiprocessing for stability.")
        use_multiprocessing = False

    if max_symbols is None:
        try:
            env_cap = int(os.getenv("ML_BUILDER_MAX_SYMBOLS", "0") or "0")
        except ValueError:
            env_cap = 0
        if env_cap > 0:
            max_symbols = env_cap
            log(f"[ml_data_builder] 🔬 Applying env symbol cap: ML_BUILDER_MAX_SYMBOLS={env_cap}")

    rolling = _read_rolling() or {}
    if not rolling:
        log("[ml_data_builder] ⚠️ Rolling empty — aborting build")
        return pd.DataFrame()

    macro = _load_macro()
    brain = _read_brain() or {}
    news_intel = _load_news_features()
    all_sectors = _collect_sectors(rolling)

    symbols: List[str] = [sym for sym in rolling.keys() if not str(sym).startswith("_")]
    total_symbols = len(symbols)

    if max_symbols is not None and max_symbols > 0:
        symbols = symbols[:max_symbols]
        log(f"[ml_data_builder] 🔬 Limiting symbols for this run: {len(symbols)}/{total_symbols} (max_symbols={max_symbols})")
    else:
        log(f"[ml_data_builder] 📈 Symbols to process: {total_symbols}")

    if not symbols:
        log("[ml_data_builder] ⚠️ No symbols to process after filtering.")
        return pd.DataFrame()

    id_cols, feature_cols_all, target_cols = _full_column_plan(rolling, macro, brain, news_intel, all_sectors)
    all_cols = id_cols + feature_cols_all + target_cols

    _wipe_old_outputs()

    stats = _RunningStats()
    total_rows_written = 0

    sample_frames: List[pd.DataFrame] = []
    sample_rows_count = 0

    latest_rows: List[Dict[str, Any]] = []

    def _append_sample(df_batch: pd.DataFrame) -> None:
        nonlocal sample_rows_count
        if corr_sample_rows <= 0:
            return
        if df_batch is None or df_batch.empty:
            return
        remaining = corr_sample_rows - sample_rows_count
        if remaining <= 0:
            return
        take = min(remaining, len(df_batch))
        if take <= 0:
            return
        sample_frames.append(df_batch.iloc[:take].copy())
        sample_rows_count += take

    parquet_writer = None
    raw_tmp = RAW_DATASET_FILE.with_suffix(".raw.tmp.parquet") if have_pyarrow else None
    csv_chunk_idx = 0

    def _normalize_batch(df_batch: pd.DataFrame) -> pd.DataFrame:
        if df_batch is None or df_batch.empty:
            return df_batch

        # update stats BEFORE filling NaNs to measure real missing rate
        stats.update_from_df(df_batch, feature_cols_all)
        _append_sample(df_batch)

        # numeric coercion
        for c in feature_cols_all + target_cols:
            if c in df_batch.columns:
                df_batch[c] = pd.to_numeric(df_batch[c], errors="coerce").replace([np.inf, -np.inf], np.nan)

        # required id cols as strings (avoid surprise object mixing)
        df_batch["symbol"] = df_batch["symbol"].astype(str)
        df_batch["name"] = df_batch["name"].astype(str)
        df_batch["asof_date"] = df_batch["asof_date"].astype(str)

        # fill numeric
        df_batch[feature_cols_all] = df_batch[feature_cols_all].fillna(0.0)
        df_batch[target_cols] = df_batch[target_cols].astype(float)

        # float32 everywhere for model columns
        df_batch[feature_cols_all] = df_batch[feature_cols_all].astype("float32", copy=False)
        df_batch[target_cols] = df_batch[target_cols].astype("float32", copy=False)

        return df_batch

    def _write_batch(df_batch: pd.DataFrame) -> None:
        nonlocal parquet_writer, total_rows_written, csv_chunk_idx

        if df_batch is None or df_batch.empty:
            return

        df_batch = _normalize_batch(df_batch)
        df_out = df_batch[all_cols]

        if have_pyarrow:
            table = pa.Table.from_pandas(df_out, preserve_index=False)
            if parquet_writer is None:
                # RAW is written to tmp first (atomic publish at end)
                parquet_writer = pq.ParquetWriter(str(raw_tmp), table.schema, compression="snappy")
            parquet_writer.write_table(table)
        else:
            chunk_path = CSV_CHUNKS_DIR / f"chunk_{csv_chunk_idx:05d}.csv"
            df_out.to_csv(chunk_path, index=False)
            csv_chunk_idx += 1

        total_rows_written += int(len(df_out))

    # ---------------------------
    # Build rows
    # ---------------------------
    try:
        if not use_multiprocessing:
            log("[ml_data_builder] 🪫 Multiprocessing disabled — running single-process mode.")
            buf_rows: List[Dict[str, Any]] = []
            buf_syms = 0

            for sym in progress_bar(
                symbols,
                desc="[ml_data_builder] Building symbol rows (single)",
                unit="sym",
                total=len(symbols),
            ):
                node = rolling.get(sym)
                if node is None:
                    continue

                rows = _build_rows_for_symbol(sym, node, macro, brain, news_intel, all_sectors)
                if rows:
                    buf_rows.extend(rows)
                    latest_rows.append(rows[-1])

                buf_syms += 1
                if buf_syms >= max(1, int(chunk_symbols)):
                    df_batch = _rows_to_df(buf_rows, id_cols, feature_cols_all, target_cols)
                    _write_batch(df_batch)
                    buf_rows.clear()
                    buf_syms = 0

            if buf_rows:
                df_batch = _rows_to_df(buf_rows, id_cols, feature_cols_all, target_cols)
                _write_batch(df_batch)

        else:
            raw_cpus = max(cpu_count(), 1)
            workers = min(max(raw_cpus // 2, 2), 8)
            log(f"[ml_data_builder] 🧵 Using {workers} workers for {len(symbols)} symbols (host CPUs={raw_cpus})")

            chunksize = max(1, len(symbols) // (workers * 4))

            with Pool(
                processes=workers,
                initializer=_init_worker,
                initargs=(rolling, macro, brain, news_intel, all_sectors, debug),
            ) as pool:
                for rows in progress_bar(
                    pool.imap_unordered(_worker_build_rows, symbols, chunksize=chunksize),
                    desc="[ml_data_builder] Building symbol rows",
                    unit="sym",
                    total=len(symbols),
                ):
                    if not rows:
                        continue
                    latest_rows.append(rows[-1])

                    df_batch = _rows_to_df(rows, id_cols, feature_cols_all, target_cols)
                    _write_batch(df_batch)

    finally:
        try:
            if parquet_writer is not None:
                parquet_writer.close()
        except Exception:
            pass

    if total_rows_written <= 0:
        log("[ml_data_builder] ❌ No valid rows written. Check rolling history availability.")
        return pd.DataFrame()

    # publish RAW atomically (pyarrow)
    if have_pyarrow:
        try:
            # preflight tmp raw before publish
            _parquet_preflight_or_raise(raw_tmp, columns=["symbol"])
            _safe_unlink(RAW_DATASET_FILE)
            _atomic_replace(raw_tmp, RAW_DATASET_FILE)
            log(f"[ml_data_builder] ✅ Raw parquet written (atomic): rows={total_rows_written} → {RAW_DATASET_FILE}")
        except Exception as e:
            log(f"[ml_data_builder] ❌ RAW parquet publish failed: {e}")
            # attempt to keep tmp for debugging
            try:
                if raw_tmp and raw_tmp.exists():
                    log(f"[ml_data_builder] ℹ️ RAW tmp left for inspection: {raw_tmp}")
            except Exception:
                pass
    else:
        log(f"[ml_data_builder] ✅ CSV chunks written: rows={total_rows_written} → {CSV_CHUNKS_DIR}")

    # ---------------------------
    # Feature filtering
    # ---------------------------
    feats = list(feature_cols_all)
    feats = _filter_missing_from_stats(stats, feats)
    feats = _filter_low_variance_from_stats(stats, feats)

    df_sample = pd.DataFrame()
    if sample_frames:
        try:
            df_sample = pd.concat(sample_frames, axis=0, ignore_index=True)
            for c in feats:
                if c in df_sample.columns:
                    df_sample[c] = (
                        pd.to_numeric(df_sample[c], errors="coerce")
                        .replace([np.inf, -np.inf], np.nan)
                        .fillna(0.0)
                        .astype("float32", copy=False)
                    )
        except Exception as e:
            log(f"[ml_data_builder] ⚠️ Failed building correlation sample df: {e}")
            df_sample = pd.DataFrame()

    feats = _filter_correlation_sample(df_sample, feats, threshold=0.98)
    final_cols = id_cols + feats + target_cols

    # quick target debug (from sample)
    try:
        if not df_sample.empty:
            dbg: Dict[str, Any] = {}
            for t in target_cols:
                if t not in df_sample.columns:
                    continue
                s = pd.to_numeric(df_sample[t], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
                if s.empty:
                    dbg[t] = {"n": 0}
                    continue
                vals = s.to_numpy(dtype=float, copy=False)
                dbg[t] = {
                    "n": int(len(vals)),
                    "mean": float(np.mean(vals)),
                    "std": float(np.std(vals)),
                    "p05": float(np.percentile(vals, 5)),
                    "p50": float(np.percentile(vals, 50)),
                    "p95": float(np.percentile(vals, 95)),
                    "min": float(np.min(vals)),
                    "max": float(np.max(vals)),
                    "zero_frac": float(np.mean(np.isclose(vals, 0.0, atol=1e-12))),
                }
            if dbg:
                msg = json.dumps(dbg, indent=2)
                log(f"[ml_data_builder] 📊 Target sample stats: {msg[:1800]}")
    except Exception:
        pass

    # ---------------------------
    # Final dataset artifact (atomic)
    # ---------------------------
    if have_pyarrow:
        if rewrite_final:
            log(f"[ml_data_builder] 🔁 Rewriting FINAL parquet with {len(feats)} features… (atomic)")
            final_tmp = DATASET_FILE.with_suffix(".tmp.parquet")
            _safe_unlink(final_tmp)
            try:
                dataset = ds.dataset(str(RAW_DATASET_FILE), format="parquet")
                scanner = dataset.scanner(columns=final_cols, batch_size=64_000)
                final_writer = None
                final_rows = 0

                for batch in scanner.to_batches():
                    if batch.num_rows == 0:
                        continue
                    if final_writer is None:
                        final_writer = pq.ParquetWriter(str(final_tmp), batch.schema, compression="snappy")
                    final_writer.write_table(pa.Table.from_batches([batch]))
                    final_rows += batch.num_rows

                if final_writer is not None:
                    final_writer.close()

                # validate tmp parquet before publish
                _parquet_preflight_or_raise(final_tmp, columns=["symbol"])

                _safe_unlink(DATASET_FILE)
                _atomic_replace(final_tmp, DATASET_FILE)
                log(f"[ml_data_builder] 💾 Saved FINAL dataset (atomic) → {DATASET_FILE} (rows={final_rows})")

            except Exception as e:
                log(f"[ml_data_builder] ❌ FINAL rewrite/publish failed: {e}")
                # best-effort cleanup tmp
                _safe_unlink(final_tmp)
                # As last resort, publish RAW as FINAL (atomic) if RAW is valid
                try:
                    _parquet_preflight_or_raise(RAW_DATASET_FILE, columns=["symbol"])
                    tmp2 = DATASET_FILE.with_suffix(".raw_as_final.tmp")
                    _safe_unlink(tmp2)
                    # copy/replace approach: os.replace needs same filesystem; RAW and FINAL are same dir so OK.
                    # But we don't want to destroy RAW. So we copy.
                    import shutil
                    shutil.copy2(str(RAW_DATASET_FILE), str(tmp2))
                    _parquet_preflight_or_raise(tmp2, columns=["symbol"])
                    _safe_unlink(DATASET_FILE)
                    _atomic_replace(tmp2, DATASET_FILE)
                    log(f"[ml_data_builder] 💾 Fallback: copied RAW to FINAL (atomic) → {DATASET_FILE}")
                except Exception as e2:
                    log(f"[ml_data_builder] 🧨 Fallback RAW→FINAL copy failed: {e2}")
        else:
            # publish RAW copy as FINAL (preserve RAW)
            try:
                import shutil
                final_tmp = DATASET_FILE.with_suffix(".tmp.parquet")
                _safe_unlink(final_tmp)
                shutil.copy2(str(RAW_DATASET_FILE), str(final_tmp))
                _parquet_preflight_or_raise(final_tmp, columns=["symbol"])
                _safe_unlink(DATASET_FILE)
                _atomic_replace(final_tmp, DATASET_FILE)
                log(f"[ml_data_builder] 💾 Saved dataset (no rewrite; copied RAW) → {DATASET_FILE}")
            except Exception as e:
                log(f"[ml_data_builder] ❌ Failed publishing FINAL from RAW: {e}")
    else:
        log("[ml_data_builder] ⚠️ pyarrow missing: parquet final not created. CSV chunks are your dataset artifact for now.")

    # ---------------------------
    # Latest-features artifact (no 'name' to avoid object-dtype spikes)
    # ---------------------------
    latest_written = False
    latest_count = 0
    latest_format = "none"
    try:
        if latest_rows:
            df_latest = pd.DataFrame.from_records(latest_rows)

            # Snapshot columns: symbol, asof_date, feats (NO name)
            keep_cols = ["symbol", "asof_date"] + feats
            for c in keep_cols:
                if c not in df_latest.columns:
                    df_latest[c] = np.nan
            df_latest = df_latest[keep_cols].copy()

            df_latest["symbol"] = df_latest["symbol"].astype(str).str.upper()
            df_latest["asof_date"] = df_latest["asof_date"].astype(str)

            # Guarantee every feature exists and is float32
            for c in feats:
                if c not in df_latest.columns:
                    df_latest[c] = 0.0
                df_latest[c] = (
                    pd.to_numeric(df_latest[c], errors="coerce")
                    .replace([np.inf, -np.inf], np.nan)
                    .fillna(0.0)
                    .astype("float32", copy=False)
                )

            df_latest = (
                df_latest.sort_values(["symbol", "asof_date"])
                .drop_duplicates(subset=["symbol"], keep="last")
            )

            latest_count = int(len(df_latest))

            if have_pyarrow:
                # write snapshot atomically too
                snap_tmp = LATEST_FEATURES_FILE.with_suffix(".tmp.parquet")
                _safe_unlink(snap_tmp)
                table = pa.Table.from_pandas(df_latest, preserve_index=False)
                pq.write_table(table, str(snap_tmp), compression="snappy")
                _parquet_preflight_or_raise(snap_tmp, columns=["symbol"])
                _safe_unlink(LATEST_FEATURES_FILE)
                _atomic_replace(snap_tmp, LATEST_FEATURES_FILE)

                latest_written = True
                latest_format = "parquet"
                log(f"[ml_data_builder] ✅ Wrote latest_features snapshot (atomic) → {LATEST_FEATURES_FILE} (symbols={latest_count})")
            else:
                df_latest.to_csv(LATEST_FEATURES_CSV, index=False)
                latest_written = True
                latest_format = "csv"
                log(f"[ml_data_builder] ✅ Wrote latest_features snapshot → {LATEST_FEATURES_CSV} (symbols={latest_count})")

        else:
            log("[ml_data_builder] ⚠️ No latest_rows captured; latest_features snapshot skipped.")
    except Exception as e:
        log(f"[ml_data_builder] ⚠️ Failed writing latest_features snapshot: {e}")

    # ---------------------------
    # Metadata
    # ---------------------------
    meta = {
        "generated_at": datetime.now(TIMEZONE).isoformat(),
        "id_columns": id_cols,
        "feature_columns": feats,
        "target_columns": target_cols,
        "n_rows": int(total_rows_written),
        "n_features": int(len(feats)),
        "n_targets": int(len(target_cols)),
        "final_file": str(DATASET_FILE),
        "raw_file": str(RAW_DATASET_FILE),
        "csv_chunks_dir": str(CSV_CHUNKS_DIR),
        "has_pyarrow": bool(have_pyarrow),
        "sector_dummy_count": int(len(_collect_sectors(rolling))),
        "corr_sample_rows": int(sample_rows_count),
        "return_dataframe_mode": return_dataframe,
        "max_return_rows": int(MAX_RETURN_ROWS),
        "sample_return_rows": int(SAMPLE_RETURN_ROWS),
        "latest_features_file": str(LATEST_FEATURES_FILE),
        "latest_features_csv": str(LATEST_FEATURES_CSV),
        "latest_features_written": bool(latest_written),
        "latest_features_symbols": int(latest_count),
        "latest_features_format": str(latest_format),
        "dtype_features": "float32",
        "dtype_targets": "float32",
        "horizon_steps": dict(HORIZON_STEPS),
        "min_valid_close": float(MIN_VALID_CLOSE),
        "max_abs_target_ret": float(MAX_ABS_TARGET_RET),
    }

    try:
        FEATURE_LIST_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log("[ml_data_builder] 📝 Feature list saved")
    except Exception as e:
        log(f"[ml_data_builder] ⚠️ Failed saving feature list: {e}")

    log("[ml_data_builder] ✅ Build complete (streaming)")
    log("=======================================================")

    # ----------------------------------------------------------
    # Return behavior
    # ----------------------------------------------------------
    if return_dataframe == "none":
        return pd.DataFrame()

    if not have_pyarrow:
        if return_dataframe in ("auto", "sample") and not df_sample.empty:
            take = min(int(SAMPLE_RETURN_ROWS), len(df_sample))
            log(f"[ml_data_builder] ℹ️ Returning sample df from in-memory sample rows={take} (pyarrow missing).")
            return df_sample.iloc[:take].copy()
        log("[ml_data_builder] ⚠️ pyarrow missing; returning empty df (dataset exists as CSV chunks).")
        return pd.DataFrame()

    if return_dataframe == "sample" or (return_dataframe == "auto" and total_rows_written > MAX_RETURN_ROWS):
        take = min(SAMPLE_RETURN_ROWS, total_rows_written)
        log(
            f"[ml_data_builder] ⚠️ Dataset too large to return FULL df safely "
            f"(rows={total_rows_written}, cap={MAX_RETURN_ROWS}). Returning sample rows={take}."
        )
        try:
            dataset = ds.dataset(str(DATASET_FILE), format="parquet")
            scanner = dataset.scanner(columns=final_cols, batch_size=int(take))
            batches = scanner.to_batches()
            if not batches:
                return pd.DataFrame()
            tbl = pa.Table.from_batches(batches)
            return tbl.to_pandas()
        except Exception as e:
            log(f"[ml_data_builder] ⚠️ Failed returning sample df: {e}")
            return pd.DataFrame()

    try:
        log(f"[ml_data_builder] 📦 Returning FULL df from parquet (rows={total_rows_written})")
        return pd.read_parquet(DATASET_FILE)
    except Exception as e:
        log(f"[ml_data_builder] ⚠️ Failed returning full df: {e}")
        return pd.DataFrame()


def build_daily_dataset(*args, **kwargs):
    """
    Wrapper used by nightly_job / API.

    Returns:
      {"rows": <true rows>, "returned_rows": <rows in returned df>, "file": <parquet path>, "has_pyarrow": bool}
    """
    return_mode: ReturnMode = kwargs.get("return_dataframe", "none")

    df = build_ml_dataset(
        as_of_date=kwargs.get("as_of_date"),
        strict=bool(kwargs.get("strict", False)),
        use_multiprocessing=kwargs.get("use_multiprocessing", True),
        debug=kwargs.get("debug", False),
        max_symbols=kwargs.get("max_symbols"),
        chunk_symbols=kwargs.get("chunk_symbols", 50),
        corr_sample_rows=kwargs.get("corr_sample_rows", 50_000),
        rewrite_final=kwargs.get("rewrite_final", True),
        return_dataframe=return_mode,
    )

    rows = 0
    has_pyarrow = False
    try:
        if FEATURE_LIST_FILE.exists():
            meta = json.loads(FEATURE_LIST_FILE.read_text(encoding="utf-8"))
            rows = int(meta.get("n_rows", 0) or 0)
            has_pyarrow = bool(meta.get("has_pyarrow", False))
    except Exception:
        rows = 0
        has_pyarrow = False

    returned_rows = int(len(df)) if isinstance(df, pd.DataFrame) else 0
    return {"rows": rows, "returned_rows": returned_rows, "file": str(DATASET_FILE), "has_pyarrow": has_pyarrow}


# ===============================================================
# Debug CLI harness
# ===============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AION ML Dataset Builder (daily, streaming)")
    parser.add_argument("--no-mp", action="store_true", help="Disable multiprocessing (single-process debug mode)")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging inside workers")
    parser.add_argument("--max-symbols", type=int, default=0, help="Limit number of symbols for this run (0 = no limit)")
    parser.add_argument("--chunk-symbols", type=int, default=50, help="Symbols per write chunk (single-process)")
    parser.add_argument("--corr-sample-rows", type=int, default=50000, help="Max rows kept for correlation sample")
    parser.add_argument("--no-rewrite", action="store_true", help="Skip final rewrite; use raw as final")
    parser.add_argument(
        "--return-df",
        type=str,
        default="auto",
        choices=["auto", "full", "sample", "none"],
        help="Return df behavior: auto|full|sample|none",
    )

    args = parser.parse_args()

    use_mp = not args.no_mp
    max_syms: Optional[int] = args.max_symbols if args.max_symbols > 0 else None

    log(
        f"[ml_data_builder] 🧪 Debug harness start "
        f"(mp={use_mp}, debug={args.debug}, max_symbols={max_syms}, chunk_symbols={args.chunk_symbols})"
    )

    build_ml_dataset(
        use_multiprocessing=use_mp,
        debug=args.debug,
        max_symbols=max_syms,
        chunk_symbols=max(1, int(args.chunk_symbols)),
        corr_sample_rows=max(0, int(args.corr_sample_rows)),
        rewrite_final=not args.no_rewrite,
        return_dataframe=args.return_df,
    )
