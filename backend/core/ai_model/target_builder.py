# Auto-refactor from backend/core/ai_model.py v1.7.0 (mechanical split)

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Dict, Any

import numpy as np

from backend.core.config import PATHS
from backend.core.data_pipeline import log

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

def _model_path(horizon: str, model_root: Path | None = None) -> Path:
    root = model_root or MODEL_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root / f"regressor_{horizon}.pkl"

def _booster_path(horizon: str, model_root: Path | None = None) -> Path:
    root = model_root or MODEL_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root / f"regressor_{horizon}.txt"

def _feature_map_path(horizon: str, model_root: Path | None = None) -> Path:
    root = model_root or MODEL_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root / f"feature_map_{horizon}.json"


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

def _load_horizon_feature_map(horizon: str, fallback: List[str], model_root: Path | None = None) -> List[str]:
    p = _feature_map_path(horizon, model_root=model_root)
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

def _save_return_stats(stats: Dict[str, Any], stats_path: Path | None = None) -> None:
    payload = {"generated_at": datetime.now(TIMEZONE).isoformat(), "horizons": stats}
    try:
        METRICS_ROOT.mkdir(parents=True, exist_ok=True)
        (stats_path or RETURN_STATS_FILE).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log(f"[ai_model] 📊 Return stats written → {RETURN_STATS_FILE}")
    except Exception as e:
        log(f"[ai_model] ⚠️ Failed to write return stats: {e}")

def _load_return_stats(stats_path: Path | None = None) -> Dict[str, Any]:
    path = stats_path or RETURN_STATS_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
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
    symbol_whitelist: set[str] | None = None,
):
    pa, ds = _try_import_pyarrow()
    if pa is None or ds is None:
        raise RuntimeError("pyarrow is required for streaming parquet batches")

    dataset = ds.dataset(str(parquet_path), format="parquet")
    scanner = dataset.scanner(columns=columns, batch_size=int(batch_size))
    for batch in scanner.to_batches():
        if batch.num_rows <= 0:
            continue
        df = batch.to_pandas()
        if symbol_whitelist and 'symbol' in df.columns:
            df = df[df['symbol'].astype(str).str.upper().isin(symbol_whitelist)]
        if df is None or len(df) <= 0:
            continue
        yield df


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
    symbol_whitelist: set[str] | None = None,
) -> Dict[str, Any]:
    usable = 0
    samples: List[float] = []

    try:
        for df in _iter_parquet_batches(parquet_path, [target_col], batch_size=batch_size, symbol_whitelist=symbol_whitelist):
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
    symbol_whitelist: set[str] | None = None,
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

    for df in _iter_parquet_batches(parquet_path, cols, batch_size=batch_size, symbol_whitelist=symbol_whitelist):
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
