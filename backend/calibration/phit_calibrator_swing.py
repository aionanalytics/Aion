"""backend.calibration.phit_calibrator_swing

Phase 7 (Swing): turn a base confidence + expected return into a P(hit) in [0,1].

Enhanced with outcome-based learning:
- Learns actual win rates from historical outcomes
- Bins trades by confidence level, expected return, and regime
- Updates P(hit) estimates weekly
- Falls back to formula-based calibration if insufficient data

Binning Strategy:
- Confidence buckets: 0.50-0.60, 0.60-0.70, 0.70-0.80, 0.80-1.00
- Expected return buckets: <0%, 0-3%, 3-6%, 6-10%, >10%
- Market regimes: bull, bear, chop, stress

Env knobs
---------
SWING_PHIT_A (default 2.2): strength of the base-confidence term
SWING_PHIT_B (default 6.0): strength of the expected-return term
SWING_PHIT_MIN (default 0.05): clamp floor
SWING_PHIT_MAX (default 0.97): clamp ceiling
SWING_PHIT_CALIBRATION_ENABLED (default: true): Use outcome-based calibration
SWING_PHIT_MIN_SAMPLES (default: 10): Minimum trades per bucket for calibration

Expected return units
---------------------
expected_return is assumed to be a fraction (0.03 = +3%).

"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.getenv(name, "") or "").strip()
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


def _sigmoid(x: float) -> float:
    # Numerically stable sigmoid
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.getenv(name, "") or "").strip()
        return int(float(raw)) if raw else int(default)
    except Exception:
        return int(default)


def _calibration_table_path() -> Path:
    """Return path to calibration table."""
    try:
        from config import PATHS  # type: ignore
        da = PATHS.get("da_brains")
        if da:
            return Path(da) / "swing" / "phit_calibration.json"
    except Exception:
        pass
    return Path("da_brains") / "swing" / "phit_calibration.json"


def _get_conf_bucket(confidence: float) -> str:
    """Map confidence to bucket."""
    if confidence < 0.60:
        return "0.50-0.60"
    elif confidence < 0.70:
        return "0.60-0.70"
    elif confidence < 0.80:
        return "0.70-0.80"
    else:
        return "0.80-1.00"


def _get_er_bucket(expected_return: float) -> str:
    """Map expected return to bucket."""
    if expected_return < 0:
        return "<0%"
    elif expected_return < 0.03:
        return "0-3%"
    elif expected_return < 0.06:
        return "3-6%"
    elif expected_return < 0.10:
        return "6-10%"
    else:
        return ">10%"


def load_calibration_table() -> Dict[str, Any]:
    """Load calibration table from disk."""
    try:
        path = _calibration_table_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def update_calibration_from_outcomes(
    bot_key: str,
    outcomes: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Update P(hit) calibration table from trade outcomes.
    
    Args:
        bot_key: Bot identifier
        outcomes: List of trade outcomes
    
    Returns:
        Updated calibration table
    """
    # Load existing table
    table = load_calibration_table()
    
    if bot_key not in table:
        table[bot_key] = {}
    
    # Group outcomes by regime, conf bucket, er bucket
    buckets = {}
    
    for outcome in outcomes:
        regime = outcome.get("regime_entry", "unknown")
        confidence = outcome.get("entry_confidence", 0.0)
        expected_return = outcome.get("expected_return", 0.0)
        actual_return = outcome.get("actual_return", 0.0)
        
        conf_bucket = _get_conf_bucket(confidence)
        er_bucket = _get_er_bucket(expected_return)
        
        key = f"{regime}_{conf_bucket}_{er_bucket}"
        
        if key not in buckets:
            buckets[key] = {"wins": 0, "total": 0, "returns": []}
        
        buckets[key]["total"] += 1
        buckets[key]["returns"].append(actual_return)
        if actual_return > 0:
            buckets[key]["wins"] += 1
    
    # Calculate win rates for each bucket
    min_samples = _env_int("SWING_PHIT_MIN_SAMPLES", 10)
    
    for key, data in buckets.items():
        if data["total"] >= min_samples:
            win_rate = data["wins"] / data["total"]
            table[bot_key][key] = {
                "phit": win_rate,
                "samples": data["total"],
                "avg_return": sum(data["returns"]) / len(data["returns"])
            }
    
    # Save updated table
    try:
        path = _calibration_table_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(table, f, indent=2)
    except Exception:
        pass
    
    return table


def get_calibrated_phit(
    *,
    bot_key: str,
    base_conf: float,
    expected_return: float,
    regime_label: Optional[str] = None
) -> Tuple[float, bool]:
    """
    Get calibrated P(hit) from outcomes or formula.
    
    Args:
        bot_key: Bot identifier for calibration lookup
        base_conf: Model/policy confidence (0..1)
        expected_return: Expected return (fraction)
        regime_label: Market regime
    
    Returns:
        Tuple of (phit, is_calibrated)
        - phit: Probability of hit
        - is_calibrated: True if from calibration table, False if from formula
    """
    calibration_enabled = _env_bool("SWING_PHIT_CALIBRATION_ENABLED", True)
    
    if calibration_enabled and regime_label:
        # Try to get calibrated value
        table = load_calibration_table()
        
        if bot_key in table:
            conf_bucket = _get_conf_bucket(base_conf)
            er_bucket = _get_er_bucket(expected_return)
            key = f"{regime_label}_{conf_bucket}_{er_bucket}"
            
            if key in table[bot_key]:
                calibrated = table[bot_key][key]
                phit = calibrated["phit"]
                
                # Clamp to bounds
                pmin = _env_float("SWING_PHIT_MIN", 0.05)
                pmax = _env_float("SWING_PHIT_MAX", 0.97)
                phit = max(min(phit, pmax), pmin)
                
                return (phit, True)
    
    # Fall back to formula
    phit = get_phit_formula(
        base_conf=base_conf,
        expected_return=expected_return,
        regime_label=regime_label
    )
    return (phit, False)


def get_phit_formula(
    *,
    base_conf: float,
    expected_return: float,
    regime_label: Optional[str] = None
) -> float:
    """
    Calculate P(hit) using formula (original implementation).
    
    Args:
        base_conf: Model/policy confidence (0..1)
        expected_return: Expected return (fraction; 0.03=3%)
        regime_label: Optional regime (unused)
    
    Returns:
        P(hit) in [0, 1]
    """
    # Knobs
    a = _env_float("SWING_PHIT_A", 2.2)
    b = _env_float("SWING_PHIT_B", 6.0)
    pmin = _env_float("SWING_PHIT_MIN", 0.05)
    pmax = _env_float("SWING_PHIT_MAX", 0.97)

    c = max(0.0, min(1.0, float(base_conf)))
    er = float(expected_return)

    # Center confidence around 0.5 (coin-flip) and scale.
    conf_term = (c - 0.5)

    # Expected-return term: small signal for small returns, stronger for larger.
    # Scale expected_return (fractions) into a comparable range.
    er_term = max(-0.25, min(0.25, er))  # clamp to avoid insane tails

    # Simple logit
    logit = a * conf_term + b * er_term

    p = _sigmoid(logit)
    p = max(min(float(p), float(pmax)), float(pmin))
    return p


def get_phit(*, base_conf: float, expected_return: float, regime_label: Optional[str] = None) -> float:
    """Return P(hit) in [0,1].

    Args:
        base_conf: model/policy confidence (0..1)
        expected_return: expected return (fraction; 0.03=3%)
        regime_label: optional; market regime for calibration

    Returns:
        P(hit) probability
    """
    # Use formula-based approach (maintains backward compatibility)
    return get_phit_formula(
        base_conf=base_conf,
        expected_return=expected_return,
        regime_label=regime_label
    )
