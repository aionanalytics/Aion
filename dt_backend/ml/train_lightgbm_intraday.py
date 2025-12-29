"""Train a fast 3-class LightGBM intraday model.

Expects a parquet built by ml_data_builder_intraday.py with:
  - feature columns
  - label column: 'label' (SELL/HOLD/BUY) or 'label_id' (0/1/2)

Saves artifacts under: DT_PATHS["dtmodels"] / "lightgbm_intraday"
  - model.txt
  - feature_map.json
  - label_map.json

Why this patch exists:
- Your runtime loader expects: dt_backend/models/lightgbm_intraday/model.txt
- Your previous trainer was saving into get_model_dir("lightgbm") which can point
  somewhere else (often dt_backend/models/lightgbm/...), leaving the intraday
  path stuck with a placeholder file and causing:
  [LightGBM] [Fatal] Unknown model format or submodel type

This version *always* writes to the intraday folder and writes atomically.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

import lightgbm as lgb
import pandas as pd

try:
    from dt_backend.core.config_dt import DT_PATHS  # type: ignore
except Exception:
    DT_PATHS: Dict[str, Path] = {
        "dtml_data": Path("ml_data_dt"),
        "dtmodels": Path("dt_backend") / "models",
    }

from dt_backend.models import LABEL_ORDER, LABEL2ID, ID2LABEL

try:
    from dt_backend.core.data_pipeline_dt import log  # type: ignore
except Exception:
    def log(msg: str) -> None:
        print(msg, flush=True)


def _resolve_training_data() -> Path:
    base = Path(DT_PATHS.get("dtml_data", Path("ml_data_dt")))
    return base / "training_data_intraday.parquet"


def _load_training_data() -> Tuple[pd.DataFrame, pd.Series]:
    path = _resolve_training_data()
    if not path.exists():
        raise FileNotFoundError(f"Intraday training data not found at {path}")

    log(f"[train_lightgbm_intraday] 📦 Loading training data from {path}")
    df = pd.read_parquet(path)
    if df.empty:
        raise ValueError(f"Training dataframe at {path} is empty.")

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

    X = df.drop(columns=[c for c in ("label", "label_id") if c in df.columns])

    # Keep only numeric columns (LightGBM can handle categorical, but you must
    # explicitly encode; safest default for intraday stability is numeric-only).
    non_numeric = [c for c in X.columns if not pd.api.types.is_numeric_dtype(X[c])]
    if non_numeric:
        log(f"[train_lightgbm_intraday] ⚠️ Dropping non-numeric columns: {non_numeric[:10]}{'...' if len(non_numeric)>10 else ''}")
        X = X.drop(columns=non_numeric)

    if X.shape[1] == 0:
        raise ValueError("No usable numeric feature columns found after filtering.")

    return X, y


def _train_lgb(
    X: pd.DataFrame,
    y: pd.Series,
    params: Dict[str, Any] | None = None,
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

    log(f"[train_lightgbm_intraday] 🚀 Training on {len(X):,} rows, {X.shape[1]} features...")
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=400,
        valid_sets=[dtrain],
        valid_names=["train"],
        verbose_eval=50,
    )
    log("[train_lightgbm_intraday] ✅ Training complete.")
    return booster


def _model_dir_intraday() -> Path:
    # IMPORTANT: Always match the runtime loader path
    base = Path(DT_PATHS.get("dtmodels", Path("dt_backend") / "models"))
    return base / "lightgbm_intraday"


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _save_artifacts(booster: lgb.Booster, feature_names: list[str]) -> None:
    model_dir = _model_dir_intraday()
    model_dir.mkdir(parents=True, exist_ok=True)

    model_path = model_dir / "model.txt"
    fmap_path = model_dir / "feature_map.json"
    label_map_path = model_dir / "label_map.json"

    # Atomic model save: write to temp then rename
    tmp_model = model_path.with_suffix(".txt.tmp")
    booster.save_model(str(tmp_model))
    tmp_model.replace(model_path)

    _atomic_write_text(fmap_path, json.dumps(feature_names, ensure_ascii=False, indent=2))

    _atomic_write_text(
        label_map_path,
        json.dumps(
            {
                "label_order": LABEL_ORDER,
                "label2id": LABEL2ID,
                "id2label": ID2LABEL,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    log(f"[train_lightgbm_intraday] 💾 Saved model → {model_path}")
    log(f"[train_lightgbm_intraday] 💾 Saved feature_map → {fmap_path}")
    log(f"[train_lightgbm_intraday] 💾 Saved label_map → {label_map_path}")


def train_lightgbm_intraday() -> Dict[str, Any]:
    """High-level entrypoint to train & persist the intraday LightGBM model."""
    X, y = _load_training_data()
    booster = _train_lgb(X, y)
    _save_artifacts(booster, list(X.columns))
    summary = {
        "n_rows": int(len(X)),
        "n_features": int(X.shape[1]),
        "label_order": LABEL_ORDER,
        "model_dir": str(_model_dir_intraday()),
    }
    log(f"[train_lightgbm_intraday] 📊 Summary: {summary}")
    return summary


if __name__ == "__main__":
    train_lightgbm_intraday()
