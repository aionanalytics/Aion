# dt_backend/jobs/daytrading_job.py — v3.0 (SINGLE-ROLLING, LINUX-SAFE)
"""Main intraday trading loop for AION dt_backend.

This job wires together the intraday pipeline:

    rolling → context_dt → features_dt → predictions_dt
            → regime → policy_dt → execution_dt → (optional) broker execution

Architecture (Linux server)
---------------------------
We use a **single** rolling cache file (DT_PATHS['rolling_intraday_file']).

* Live bars are a bounded sliding window and are expected to overwrite.
* Policy/execution/learning state is written into the same rolling.
* Long-lived learning should live in a separate dt_brain artifact (see
  dt_backend/core/dt_brain.py), not in rolling.

Windows note
------------
If you ever need to split market bars into a separate rolling again (Windows
contention), you must also add an explicit merge/bridge step. On Linux we keep
this simple.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from dt_backend.core import (
    log,
    build_intraday_context,
    classify_intraday_regime,
    apply_intraday_policy,
)
from dt_backend.core.execution_dt import run_execution_intraday
from dt_backend.engines.feature_engineering import build_intraday_features
from dt_backend.ml import score_intraday_tickers, build_intraday_signals
from dt_backend.engines.trade_executor import ExecutionConfig, execute_from_policy


def run_daytrading_cycle(
    execute: bool = False,
    max_symbols: Optional[int] = None,
    max_positions: int = 50,
    execution_cfg: ExecutionConfig | None = None,
) -> Dict[str, Any]:
    """Run one full intraday cycle.

    Parameters
    ----------
    execute:
        If True, submit orders using the configured broker API (paper by default).
    max_symbols:
        Optional cap on number of symbols processed for features/scoring.
    max_positions:
        Max symbols selected by policy (ranking step).
    execution_cfg:
        Optional execution sizing / safety config.
    """
    log("[daytrading_job] 🚀 starting intraday cycle")

    # Single rolling file. Lock is optional; safe on Linux with atomic rename.
    os.environ.setdefault("DT_USE_LOCK", os.getenv("DT_USE_LOCK", "0") or "0")
    os.environ.pop("DT_ROLLING_PATH", None)

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

    log("[daytrading_job] ✅ intraday cycle complete")
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
