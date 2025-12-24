# backend/core/regime_detector.py — v1.2.2 (Deterministic macro loader, single normalization)
"""
Regime Detector — AION Analytics

FINALIZED (v1.2.2):
  ✅ Deterministic macro preference order
  ✅ Single normalization point (no duplicate guards)
  ✅ No pandas dependency
  ✅ Handles market_state.json macro wrapper
  ✅ Normalizes macro fields into classifier-ready shape
  ✅ AION brain meta passthrough for traceability
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import safe_float, log, _read_aion_brain

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ML_DATA_ROOT: Path = PATHS.get("ml_data", Path("ml_data"))
GLBL_PATH: Path = ML_DATA_ROOT / "market_state.json"
MACRO_STATE_PATH: Path = PATHS.get("macro_state", ML_DATA_ROOT / "macro_state.json")
MACRO_DIR: Path = PATHS.get("macro", PATHS.get("root", Path(".")) / "macro")

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _load_json(path: Optional[Path]) -> Dict[str, Any]:
    try:
        if not path or not path.exists():
            return {}
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _latest_with_prefix(folder: Path, prefix: str, suffix: str = ".json") -> Optional[Path]:
    try:
        if not folder.exists():
            return None
        files = [p for p in folder.glob(f"{prefix}*{suffix}") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    except Exception:
        return None

def _norm_risk_off(x: Any) -> float:
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    return safe_float(x)

def _parse_ts(ts: Any) -> Optional[datetime]:
    try:
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        if isinstance(ts, str):
            s = ts.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return None

def _extract_macro_block(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    if "macro" in raw and isinstance(raw.get("macro"), dict):
        m = dict(raw["macro"])
        for k in ("generated_at", "updated_at", "ts"):
            if k not in m and k in raw:
                m[k] = raw.get(k)
        return m
    return raw

def _normalize_macro(m: Dict[str, Any]) -> Dict[str, Any]:
    vix = safe_float(m.get("vix", m.get("vix_close", m.get("vix_level", 0.0))))
    spy = safe_float(m.get("spy_pct", m.get("spy_daily_pct", m.get("spy_change", 0.0))))
    breadth = safe_float(m.get("breadth", m.get("breadth_proxy", m.get("advance_decline", 0.0))))
    vol = safe_float(m.get("volatility", vix / 100.0 if vix else 0.0))
    risk_off = _norm_risk_off(m.get("risk_off", 0.0))

    ts = m.get("generated_at") or m.get("updated_at") or m.get("ts")
    ts_dt = _parse_ts(ts)

    out = dict(m)
    out.update({
        "vix": float(vix),
        "spy_pct": float(spy),
        "breadth": float(breadth),
        "volatility": float(vol),
        "risk_off": float(risk_off),
        "regime_hint": (m.get("regime_hint") or "neutral").lower(),
    })
    if ts_dt:
        out["generated_at"] = ts_dt.isoformat()
    return out

def _looks_sane(m: Dict[str, Any]) -> bool:
    return (
        abs(safe_float(m.get("vix", 0.0))) > 0.01 or
        abs(safe_float(m.get("spy_pct", 0.0))) > 0.0001 or
        abs(safe_float(m.get("breadth", 0.0))) > 0.0001
    )

def _recent_enough(m: Dict[str, Any], days: float = 3.0) -> bool:
    ts = _parse_ts(m.get("generated_at"))
    if not ts:
        return True
    age = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    return age <= days

# ---------------------------------------------------------------------------
# Macro loader (deterministic)
# ---------------------------------------------------------------------------

def _load_macro_state() -> Dict[str, Any]:
    raw: Dict[str, Any] = {}

    try:
        if GLBL_PATH.exists():
            ms = _load_json(GLBL_PATH)
            mm = _extract_macro_block(ms)
            if isinstance(mm, dict):
                mm = _normalize_macro(mm)
                if _looks_sane(mm) and _recent_enough(mm):
                    raw = mm
    except Exception:
        pass

    if not raw:
        try:
            if MACRO_STATE_PATH.exists():
                mm = _normalize_macro(_extract_macro_block(_load_json(MACRO_STATE_PATH)))
                if _looks_sane(mm):
                    raw = mm
        except Exception:
            pass

    if not raw:
        try:
            latest = _latest_with_prefix(MACRO_DIR, "macro_state")
            if latest:
                mm = _normalize_macro(_extract_macro_block(_load_json(latest)))
                if _looks_sane(mm):
                    raw = mm
        except Exception:
            pass

    return raw if isinstance(raw, dict) else {}

# ---------------------------------------------------------------------------
# AION brain meta
# ---------------------------------------------------------------------------

def _aion_meta_snapshot() -> Dict[str, Any]:
    try:
        brain = _read_aion_brain() or {}
        meta = brain.get("_meta", {}) if isinstance(brain, dict) else {}
        return {
            "updated_at": meta.get("updated_at"),
            "confidence_bias": safe_float(meta.get("confidence_bias", 1.0)) or 1.0,
            "risk_bias": safe_float(meta.get("risk_bias", 1.0)) or 1.0,
            "aggressiveness": safe_float(meta.get("aggressiveness", 1.0)) or 1.0,
        }
    except Exception:
        return {
            "updated_at": None,
            "confidence_bias": 1.0,
            "risk_bias": 1.0,
            "aggressiveness": 1.0,
        }

# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def _classify_regime(m: Dict[str, Any]) -> Dict[str, Any]:
    vix = safe_float(m.get("vix", 0.0))
    spy = safe_float(m.get("spy_pct", 0.0))
    breadth = safe_float(m.get("breadth", 0.0))
    vol = safe_float(m.get("volatility", vix / 100.0))
    risk = safe_float(m.get("risk_off", 0.0))
    hint = (m.get("regime_hint") or "neutral").lower()

    label, conf = "chop", 0.45

    if spy <= -0.025 and breadth <= -0.25 and (vix >= 30 or vol >= 0.04 or risk >= 0.65):
        label, conf = "panic", 0.8
    elif spy <= -0.012 and breadth <= -0.10 and (vix >= 22 or vol >= 0.03 or risk >= 0.50):
        label, conf = "bear", 0.65
    elif spy >= 0.012 and breadth >= 0.10 and vix <= 22:
        label, conf = "bull", 0.7

    if hint in ("bull", "bear", "panic", "chop"):
        conf = min(1.0, conf + 0.1) if hint == label else max(0.3, conf - 0.05)

    conf = max(0.25, min(0.95, conf))

    return {
        "label": label,
        "confidence": conf,
        "vix": vix,
        "spy_pct": spy,
        "breadth": breadth,
        "risk_off": risk,
        "volatility": vol,
        "timestamp": m.get("generated_at") or datetime.now(TIMEZONE).isoformat(),
    }

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_regime(_: Dict[str, Any] | None = None) -> Dict[str, Any]:
    macro = _load_macro_state()
    if not macro:
        log("[regime_detector] ⚠️ No macro found — defaulting to chop.")
        return {
            "label": "chop",
            "confidence": 0.3,
            "vix": 0.0,
            "spy_pct": 0.0,
            "breadth": 0.0,
            "risk_off": 0.0,
            "volatility": 0.0,
            "timestamp": datetime.now(TIMEZONE).isoformat(),
            "aion_brain": _aion_meta_snapshot(),
        }

    regime = _classify_regime(macro)
    regime["aion_brain"] = _aion_meta_snapshot()

    log(
        f"[regime_detector] 📊 {regime['label']} "
        f"(conf={regime['confidence']:.2f}, spy={regime['spy_pct']:.3f}, "
        f"vix={regime['vix']:.1f}, breadth={regime['breadth']:.3f})"
    )
    return regime

if __name__ == "__main__":
    print(json.dumps(detect_regime(None), indent=2))
