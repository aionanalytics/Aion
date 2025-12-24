# backend/core/ai_model.py — v1.7.0
"""
AION Analytics — Regression Engine (Multi-Horizon Expected Returns)

What this module does
- Trains one regressor per horizon on target_ret_<h> (1d…52w)
- Produces, per symbol+per horizon:
    predicted_return (decimal)
    label (-1/0/+1)
    rating (STRONG_SELL … STRONG_BUY) + rating_score (-2..+2)
    confidence ∈ [0.50, 0.98] (intended to become calibrated P(hit))
    score ∈ [-1, 1] (ranking signal)

Key upgrades in v1.7.0 (fixes the “±80% everywhere” clown-show)
- Dynamic, per-horizon clipping derived from the *observed* target distribution
  (p01/p99 + std) instead of a single hard +/-80% clamp.
- Quantile-based target clipping during training (same clip limits) to reduce
  outlier-driven model instability.
- Prediction diagnostics written nightly:
    raw_pred histogram, clipped_pred histogram, % clipped, and clip_limit used.
- Stronger post-train sanity gate uses the *dynamic* clip limit, not the hard cap.

Important note on “confidence”
- Today, confidence is still mostly “P(direction correct)” via accuracy_engine buckets
  unless your hit-definition becomes magnitude-aware and you track calibration error
  (Brier score) per horizon. This module is written so it will not fight that future.

"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

import numpy as np
import pandas as pd

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import log, _read_rolling, _read_aion_brain

from backend.core.memmap_trainer import train_lgbm_memmap_reservoir
from backend.core.confidence_calibrator import (
    load_calibration_map,
    load_accuracy_latest,
    calibrated_confidence,
    recent_horizon_accuracy_conf,
    combine_confidence,
    soft_performance_cap,
)

# ==========================================================
# LightGBM / RF
# ==========================================================

try:
    import lightgbm as lgb  # type: ignore
    HAS_LGBM = True
except Exception:
    HAS_LGBM = False

from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from joblib import dump, load

# ==========================================================
# Optional Optuna
# ==========================================================

try:
    import optuna
    HAS_OPTUNA = True
except Exception:
    HAS_OPTUNA = False


# ==========================================================
# Optional PyArrow (batch parquet scanning)
# ==========================================================

def _try_import_pyarrow():
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.dataset as ds  # type: ignore
        return pa, ds
    except Exception as e:
        log(f"[ai_model] ⚠️ pyarrow not available: {e}")
        return None, None


# ==========================================================
# Paths & constants
# ==========================================================

ML_DATA_ROOT: Path = PATHS.get("ml_data", Path("ml_data"))
DATASET_DIR: Path = ML_DATA_ROOT / "nightly" / "dataset"
DATASET_DIR.mkdir(parents=True, exist_ok=True)

DATASET_FILE: Path = DATASET_DIR / "training_data_daily.parquet"
FEATURE_LIST_FILE: Path = DATASET_DIR / "feature_list_daily.json"

LATEST_FEATURES_FILE: Path = DATASET_DIR / "latest_features_daily.parquet"
LATEST_FEATURES_CSV: Path = DATASET_DIR / "latest_features_daily.csv"

MODEL_ROOT: Path = PATHS.get("ml_models", ML_DATA_ROOT / "nightly" / "models")
MODEL_ROOT.mkdir(parents=True, exist_ok=True)

TMP_MEMMAP_ROOT: Path = PATHS.get("ml_tmp_memmap", ML_DATA_ROOT / "tmp" / "memmap")
TMP_MEMMAP_ROOT.mkdir(parents=True, exist_ok=True)

METRICS_ROOT: Path = ML_DATA_ROOT / "metrics"
METRICS_ROOT.mkdir(parents=True, exist_ok=True)

RETURN_STATS_FILE: Path = METRICS_ROOT / "return_stats.json"
PRED_DIAG_FILE: Path = METRICS_ROOT / "prediction_diagnostics.json"

HORIZONS: List[str] = ["1d", "3d", "1w", "2w", "4w", "13w", "26w", "52w"]

# Hard caps (safety rails). Dynamic clip is always <= these.
HARD_MAX_ABS_RET: float = 0.80
MIN_CONF: float = 0.50
MAX_CONF: float = 0.98

# Histogram bins for nightly diagnostics (returns are decimals)
DIAG_BINS: np.ndarray = np.array(
    [-0.50, -0.30, -0.20, -0.12, -0.08, -0.05, -0.03, -0.02, -0.01,
      0.00,
      0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.20, 0.30, 0.50],
    dtype=float
)

# --------------------------
# Sanity-gate thresholds
# --------------------------

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return int(default)

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return float(default)

MIN_USABLE_ROWS: int = _env_int("AION_ML_MIN_ROWS_PER_HORIZON", 20_000)
MIN_TARGET_STD: float = _env_float("AION_ML_MIN_TARGET_STD", 1e-4)
MIN_PRED_STD: float = _env_float("AION_ML_MIN_PRED_STD", 1e-6)
MAX_TARGET_ZERO_FRAC: float = _env_float("AION_ML_MAX_TARGET_ZERO_FRAC", 0.995)
MAX_CLIP_SAT_FRAC: float = _env_float("AION_ML_MAX_CLIP_SATURATION_FRAC", 0.18)  # tighter than before

MAX_STATS_SAMPLES: int = _env_int("AION_ML_MAX_STATS_SAMPLES", 200_000)
MAX_VAL_SAMPLES: int = _env_int("AION_ML_MAX_VAL_SAMPLES", 80_000)

# Dynamic clip controls
CLIP_FACTOR: float = _env_float("AION_ML_CLIP_FACTOR", 1.20)  # inflate p01/p99 a bit
MIN_CLIP_SHORT: float = _env_float("AION_ML_MIN_CLIP_SHORT", 0.03)  # 3%
MIN_CLIP_LONG: float = _env_float("AION_ML_MIN_CLIP_LONG", 0.06)    # 6%
MAX_CLIP_LONG: float = _env_float("AION_ML_MAX_CLIP_LONG", 0.45)    # 45%
MAX_CLIP_SHORT: float = _env_float("AION_ML_MAX_CLIP_SHORT", 0.25)  # 25%


# ==========================================================
# AION BRAIN HELPERS
# ==========================================================

def _aion_meta_snapshot() -> Dict[str, Any]:
    try:
        ab = _read_aion_brain() or {}
        meta = ab.get("_meta", {}) if isinstance(ab, dict) else {}
        if not isinstance(meta, dict):
            meta = {}

        cb = float(meta.get("confidence_bias", 1.0) or 1.0)
        rb = float(meta.get("risk_bias", 1.0) or 1.0)
        ag = float(meta.get("aggressiveness", 1.0) or 1.0)

        cb = float(max(0.70, min(1.30, cb)))
        rb = float(max(0.60, min(1.40, rb)))
        ag = float(max(0.60, min(1.50, ag)))

        return {
            "updated_at": meta.get("updated_at"),
            "confidence_bias": cb,
            "risk_bias": rb,
            "aggressiveness": ag,
        }
    except Exception:
        return {"updated_at": None, "confidence_bias": 1.0, "risk_bias": 1.0, "aggressiveness": 1.0}


# ==========================================================
# PATH HELPERS
# ==========================================================

def _model_path(horizon: str) -> Path:
    return MODEL_ROOT / f"regressor_{horizon}.pkl"

def _booster_path(horizon: str) -> Path:
    return MODEL_ROOT / f"regressor_{horizon}.txt"

def _feature_map_path(horizon: str) -> Path:
    return MODEL_ROOT / f"feature_map_{horizon}.json"


# ==========================================================
# DATASET HELPERS
# ==========================================================

def _load_feature_list() -> Dict[str, Any]:
    if not FEATURE_LIST_FILE.exists():
        raise FileNotFoundError(f"Feature list missing at {FEATURE_LIST_FILE}")
    return json.loads(FEATURE_LIST_FILE.read_text(encoding="utf-8"))

def _resolve_dataset_path(dataset_name: str | None = None) -> Path:
    if dataset_name and dataset_name != DATASET_FILE.name:
        df_path = DATASET_DIR / dataset_name
    else:
        df_path = DATASET_FILE
    if not df_path.exists():
        raise FileNotFoundError(f"Dataset missing: {df_path}")
    return df_path

def _load_horizon_feature_map(horizon: str, fallback: List[str]) -> List[str]:
    p = _feature_map_path(horizon)
    if not p.exists():
        return list(fallback)
    try:
        arr = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(arr, list) and arr:
            return [str(x) for x in arr]
    except Exception:
        pass
    return list(fallback)


# ==========================================================
# REGRESSOR FACTORY & TUNING
# ==========================================================

def _make_regressor(params: Optional[dict] = None):
    return RandomForestRegressor(
        n_estimators=params.get("n_estimators", 300) if params else 300,
        max_depth=params.get("max_depth", None) if params else None,
        n_jobs=-1,
        random_state=42,
    )

def _tune_lightgbm_regressor(
    X: np.ndarray,
    y: np.ndarray,
    horizon: str,
    n_trials: int = 20,
) -> Dict[str, Any]:
    if not (HAS_OPTUNA and HAS_LGBM):
        return {}

    if len(y) < 200:
        log(f"[ai_model] ⚠️ Skipping Optuna for {horizon}: too few samples ({len(y)})")
        return {}

    log(f"[ai_model] 🔍 Optuna regression tuning horizon={horizon}, trials={n_trials}")

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

    def objective(trial: "optuna.trial.Trial") -> float:
        params = {
            "objective": "regression",
            "metric": "rmse",
            "verbosity": -1,
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 16, 256),
            "max_depth": trial.suggest_int("max_depth", 3, 12),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
            "bagging_freq": trial.suggest_int("bagging_freq", 0, 10),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 10, 200),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-4, 10.0, log=True),
        }

        dtrain = lgb.Dataset(X_train, label=y_train, free_raw_data=True)
        dval = lgb.Dataset(X_val, label=y_val, reference=dtrain, free_raw_data=True)

        booster = lgb.train(
            params,
            dtrain,
            num_boost_round=600,
            valid_sets=[dval],
            valid_names=["val"],
            callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
        )
        pred = booster.predict(X_val, num_iteration=booster.best_iteration)
        rmse = float(np.sqrt(mean_squared_error(y_val, pred)))
        return rmse

    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials)

    best = study.best_params
    log(f"[ai_model] 🎯 Best regression params for {horizon}: {best}")
    return best


# ==========================================================
# HORIZON STATS (RETURN DISTRIBUTIONS)
# ==========================================================

def _save_return_stats(stats: Dict[str, Any]) -> None:
    payload = {"generated_at": datetime.now(TIMEZONE).isoformat(), "horizons": stats}
    try:
        METRICS_ROOT.mkdir(parents=True, exist_ok=True)
        RETURN_STATS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log(f"[ai_model] 📊 Return stats written → {RETURN_STATS_FILE}")
    except Exception as e:
        log(f"[ai_model] ⚠️ Failed to write return stats: {e}")

def _load_return_stats() -> Dict[str, Any]:
    if not RETURN_STATS_FILE.exists():
        return {}
    try:
        raw = json.loads(RETURN_STATS_FILE.read_text(encoding="utf-8"))
        return raw.get("horizons", {})
    except Exception:
        return {}

def _clip_limit_for_horizon(horizon: str, stats: Dict[str, Any]) -> float:
    """
    Compute a sane, per-horizon clip limit (absolute return).

    Uses:
      - p01/p99 (preferred)
      - std fallback
    Applies:
      - CLIP_FACTOR inflation
      - min/max bounds by horizon class
      - always <= HARD_MAX_ABS_RET
    """
    std = float(stats.get("std", 0.05) or 0.05)
    p01 = float(stats.get("p01", 0.0) or 0.0)
    p99 = float(stats.get("p99", 0.0) or 0.0)

    base = max(abs(p01), abs(p99))
    if base <= 0:
        base = 6.0 * std  # decent fallback if percentiles missing

    base = float(base) * float(CLIP_FACTOR)

    is_long = horizon in ("13w", "26w", "52w")
    min_lim = float(MIN_CLIP_LONG if is_long else MIN_CLIP_SHORT)
    max_lim = float(MAX_CLIP_LONG if is_long else MAX_CLIP_SHORT)

    lim = max(base, 3.0 * std, min_lim)
    lim = min(lim, max_lim, HARD_MAX_ABS_RET)
    return float(lim)


# ==========================================================
# Batch readers (pyarrow streaming)
# ==========================================================

def _iter_parquet_batches(
    parquet_path: Path,
    columns: List[str],
    batch_size: int = 100_000,
):
    pa, ds = _try_import_pyarrow()
    if pa is None or ds is None:
        raise RuntimeError("pyarrow is required for streaming parquet batches")

    dataset = ds.dataset(str(parquet_path), format="parquet")
    scanner = dataset.scanner(columns=columns, batch_size=int(batch_size))
    for batch in scanner.to_batches():
        if batch.num_rows <= 0:
            continue
        yield batch.to_pandas()


def _preflight_dataset_or_die(parquet_path: Path) -> None:
    """Hard gate: ensure parquet exists, is non-empty, and is scan-readable."""
    if not parquet_path.exists():
        raise FileNotFoundError(f"[DATA PREFLIGHT] Parquet file missing: {parquet_path}")
    try:
        if parquet_path.stat().st_size <= 0:
            raise RuntimeError(f"[DATA PREFLIGHT] Parquet file is empty: {parquet_path}")
    except Exception as e:
        raise RuntimeError(f"[DATA PREFLIGHT] Failed to stat parquet: {e}")

    # Minimal scan to ensure dataset is readable and yields at least one batch.
    try:
        for df in _iter_parquet_batches(parquet_path, columns=[], batch_size=10_000):
            # to_pandas() returns a DataFrame (possibly empty if batch had rows? unlikely) — treat presence as success.
            return
        raise RuntimeError("[DATA PREFLIGHT] Parquet readable but yielded no rows.")
    except Exception as e:
        raise RuntimeError(f"[DATA PREFLIGHT] Failed to scan parquet: {e}")


# ==========================================================
# Streamed diagnostics + sanity gates
# ==========================================================

def _stream_target_stats(
    parquet_path: Path,
    target_col: str,
    *,
    batch_size: int = 200_000,
    max_samples: int = MAX_STATS_SAMPLES,
) -> Dict[str, Any]:
    usable = 0
    samples: List[float] = []

    try:
        for df in _iter_parquet_batches(parquet_path, [target_col], batch_size=batch_size):
            if df is None or df.empty or target_col not in df.columns:
                continue
            s = pd.to_numeric(df[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
            v = s.dropna()
            if v.empty:
                continue

            arr = v.to_numpy(dtype=float, copy=False)
            usable += int(arr.size)

            if len(samples) < max_samples:
                take = min(max_samples - len(samples), int(arr.size))
                if take > 0:
                    sl = np.clip(arr[:take], -2.0, 2.0)  # keep tails bounded while sampling
                    samples.extend([float(x) for x in sl])

    except Exception as e:
        return {"status": "error", "error": str(e)}

    if not samples:
        return {
            "status": "ok",
            "usable_rows_est": int(usable),
            "n_samples": 0,
            "mean": 0.0,
            "std": 0.0,
            "p01": 0.0,
            "p05": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "zero_frac": 1.0,
        }

    y = np.array(samples, dtype=float)
    zero_frac = float(np.mean(np.isclose(y, 0.0, atol=1e-12)))

    return {
        "status": "ok",
        "usable_rows_est": int(usable),
        "n_samples": int(len(y)),
        "mean": float(np.mean(y)),
        "std": float(np.std(y)),
        "p01": float(np.percentile(y, 1)),
        "p05": float(np.percentile(y, 5)),
        "p25": float(np.percentile(y, 25)),
        "p50": float(np.percentile(y, 50)),
        "p75": float(np.percentile(y, 75)),
        "p95": float(np.percentile(y, 95)),
        "p99": float(np.percentile(y, 99)),
        "zero_frac": float(zero_frac),
    }


def _stream_validation_sample(
    parquet_path: Path,
    feature_cols: List[str],
    target_col: str,
    *,
    batch_size: int = 200_000,
    max_rows: int = MAX_VAL_SAMPLES,
    seed: int = 42,
    y_clip_low: Optional[float] = None,
    y_clip_high: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n_features = int(len(feature_cols))
    if n_features <= 0:
        return np.empty((0, 0), dtype=np.float32), np.empty((0,), dtype=np.float32)

    X_res = np.empty((max_rows, n_features), dtype=np.float32)
    y_res = np.empty((max_rows,), dtype=np.float32)
    rows_seen = 0
    rows_used = 0

    cols = list(feature_cols) + [target_col]

    for df in _iter_parquet_batches(parquet_path, cols, batch_size=batch_size):
        if df is None or df.empty or target_col not in df.columns:
            continue

        y = pd.to_numeric(df[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        mask = y.notna()
        if not mask.any():
            continue

        df_use = df.loc[mask, feature_cols]
        y_use = y.loc[mask]

        Xn = df_use.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        yn = pd.to_numeric(y_use, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if yn.empty:
            continue

        if y_clip_low is not None and y_clip_high is not None:
            yn = yn.clip(lower=float(y_clip_low), upper=float(y_clip_high))

        idx = yn.index
        X_arr = Xn.loc[idx].to_numpy(dtype=np.float32, copy=False)
        y_arr = yn.to_numpy(dtype=np.float32, copy=False)
        if X_arr.size == 0 or y_arr.size == 0:
            continue

        for i in range(X_arr.shape[0]):
            rows_seen += 1
            if rows_used < max_rows:
                X_res[rows_used] = X_arr[i]
                y_res[rows_used] = y_arr[i]
                rows_used += 1
            else:
                j = int(rng.integers(0, rows_seen))
                if j < max_rows:
                    X_res[j] = X_arr[i]
                    y_res[j] = y_arr[i]

    return X_res[:rows_used], y_res[:rows_used]


def _post_train_sanity(
    model: Any,
    X_val: np.ndarray,
    y_val: np.ndarray,
    *,
    horizon: str,
    clip_limit: float,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": "ok"}

    if X_val is None or y_val is None or len(y_val) < 2000:
        out["status"] = "insufficient_val"
        out["n_val"] = int(len(y_val) if y_val is not None else 0)
        return out

    try:
        pred = np.asarray(model.predict(X_val), dtype=float)
    except Exception as e:
        return {"status": "error", "error": f"predict_failed: {e}"}

    if pred.size == 0:
        return {"status": "error", "error": "empty_predictions"}

    pred_std = float(np.std(pred))
    pred_zero_frac = float(np.mean(np.isclose(pred, 0.0, atol=1e-12)))

    lim = float(max(1e-9, clip_limit))
    pred_clip = np.clip(pred, -lim, lim)
    sat = float(np.mean((pred_clip <= (-lim + 1e-12)) | (pred_clip >= (lim - 1e-12))))

    corr = None
    try:
        if float(np.std(y_val)) > 0 and float(np.std(pred)) > 0:
            corr = float(np.corrcoef(pred, y_val)[0, 1])
    except Exception:
        corr = None

    out.update(
        {
            "n_val": int(len(y_val)),
            "clip_limit": float(lim),
            "pred_std": float(pred_std),
            "pred_zero_frac": float(pred_zero_frac),
            "clip_saturation_frac": float(sat),
            "pred_mean": float(np.mean(pred)),
            "pred_p05": float(np.percentile(pred, 5)),
            "pred_p50": float(np.percentile(pred, 50)),
            "pred_p95": float(np.percentile(pred, 95)),
            "corr_pred_y": corr,
        }
    )

    if pred_std < MIN_PRED_STD:
        out["status"] = "reject"
        out["reject_reason"] = f"degenerate_predictions(pred_std<{MIN_PRED_STD})"
        return out

    if pred_zero_frac >= 0.995:
        out["status"] = "reject"
        out["reject_reason"] = "degenerate_predictions(all_zero)"
        return out

    if sat >= float(MAX_CLIP_SAT_FRAC):
        out["status"] = "reject"
        out["reject_reason"] = f"unstable_predictions(clip_saturation>={MAX_CLIP_SAT_FRAC:.3f})"
        return out

    return out


# ==========================================================
# TRAINING — PURE REGRESSION (memmap for LGBM)
# ==========================================================

def train_model(
    dataset_name: str = "training_data_daily.parquet",
    use_optuna: bool = True,
    n_trials: int = 20,
    batch_size: int = 150_000,
) -> Dict[str, Any]:
    log(f"[ai_model] 🧠 Training regression models v1.7.0 (optuna={use_optuna}, batch_rows={batch_size})")

    feat_info = _load_feature_list()
    feature_cols: List[str] = feat_info.get("feature_columns", [])
    target_cols: List[str] = feat_info.get("target_columns", [])

    if not feature_cols:
        log("[ai_model] ❌ No feature columns found in feature_list.")
        return {"status": "error", "error": "no_features"}

    df_path = _resolve_dataset_path(dataset_name)
    _preflight_dataset_or_die(df_path)

    summaries: Dict[str, Any] = {}
    return_stats: Dict[str, Any] = {}

    for horizon in HORIZONS:
        tgt_ret = f"target_ret_{horizon}"
        if tgt_ret not in target_cols:
            continue

        # Persist horizon feature map (currently identical to global list)
        try:
            _feature_map_path(horizon).write_text(json.dumps(feature_cols, indent=2), encoding="utf-8")
        except Exception:
            pass

        tstats = _stream_target_stats(
            df_path,
            tgt_ret,
            batch_size=max(50_000, int(batch_size)),
            max_samples=MAX_STATS_SAMPLES,
        )
        if tstats.get("status") != "ok":
            summaries[horizon] = {"status": "error", "error": f"target_stats_failed: {tstats.get('error')}"}
            continue

        usable_est = int(tstats.get("usable_rows_est", 0) or 0)
        y_std = float(tstats.get("std", 0.0) or 0.0)
        y_zero_frac = float(tstats.get("zero_frac", 1.0) or 1.0)

        clip_lim = _clip_limit_for_horizon(horizon, tstats)
        y_clip_low = -clip_lim
        y_clip_high = clip_lim

        log(
            f"[ai_model] 📌 Horizon={horizon} target stats: "
            f"usable_est={usable_est}, std={y_std:.6g}, zero_frac={y_zero_frac:.4f}, "
            f"p01={tstats.get('p01'):.6g}, p50={tstats.get('p50'):.6g}, p99={tstats.get('p99'):.6g}, "
            f"clip_limit={clip_lim:.4f}"
        )

        if usable_est < MIN_USABLE_ROWS:
            summaries[horizon] = {"status": "skipped", "reason": f"too_few_usable_rows({usable_est}<{MIN_USABLE_ROWS})", "target_stats": tstats}
            continue

        if y_std < MIN_TARGET_STD:
            summaries[horizon] = {"status": "skipped", "reason": f"low_target_variance(std<{MIN_TARGET_STD})", "target_stats": tstats}
            continue

        if y_zero_frac >= MAX_TARGET_ZERO_FRAC:
            summaries[horizon] = {"status": "skipped", "reason": f"targets_mostly_zero(zero_frac>={MAX_TARGET_ZERO_FRAC})", "target_stats": tstats}
            continue

        tstats["clip_limit"] = float(clip_lim)
        return_stats[horizon] = dict(tstats)

        # --------------------------
        # LightGBM path
        # --------------------------
        if HAS_LGBM:
            try:
                base = {
                    "objective": "regression",
                    "metric": "rmse",
                    "verbosity": -1,
                    "learning_rate": 0.05,
                    "num_leaves": 64,
                    "feature_fraction": 0.8,
                    "bagging_fraction": 0.8,
                    "bagging_freq": 1,
                    "min_data_in_leaf": 50,
                    "lambda_l2": 1.0,
                    "num_boost_round": 800,
                }

                tuned: Dict[str, Any] = {}
                if use_optuna and n_trials and n_trials > 0:
                    log(f"[ai_model] ℹ️ Optuna requested for {horizon}. Building arrays for tuning (bounded).")

                    needed_cols = feature_cols + [tgt_ret]
                    X_parts: List[np.ndarray] = []
                    y_parts: List[np.ndarray] = []
                    total_used = 0

                    for df_batch in _iter_parquet_batches(df_path, needed_cols, batch_size=batch_size):
                        if df_batch.empty or tgt_ret not in df_batch.columns:
                            continue

                        y_raw = pd.to_numeric(df_batch[tgt_ret], errors="coerce").replace([np.inf, -np.inf], np.nan)
                        mask = y_raw.notna()
                        if not mask.any():
                            continue

                        X_df = df_batch.loc[mask, feature_cols]
                        X_df = X_df.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

                        y = y_raw.loc[mask].clip(lower=y_clip_low, upper=y_clip_high).to_numpy(dtype=np.float32, copy=False)
                        X = X_df.to_numpy(dtype=np.float32, copy=False)

                        if X.size == 0 or y.size == 0:
                            continue

                        X_parts.append(X)
                        y_parts.append(y)
                        total_used += int(len(y))
                        if total_used >= 250_000:
                            break

                    if total_used >= 5000:
                        X_all = np.concatenate(X_parts, axis=0)
                        y_all = np.concatenate(y_parts, axis=0)
                        tuned = _tune_lightgbm_regressor(X_all, y_all, horizon, n_trials=int(n_trials))
                    else:
                        log(f"[ai_model] ⚠️ Not enough rows for Optuna sample on {horizon}. Skipping tuning.")

                base.update(tuned or {})

                mm = train_lgbm_memmap_reservoir(
                    parquet_path=str(df_path),
                    feature_cols=feature_cols,
                    target_col=tgt_ret,
                    lgb_params=base,
                    tmp_root=str(TMP_MEMMAP_ROOT),
                    max_rows=800_000,
                    batch_rows=int(batch_size),
                    min_rows=MIN_USABLE_ROWS,
                    seed=42,
                    cleanup=True,
                    y_clip_low=float(y_clip_low),
                    y_clip_high=float(y_clip_high),
                )

                booster = mm.model

                Xv, yv = _stream_validation_sample(
                    df_path,
                    feature_cols=feature_cols,
                    target_col=tgt_ret,
                    batch_size=max(50_000, int(batch_size)),
                    max_rows=MAX_VAL_SAMPLES,
                    seed=42,
                    y_clip_low=float(y_clip_low),
                    y_clip_high=float(y_clip_high),
                )

                vdiag = _post_train_sanity(booster, Xv, yv, horizon=horizon, clip_limit=float(clip_lim))

                if vdiag.get("status") == "reject":
                    summaries[horizon] = {
                        "status": "rejected",
                        "reason": vdiag.get("reject_reason"),
                        "target_stats": tstats,
                        "validation": vdiag,
                        "rows_seen": int(mm.rows_seen),
                        "rows_used": int(mm.rows_used),
                        "seconds_ingest": float(mm.seconds_ingest),
                        "seconds_train": float(mm.seconds_train),
                    }
                    log(f"[ai_model] 🧨 Rejecting horizon={horizon}: {vdiag.get('reject_reason')}")
                    continue

                bp = _booster_path(horizon)
                booster.save_model(str(bp))
                dump(booster, _model_path(horizon))

                summaries[horizon] = {
                    "status": "ok",
                    "model_path": str(_model_path(horizon)),
                    "booster_path": str(bp),
                    "target_stats": tstats,
                    "validation": vdiag,
                    "clip_limit": float(clip_lim),
                    "rows_seen": int(mm.rows_seen),
                    "rows_used": int(mm.rows_used),
                    "seconds_ingest": float(mm.seconds_ingest),
                    "seconds_train": float(mm.seconds_train),
                }

            except Exception as e:
                log(f"[ai_model] ❌ Memmap training failed for {horizon}: {e}")
                summaries[horizon] = {"status": "error", "error": str(e), "target_stats": tstats}

            continue

        # --------------------------
        # RandomForest fallback
        # --------------------------
        try:
            needed_cols = feature_cols + [tgt_ret]
            X_parts: List[np.ndarray] = []
            y_parts: List[np.ndarray] = []
            total_used = 0

            for df_batch in _iter_parquet_batches(df_path, needed_cols, batch_size=batch_size):
                if df_batch.empty or tgt_ret not in df_batch.columns:
                    continue

                y_raw = pd.to_numeric(df_batch[tgt_ret], errors="coerce").replace([np.inf, -np.inf], np.nan)
                mask = y_raw.notna()
                if not mask.any():
                    continue

                X_df = df_batch.loc[mask, feature_cols].apply(pd.to_numeric, errors="coerce") \
                    .replace([np.inf, -np.inf], np.nan).fillna(0.0)

                y = y_raw.loc[mask].clip(lower=y_clip_low, upper=y_clip_high).to_numpy(dtype=np.float32, copy=False)
                X = X_df.to_numpy(dtype=np.float32, copy=False)

                if X.size == 0 or y.size == 0:
                    continue

                X_parts.append(X)
                y_parts.append(y)
                total_used += int(len(y))

                if total_used >= 500_000:
                    break

            if total_used < 5000:
                summaries[horizon] = {"status": "skipped", "reason": f"too_few_samples({total_used})", "target_stats": tstats}
                continue

            X_all = np.concatenate(X_parts, axis=0)
            y_all = np.concatenate(y_parts, axis=0)

            X_train, X_val, y_train, y_val = train_test_split(X_all, y_all, test_size=0.2, random_state=42)

            model = _make_regressor({})
            model.fit(X_train, y_train)
            y_pred_val = model.predict(X_val)

            mae = float(mean_absolute_error(y_val, y_pred_val))
            rmse = float(np.sqrt(mean_squared_error(y_val, y_pred_val)))
            r2 = float(r2_score(y_val, y_pred_val))

            vdiag = _post_train_sanity(
                model,
                X_val.astype(np.float32, copy=False),
                y_val.astype(np.float32, copy=False),
                horizon=horizon,
                clip_limit=float(clip_lim),
            )
            if vdiag.get("status") == "reject":
                summaries[horizon] = {"status": "rejected", "reason": vdiag.get("reject_reason"), "target_stats": tstats, "validation": vdiag}
                log(f"[ai_model] 🧨 Rejecting RF horizon={horizon}: {vdiag.get('reject_reason')}")
                continue

            mp = _model_path(horizon)
            dump(model, mp)

            summaries[horizon] = {
                "status": "ok",
                "model_path": str(mp),
                "metrics": {"mae": mae, "rmse": rmse, "r2": r2, "n_val": int(len(y_val))},
                "target_stats": tstats,
                "validation": vdiag,
                "clip_limit": float(clip_lim),
            }

        except Exception as e:
            log(f"[ai_model] ❌ RF training failed for {horizon}: {e}")
            summaries[horizon] = {"status": "error", "error": str(e), "target_stats": tstats}

    if return_stats:
        _save_return_stats(return_stats)

    return {"status": "ok", "horizons": summaries}


def train_all_models(
    dataset_name: str = "training_data_daily.parquet",
    use_optuna: bool = True,
    n_trials: int = 20,
) -> Dict[str, Any]:
    return train_model(dataset_name, use_optuna, n_trials)


# ==========================================================
# LOAD REGRESSION MODELS
# ==========================================================

def _load_regressors() -> Dict[str, Any]:
    models: Dict[str, Any] = {}
    for horizon in HORIZONS:
        pkl = _model_path(horizon)
        txt = _booster_path(horizon)

        if pkl.exists():
            try:
                models[horizon] = load(pkl)
                continue
            except Exception as e:
                log(f"[ai_model] ⚠️ Failed to load regressor pkl for {horizon}: {e}")

        if HAS_LGBM and txt.exists():
            try:
                models[horizon] = lgb.Booster(model_file=str(txt))
            except Exception as e:
                log(f"[ai_model] ⚠️ Failed to load booster txt for {horizon}: {e}")

    return models


# ==========================================================
# RATING / LABEL / CONFIDENCE HELPERS
# ==========================================================

def _rating_from_return(pred_ret: float, stats: Dict[str, Any], base_conf: float) -> Tuple[str, int, int]:
    std = float(stats.get("std", 0.05)) or 0.05
    t_hold = 0.25 * std
    t_buy = 0.75 * std
    t_strong = 1.5 * std

    if pred_ret >= t_strong and base_conf >= 0.6:
        return "STRONG_BUY", 2, 1
    if pred_ret >= t_buy:
        return "BUY", 1, 1
    if pred_ret <= -t_strong and base_conf >= 0.6:
        return "STRONG_SELL", -2, -1
    if pred_ret <= -t_buy:
        return "SELL", -1, -1
    if abs(pred_ret) <= t_hold:
        return "HOLD", 0, 0

    if pred_ret > 0:
        return "BUY", 1, 1
    if pred_ret < 0:
        return "SELL", -1, -1
    return "HOLD", 0, 0


def _confidence_from_signal(pred_ret: np.ndarray, stats: Dict[str, Any], sector_momo: np.ndarray | None = None) -> np.ndarray:
    std = float(stats.get("std", 0.05)) or 0.05
    eps = 1e-8

    z = np.abs(pred_ret) / (std + eps)
    base_conf = 0.5 + 0.5 * (1.0 - np.exp(-z))

    if sector_momo is not None:
        sec = np.clip(sector_momo, -0.20, 0.20)
        tilt = 1.0 + 0.5 * sec
        base_conf = base_conf * tilt

    return np.clip(base_conf, MIN_CONF, MAX_CONF)


# ==========================================================
# Prediction features loader (parquet + csv snapshot)
# ==========================================================

def _read_latest_snapshot_any() -> Optional[pd.DataFrame]:
    if LATEST_FEATURES_FILE.exists():
        try:
            return pd.read_parquet(LATEST_FEATURES_FILE)
        except Exception as e:
            log(f"[ai_model] ⚠️ Failed reading latest_features parquet: {e}")

    if LATEST_FEATURES_CSV.exists():
        try:
            return pd.read_csv(LATEST_FEATURES_CSV)
        except Exception as e:
            log(f"[ai_model] ⚠️ Failed reading latest_features csv: {e}")

    return None


def _load_latest_features_df(required_feature_cols: List[str]) -> pd.DataFrame:
    snap = _read_latest_snapshot_any()
    if snap is not None and not snap.empty:
        try:
            df = snap
            if "symbol" not in df.columns:
                raise ValueError("latest_features missing symbol")

            df["symbol"] = df["symbol"].astype(str).str.upper()
            df = df.set_index("symbol")

            for c in required_feature_cols:
                if c not in df.columns:
                    df[c] = 0.0

            out = df[required_feature_cols].copy()
            out = out.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
            return out.astype(np.float32, copy=False)

        except Exception as e:
            log(f"[ai_model] ⚠️ Failed preparing latest_features snapshot df: {e}")

    pa, ds = _try_import_pyarrow()
    if pa is None or ds is None:
        raise RuntimeError("No latest_features snapshot and pyarrow unavailable for fallback prediction load.")

    df_path = _resolve_dataset_path(DATASET_FILE.name)
    cols = ["symbol", "asof_date"] + required_feature_cols

    latest_map: Dict[str, Tuple[str, np.ndarray]] = {}

    for df_batch in _iter_parquet_batches(df_path, cols, batch_size=200_000):
        if df_batch.empty:
            continue
        df_batch["symbol"] = df_batch["symbol"].astype(str).str.upper()
        df_batch["asof_date"] = df_batch["asof_date"].astype(str)

        feats = df_batch[required_feature_cols].apply(pd.to_numeric, errors="coerce") \
            .replace([np.inf, -np.inf], np.nan).fillna(0.0) \
            .to_numpy(dtype=np.float32, copy=False)

        syms = df_batch["symbol"].values
        dates = df_batch["asof_date"].values

        for i in range(len(df_batch)):
            s = str(syms[i])
            d = str(dates[i])
            prev = latest_map.get(s)
            if prev is None or d > prev[0]:
                latest_map[s] = (d, feats[i].copy())

    if not latest_map:
        raise RuntimeError("Prediction feature fallback produced no rows.")

    symbols = sorted(latest_map.keys())
    mat = np.vstack([latest_map[s][1] for s in symbols]).astype(np.float32, copy=False)
    out = pd.DataFrame(mat, index=symbols, columns=required_feature_cols)
    return out


# ==========================================================
# Prediction diagnostics writer
# ==========================================================

def _hist_counts(values: np.ndarray, bins: np.ndarray) -> List[int]:
    try:
        counts, _ = np.histogram(values.astype(float, copy=False), bins=bins)
        return [int(x) for x in counts]
    except Exception:
        return []

def _write_pred_diagnostics(diag: Dict[str, Any]) -> None:
    try:
        PRED_DIAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        PRED_DIAG_FILE.write_text(json.dumps(diag, indent=2), encoding="utf-8")
        log(f"[ai_model] 📈 Prediction diagnostics written → {PRED_DIAG_FILE}")
    except Exception as e:
        log(f"[ai_model] ⚠️ Failed writing prediction diagnostics: {e}")


# ==========================================================
# PREDICTION — REGRESSION OVER ROLLING (EARNED CONFIDENCE)
# ==========================================================

def predict_all(
    rolling: Optional[Dict[str, Any]] = None,
    *,
    write_diagnostics: Optional[bool] = None,
) -> Dict[str, Any]:
    if rolling is None:
        rolling = _read_rolling() or {}

    if not rolling:
        log("[ai_model] ⚠️ predict_all: rolling is empty.")
        return {}

    # default: write diagnostics unless explicitly disabled
    if write_diagnostics is None:
        write_diagnostics = (os.getenv("AION_PRED_DIAGNOSTICS", "1") == "1")

    aion_meta = _aion_meta_snapshot()
    aion_cb = float(aion_meta.get("confidence_bias", 1.0) or 1.0)

    feat_info = _load_feature_list()
    feature_cols: List[str] = feat_info.get("feature_columns", [])
    if not feature_cols:
        log("[ai_model] ❌ predict_all: feature_columns list is empty.")
        return {}

    try:
        X_df = _load_latest_features_df(feature_cols)
    except Exception as e:
        log(f"[ai_model] ❌ predict_all: failed to load latest features: {e}")
        return {}

    rolling_key_by_upper: Dict[str, str] = {}
    for k in rolling.keys():
        if str(k).startswith("_"):
            continue
        rolling_key_by_upper[str(k).upper()] = str(k)

    symbols: List[str] = [s for s in rolling_key_by_upper.keys() if s in X_df.index]
    if not symbols:
        log("[ai_model] ⚠️ predict_all: no overlap between rolling symbols and latest feature snapshot.")
        return {}

    X_df = X_df.loc[symbols, feature_cols]
    n_samples = int(X_df.shape[0])

    sec1 = X_df["sector_ret_1w"].to_numpy(dtype=float, copy=False) if "sector_ret_1w" in X_df.columns else np.zeros(n_samples, dtype=float)
    sec4 = X_df["sector_ret_4w"].to_numpy(dtype=float, copy=False) if "sector_ret_4w" in X_df.columns else np.zeros(n_samples, dtype=float)
    sector_momo = 0.5 * sec1 + 0.5 * sec4

    regressors = _load_regressors()
    stats_map = _load_return_stats()

    cal_map = load_calibration_map()
    acc_latest = load_accuracy_latest()

    preds: Dict[str, Dict[str, Any]] = {}
    horizon_raw_preds: Dict[str, np.ndarray] = {}
    horizon_clip_limits: Dict[str, float] = {}

    # batch predict per horizon
    for h in HORIZONS:
        model = regressors.get(h)
        if model is None:
            continue

        stats = stats_map.get(h, {}) if isinstance(stats_map, dict) else {}
        if not isinstance(stats, dict):
            stats = {}
        clip_lim = float(stats.get("clip_limit") or _clip_limit_for_horizon(h, stats or {"std": 0.05}))
        horizon_clip_limits[h] = float(clip_lim)

        h_feats = _load_horizon_feature_map(h, fallback=feature_cols)
        Xh = X_df.reindex(columns=h_feats, fill_value=0.0)
        X_np = Xh.to_numpy(dtype=np.float32, copy=False)

        try:
            horizon_raw_preds[h] = np.asarray(model.predict(X_np), dtype=float)
        except Exception as e:
            log(f"[ai_model] ⚠️ Batch prediction failed for horizon={h}: {e}")
            continue

    if not horizon_raw_preds:
        log("[ai_model] ⚠️ predict_all: no horizons produced predictions (no models loaded or all failed).")
        return {}

    # build diagnostics
    diagnostics: Dict[str, Any] = {
        "generated_at": datetime.now(TIMEZONE).isoformat(),
        "symbols": int(len(symbols)),
        "bins": [float(x) for x in DIAG_BINS.tolist()],
        "horizons": {},
    }

    # precompute clipped arrays for diagnostics (cheap + keeps logic consistent)
    horizon_clipped_preds: Dict[str, np.ndarray] = {}
    for h, raw_arr in horizon_raw_preds.items():
        lim = float(horizon_clip_limits.get(h, HARD_MAX_ABS_RET))
        clipped = np.clip(raw_arr.astype(float, copy=False), -lim, lim)
        horizon_clipped_preds[h] = clipped
        clip_frac = float(np.mean(np.isclose(clipped, -lim, atol=1e-12) | np.isclose(clipped, lim, atol=1e-12)))
        diagnostics["horizons"][h] = {
            "clip_limit": float(lim),
            "n": int(clipped.size),
            "raw_mean": float(np.mean(raw_arr)),
            "raw_std": float(np.std(raw_arr)),
            "clipped_mean": float(np.mean(clipped)),
            "clipped_std": float(np.std(clipped)),
            "clip_frac": float(clip_frac),
            "raw_hist": _hist_counts(raw_arr, DIAG_BINS),
            "clipped_hist": _hist_counts(clipped, DIAG_BINS),
        }

    # warn if clipping is still heavy
    for h, info in diagnostics["horizons"].items():
        try:
            if float(info.get("clip_frac", 0.0)) >= 0.20:
                log(f"[ai_model] ⚠️ Horizon={h} heavy clipping: clip_frac={info.get('clip_frac'):.3f} (instability likely)")
        except Exception:
            pass

    if write_diagnostics:
        _write_pred_diagnostics(diagnostics)

    # materialize per symbol predictions
    for idx, sym_u in enumerate(symbols):
        rolling_key = rolling_key_by_upper.get(sym_u)
        node = rolling.get(rolling_key, {}) if rolling_key is not None else {}

        last_close = _last_close_asof(node, as_of_date)

        sym_res: Dict[str, Any] = {}
        sec_momo_val = float(sector_momo[idx]) if idx < len(sector_momo) else 0.0

        for h in HORIZONS:
            if h not in horizon_raw_preds:
                continue

            stats = stats_map.get(h, {}) if isinstance(stats_map, dict) else {}
            if not isinstance(stats, dict) or not stats:
                stats = {"std": 0.05}

            lim = float(horizon_clip_limits.get(h, HARD_MAX_ABS_RET))

            try:
                raw_pred = float(horizon_raw_preds[h][idx])
            except Exception as e:
                log(f"[ai_model] ⚠️ Regression prediction read failed for {sym_u}, horizon={h}: {e}")
                continue

            pred_ret = float(np.clip(raw_pred, -lim, lim))
            was_clipped = bool(abs(raw_pred) > lim + 1e-12)

            conf_raw_arr = _confidence_from_signal(
                np.array([pred_ret], dtype=float),
                stats,
                np.array([sec_momo_val], dtype=float),
            )
            conf_raw = float(conf_raw_arr[0])

            conf_cal = calibrated_confidence(conf_raw, h, cal_map, min_conf=MIN_CONF, max_conf=MAX_CONF)
            conf_perf = recent_horizon_accuracy_conf(h, acc_latest, window_days=30, min_conf=MIN_CONF, max_conf=MAX_CONF)
            conf = combine_confidence(conf_raw, conf_cal, conf_perf, min_conf=MIN_CONF, max_conf=MAX_CONF)

            cb = float(np.clip(aion_cb, 0.70, 1.30))
            conf = float(np.clip(conf * (0.85 + 0.15 * cb), MIN_CONF, MAX_CONF))

            conf = soft_performance_cap(conf, conf_perf, max_overhang=0.12, min_conf=MIN_CONF, max_conf=MAX_CONF)

            rating, rating_score, label = _rating_from_return(pred_ret, stats, conf)

            std = float(stats.get("std", 0.05)) or 0.05
            score = float(np.tanh(pred_ret / (2.0 * std)) * conf)

            target_price = float(last_close * (1.0 + pred_ret)) if last_close is not None else None

            components: Dict[str, Any] = {
                "model": {
                    "raw_prediction": float(raw_pred),
                    "predicted_return": float(pred_ret),
                    "clip_limit": float(lim),
                    "was_clipped": bool(was_clipped),
                },
                "confidence": {
                    "raw": float(conf_raw),
                    "calibrated": float(conf_cal),
                    "recent_perf": float(conf_perf),
                    "p_hit": float(conf),
                },
                "aion_brain": {
                    "confidence_bias": float(aion_cb),
                    "updated_at": aion_meta.get("updated_at"),
                },
            }

            sym_res[h] = {
                "label": int(label),
                "confidence": float(conf),
                "score": float(score),
                "predicted_return": float(pred_ret),
                "target_price": target_price,
                "rating": rating,
                "rating_score": int(rating_score),
                "components": components,
            }

        if sym_res:
            preds[sym_u] = sym_res

    return preds


# ==========================================================
# CLI
# ==========================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AION Analytics Regression Model Engine")
    parser.add_argument("--mode", choices=["train", "predict"], default="train", help="Mode: train or predict")
    parser.add_argument("--trials", type=int, default=10, help="Optuna trials")
    parser.add_argument("--dataset", type=str, default="training_data_daily.parquet", help="Dataset name")
    parser.add_argument("--batch-size", type=int, default=150_000, help="Parquet scan batch size")
    parser.add_argument("--no-optuna", action="store_true", help="Disable Optuna tuning")
    parser.add_argument("--no-diag", action="store_true", help="Disable prediction diagnostics write (predict mode)")

    args = parser.parse_args()

    if args.mode == "train":
        summary = train_model(
            dataset_name=args.dataset,
            use_optuna=(not bool(args.no_optuna)),
            n_trials=int(args.trials),
            batch_size=int(args.batch_size),
        )
        print(json.dumps(summary, indent=2))
    else:
        log("[ai_model] 🔍 Running batch regression prediction (--mode predict)…")
        rolling = _read_rolling() or {}
        if not rolling:
            print(json.dumps({"error": "rolling_empty"}, indent=2))
        else:
            preds = predict_all(rolling, write_diagnostics=(not bool(args.no_diag)))
            out = {"symbols_predicted": len(preds), "sample_preview": {}}
            for s in sorted(preds.keys())[:5]:
                out["sample_preview"][s] = preds[s]
            print(json.dumps(out, indent=2))
