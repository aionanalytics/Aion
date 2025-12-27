# dt_backend/jobs/end_of_day_cleanup.py — v1.0
"""End-of-day lifecycle job for dt_backend.

At market close (or before next open), run this job to:
  1) Write durable learning/audit signals from rolling → dt_brain
  2) Clear intraday-only rolling fields so the next trading day starts clean

This is the missing lifecycle hook that prevents stale bars/context from
polluting the next session.
"""

from __future__ import annotations

from typing import Any, Dict

from dt_backend.core.data_pipeline_dt import _read_rolling, save_rolling, ensure_symbol_node
from dt_backend.core.logger_dt import log
from dt_backend.core.dt_brain import update_dt_brain_from_rolling, read_dt_brain, write_dt_brain

try:
    # Prefer shared market-hours utilities when available.
    from utils.time_utils import now_ny  # type: ignore
except Exception:  # pragma: no cover
    now_ny = None  # type: ignore

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from datetime import datetime, timezone


INTRADAY_CLEAR_FIELDS = (
    "bars_intraday",
    "bars_intraday_5m",
    "features_dt",
    "context_dt",
    "predictions_dt",
    "policy_dt",
    "execution_dt",
    "exec_dt",
)


def run_end_of_day_cleanup(clear_global_blocks: bool = True) -> Dict[str, Any]:
    rolling = _read_rolling() or {}
    if not rolling:
        log("[dt_eod] ⚠️ rolling empty; nothing to clean")
        return {"status": "empty"}

    # 1) Persist durable signals
    _brain, brain_summary = update_dt_brain_from_rolling(rolling)

    # Stamp an explicit EOD marker so schedulers can be idempotent.
    # We record the NY trading-date (YYYY-MM-DD) because that's how humans think
    # about sessions, and it handles overnight UTC date flips.
    try:
        if callable(now_ny):
            session_date = now_ny().date().isoformat()  # type: ignore[call-arg]
        elif ZoneInfo is not None:
            session_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()  # type: ignore[misc]
        else:
            session_date = datetime.now(timezone.utc).date().isoformat()

        brain2 = read_dt_brain()
        meta = brain2.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
            brain2["_meta"] = meta
        meta["last_eod_cleanup_session_date"] = str(session_date)
        meta["last_eod_cleanup_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        write_dt_brain(brain2)
        brain_summary["session_date"] = str(session_date)
    except Exception:
        pass
    # 2) Clear intraday-only fields
    cleared_symbols = 0
    for sym in list(rolling.keys()):
        if str(sym).startswith("_"):
            continue
        node = rolling.get(sym)
        if not isinstance(node, dict):
            node = ensure_symbol_node(rolling, str(sym))

        for k in INTRADAY_CLEAR_FIELDS:
            if k.startswith("bars_"):
                node[k] = []
            elif k in {"features_dt", "context_dt", "predictions_dt", "policy_dt", "execution_dt", "exec_dt"}:
                node[k] = {}
            else:
                # fallback
                node.pop(k, None)

        rolling[str(sym).upper()] = node
        cleared_symbols += 1

    if clear_global_blocks:
        # Keep other global keys if you have them, but allow clearing DT globals
        for gk in ("_GLOBAL_DT",):
            if gk in rolling:
                rolling.pop(gk, None)

    save_rolling(rolling)
    log(f"[dt_eod] ✅ cleaned intraday rolling fields for {cleared_symbols} symbols")

    return {
        "status": "ok",
        "cleared_symbols": int(cleared_symbols),
        "brain": brain_summary,
    }


def main() -> None:
    out = run_end_of_day_cleanup()
    log(f"[dt_eod] done: {out}")


if __name__ == "__main__":
    main()
