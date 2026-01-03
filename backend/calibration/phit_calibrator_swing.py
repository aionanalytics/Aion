"""backend.calibration.phit_calibrator_swing

Phase 7 (Swing): turn a base confidence + expected return into a P(hit) in [0,1].

This is a lightweight placeholder calibrator.

Rationale
---------
You want the semantics and wiring in place now:
- policy emits a base confidence (0..1)
- the bot can convert that into a probability-of-hit (0..1)
- sizing can be driven by P(hit) and expected return

A *real* calibrator should be fit from historical outcomes (hit/miss), probably
separately per horizon and regime. This module keeps the interface stable so the
upgrade later is a drop-in.

Env knobs
---------
SWING_PHIT_A (default 2.2): strength of the base-confidence term
SWING_PHIT_B (default 6.0): strength of the expected-return term
SWING_PHIT_MIN (default 0.05): clamp floor
SWING_PHIT_MAX (default 0.97): clamp ceiling

Expected return units
---------------------
expected_return is assumed to be a fraction (0.03 = +3%).

"""

from __future__ import annotations

import math
import os
from typing import Optional


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


def get_phit(*, base_conf: float, expected_return: float, regime_label: Optional[str] = None) -> float:
    """Return P(hit) in [0,1].

    Args:
        base_conf: model/policy confidence (0..1)
        expected_return: expected return (fraction; 0.03=3%)
        regime_label: optional; currently unused (kept for forward compatibility)
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
