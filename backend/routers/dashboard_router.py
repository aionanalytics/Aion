# backend/routers/dashboard_router.py
"""
Dashboard API Router — AION Analytics

Purpose:
    Provide UI-facing endpoints expected by the dashboard:
      • GET /dashboard/metrics
      • GET /dashboard/top/{horizon}

This router is a thin adapter layer over existing backend artifacts.
It does NOT recompute anything — it only reads what nightly + intraday
already produce.

Safe behaviors:
    - Missing files return graceful defaults
    - Always returns ranked arrays (never dicts)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, List

from fastapi import APIRouter, HTTPException

from backend.core.config import PATHS
from backend.core.data_pipeline import log, safe_float

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

ACCURACY_DIR = Path(PATHS.get("accuracy", Path("ml_data") / "metrics" / "accuracy"))
ACCURACY_LATEST = ACCURACY_DIR / "accuracy_latest.json"

INSIGHTS_DIR = Path(PATHS.get("insights", Path("ml_data") / "insights"))
PRED_LATEST = INSIGHTS_DIR / "predictions_latest.json"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception as e:
        log(f"[dashboard_router] ⚠️ Failed reading {path}: {e}")
        return {}


def _extract_accuracy_30d(latest: Dict[str, Any]) -> float:
    """
    UI expects a single accuracy_30d number.
    We compute a weighted mean across horizons where available.
    """
    horizons = latest.get("horizons")
    if not isinstance(horizons, dict):
        return 0.0

    vals = []
    weights = []

    for h, blk in horizons.items():
        windows = blk.get("windows") if isinstance(blk, dict) else None
        w30 = windows.get("30") if isinstance(windows, dict) else None
        if not isinstance(w30, dict) or w30.get("status") != "ok":
            continue

        acc = safe_float(w30.get("directional_accuracy", 0.0))
        n = int(w30.get("n", 0) or 0)

        if acc > 0 and n > 0:
            vals.append(acc * n)
            weights.append(n)

    if not weights:
        return 0.0

    return sum(vals) / sum(weights)


def _load_ranked_predictions(horizon: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Returns ranked predictions for a given horizon.
    Output is ALWAYS a list sorted by score descending.
    """
    js = _read_json(PRED_LATEST)
    rows = js.get("predictions")

    if not isinstance(rows, list):
        return []

    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("horizon")) != horizon:
            continue
        out.append(r)

    out.sort(key=lambda x: safe_float(x.get("score", 0.0)), reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------

@router.get("/metrics")
def dashboard_metrics() -> Dict[str, Any]:
    """
    Dashboard metrics summary.

    Response:
      {
        "accuracy_30d": 0.57,
        "updated_at": "2025-12-15T03:12:44Z"
      }
    """
    latest = _read_json(ACCURACY_LATEST)

    acc_30d = _extract_accuracy_30d(latest)
    updated = latest.get("updated_at")

    return {
        "accuracy_30d": round(float(acc_30d), 4),
        "updated_at": updated,
    }


@router.get("/top/{horizon}")
def dashboard_top_predictions(horizon: str, limit: int = 50) -> Dict[str, Any]:
    """
    Top-ranked predictions for a horizon.

    Horizon examples:
        1d, 3d, 1w, 2w, 4w, 13w, 26w, 52w
    """
    rows = _load_ranked_predictions(horizon, limit=limit)

    return {
        "horizon": horizon,
        "count": len(rows),
        "results": rows,
    }
