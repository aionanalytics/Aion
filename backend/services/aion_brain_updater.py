# backend/services/aion_brain_updater.py — v1.3 (Import-Safe, Contract-Resilient)
"""
AION Brain Updater — AION Analytics (Behavioral Memory)

Purpose:
    Convert execution truth (system_perf.json) into gradual adjustments
    to AION brain meta knobs consumed by policy_engine.

Design principles:
    - Import-safe (never crashes at import time)
    - No hard dependency on PATHS["aion_brain"]
    - Slow EMA updates (non-reactive)
    - Fully bounded + reversible
"""

from __future__ import annotations

import json
import gzip
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import safe_float, log

# ==========================================================
# Paths (resolved lazily — NEVER at import time)
# ==========================================================

ML_DATA = Path(PATHS["ml_data"])
PERF_FILE = ML_DATA / "performance" / "system_perf.json"

def _resolve_aion_brain_path() -> Path:
    """
    Resolve AION brain path safely.

    Priority:
      1) PATHS["aion_brain"] if defined
      2) <ml_data>/da_brains/core/aion_brain.json.gz (canonical fallback)

    This function MUST NOT raise.
    """
    p = PATHS.get("aion_brain")
    if p:
        try:
            return Path(p)
        except Exception:
            pass

    return ML_DATA / "da_brains" / "core" / "aion_brain.json.gz"


# ==========================================================
# Constants
# ==========================================================

CONF_MIN, CONF_MAX = 0.70, 1.30
RISK_MIN, RISK_MAX = 0.60, 1.40
AGGR_MIN, AGGR_MAX = 0.60, 1.50

ALPHA = 0.05  # slow EMA

__all__ = ["update_aion_brain", "update_aion_brain_from_performance"]


# ==========================================================
# Helpers
# ==========================================================

def _clamp(x: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, x)))


def _read_gz_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _write_gz_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    tmp.replace(path)


def _load_perf() -> Dict[str, Any]:
    if not PERF_FILE.exists():
        return {}
    try:
        obj = json.loads(PERF_FILE.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _default_brain() -> Dict[str, Any]:
    return {
        "_meta": {
            "confidence_bias": 1.0,
            "risk_bias": 1.0,
            "aggressiveness": 1.0,
            "regime_mods": {},
            "updated_at": None,
        }
    }


def _ema(old: float, target: float) -> float:
    return (1.0 - ALPHA) * old + ALPHA * target


# ==========================================================
# Main updater
# ==========================================================

def update_aion_brain() -> Dict[str, Any]:
    log("[aion_brain_updater] 🧠 Updating AION brain from performance…")

    perf = _load_perf()
    metrics = perf.get("metrics", {}) if isinstance(perf, dict) else {}

    closed = int(metrics.get("closed_trades", 0) or 0)
    win_rate = safe_float(metrics.get("win_rate", 0.5))
    drawdown = safe_float(metrics.get("drawdown_14d", 0.0))
    pnl_sum = safe_float(metrics.get("pnl_sum", 0.0))

    brain_path = _resolve_aion_brain_path()
    brain = _read_gz_json(brain_path) or _default_brain()
    meta = brain.get("_meta", {}) if isinstance(brain, dict) else {}

    cb = safe_float(meta.get("confidence_bias", 1.0))
    rb = safe_float(meta.get("risk_bias", 1.0))
    ag = safe_float(meta.get("aggressiveness", 1.0))

    target_cb = target_rb = target_ag = 1.0

    # Conservative behavior tuning rules
    if drawdown <= -0.10:
        target_cb, target_rb, target_ag = 0.90, 0.80, 0.75
    elif drawdown <= -0.05:
        target_cb, target_rb, target_ag = 0.95, 0.90, 0.85
    elif closed >= 10 and win_rate >= 0.55 and pnl_sum > 0:
        target_cb, target_ag = 1.05, 1.08
        if closed >= 15 and win_rate >= 0.58:
            target_cb, target_ag, target_rb = 1.08, 1.12, 1.05

    meta["confidence_bias"] = round(_clamp(_ema(cb, target_cb), CONF_MIN, CONF_MAX), 4)
    meta["risk_bias"] = round(_clamp(_ema(rb, target_rb), RISK_MIN, RISK_MAX), 4)
    meta["aggressiveness"] = round(_clamp(_ema(ag, target_ag), AGGR_MIN, AGGR_MAX), 4)
    meta["updated_at"] = datetime.now(TIMEZONE).isoformat()

    brain["_meta"] = meta
    _write_gz_json(brain_path, brain)

    log(f"[aion_brain_updater] ✅ Brain updated → {brain_path}")
    return {"status": "ok", "meta": meta, "inputs": metrics}


# Backward compatibility
def update_aion_brain_from_performance() -> Dict[str, Any]:
    return update_aion_brain()


if __name__ == "__main__":
    print(json.dumps(update_aion_brain(), indent=2))
