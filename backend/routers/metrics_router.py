# backend/routers/metrics_router.py
"""
Metrics Router (EXTENDED v2.0)

Existing endpoints preserved:
    • /accuracy
    • /top-performers
    • /refresh-top-performers

New diagnostics added:
    • /confidence
    • /drift
    • /sector-drift
    • /overview

This merges AION's performance, calibration, drift, and
top-performer metrics into one unified metrics router.

SAFE: No breaking changes.
"""

from __future__ import annotations

from typing import Dict, Any, List

from fastapi import APIRouter, HTTPException

from pathlib import Path
import json
from datetime import datetime

from backend.services import top_performers_engine as tpe
from backend.services import accuracy_engine as ae

from backend.core.data_pipeline import (
    log,
    safe_float,
    _read_rolling,
    _read_brain,
)
from backend.core.config import PATHS, TIMEZONE

# ====================================================================
# Router
# ====================================================================

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# ====================================================================
# Existing endpoints (unchanged)
# ====================================================================

def _dummy_live_price_lookup(symbols: List[str]) -> Dict[str, float]:
    log(f"[metrics] ⚠️ dummy live price lookup used for {len(symbols)} symbols.")
    return {}


@router.get("/accuracy", summary="Get Aion Analytics global accuracy badge")
def get_accuracy() -> Dict[str, Any]:
    try:
        return ae.compute_global_accuracy()
    except Exception as e:
        log(f"[metrics] ⚠️ compute_global_accuracy failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute accuracy")


@router.get("/top-performers", summary="Get top performers for 1w and 4w horizons")
def get_top_performers() -> Dict[str, Any]:
    try:
        return tpe.get_frozen_top3()
    except Exception as e:
        log(f"[metrics] ⚠️ get_top_performers failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute top performers")


@router.post("/refresh-top-performers", summary="Refresh rolling Top-50 + daily top-3")
def refresh_top_performers() -> Dict[str, Any]:
    try:
        tpe.append_today_top50_to_rolling()
        tpe.freeze_daily_top3(price_lookup=_dummy_live_price_lookup)
        return {"status": "ok"}
    except Exception as e:
        log(f"[metrics] ⚠️ refresh_top_performers failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh top performers")

# ====================================================================
# Added diagnostics (new)
# ====================================================================

# ---- Paths ----

ML_DATA_ROOT: Path = PATHS.get("ml_data", Path("ml_data"))
METRICS_ROOT: Path = ML_DATA_ROOT / "metrics"
DRIFT_DIR: Path = METRICS_ROOT / "drift"

CONF_CAL_FILE: Path = METRICS_ROOT / "confidence_calibration.json"
DRIFT_REPORT_FILE: Path = DRIFT_DIR / "drift_report.json"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"[metrics] ⚠️ Failed reading {path}: {e}")
        return {}

def _classify_drift(avg: float, n: int) -> str:
    if n < 10:
        return "insufficient_data"
    if abs(avg) < 0.05:
        return "stable"
    if avg < -0.15:
        return "severe_negative"
    if avg < 0:
        return "slightly_negative"
    if avg > 0.25:
        return "overfitting_or_regime_shift"
    return "positive"


# ====================================================================
# NEW: Confidence Calibration
# ====================================================================

@router.get("/confidence", summary="Confidence calibration curves (per horizon)")
def get_confidence() -> Dict[str, Any]:
    raw = _read_json(CONF_CAL_FILE)
    horizons = raw.get("horizons", {}) or {}

    out = {}
    for h, info in horizons.items():
        centers = info.get("centers", [])
        acc = info.get("acc", [])
        out[h] = {
            "centers": centers,
            "acc": acc,
            "points": [
                {"center": float(c), "accuracy": float(a)}
                for c, a in zip(centers, acc)
            ],
        }

    return {
        "generated_at": raw.get("generated_at"),
        "horizons": out,
        "file_exists": bool(raw),
    }


# ====================================================================
# NEW: Drift Report
# ====================================================================

@router.get("/drift", summary="Horizon-level drift metrics")
def get_drift() -> Dict[str, Any]:
    raw = _read_json(DRIFT_REPORT_FILE)
    horizons = raw.get("drift_by_horizon", {}) or {}

    out = {}
    for h, info in horizons.items():
        avg = safe_float(info.get("avg_drift", 0.0))
        n = int(info.get("n", 0))
        retrain = bool(info.get("retrain_recommended", False))

        out[h] = {
            "avg_drift": avg,
            "n": n,
            "retrain_recommended": retrain,
            "state": _classify_drift(avg, n),
        }

    return {
        "generated_at": raw.get("generated_at"),
        "horizons": out,
        "file_exists": bool(raw),
    }


# ====================================================================
# NEW: Sector-Level Drift
# ====================================================================

@router.get("/sector-drift", summary="Sector-level drift + hit ratios")
def get_sector_drift() -> Dict[str, Any]:
    brain = _read_brain() or {}
    rolling = _read_rolling() or {}

    sectors = {}

    for sym, bnode in brain.items():
        if sym.startswith("_"):
            continue

        rnode = rolling.get(sym, {}) or {}
        sector = rnode.get("sector") or "Unknown"

        perf = bnode.get("performance", {}) or {}
        short = perf.get("short_stats", {}) or {}
        longw = perf.get("long_stats", {}) or {}

        drift = safe_float(perf.get("drift_score", 0.0))
        hit_s = safe_float(short.get("hit_ratio", 0.0))
        hit_l = safe_float(longw.get("hit_ratio", 0.0))

        bucket = sectors.setdefault(
            sector,
            {"sum_drift": 0.0, "sum_hit_s": 0.0, "sum_hit_l": 0.0, "n": 0},
        )
        bucket["sum_drift"] += drift
        bucket["sum_hit_s"] += hit_s
        bucket["sum_hit_l"] += hit_l
        bucket["n"] += 1

    out = {}
    for sec, agg in sectors.items():
        n = agg["n"] or 1
        out[sec] = {
            "avg_drift": agg["sum_drift"] / n,
            "avg_hit_short": agg["sum_hit_s"] / n,
            "avg_hit_long": agg["sum_hit_l"] / n,
            "symbols": n,
        }

    return {
        "generated_at": datetime.now(TIMEZONE).isoformat(),
        "sectors": out,
        "brain_found": bool(brain),
        "rolling_found": bool(rolling),
    }


# ====================================================================
# NEW: Overview Dashboard Header
# ====================================================================

@router.get("/overview", summary="Unified metrics overview for dashboard header")
def get_overview() -> Dict[str, Any]:
    # Confidence
    cal = _read_json(CONF_CAL_FILE)
    cal_ts = cal.get("generated_at")

    # Drift
    drift = _read_json(DRIFT_REPORT_FILE)
    drift_ts = drift.get("generated_at")
    horizons = drift.get("drift_by_horizon", {}) or {}

    worst_h = None
    worst_val = 0.0
    for h, info in horizons.items():
        avg = safe_float(info.get("avg_drift", 0.0))
        if abs(avg) > abs(worst_val):
            worst_h = h
            worst_val = avg

    # Sector quick count
    brain = _read_brain() or {}
    rolling = _read_rolling() or {}
    sectors = set()

    for sym in brain.keys():
        if sym.startswith("_"):
            continue
        sec = rolling.get(sym, {}).get("sector")
        if sec:
            sectors.add(sec)

    return {
        "now": datetime.now(TIMEZONE).isoformat(),
        "confidence": {
            "generated_at": cal_ts,
            "file_exists": bool(cal),
        },
        "drift": {
            "generated_at": drift_ts,
            "file_exists": bool(drift),
            "worst_horizon": worst_h,
            "worst_drift": worst_val,
        },
        "sector_coverage": {
            "n_sectors": len(sectors),
            "sectors": sorted(sectors),
            "n_symbols_with_brain": len([s for s in brain.keys() if not s.startswith("_")]),
        },
    }
