"""
dt_backend/core/config_dt.py (SHIM)

This file is now a thin compatibility layer.

Canonical sources of truth live at repo root:
- config.py      (paths)
- settings.py    (knobs/timezone defaults)
- admin_keys.py  (secrets)

Do NOT add new configuration here.

UPDATED: Added support for model versions and regime cache paths.
"""

from __future__ import annotations

from pathlib import Path

from config import ROOT, DT_PATHS, ensure_dt_dirs, get_dt_path  # type: ignore
from settings import TIMEZONE  # type: ignore
from admin_keys import (  # type: ignore
    ALPACA_API_KEY_ID,
    ALPACA_API_SECRET_KEY,
    ALPACA_PAPER_BASE_URL,
    ALPACA_KEY,
    ALPACA_SECRET,
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_ANON_KEY,
    SUPABASE_BUCKET,
)

# ============================================================
# EXTEND DT_PATHS with Model Versioning and Regime Cache
# ============================================================

_ML_DATA_DT = DT_PATHS.get("ml_data_dt", Path("ml_data_dt"))
_MODELS_ROOT = DT_PATHS.get("models_root", Path("dt_backend") / "models")

# Model version management
DT_PATHS.setdefault("model_versions_root", _MODELS_ROOT / "versions")
DT_PATHS.setdefault("model_versions_lgbm", _MODELS_ROOT / "versions" / "lightgbm_intraday")
DT_PATHS.setdefault("model_versions_lstm", _MODELS_ROOT / "versions" / "lstm_intraday")
DT_PATHS.setdefault("model_versions_transformer", _MODELS_ROOT / "versions" / "transformer_intraday")

# Regime calculation cache
DT_PATHS.setdefault("regime_cache_dir", _ML_DATA_DT / "intraday" / "regime_cache")

# Validation results
DT_PATHS.setdefault("replay_validation_dir", _ML_DATA_DT / "intraday" / "replay" / "validation")

# Ensure new directories exist
try:
    for key in ["model_versions_root", "regime_cache_dir", "replay_validation_dir"]:
        path = DT_PATHS.get(key)
        if path:
            path.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
