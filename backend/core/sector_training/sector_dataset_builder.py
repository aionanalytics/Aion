from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backend.core.data_pipeline import log
from backend.core.ai_model.target_builder import _stream_validation_sample  # reuse existing sampler if present

@dataclass
class RegimeProfile:
    """Simple regime selector for long horizons."""
    vol_col: str = "volatility_20d"
    trend_col: str = "trend_strength_20d"
    vol_min: float = 0.0
    trend_min: float = 0.0

def default_regime_profile_for_horizon(horizon: str) -> RegimeProfile:
    # Long horizons: require some volatility + trend to avoid calm-market poisoning.
    is_long = horizon in ("13w", "26w", "52w")
    if not is_long:
        return RegimeProfile(vol_min=0.0, trend_min=0.0)
    vol_min = float(os.getenv("AION_REGIME_VOL_MIN_LONG", "0.015"))  # ~1.5% daily vol proxy (depends on feature)
    trend_min = float(os.getenv("AION_REGIME_TREND_MIN_LONG", "0.10"))  # arbitrary proxy scale; tune later
    return RegimeProfile(vol_min=vol_min, trend_min=trend_min)

def apply_regime_filter(df: pd.DataFrame, profile: RegimeProfile) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df
    if profile.vol_min > 0 and profile.vol_col in out.columns:
        out = out[pd.to_numeric(out[profile.vol_col], errors="coerce").fillna(0.0) >= profile.vol_min]
    if profile.trend_min > 0 and profile.trend_col in out.columns:
        out = out[pd.to_numeric(out[profile.trend_col], errors="coerce").fillna(0.0) >= profile.trend_min]
    return out

def build_sector_row_mask(sector_labels: pd.Series, sector: str) -> np.ndarray:
    sec = str(sector).upper().strip()
    return (sector_labels.astype(str).str.upper().values == sec)

