# backend/supervisor_agent.py — v1.0 (rules; LLM-ready)
from __future__ import annotations
from typing import Dict, Any
import json, os
from .config import PATHS
from .data_pipeline import log

OVERRIDES_PATH = PATHS["ml_data"] / "supervisor_overrides.json"


def _save(js: dict):
    try:
        OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump(js, f, indent=2)
    except Exception:
        pass


def step(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    metrics sample: {"hit_ratio": 0.54, "drawdown_7d": -0.032,
                     "regime": "neutral", "regime_conf": 0.62}
    Writes overrides for policy thresholds (non-destructive).
    """
    overrides = {}
    dd = float(metrics.get("drawdown_7d", 0.0) or 0.0)
    regime = (metrics.get("regime") or "neutral").lower()
    rconf  = float(metrics.get("regime_conf", 0.0) or 0.0)

    if dd <= -0.05:
        overrides = {"kill_switch": True, "conf_min": 0.6, "exposure_cap": 0.5}
    elif regime == "panic" and rconf > 0.7:
        overrides = {"kill_switch": False, "conf_min": 0.6, "exposure_cap": 0.6}
    else:
        overrides = {"kill_switch": False, "conf_min": 0.52, "exposure_cap": 1.2}

    _save(overrides)
    log(f"[supervisor_agent] ✅ overrides updated → {overrides}")
    return overrides
