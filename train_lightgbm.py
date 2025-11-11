"""
train_lightgbm.py — v1.7.0 (Rolling-Native + Key Normalization)
Author: AION Analytics / StockAnalyzerPro

Phase-2 rolling-native version:
- Exports both model.txt and feature_list.json for strict inference.
- Safe trainer: handles sparse/NaN-heavy data and trains per-target where possible.
- Normalizes all input columns to snake_case for schema consistency.
- Compatible with nightly_job orchestration.
"""

import os, sys, warnings, json, glob
import numpy as np
import pandas as pd
from typing import Dict
from backend.config import PATHS  # ✅ unified path configuration

# --- Safe path fix (ensures imports work as module or script) ---
if __package__ is None or __package__ == "":
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------
# Data paths from config.py
# ---------------------------------------------------------------------
DATA_DIR = PATHS["ml_data"]          # ✅ use base dir instead of single file
MODEL_ROOT = PATHS["ml_models"]      # ✅ unified model directory

# ---------------------------------------------------------------------
# Dataset discovery
# ---------------------------------------------------------------------
def find_datasets():
    """Finds all training_data_*.parquet files under ml_data."""
    paths = glob.glob(os.path.join(DATA_DIR, "training_data_*.parquet"))
    return [p for p in paths if os.path.isfile(p)]

# ---------------------------------------------------------------------
# Normalization Helpers
# ---------------------------------------------------------------------
NORMALIZE_KEYS = {
    "peRatio": "pe_ratio", "pbRatio": "pb_ratio", "psRatio": "ps_ratio",
    "pegRatio": "peg_ratio", "debtEquity": "debt_equity", "debtEbitda": "debt_ebitda",
    "revenueGrowth": "revenue_growth", "epsGrowth": "eps_growth",
    "profitMargin": "profit_margin", "operatingMargin": "operating_margin",
    "grossMargin": "gross_margin", "dividendYield": "dividend_yield",
    "payoutRatio": "payout_ratio", "marketCap": "marketCap",
    "roa": "roa", "roe": "roe", "roic": "roic",
}

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names from camelCase → snake_case."""
    if not isinstance(df, pd.DataFrame):
        return df
    rename_map = {old: new for old, new in NORMALIZE_KEYS.items() if old in df.columns}
    return df.rename(columns=rename_map)

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
def _log(msg: str) -> None:
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def _is_classification(colname: str) -> bool:
    return colname.endswith("_label") or colname.endswith("_cls")

# ---------------------------------------------------------------------
# Core training routine
# ---------------------------------------------------------------------
def train_lightgbm_models() -> Dict[str, dict]:
    """Train LightGBM models for each available target in the dataset."""
    try:
        import lightgbm as lgb  # type: ignore
    except Exception as e:
        _log(f"⚠️ LightGBM not available: {e}")
        return {}

    data_path = os.getenv("SAP_DAILY_PARQUET", str(PATHS["training_data_daily"]))
    data_path = Path(data_path)

    if not data_path.exists():
        _log(f"⚠️ Data not found: {data_path}")
        return {}

    df = pd.read_parquet(data_path)
    if df.empty:
        _log("⚠️ Empty dataset for training")
        return {}

    # ✅ Normalize all columns first
    df = normalize_columns(df)

    targets = [c for c in df.columns if c.startswith("target_")]
    _log(f"🧠 Found {len(targets)} targets → {targets}")

    drop_cols = set(["symbol", "date"]) | set(targets)
    X_full = (
        df.drop(columns=list(drop_cols), errors="ignore")
        .select_dtypes(include=["float64", "float32", "int64", "int32"])
        .copy()
    )

    metrics = {}
    for target in targets:
        y = df[target]
        mask = y.notna()
        X = X_full.loc[mask].copy()
        y = y.loc[mask]

        X = X.dropna(axis=1, how="all")
        thresh = max(1, int(X.shape[1] * 0.8))
        X = X.dropna(thresh=thresh)
        y = y.loc[X.index]

        if X.shape[1] == 0 or y.shape[0] < 100:
            _log(f"⚠️ Skipping {target} — insufficient usable data (X:{X.shape}, y:{y.shape})")
            continue

        n = len(y)
        split = int(n * 0.8)
        X_train, X_valid = X.iloc[:split], X.iloc[split:]
        y_train, y_valid = y.iloc[:split], y.iloc[split:]

        if X_train.empty or X_valid.empty:
            _log(f"⚠️ Skipping {target} — empty train/valid split")
            continue

        try:
            import lightgbm as lgb

            MODEL_ROOT.mkdir(parents=True, exist_ok=True)

            h = target.replace("target_", "")
            model_dir = MODEL_ROOT / h
            model_dir.mkdir(parents=True, exist_ok=True)

            if _is_classification(target):
                clf = lgb.LGBMClassifier(
                    n_estimators=300, learning_rate=0.05,
                    subsample=0.8, colsample_bytree=0.8
                )
                clf.fit(X_train, y_train)
                acc = float((clf.predict(X_valid) == y_valid).mean())
                metrics[target] = {
                    "task": "classification",
                    "acc": acc,
                    "n_features": int(X.shape[1]),
                    "n_samples": int(n),
                }

                model_path = model_dir / "model.txt"
                clf.booster_.save_model(str(model_path))
                with open(model_dir / "feature_list.json", "w", encoding="utf-8") as f:
                    json.dump(list(X_train.columns), f, indent=2)
                _log(f"💾 Saved classification model → {model_path}")

            else:
                reg = lgb.LGBMRegressor(
                    n_estimators=400, learning_rate=0.05,
                    subsample=0.8, colsample_bytree=0.8
                )
                reg.fit(X_train, y_train)
                pred = reg.predict(X_valid)
                rmse = float(np.sqrt(np.mean((pred - y_valid.values) ** 2)))
                metrics[target] = {
                    "task": "regression",
                    "rmse": rmse,
                    "n_features": int(X.shape[1]),
                    "n_samples": int(n),
                }

                model_path = model_dir / "model.txt"
                reg.booster_.save_model(str(model_path))
                with open(model_dir / "feature_list.json", "w", encoding="utf-8") as f:
                    json.dump(list(X_train.columns), f, indent=2)
                _log(f"💾 Saved regression model → {model_path}")

            _log(f"✅ Trained {target} — {metrics[target]}")

        except Exception as e:
            _log(f"⚠️ Training failed for {target}: {e}")
            continue

    return metrics

# ---------------------------------------------------------------------
# ✅ Added for nightly_job.py compatibility
# ---------------------------------------------------------------------
def train_all_models():
    """
    Trains all core ML models on freshly built datasets (daily + weekly).
    Safe for nightly_job automation.
    """
    results = {}
    try:
        from backend.config import get_path
        from backend.train_lightgbm import train_lightgbm_models

        daily_path = get_path("training_data_daily")
        weekly_path = get_path("training_data_weekly")

        print("[train_lightgbm] 🚀 Starting full model training...")

        # ✅ Automatically detect known datasets (daily, weekly, monthly, etc.)
        datasets = {
            "daily": daily_path,
            "weekly": weekly_path,
        }

        # ✅ Loop through configured datasets
        for tag, path in datasets.items():
            if not path or not os.path.exists(path):
                print(f"[train_lightgbm] ⚠️ Skipping {tag} — dataset not found at {path}")
                continue

            print(f"[train_lightgbm] 🧩 Found {tag} dataset: {path}")
            os.environ["SAP_DAILY_PARQUET"] = str(path)
            results[tag] = train_lightgbm_models()

        print(f"[train_lightgbm] ✅ Training complete for {len(results)} datasets.")
        return results

    except Exception as e:
        print(f"[train_lightgbm] ⚠️ train_all_models() failed: {e}")
        return {}

