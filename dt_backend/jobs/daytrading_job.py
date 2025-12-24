# dt_backend/jobs/daytrading_job.py — v2.1 (ROLLING-SAFE)
"""
Main intraday trading loop for AION dt_backend.

This job wires together the full intraday pipeline:

    rolling(snapshot) → context_dt → features_dt → predictions_dt
                       → regime → policy_dt → signals + (optional) execution

IMPORTANT ARCHITECTURAL RULE:
-------------------------------------------------
This job is READ-ONLY with respect to rolling state.

Only the live_market_data_loop is allowed to write
rolling_intraday.json.gz.

This job operates on a per-run snapshot to ensure:
    • Windows file safety
    • Deterministic behavior
    • No concurrent file corruption
"""

from __future__ import annotations

import os
import shutil
import gzip
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from dt_backend.core import (
    log,
    build_intraday_context,
    classify_intraday_regime,
    apply_intraday_policy,
)
from dt_backend.core.execution_dt import run_execution_intraday
from dt_backend.core.config_dt import DT_PATHS
from dt_backend.core.data_pipeline_dt import _read_rolling, save_rolling, ensure_symbol_node
from dt_backend.engines.feature_engineering import build_intraday_features
from dt_backend.ml import (
    score_intraday_tickers,
    build_intraday_signals,
)
from dt_backend.engines.trade_executor import ExecutionConfig, execute_from_policy


# -------------------------------------------------
# Rolling snapshot helpers
# -------------------------------------------------

def _create_rolling_snapshot() -> Path | None:
    """
    Create a read-only snapshot of rolling_intraday.json.gz.

    Returns snapshot path or None if rolling does not exist.
    """
    rolling_path = DT_PATHS["rolling_intraday_file"]
    snapshot_path = rolling_path.with_suffix(".snapshot.json.gz")

    if not rolling_path.exists():
        log("[daytrading_job] ⚠️ rolling file not found; skipping snapshot.")
        return None


def _read_gz_json(path: Path) -> Dict[str, Any]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _snapshot_market_rolling_with_retry(max_tries: int = 8, base_sleep: float = 0.15) -> Path | None:
    """Create a stable snapshot of the market rolling file (bars-only).

    The live loop writes this file; on Windows a read during rename/write can
    intermittently fail. We retry a few times with backoff.
    """
    market_path = DT_PATHS.get("rolling_market_intraday_file")
    if not market_path:
        return None
    market_path = Path(market_path)
    if not market_path.exists():
        return None

    snap_name = f"{market_path.stem}.snapshot.{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}{market_path.suffix}"
    snap_path = market_path.with_name(snap_name)

    for i in range(max(1, int(max_tries))):
        try:
            shutil.copyfile(market_path, snap_path)
            # Quick sanity: ensure it's readable gzip json
            _ = _read_gz_json(snap_path)
            return snap_path
        except Exception:
            time.sleep(base_sleep * (2 ** i))

    return None


def _sync_market_bars_into_dt_rolling() -> Dict[str, Any]:
    """Copy latest bars from market rolling into dt rolling, without touching market file."""

    snap = _snapshot_market_rolling_with_retry()
    if not snap:
        return {"status": "no_market_snapshot"}

    market = _read_gz_json(snap)
    try:
        snap.unlink(missing_ok=True)
    except Exception:
        pass

    # Read current dt rolling (this process writes dt rolling only)
    dt_rolling = _read_rolling() or {}
    if not isinstance(dt_rolling, dict):
        dt_rolling = {}

    bars_keys = ("bars_intraday", "bars_intraday_5m")
    updated = 0

    for sym, mnode in market.items():
        if not isinstance(sym, str) or sym.startswith("_"):
            continue
        if not isinstance(mnode, dict):
            continue

        dnode = ensure_symbol_node(dt_rolling, sym)
        touched = False

        for k in bars_keys:
            mb = mnode.get(k)
            if isinstance(mb, list) and mb:
                dnode[k] = mb
                touched = True

        # Keep any light meta fields if present (name/sector etc.)
        for k in ("meta", "name", "sector", "industry"):
            if k in mnode and k not in dnode:
                dnode[k] = mnode.get(k)

        if touched:
            dt_rolling[sym] = dnode
            updated += 1

    save_rolling(dt_rolling)
    return {"status": "ok", "symbols_updated": int(updated)}

    try:
        shutil.copyfile(rolling_path, snapshot_path)
        log(f"[daytrading_job] 📸 rolling snapshot created: {snapshot_path}")
        return snapshot_path
    except Exception as e:
        log(f"[daytrading_job] ❌ failed to create rolling snapshot: {e}")
        return None


def _load_gz_json(path: Path) -> Dict[str, Any]:
    """Best-effort load of a gzip JSON file."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _create_market_snapshot(retries: int = 8, base_sleep_s: float = 0.25) -> Path | None:
    """Snapshot the market rolling (bars-only) file with backoff."""
    market_path = Path(DT_PATHS.get("rolling_market_intraday_file") or "")
    if not market_path or not market_path.exists():
        log("[daytrading_job] ⚠️ market rolling file not found; cannot sync bars.")
        return None

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    snapshot_path = market_path.with_name(f"{market_path.stem}.snapshot.{stamp}.json.gz")

    for i in range(max(1, int(retries))):
        try:
            shutil.copyfile(market_path, snapshot_path)
            # Quick sanity check: can we read it as gzip JSON?
            _ = _load_gz_json(snapshot_path)
            log(f"[daytrading_job] 📸 market snapshot created: {snapshot_path}")
            return snapshot_path
        except Exception:
            time.sleep(base_sleep_s * (1.6 ** i))

    log("[daytrading_job] ❌ failed to snapshot market rolling after retries.")
    return None


def _sync_market_bars_into_dt_rolling() -> Dict[str, Any]:
    """Copy latest bars from market rolling into dt engine rolling (locked writes)."""
    snap = _create_market_snapshot()
    if not snap:
        return {"status": "no_market_snapshot"}

    market = _load_gz_json(snap)
    if not market:
        return {"status": "market_snapshot_empty"}

    dt_rolling = _read_rolling() or {}
    if not isinstance(dt_rolling, dict):
        dt_rolling = {}

    updated = 0
    for sym, mnode in market.items():
        if not isinstance(sym, str) or sym.startswith("_"):
            continue
        if not isinstance(mnode, dict):
            continue

        node = ensure_symbol_node(dt_rolling, sym)
        # Bars keys that are produced by the live market loop
        if "bars_intraday" in mnode:
            node["bars_intraday"] = mnode.get("bars_intraday")
        if "bars_intraday_5m" in mnode:
            node["bars_intraday_5m"] = mnode.get("bars_intraday_5m")
        dt_rolling[sym] = node
        updated += 1

    save_rolling(dt_rolling)
    return {"status": "ok", "symbols_synced": int(updated), "snapshot": str(snap)}


# -------------------------------------------------
# Main job
# -------------------------------------------------

def run_daytrading_cycle(
    execute: bool = False,
    max_symbols: int | None = None,
    max_positions: int = 50,
    execution_cfg: ExecutionConfig | None = None,
) -> Dict[str, Any]:
    """
    Run one full intraday cycle (READ-ONLY rolling).

    Parameters
    ----------
    execute:
        If True, actually call `execute_from_policy` (paper by default).
    max_symbols:
        Optional cap on number of symbols for features/scoring.
    max_positions:
        Max symbols selected by policy (ranking step).
    execution_cfg:
        Optional ExecutionConfig override for trade sizing.

    Returns
    -------
    Summary dict with keys:
        context, features, scoring, regime, policy, signals, execution
    """
    log("[daytrading_job] 🚀 starting intraday cycle (READ-ONLY rolling).")

    # -------------------------------------------------
    # Use DT-engine rolling (writes) + file lock
    # -------------------------------------------------
    os.environ.pop("DT_ROLLING_READONLY", None)
    os.environ.pop("DT_ROLLING_SNAPSHOT_PATH", None)
    os.environ["DT_USE_LOCK"] = "1"
    os.environ["DT_ROLLING_PATH"] = str(DT_PATHS["rolling_intraday_file"])

    # -------------------------------------------------
    # Sync live bars from market rolling into DT rolling (no writes to market)
    # -------------------------------------------------
    _sync_market_bars_into_dt_rolling(max_wait_s=25.0)

    # -------------------------------------------------
    # Pipeline (NO rolling writes allowed past this point)
    # -------------------------------------------------
    ctx_summary = build_intraday_context()
    feat_summary = build_intraday_features(max_symbols=max_symbols)
    score_summary = score_intraday_tickers(max_symbols=max_symbols)
    regime_summary = classify_intraday_regime()
    policy_summary = apply_intraday_policy(max_positions=max_positions)

    # Convert policy → execution intents
    exec_dt_summary = run_execution_intraday()
    signals_summary = build_intraday_signals()

    exec_summary: Dict[str, Any] | None = None
    if execute:
        exec_summary = execute_from_policy(execution_cfg)

    log("[daytrading_job] ✅ intraday cycle complete.")

    return {
        "context": ctx_summary,
        "features": feat_summary,
        "scoring": score_summary,
        "regime": regime_summary,
        "policy": policy_summary,
        "execution_dt": exec_dt_summary,
        "signals": signals_summary,
        "execution": exec_summary,
    }

def main() -> None:
    run_daytrading_cycle(execute=False)


if __name__ == "__main__":
    main()

def main() -> None:
    run_daytrading_cycle(execute=False)


if __name__ == "__main__":
    main()

