"""dt_backend/core/dt_brain.py

DT brain = durable learning state for the intraday system.

Philosophy
----------
* rolling (da_brains/intraday/rolling_intraday.json.gz) is *working memory*.
  It is overwritten constantly and is safe to clear at end-of-day.
* dt_brain (da_brains/core/dt_brain.json.gz) is *long-lived memory*.
  It stores performance aggregates, calibration hints, and policy/execution meta.

This module keeps the first version intentionally small: it provides safe
read/write helpers and an update hook that can be expanded over time.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from .config_dt import DT_PATHS
from .logger_dt import log


def _brain_path() -> Path:
    return Path(DT_PATHS.get("dt_brain_file") or (Path(DT_PATHS["da_brains"]) / "core" / "dt_brain.json.gz"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_dt_brain() -> Dict[str, Any]:
    """Best-effort brain read. Never raises."""
    p = _brain_path()
    if not p.exists():
        return {"_meta": {"created_at": _utc_now_iso(), "version": "dt_brain_v1"}, "symbols": {}}
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            return {"_meta": {"created_at": _utc_now_iso(), "version": "dt_brain_v1"}, "symbols": {}}
        obj.setdefault("_meta", {})
        obj.setdefault("symbols", {})
        return obj
    except Exception as e:
        log(f"[dt_brain] ⚠️ failed reading {p}: {e}")
        return {"_meta": {"created_at": _utc_now_iso(), "version": "dt_brain_v1"}, "symbols": {}}


def write_dt_brain(brain: Dict[str, Any]) -> None:
    """Atomic brain write. Best-effort; never raises."""
    p = _brain_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        if not isinstance(brain, dict):
            brain = {}
        brain.setdefault("_meta", {})
        meta = brain.get("_meta")
        if isinstance(meta, dict):
            meta.setdefault("version", "dt_brain_v1")
            meta["updated_at"] = _utc_now_iso()
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(brain, f, ensure_ascii=False, indent=2)
        tmp.replace(p)
    except Exception as e:
        log(f"[dt_brain] ⚠️ failed writing {p}: {e}")


def update_dt_brain_from_rolling(rolling: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Extract end-of-day learning signals from rolling into dt_brain.

    Current v1 behavior (safe + minimal):
    * Persist the latest execution audit per symbol (rolling[s]['exec_dt'])
      under brain['symbols'][s]['last_exec_dt'].
    * Persist a small daily counter of fills.

    This is intentionally conservative; you can extend it later to incorporate
    PnL, hit rates, calibration, slippage, etc.

    Returns: (brain_after, summary)
    """
    brain = read_dt_brain()
    sym_store = brain.get("symbols")
    if not isinstance(sym_store, dict):
        sym_store = {}
        brain["symbols"] = sym_store

    fills = 0
    audited = 0

    for sym, node in (rolling or {}).items():
        if not isinstance(sym, str) or sym.startswith("_"):
            continue
        if not isinstance(node, dict):
            continue

        exec_dt = node.get("exec_dt")
        if not isinstance(exec_dt, dict) or not exec_dt:
            continue

        audited += 1
        last_res = exec_dt.get("last_result")
        if isinstance(last_res, dict) and str(last_res.get("status") or "").lower() == "filled":
            fills += 1

        srec = sym_store.get(sym)
        if not isinstance(srec, dict):
            srec = {}
        srec["last_exec_dt"] = exec_dt
        srec["last_seen_utc"] = _utc_now_iso()
        sym_store[sym] = srec

    # global meta counters (very small / safe)
    meta = brain.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
        brain["_meta"] = meta
    meta.setdefault("version", "dt_brain_v1")
    meta["updated_at"] = _utc_now_iso()
    meta["last_eod_audited_symbols"] = int(audited)
    meta["last_eod_fills"] = int(fills)

    write_dt_brain(brain)
    summary = {"status": "ok", "audited": int(audited), "fills": int(fills), "brain_path": str(_brain_path())}
    return brain, summary
