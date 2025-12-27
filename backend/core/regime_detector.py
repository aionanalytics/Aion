# backend/core/regime_detector.py — v1.2.3 (Macro path + debug + spy unit fix)
"""
Regime Detector — AION Analytics

v1.2.3:
  ✅ Uses PATHS["market_state"] / PATHS["macro_state"] if provided
  ✅ Fallback macro dir defaults to ml_data/macro (NOT root/macro guessing)
  ✅ Optional env overrides:
       AION_MARKET_STATE_PATH
       AION_MACRO_STATE_PATH
       AION_MACRO_DIR
       AION_REGIME_DEBUG=1  (verbose search logging)
  ✅ Fix SPY unit handling:
       prefers spy_pct_decimal (true decimal)
       otherwise converts percent → decimal when needed
  ✅ When macro isn't found, logs what paths were checked and why
"""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import safe_float, log, _read_aion_brain


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "y", "on")


DEBUG = _env_bool("AION_REGIME_DEBUG", False)


def _p_from_env(name: str) -> Optional[Path]:
    s = (os.getenv(name, "") or "").strip()
    if not s:
        return None
    try:
        return Path(s)
    except Exception:
        return None


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
    """
    Supports either:
      - {"macro": {...}, ...}
      - {...} (already macro)
    """
    if not isinstance(raw, dict):
        return {}
    if "macro" in raw and isinstance(raw.get("macro"), dict):
        m = dict(raw["macro"])
        # bubble timestamp fields if needed
        for k in ("generated_at", "updated_at", "ts"):
            if k not in m and k in raw:
                m[k] = raw.get(k)
        return m
    return raw


def _norm_risk_off(x: Any) -> float:
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    return float(safe_float(x))


def _spy_to_decimal(m: Dict[str, Any]) -> float:
    """
    Prefer spy_pct_decimal (already decimal).
    Else use spy_pct if present:
       - if abs(spy_pct) > 0.35 -> assume it's percent and divide by 100
       - else assume already decimal
    Else use spy_daily_pct (percent) / 100
    """
    spy_dec = safe_float(m.get("spy_pct_decimal", 0.0))
    if abs(spy_dec) > 0:
        return float(spy_dec)

    spy_pct = safe_float(m.get("spy_pct", 0.0))
    if abs(spy_pct) > 0:
        # Heuristic: decimals are usually < 0.20; percents are usually < 20
        if abs(spy_pct) > 0.35:
            return float(spy_pct / 100.0)
        return float(spy_pct)

    spy_daily_pct = safe_float(m.get("spy_daily_pct", 0.0))
    if abs(spy_daily_pct) > 0:
        return float(spy_daily_pct / 100.0)

    spy_change = safe_float(m.get("spy_change", 0.0))
    if abs(spy_change) > 0.35:
        return float(spy_change / 100.0)
    return float(spy_change)


def _normalize_macro(m: Dict[str, Any]) -> Dict[str, Any]:
    vix = safe_float(m.get("vix", m.get("vix_close", m.get("vix_level", 0.0))))
    spy = _spy_to_decimal(m)
    breadth = safe_float(m.get("breadth", m.get("breadth_proxy", m.get("advance_decline", 0.0))))
    vol = safe_float(m.get("volatility", (float(vix) / 100.0 if vix else 0.0)))
    risk_off = _norm_risk_off(m.get("risk_off", 0.0))

    ts = m.get("generated_at") or m.get("updated_at") or m.get("ts")
    ts_dt = _parse_ts(ts)

    out = dict(m)
    out.update({
        "vix": float(vix),
        "spy_pct": float(spy),           # DECIMAL (0.012 = +1.2%)
        "breadth": float(breadth),
        "volatility": float(vol),
        "risk_off": float(risk_off),
        "regime_hint": (m.get("regime_hint") or "neutral").lower(),
    })
    if ts_dt:
        out["generated_at"] = ts_dt.isoformat()
    return out


def _looks_sane(m: Dict[str, Any]) -> bool:
    # We want *some* movement or valid volatility
    vix = abs(safe_float(m.get("vix", 0.0)))
    spy = abs(safe_float(m.get("spy_pct", 0.0)))
    br = abs(safe_float(m.get("breadth", 0.0)))
    return (vix > 0.01) or (spy > 0.0001) or (br > 0.0001)


def _recent_enough(m: Dict[str, Any], days: float = 3.0) -> bool:
    ts = _parse_ts(m.get("generated_at"))
    if not ts:
        return True
    age = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    return age <= days


# ---------------------------------------------------------------------------
# Paths (with overrides)
# ---------------------------------------------------------------------------

ML_DATA_ROOT: Path = PATHS.get("ml_data", Path("ml_data"))

# Preferred explicit path keys if present
DEFAULT_MARKET_STATE: Path = PATHS.get("market_state", ML_DATA_ROOT / "market_state.json")
DEFAULT_MACRO_STATE: Path = PATHS.get("macro_state", ML_DATA_ROOT / "macro_state.json")

# Better default macro dir: ml_data/macro/
DEFAULT_MACRO_DIR: Path = PATHS.get("macro", ML_DATA_ROOT / "macro")

MARKET_STATE_PATH: Path = _p_from_env("AION_MARKET_STATE_PATH") or DEFAULT_MARKET_STATE
MACRO_STATE_PATH: Path = _p_from_env("AION_MACRO_STATE_PATH") or DEFAULT_MACRO_STATE
MACRO_DIR: Path = _p_from_env("AION_MACRO_DIR") or DEFAULT_MACRO_DIR


# ---------------------------------------------------------------------------
# AION brain meta
# ---------------------------------------------------------------------------

def _aion_meta_snapshot() -> Dict[str, Any]:
    try:
        ab = _read_aion_brain() or {}
        meta = ab.get("_meta", {}) if isinstance(ab, dict) else {}
        return {
            "updated_at": meta.get("updated_at"),
            "confidence_bias": safe_float(meta.get("confidence_bias", 1.0)) or 1.0,
            "risk_bias": safe_float(meta.get("risk_bias", 1.0)) or 1.0,
            "aggressiveness": safe_float(meta.get("aggressiveness", 1.0)) or 1.0,
        }
    except Exception:
        return {"updated_at": None, "confidence_bias": 1.0, "risk_bias": 1.0, "aggressiveness": 1.0}


# ---------------------------------------------------------------------------
# Macro loader (deterministic + debuggable)
# ---------------------------------------------------------------------------

def _load_macro_state() -> Dict[str, Any]:
    attempts: list[dict] = []

    # 1) market_state.json
    try:
        p = MARKET_STATE_PATH
        if p.exists():
            ms = _load_json(p)
            mm = _normalize_macro(_extract_macro_block(ms))
            ok = _looks_sane(mm) and _recent_enough(mm)
            if ok:
                return mm
            attempts.append({
                "source": "market_state",
                "path": str(p),
                "exists": True,
                "looks_sane": _looks_sane(mm),
                "recent_enough": _recent_enough(mm),
                "vix": float(mm.get("vix", 0.0) or 0.0),
                "spy_pct": float(mm.get("spy_pct", 0.0) or 0.0),
                "breadth": float(mm.get("breadth", 0.0) or 0.0),
            })
        else:
            attempts.append({"source": "market_state", "path": str(p), "exists": False})
    except Exception:
        attempts.append({"source": "market_state", "path": str(MARKET_STATE_PATH), "exists": False, "error": "exception"})

    # 2) macro_state.json
    try:
        p = MACRO_STATE_PATH
        if p.exists():
            mm = _normalize_macro(_extract_macro_block(_load_json(p)))
            ok = _looks_sane(mm)
            if ok:
                return mm
            attempts.append({
                "source": "macro_state",
                "path": str(p),
                "exists": True,
                "looks_sane": _looks_sane(mm),
                "vix": float(mm.get("vix", 0.0) or 0.0),
                "spy_pct": float(mm.get("spy_pct", 0.0) or 0.0),
                "breadth": float(mm.get("breadth", 0.0) or 0.0),
            })
        else:
            attempts.append({"source": "macro_state", "path": str(p), "exists": False})
    except Exception:
        attempts.append({"source": "macro_state", "path": str(MACRO_STATE_PATH), "exists": False, "error": "exception"})

    # 3) latest macro_state*.json in macro dir
    try:
        folder = MACRO_DIR
        latest = _latest_with_prefix(folder, "macro_state")
        if latest and latest.exists():
            mm = _normalize_macro(_extract_macro_block(_load_json(latest)))
            ok = _looks_sane(mm)
            if ok:
                return mm
            attempts.append({
                "source": "macro_dir_latest",
                "path": str(latest),
                "exists": True,
                "looks_sane": _looks_sane(mm),
                "vix": float(mm.get("vix", 0.0) or 0.0),
                "spy_pct": float(mm.get("spy_pct", 0.0) or 0.0),
                "breadth": float(mm.get("breadth", 0.0) or 0.0),
            })
        else:
            attempts.append({"source": "macro_dir_latest", "path": str(folder), "exists": bool(folder.exists()), "note": "no matching files"})
    except Exception:
        attempts.append({"source": "macro_dir_latest", "path": str(MACRO_DIR), "exists": False, "error": "exception"})

    if DEBUG:
        log(f"[regime_detector] DEBUG macro search attempts: {json.dumps(attempts, indent=2)}")
    else:
        # still log minimal useful info once
        log(
            "[regime_detector] ⚠️ No macro found. "
            f"checked market_state={str(MARKET_STATE_PATH)} "
            f"macro_state={str(MACRO_STATE_PATH)} "
            f"macro_dir={str(MACRO_DIR)}"
        )

    return {}


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def _classify_regime(m: Dict[str, Any]) -> Dict[str, Any]:
    vix = safe_float(m.get("vix", 0.0))
    spy = safe_float(m.get("spy_pct", 0.0))         # DECIMAL
    breadth = safe_float(m.get("breadth", 0.0))
    vol = safe_float(m.get("volatility", vix / 100.0))
    risk = safe_float(m.get("risk_off", 0.0))
    hint = (m.get("regime_hint") or "neutral").lower()

    label, conf = "chop", 0.45

    if spy <= -0.025 and breadth <= -0.25 and (vix >= 30 or vol >= 0.04 or risk >= 0.65):
        label, conf = "panic", 0.80
    elif spy <= -0.012 and breadth <= -0.10 and (vix >= 22 or vol >= 0.03 or risk >= 0.50):
        label, conf = "bear", 0.65
    elif spy >= 0.012 and breadth >= 0.10 and vix <= 22:
        label, conf = "bull", 0.70

    if hint in ("bull", "bear", "panic", "chop"):
        conf = min(1.0, conf + 0.10) if hint == label else max(0.30, conf - 0.05)

    conf = max(0.25, min(0.95, conf))

    return {
        "label": label,
        "confidence": float(conf),
        "vix": float(vix),
        "spy_pct": float(spy),
        "breadth": float(breadth),
        "risk_off": float(risk),
        "volatility": float(vol),
        "timestamp": m.get("generated_at") or datetime.now(TIMEZONE).isoformat(),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_regime(_: Dict[str, Any] | None = None) -> Dict[str, Any]:
    macro = _load_macro_state()
    if not macro:
        # (the detailed “checked paths” log happens in _load_macro_state)
        return {
            "label": "chop",
            "confidence": 0.30,
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
