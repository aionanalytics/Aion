# dt_backend/core/regime_detector_dt.py — v1.1
"""
Intraday regime detector for dt_backend.

Writes:
    rolling["_GLOBAL_DT"]["regime"] = {
        "label": "bull" | "bear" | "chop" | "unknown",
        "breadth_up": float,
        "n": int,
        "ts": "...Z",
    }

Logic (simple + stable):
  • breadth_up = fraction of symbols with intraday_return > 0
  • if breadth_up > 0.60  → "bull"
  • if breadth_up < 0.40  → "bear"
  • otherwise             → "chop"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from .data_pipeline_dt import _read_rolling, save_rolling, log, ensure_symbol_node


def classify_intraday_regime(min_symbols: int = 100) -> Dict[str, Any]:
    rolling = _read_rolling()
    if not rolling:
        log("[regime_dt] ⚠️ rolling empty.")
        return {"label": "unknown", "breadth_up": 0.5, "n": 0}

    up = 0
    total = 0

    for sym, node in rolling.items():
        if str(sym).startswith("_"):
            continue
        if not isinstance(node, dict):
            continue

        ctx = node.get("context_dt") or {}
        if not isinstance(ctx, dict):
            continue

        r = ctx.get("intraday_return")
        try:
            r_f = float(r)
        except Exception:
            continue

        total += 1
        if r_f > 0.0:
            up += 1

    if total < max(int(min_symbols), 1):
        breadth = 0.5
        label = "unknown"
        log(f"[regime_dt] ⚠️ only {total} symbols with context_dt (min={min_symbols}).")
    else:
        breadth = up / float(total)
        if breadth > 0.60:
            label = "bull"
        elif breadth < 0.40:
            label = "bear"
        else:
            label = "chop"

    node_global = ensure_symbol_node(rolling, "_GLOBAL_DT")
    regime_info = {
        "label": label,
        "breadth_up": float(breadth),
        "n": int(total),
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    node_global["regime"] = regime_info
    rolling["_GLOBAL_DT"] = node_global

    save_rolling(rolling)
    log(f"[regime_dt] ✅ {label} (breadth_up={breadth:.2%}, n={total})")
    return regime_info