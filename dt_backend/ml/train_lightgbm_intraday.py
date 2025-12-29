"""Train a fast 3-class LightGBM intraday model.

Expects a parquet built by ml_data_builder_intraday.py with:
  - feature columns
  - label column: 'label' (SELL/HOLD/BUY) or 'label_id' (0/1/2)

Saves artifacts under: DT_PATHS["dtmodels"] / "lightgbm_intraday"
  - model.txt
  - feature_map.json
  - label_map.json

Important:
- Your scheduler is reading:
    dt_backend/models/lightgbm_intraday/model.txt
  So training MUST write there (not dt_backend/models/lightgbm/model.txt).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import lightgbm as lgb
import numpy as np
import pandas as pd

try:
    from dt_backend.core.config_dt import DT_PATHS  # type: ignore
except Exception:
    DT_PATHS: Dict[str, Path] = {
        "ml_data_dt": Path("ml_data_dt"),
        "dtmodels": Path("dt_backend") / "models",
    }

from dt_backend.models import LABEL_ORDER, LABEL2ID, ID2LABEL

try:
    from dt_backend.core.data_pipeline_dt import log  # type: ignore
except Exception:
    def log(msg: str) -> None:
        print(msg, flush=True)


def _resolve_training_data() -> Path:
    root = (
        DT_PATHS.get("ml_data_dt")
        or DT_PATHS.get("dtml_data")
        or DT_PATHS.get("ml_data")
        or Path("ml_data_dt")
    )
    return Path(root) / "training_data_intraday.parquet"


def _coerce_features(X: pd.DataFrame) -> pd.DataFrame:
    """Convert arbitrary feature dataframe into numeric matrix for LightGBM."""
    X2 = pd.DataFrame(index=X.index)

    for c in X.columns:
        s = X[c]

        # datetime-like
        if pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_datetime64tz_dtype(s):
            try:
                if pd.api.types.is_datetime64tz_dtype(s):
                    s = s.dt.tz_convert("UTC").dt.tz_localize(None)
                X2[c] = s.astype("int64").fillna(0).astype(np.int64)
            except Exception:
                X2[c] = 0
            continue

        # bool
        if pd.api.types.is_bool_dtype(s):
            X2[c] = s.fillna(False).astype(np.int8)
            continue

        # category / object -> factorize
        if pd.api.types.is_object_dtype(s) or isinstance(s.dtype, pd.CategoricalDtype):
            codes, _ = pd.factorize(s.astype("string"), sort=True)
            X2[c] = pd.Series(codes, index=X.index).astype(np.int32)
            continue

        # numeric
        if pd.api.types.is_numeric_dtype(s):
            X2[c] = pd.to_numeric(s, errors="coerce").fillna(0.0).astype(np.float32)
            continue

        # fallback
        X2[c] = pd.to_numeric(s.astype("string"), errors="coerce").fillna(0.0).astype(np.float32)

    return X2


def _load_training_data() -> Tuple[pd.DataFrame, pd.Series]:
    path = _resolve_training_data()
    if not path.exists():
        raise FileNotFoundError(f"Intraday training data not found at {path}")
    log(f"[train_lightgbm_intraday] 📦 Loading training data from {path}")
    df = pd.read_parquet(path)
    if df.empty:
        raise ValueError(f"Training dataframe at {path} is empty.")

    # Labels
    if "label_id" in df.columns:
        y = df["label_id"].astype(int)
    elif "label" in df.columns:
        y = df["label"].map(LABEL2ID)
        if y.isna().any():
            bad = df["label"][y.isna()].unique().tolist()
            raise ValueError(f"Unknown labels in training data: {bad}")
        y = y.astype(int)
    else:
        raise ValueError("Training data must contain 'label' or 'label_id' column.")

    drop_cols = [c for c in ("label", "label_id") if c in df.columns]
    X = df.drop(columns=drop_cols)

    # Prevent ticker memorization
    if "symbol" in X.columns:
        X = X.drop(columns=["symbol"])

    X = _coerce_features(X)
    return X, y


def _train_lgb(
    X: pd.DataFrame,
    y: pd.Series,
    params: Optional[Dict[str, Any]] = None,
) -> lgb.Booster:
    if params is None:
        params = {
            "objective": "multiclass",
            "num_class": len(LABEL_ORDER),
            "metric": ["multi_logloss"],
            "learning_rate": 0.05,
            "num_leaves": 63,
            "max_depth": -1,
            "feature_fraction": 0.9,
            "bagging_fraction": 0.9,
            "bagging_freq": 1,
            "min_data_in_leaf": 50,
            "seed": 42,
            "verbosity": -1,
        }

    dtrain = lgb.Dataset(X, label=y.values)

    try:
        log(f"[train_lightgbm_intraday] ℹ️ Using LightGBM v{getattr(lgb, '__version__', '?')}")
    except Exception:
        pass

    log(f"[train_lightgbm_intraday] 🚀 Training on {len(X):,} rows, {X.shape[1]} features...")

    callbacks = [lgb.log_evaluation(period=50)]
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=400,
        valid_sets=[dtrain],
        valid_names=["train"],
        callbacks=callbacks,
    )
    log("[train_lightgbm_intraday] ✅ Training complete.")
    return booster


def _resolve_model_dir() -> Path:
    base = DT_PATHS.get("dtmodels") or DT_PATHS.get("dt_models") or (Path("dt_backend") / "models")
    return Path(base) / "lightgbm_intraday"


def _save_artifacts(booster: lgb.Booster, feature_names: list[str]) -> None:
    model_dir = _resolve_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)

    model_path = model_dir / "model.txt"
    fmap_path = model_dir / "feature_map.json"
    label_map_path = model_dir / "label_map.json"

    booster.save_model(str(model_path))

    with fmap_path.open("w", encoding="utf-8") as f:
        json.dump(feature_names, f, ensure_ascii=False, indent=2)

    with label_map_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "label_order": LABEL_ORDER,
                "label2id": LABEL2ID,
                "id2label": ID2LABEL,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    log(f"[train_lightgbm_intraday] 💾 Saved model → {model_path}")
    log(f"[train_lightgbm_intraday] 💾 Saved feature_map → {fmap_path}")
    log(f"[train_lightgbm_intraday] 💾 Saved label_map → {label_map_path}")


def train_lightgbm_intraday() -> Dict[str, Any]:
    X, y = _load_training_data()
    booster = _train_lgb(X, y)
    _save_artifacts(booster, list(X.columns))
    summary = {
        "n_rows": int(len(X)),
        "n_features": int(X.shape[1]),
        "label_order": LABEL_ORDER,
        "labels": sorted(set(int(v) for v in np.unique(y.values))),
        "model_dir": str(_resolve_model_dir()),
    }
    log(f"[train_lightgbm_intraday] 📊 Summary: {summary}")
    return summary


if __name__ == "__main__":
    train_lightgbm_intraday()
