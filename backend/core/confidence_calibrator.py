# backend/core/confidence_calibrator.py — v1.2
"""
Confidence Calibrator — v1.2

Purpose:
  Turn "raw confidence" (signal-strength derived) into "earned confidence"
  using realized performance + calibration buckets computed by accuracy_engine.

Reads:
  - metrics/accuracy/accuracy_latest.json
  - metrics/accuracy/confidence_calibration.json

Outputs:
  - Helper functions used by ai_model.predict_all()

Design:
  - Safe fallbacks when files are missing or sparse
  - Bucket-based calibration (piecewise, horizon-aware)
  - Recent accuracy influences but does NOT dominate
  - Soft caps prevent confidence hallucination
  - Backward compatible with older calibration formats
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.core.config import PATHS
from backend.core.data_pipeline import log, safe_float


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
ACCURACY_DIR: Path = PATHS.get("accuracy", Path("ml_data") / "metrics" / "accuracy")
CALIBRATION_PATH: Path = ACCURACY_DIR / "confidence_calibration.json"
ACCURACY_LATEST_PATH: Path = ACCURACY_DIR / "accuracy_latest.json"


# ---------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as e:
        log(f"[confidence_calibrator] ⚠️ Failed reading {path}: {e}")
        return None


def _parse_range_label(r: Any) -> Optional[Tuple[float, float]]:
    """
    Accepts:
      "0.70-0.75"
      "0.7 - 0.75"
    """
    try:
        s = str(r).replace(" ", "")
        lo_s, hi_s = s.split("-")
        return float(lo_s), float(hi_s)
    except Exception:
        return None


def _clip(x: float, lo: float, hi: float) -> float:
    try:
        return float(max(lo, min(hi, float(x))))
    except Exception:
        return float(lo)


def _get_calibration_horizon_block(
    calibration_map: Dict[str, Any],
    horizon: str,
) -> Optional[Dict[str, Any]]:
    """
    Supports BOTH formats:

    Old:
      {
        "1w": {"buckets": [...]}
      }

    New (accuracy_engine v1.1+):
      {
        "updated_at": "...",
        "window_days": 30,
        "horizons": {
          "1w": {"buckets": [...], "n": 123}
        }
      }
    """
    if not isinstance(calibration_map, dict):
        return None

    hz = calibration_map.get("horizons")
    if isinstance(hz, dict):
        blk = hz.get(horizon)
        return blk if isinstance(blk, dict) else None

    blk = calibration_map.get(horizon)
    return blk if isinstance(blk, dict) else None


# ---------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------
def load_calibration_map() -> Dict[str, Any]:
    """
    Returns parsed confidence_calibration.json (or {}).
    """
    obj = _read_json(CALIBRATION_PATH)
    return obj if isinstance(obj, dict) else {}


def load_accuracy_latest() -> Dict[str, Any]:
    """
    Returns accuracy_latest.json (or {}).
    """
    obj = _read_json(ACCURACY_LATEST_PATH)
    return obj if isinstance(obj, dict) else {}


# ---------------------------------------------------------------------
# Calibration logic
# ---------------------------------------------------------------------
def calibrated_confidence(
    conf_raw: float,
    horizon: str,
    calibration_map: Dict[str, Any],
    *,
    min_conf: float = 0.50,
    max_conf: float = 0.98,
) -> float:
    """
    Convert raw confidence → calibrated confidence using bucket hit-rates.

    Behavior:
      - If no calibration exists, return conf_raw
      - If bucket hit_rate is pathological (< min_conf), clamp
      - Buckets are horizon-specific
    """
    cr = _clip(safe_float(conf_raw), min_conf, max_conf)

    h_block = _get_calibration_horizon_block(calibration_map, horizon)
    if not isinstance(h_block, dict):
        return cr

    buckets = h_block.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        return cr

    for b in buckets:
        if not isinstance(b, dict):
            continue

        parsed = _parse_range_label(b.get("range"))
        if not parsed:
            continue

        lo, hi = parsed
        if lo <= cr < hi:
            hr = safe_float(b.get("hit_rate", cr))
            return _clip(hr, min_conf, max_conf)

    return cr


def recent_horizon_accuracy_conf(
    horizon: str,
    accuracy_latest: Dict[str, Any],
    *,
    window_days: int = 30,
    min_conf: float = 0.50,
    max_conf: float = 0.98,
) -> float:
    """
    Extract recent directional accuracy and treat it as
    a *weak confidence signal*, not ground truth.
    """
    if not isinstance(accuracy_latest, dict):
        return float(min_conf)

    hz = accuracy_latest.get("horizons")
    if not isinstance(hz, dict):
        return float(min_conf)

    h = hz.get(horizon)
    if not isinstance(h, dict):
        return float(min_conf)

    windows = h.get("windows")
    if not isinstance(windows, dict):
        return float(min_conf)

    w = windows.get(str(int(window_days)))
    if not isinstance(w, dict) or w.get("status") != "ok":
        return float(min_conf)

    da = safe_float(w.get("directional_accuracy", 0.0))
    if da <= 0.0:
        return float(min_conf)

    return _clip(da, min_conf, max_conf)


def combine_confidence(
    conf_raw: float,
    conf_cal: float,
    conf_perf: float,
    *,
    w_raw: float = 0.45,
    w_cal: float = 0.35,
    w_perf: float = 0.20,
    min_conf: float = 0.50,
    max_conf: float = 0.98,
) -> float:
    """
    Weighted blend.

    Philosophy:
      - Raw signal matters most early
      - Calibration matters as data accumulates
      - Performance nudges, it does not dominate
    """
    cr = _clip(safe_float(conf_raw), min_conf, max_conf)
    cc = _clip(safe_float(conf_cal), min_conf, max_conf)
    cp = _clip(safe_float(conf_perf), min_conf, max_conf)

    s = (w_raw * cr) + (w_cal * cc) + (w_perf * cp)
    return _clip(s, min_conf, max_conf)


def soft_performance_cap(
    conf: float,
    conf_perf: float,
    *,
    max_overhang: float = 0.12,
    min_conf: float = 0.50,
    max_conf: float = 0.98,
) -> float:
    """
    Prevent confidence hallucinations when recent accuracy is poor.

    If conf > (perf + overhang), pull it down smoothly.
    """
    c = _clip(safe_float(conf), min_conf, max_conf)
    p = _clip(safe_float(conf_perf), min_conf, max_conf)

    cap = _clip(p + float(max_overhang), min_conf, max_conf)
    if c <= cap:
        return c

    # Soft pull-down (keeps continuity)
    return _clip((0.65 * c) + (0.35 * cap), min_conf, max_conf)
