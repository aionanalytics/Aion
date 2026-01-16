"""Runtime intraday model loader + scorer (LightGBM + optional deep ensemble).

Patch: robust feature coercion
------------------------------
Fixes crashes like:
    could not convert string to float: '2025-12-30T16:18:16Z'

Root cause:
  • training coerces datetime/categorical/object features to numeric
  • runtime was passing raw strings into LightGBM predict()

This version coerces features in scoring to match training behavior as closely
as possible (datetime -> int64 ns; common categoricals -> stable codes; other
objects -> numeric-or-factorize fallback).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple, Dict

import lightgbm as lgb
import numpy as np
import pandas as pd

try:
    from dt_backend.core.config_dt import DT_PATHS  # type: ignore
except Exception:
    DT_PATHS: Dict[str, Path] = {
        "dtmodels": Path("dt_backend") / "models",
    }

from dt_backend.models import LABEL_ORDER, LABEL2ID, ID2LABEL, get_model_dir
from dt_backend.models.ensemble.intraday_hybrid_ensemble import (
    EnsembleConfig,
    IntradayHybridEnsemble,
)

try:
    from dt_backend.core.data_pipeline_dt import log  # type: ignore
except Exception:
    def log(msg: str) -> None:
        print(msg, flush=True)


@dataclass
class LoadedModels:
    lgb: Optional[lgb.Booster]
    lgb_features: Optional[list[str]]
    lstm: Any | None
    transf: Any | None
    ensemble_cfg: EnsembleConfig


# -------------------------------
# Feature coercion (runtime)
# -------------------------------

# These come from ml_data_builder_intraday.py defaults; training used factorize(sort=True),
# so we mirror that stable code mapping to minimize train/serve skew.
_TREND_CODES = {
    "down": 0,
    "flat": 1,
    "strong_down": 2,
    "strong_up": 3,
    "up": 4,
}
_VOL_BUCKET_CODES = {
    "high": 0,
    "low": 1,
    "mid": 2,
}

def _coerce_features_runtime(X: pd.DataFrame) -> pd.DataFrame:
    """Coerce mixed feature frame into numeric types safe for LightGBM predict()."""
    X2 = pd.DataFrame(index=X.index)

    for c in X.columns:
        s = X[c]

        # Handle known categoricals with stable codes
        if c == "intraday_trend":
            ss = s.astype("string").str.lower()
            X2[c] = ss.map(_TREND_CODES).fillna(-1).astype(np.int32)
            continue
        if c == "vol_bucket":
            ss = s.astype("string").str.lower()
            X2[c] = ss.map(_VOL_BUCKET_CODES).fillna(-1).astype(np.int32)
            continue

        # datetimes
        if pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_datetime64tz_dtype(s):
            try:
                if pd.api.types.is_datetime64tz_dtype(s):
                    s = s.dt.tz_convert("UTC").dt.tz_localize(None)
                X2[c] = s.astype("int64").fillna(0).astype(np.int64)
            except Exception:
                X2[c] = 0
            continue

        # bools
        if pd.api.types.is_bool_dtype(s):
            X2[c] = s.fillna(False).astype(np.int8)
            continue

        # numerics
        if pd.api.types.is_numeric_dtype(s):
            X2[c] = pd.to_numeric(s, errors="coerce").fillna(0.0).astype(np.float32)
            continue

        # object / strings: try datetime -> numeric -> factorize
        try:
            # datetime-ish strings (e.g. 2025-12-30T16:18:16Z)
            dt = pd.to_datetime(s, errors="coerce", utc=True)
            if dt.notna().any():
                # Convert to UTC-naive ns (matches training coercion style)
                dt2 = dt.dt.tz_convert("UTC").dt.tz_localize(None)
                ns = dt2.astype("int64")
                ns = ns.where(dt.notna(), 0)
                X2[c] = ns.astype(np.int64)
                continue
        except Exception:
            pass

        try:
            num = pd.to_numeric(s, errors="coerce")
            if num.notna().any():
                X2[c] = num.fillna(0.0).astype(np.float32)
                continue
        except Exception:
            pass

        try:
            # Last resort: factorize for any remaining objects (matches training approach)
            codes, _ = pd.factorize(s.astype("string"), sort=True)
            X2[c] = pd.Series(codes, index=X.index).astype(np.int32)
        except Exception:
            X2[c] = 0.0

    return X2


# ----- LightGBM loader -----

def _safe_load_booster(model_path: Path) -> Optional[lgb.Booster]:
    try:
        if model_path.stat().st_size < 64:
            log(f"[ai_model_intraday] ⚠️ model file too small ({model_path.stat().st_size} bytes): {model_path}")
            return None
    except Exception:
        pass
    try:
        return lgb.Booster(model_file=str(model_path))
    except Exception as e:
        log(f"[ai_model_intraday] ⚠️ failed to load LightGBM model at {model_path}: {e}")
        return None


def _load_lgbm(version_date: Optional[str] = None) -> Tuple[Optional[lgb.Booster], Optional[list[str]]]:
    """
    Load LightGBM model, optionally from a specific version.
    
    Args:
        version_date: Optional date string (YYYY-MM-DD) to load a specific version.
                     If None, loads the latest/current model.
    """
    # Try loading versioned model if date is specified
    if version_date:
        try:
            from dt_backend.ml.model_version_manager import load_model_version
            version_dir = load_model_version("lightgbm_intraday", version_date)
            if version_dir:
                model_path = version_dir / "model.txt"
                fmap_path = version_dir / "feature_map.json"
                
                if model_path.exists():
                    booster = _safe_load_booster(model_path)
                    if booster:
                        features: Optional[list[str]] = None
                        try:
                            if fmap_path.exists():
                                with fmap_path.open("r", encoding="utf-8") as f:
                                    features = json.load(f)
                            else:
                                features = booster.feature_name()
                        except Exception:
                            features = booster.feature_name()
                        
                        log(f"[ai_model_intraday] ✅ Loaded LightGBM model version {version_date}")
                        return booster, features
        except Exception as e:
            log(f"[ai_model_intraday] ⚠️ Failed to load versioned model: {e}")
    
    # Load current/latest model
    model_dir = get_model_dir("lightgbm_intraday")  # canonical intraday artifact folder
    model_path = model_dir / "model.txt"
    bak_path = model_dir / "model.txt.bak"
    fmap_path = model_dir / "feature_map.json"

    if not model_path.exists() and not bak_path.exists():
        log(f"[ai_model_intraday] ⚠️ LightGBM model not found at {model_path}")
        return None, None

    booster = None
    if model_path.exists():
        booster = _safe_load_booster(model_path)

    # Fallback to backup if primary is corrupt/unloadable
    if booster is None and bak_path.exists():
        log("[ai_model_intraday] ⚠️ Falling back to model.txt.bak")
        booster = _safe_load_booster(bak_path)

    if booster is None:
        return None, None

    features: Optional[list[str]] = None
    try:
        if fmap_path.exists():
            with fmap_path.open("r", encoding="utf-8") as f:
                features = json.load(f)
        else:
            features = booster.feature_name()
    except Exception:
        features = booster.feature_name()

    log("[ai_model_intraday] ✅ Loaded LightGBM intraday model.")
    return booster, features


# ----- Optional deep models -----

def _load_lstm() -> Any | None:
    try:
        from dt_backend.models.lstm_intraday import load_lstm_intraday  # type: ignore
    except Exception:
        log("[ai_model_intraday] ℹ️ LSTM intraday module not available.")
        return None
    try:
        model = load_lstm_intraday()
        log("[ai_model_intraday] ✅ Loaded LSTM intraday model.")
        return model
    except Exception as e:
        log(f"[ai_model_intraday] ⚠️ Failed to load LSTM intraday model: {e}")
        return None


def _load_transformer() -> Any | None:
    try:
        from dt_backend.models.transformer_intraday import load_transformer_intraday  # type: ignore
    except Exception:
        log("[ai_model_intraday] ℹ️ Transformer intraday module not available.")
        return None
    try:
        model = load_transformer_intraday()
        log("[ai_model_intraday] ✅ Loaded Transformer intraday model.")
        return model
    except Exception as e:
        log(f"[ai_model_intraday] ⚠️ Failed to load Transformer intraday model: {e}")
        return None


# ----- Public loader -----

def load_intraday_models(version_date: Optional[str] = None) -> LoadedModels:
    """
    Load intraday models, optionally from a specific version date.
    
    Args:
        version_date: Optional date string (YYYY-MM-DD) to load versioned models.
                     If None, loads the latest/current models.
    
    Returns:
        LoadedModels with all available models
    """
    lgb_model, lgb_feats = _load_lgbm(version_date=version_date)
    lstm_model = _load_lstm()
    transf_model = _load_transformer()

    cfg = EnsembleConfig.load()

    if lgb_model is None and lstm_model is None and transf_model is None:
        log("[ai_model_intraday] ❌ No intraday models available.")

    if lgb_model is not None:
        log("[ai_model_intraday] 🔗 LightGBM active.")
    if lstm_model is not None:
        log("[ai_model_intraday] 🔗 LSTM active.")
    if transf_model is not None:
        log("[ai_model_intraday] 🔗 Transformer active.")

    return LoadedModels(
        lgb=lgb_model,
        lgb_features=lgb_feats,
        lstm=lstm_model,
        transf=transf_model,
        ensemble_cfg=cfg,
    )


# ----- Scoring helpers -----

def _predict_lgbm_proba(
    booster: lgb.Booster,
    X: pd.DataFrame,
    feature_names: Optional[list[str]] = None,
) -> np.ndarray:
    # Coerce to numeric first (fixes string timestamp crashes)
    X_local = _coerce_features_runtime(X)

    if feature_names is not None:
        missing = [c for c in feature_names if c not in X_local.columns]
        if missing:
            for c in missing:
                X_local[c] = 0.0
        X_local = X_local[feature_names]

    # LightGBM happily consumes numeric DataFrame; keep as DataFrame to preserve dtypes.
    raw = booster.predict(X_local)
    return np.asarray(raw, dtype=float)


def score_intraday_batch(
    features: pd.DataFrame,
    models: Optional[LoadedModels] = None,
) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Score a batch of intraday features.

    Args:
        features: DataFrame with one row per symbol / sample.
        models: optional pre-loaded models; if None, they are loaded on demand.

    Returns:
        proba_df: DataFrame with columns LABEL_ORDER.
        label_series: Series of predicted label strings.
    """
    if models is None:
        models = load_intraday_models()

    if models.lgb is None and models.lstm is None and models.transf is None:
        raise RuntimeError("No intraday models available for scoring.")

    X = features.copy()

    p_lgb = None
    p_lstm = None
    p_transf = None

    if models.lgb is not None:
        p_lgb = _predict_lgbm_proba(models.lgb, X, feature_names=models.lgb_features)

    if models.lstm is not None:
        try:
            p_lstm = models.lstm.predict_proba(X)  # type: ignore[attr-defined]
        except Exception as e:
            log(f"[ai_model_intraday] ⚠️ LSTM.predict_proba failed: {e}")
            p_lstm = None

    if models.transf is not None:
        try:
            p_transf = models.transf.predict_proba(X)  # type: ignore[attr-defined]
        except Exception as e:
            log(f"[ai_model_intraday] ⚠️ Transformer.predict_proba failed: {e}")
            p_transf = None

    active = [p for p in (p_lgb, p_lstm, p_transf) if p is not None]
    if not active:
        raise RuntimeError("No valid probability outputs from intraday models.")

    if len(active) == 1:
        proba = active[0]
    else:
        ensemble = IntradayHybridEnsemble(models.ensemble_cfg)
        proba = ensemble.predict_proba(p_lgb=p_lgb, p_lstm=p_lstm, p_transf=p_transf)

    proba_df = pd.DataFrame(proba, index=features.index, columns=LABEL_ORDER)
    idx = np.argmax(proba, axis=1)
    labels = [LABEL_ORDER[int(i)] for i in idx]
    label_series = pd.Series(labels, index=features.index, name="label_pred")
    return proba_df, label_series


if __name__ == "__main__":
    # Tiny smoke test with random-ish features, including string timestamps
    n = 5
    dummy = pd.DataFrame({f"f{i}": np.random.randn(n) for i in range(10)})
    dummy["ts"] = ["2025-12-30T16:18:16Z"] * n
    dummy["intraday_trend"] = ["flat", "up", "down", "strong_up", "strong_down"]
    dummy["vol_bucket"] = ["low", "mid", "high", "low", "mid"]
    try:
        proba_df, labels = score_intraday_batch(dummy)
        log(f"[ai_model_intraday] Demo OK — got {len(proba_df)} rows.")
        print(labels.head())
    except Exception as e:
        log(f"[ai_model_intraday] Demo failed: {e}")
