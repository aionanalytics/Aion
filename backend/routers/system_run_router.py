# backend/routers/system_run_router.py
"""
System Run Router — Manual Overrides

POST /api/system/run/{task}

This router triggers heavyweight maintenance tasks in background threads
so the API response returns immediately.

Tasks supported:
  - nightly
  - train
  - insights
  - metrics
  - fundamentals
  - news
  - verify
  - dashboard (stub / not implemented)
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Callable

from fastapi import APIRouter, HTTPException

from backend.core.data_pipeline import _read_rolling, _read_brain, log
from backend.core.config import PATHS


router = APIRouter(prefix="/api/system", tags=["System"])


def _run_bg(fn: Callable[[], Any], name: str) -> None:
    def _wrapped():
        try:
            log(f"[system_run] 🚀 START task={name}")
            out = fn()
            log(f"[system_run] ✅ DONE task={name} result={out}")
        except Exception as e:
            log(f"[system_run] ❌ FAIL task={name} err={e}")

    threading.Thread(target=_wrapped, daemon=True).start()


# -------------------------
# Task implementations
# -------------------------

def _task_nightly():
    # Try common entrypoints without being fragile.
    # Adjust import if your nightly job exposes a different function name.
    try:
        from backend.jobs.nightly_job import main as nightly_main  # type: ignore
        return nightly_main()
    except Exception:
        from backend.jobs.nightly_job import run as nightly_run  # type: ignore
        return nightly_run()


def _task_train():
    from backend.ai_model import train_all_models
    return train_all_models()


def _task_insights():
    from backend.services.insights_builder import build_daily_insights
    return build_daily_insights(limit=50)


def _task_metrics():
    from backend.services.metrics_fetcher import build_latest_metrics
    return build_latest_metrics()


def _task_fundamentals():
    from backend.services.fundamentals_fetcher import enrich_fundamentals
    return enrich_fundamentals()


def _task_news():
    # IMPORTANT: on Windows + uvicorn reload, multiprocessing can be cranky.
    # Manual override should be stable, so force MP off.
    from backend.services.news_fetcher import run_news_fetch
    return run_news_fetch(days_back=2, use_multiprocessing=False)


def _task_verify() -> Dict[str, Any]:
    rolling = _read_rolling() or {}
    brain = _read_brain() or {}

    rolling_syms = [k for k in rolling.keys() if not str(k).startswith("_")]
    brain_syms = [k for k in brain.keys() if not str(k).startswith("_")]

    return {
        "status": "ok",
        "paths": {
            "rolling": str(PATHS.get("rolling")),
            "brain": str(PATHS.get("rolling_brain")),
            "backups": str(PATHS.get("rolling_backups")),
        },
        "counts": {
            "rolling_keys": len(rolling),
            "rolling_symbols": len(rolling_syms),
            "brain_keys": len(brain),
            "brain_symbols": len(brain_syms),
        },
    }


TASKS: Dict[str, Callable[[], Any]] = {
    "nightly": _task_nightly,
    "train": _task_train,
    "insights": _task_insights,
    "metrics": _task_metrics,
    "fundamentals": _task_fundamentals,
    "news": _task_news,
    "verify": _task_verify,
    # dashboard intentionally not wired (yet)
}


@router.post("/run/{task}")
def run_task(task: str):
    task_key = (task or "").strip().lower()

    if task_key == "dashboard":
        raise HTTPException(status_code=501, detail="Dashboard rebuild not implemented yet.")

    fn = TASKS.get(task_key)
    if not fn:
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Unknown task '{task_key}'",
                "allowed": sorted(TASKS.keys()) + ["dashboard"],
            },
        )

    _run_bg(fn, task_key)
    return {"status": "started", "task": task_key}
