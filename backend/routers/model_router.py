# backend/routers/model_router.py — v3.0 (Regression-Only)
"""
AION Analytics — Model Router (Regression Backend Models)

Endpoints:
    GET  /api/models/status
    POST /api/models/train?use_optuna=true&n_trials=30
    POST /api/models/tune?n_trials=30
    GET  /api/models/predict?symbols=AAPL,MSFT&full=false
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query

from backend.core.config import PATHS
from backend.core.data_pipeline import _read_rolling, log
from backend.core.ai_model.core_training import train_all_models, predict_all
from backend.core.ai_model.target_builder import HORIZONS, _model_path
router = APIRouter(prefix="/api/models", tags=["models"])


# ==========================================================
# Status
# ==========================================================

def _collect_model_status() -> Dict[str, Any]:
    model_root = PATHS.get("ml_models", PATHS["ml_data"] / "nightly" / "models")
    horizons_info: Dict[str, Any] = {}

    for h in HORIZONS:
        reg_p = _model_path(h)

        horizons_info[h] = {
            "regressor": {
                "exists": reg_p.exists(),
                "path": str(reg_p),
            },
        }

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "model_root": str(model_root),
        "horizons": horizons_info,
    }


@router.get("/status")
async def get_model_status():
    """Show which regression models exist per horizon and their paths."""
    return _collect_model_status()


# ==========================================================
# Train (with optional Optuna)
# ==========================================================

@router.post("/train")
async def manual_train_models(
    use_optuna: bool = Query(
        True,
        description="Use Optuna hyperparameter tuning if available.",
    ),
    n_trials: int = Query(
        20,
        ge=0,
        le=200,
        description="Number of Optuna trials when use_optuna=true (0 = no tuning).",
    ),
):
    """
    Manually trigger regression training for all horizons.

    Calls ai_model.train_all_models(...) which:
        • Trains regression models per horizon
        • Optionally uses Optuna per horizon
    """
    log(
        f"[model_router] 🚀 Manual model training requested "
        f"(optuna={use_optuna}, trials={n_trials})"
    )
    summary = train_all_models(
        dataset_name="training_data_daily.parquet",
        use_optuna=use_optuna,
        n_trials=n_trials,
    )
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "summary": summary,
    }


# ==========================================================
# Tune (tune+train)
# ==========================================================

@router.post("/tune")
async def tune_models(
    n_trials: int = Query(
        30,
        ge=5,
        le=300,
        description="Number of Optuna trials per horizon.",
    ),
):
    """
    Run Optuna-guided training for all horizons.

    In this implementation, tuning and final training happen together,
    so this is effectively a high-quality training run focused on hyperparams.
    """
    use_optuna = True
    log(f"[model_router] 🔍 Hyperparameter tuning run (trials={n_trials})...")
    summary = train_all_models(
        dataset_name="training_data_daily.parquet",
        use_optuna=use_optuna,
        n_trials=n_trials,
    )
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "summary": summary,
    }


# ==========================================================
# Predict
# ==========================================================

@router.get("/predict")
async def predict_symbols(
    symbols: Optional[str] = Query(
        None,
        description="Comma-separated symbols (e.g. AAPL,MSFT). If omitted, use entire rolling.",
    ),
    full: bool = Query(
        False,
        description="If true, include any available component breakdown "
                    "(ignored for pure regression models).",
    ),
):
    """
    Generate predictions using the regression model engine.

    Returns per-symbol, per-horizon:
        predicted_return, confidence, etc. (schema defined by ai_model.predict_all)
    """
    rolling = _read_rolling() or {}

    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",")]
        rolling = {s: rolling.get(s, {}) for s in requested if s in rolling}

    if not rolling:
        raise HTTPException(status_code=404, detail="No symbols found in rolling.")

    preds = predict_all(rolling)
    if not preds:
        raise HTTPException(status_code=500, detail="Prediction failed or no models loaded.")

    # For compatibility: strip "components" if present and full==False.
    # (Regression engine may not include this key; pop(..., None) is safe.)
    if not full:
        for sym in preds:
            sym_block = preds[sym] or {}
            for h in sym_block:
                try:
                    sym_block[h].pop("components", None)
                except Exception:
                    continue

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "count": len(preds),
        "symbols": list(preds.keys()),
        "predictions": preds,
    }
