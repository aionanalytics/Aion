"""backend.core.ai_model.constants

Single source of truth for AI-model paths, horizons, and tunables.

Why this exists:
During the mechanical split of a monolithic ai_model.py into multiple modules,
some modules ended up depending on constants that were previously defined
"somewhere else" via star-imports. That creates circular imports and missing
names at runtime.

Putting config + paths here keeps the modules import-safe.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

import numpy as np

from backend.core.config import PATHS


# ==========================================================
# Paths & constants
# ==========================================================

ML_DATA_ROOT: Path = Path(PATHS.get("ml_data", Path("ml_data")))

DATASET_DIR: Path = ML_DATA_ROOT / "nightly" / "dataset"
DATASET_DIR.mkdir(parents=True, exist_ok=True)

DATASET_FILE: Path = DATASET_DIR / "training_data_daily.parquet"
FEATURE_LIST_FILE: Path = DATASET_DIR / "feature_list_daily.json"

LATEST_FEATURES_FILE: Path = DATASET_DIR / "latest_features_daily.parquet"
LATEST_FEATURES_CSV: Path = DATASET_DIR / "latest_features_daily.csv"

MODEL_ROOT: Path = Path(PATHS.get("ml_models", ML_DATA_ROOT / "nightly" / "models"))
MODEL_ROOT.mkdir(parents=True, exist_ok=True)

TMP_MEMMAP_ROOT: Path = Path(PATHS.get("ml_tmp_memmap", ML_DATA_ROOT / "tmp" / "memmap"))
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
    [
        -0.50,
        -0.30,
        -0.20,
        -0.12,
        -0.08,
        -0.05,
        -0.03,
        -0.02,
        -0.01,
        0.00,
        0.01,
        0.02,
        0.03,
        0.05,
        0.08,
        0.12,
        0.20,
        0.30,
        0.50,
    ],
    dtype=float,
)


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


# --------------------------
# Sanity-gate thresholds
# --------------------------

MIN_USABLE_ROWS: int = _env_int("AION_ML_MIN_ROWS_PER_HORIZON", 20_000)
MIN_TARGET_STD: float = _env_float("AION_ML_MIN_TARGET_STD", 1e-4)
MIN_PRED_STD: float = _env_float("AION_ML_MIN_PRED_STD", 1e-6)
MAX_TARGET_ZERO_FRAC: float = _env_float("AION_ML_MAX_TARGET_ZERO_FRAC", 0.995)
MAX_CLIP_SAT_FRAC: float = _env_float("AION_ML_MAX_CLIP_SATURATION_FRAC", 0.18)

MAX_STATS_SAMPLES: int = _env_int("AION_ML_MAX_STATS_SAMPLES", 200_000)
MAX_VAL_SAMPLES: int = _env_int("AION_ML_MAX_VAL_SAMPLES", 80_000)

# Dynamic clip controls
CLIP_FACTOR: float = _env_float("AION_ML_CLIP_FACTOR", 1.20)
MIN_CLIP_SHORT: float = _env_float("AION_ML_MIN_CLIP_SHORT", 0.03)
MIN_CLIP_LONG: float = _env_float("AION_ML_MIN_CLIP_LONG", 0.06)
MAX_CLIP_LONG: float = _env_float("AION_ML_MAX_CLIP_LONG", 0.45)
MAX_CLIP_SHORT: float = _env_float("AION_ML_MAX_CLIP_SHORT", 0.25)
