# dt_backend/historical_replay/job_manager.py
"""
Replay Job Manager — run-now background execution with real progress tracking.

Jobs live in-process in the JOBS dict (non-persistent).
"""

from __future__ import annotations

import threading
import uuid
from typing import Dict, Any, Optional, List

from dt_backend.historical_replay.historical_replay_manager import (
    run_replay_range,
    _discover_dates,  # internal helper is fine here
)
from dt_backend.historical_replay.historical_replay_fetcher import (
    fetch_range,
    load_universe,
)
from dt_backend.core.data_pipeline_dt import log

JOBS: Dict[str, Dict[str, Any]] = {}


def _set(job_id: str, **kwargs: Any) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    job.update(kwargs)


# -------------------------------------------------------------------
# Job CRUD
# -------------------------------------------------------------------
def create_job(
    start: Optional[str] = None,
    end: Optional[str] = None,
    mode: str = "full",
    compute_mode: str = "auto",
) -> str:
    job_id = uuid.uuid4().hex[:12]

    JOBS[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0.0,
        "start": start,
        "end": end,
        "mode": mode,
        "compute_mode": compute_mode,
        "error": None,
        "result": None,
        "cancel_requested": False,
    }

    return job_id


def list_jobs() -> List[Dict[str, Any]]:
    return list(JOBS.values())


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return JOBS.get(job_id)


def cancel_job(job_id: str) -> None:
    """
    Soft cancel — the worker loop checks `cancel_requested` between days.
    """
    _set(job_id, cancel_requested=True)


# -------------------------------------------------------------------
# Execution
# -------------------------------------------------------------------
def start_job(job_id: str) -> None:
    """Launch replay in a background thread."""
    thread = threading.Thread(
        target=_run_job,
        args=(job_id,),
        daemon=True,
    )
    thread.start()


def _run_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return

    try:
        _set(job_id, status="running", progress=0.0)

        start = job.get("start")
        end = job.get("end")
        mode = job.get("mode")
        compute_mode = job.get("compute_mode")

        # -------------------------------------------------
        # 1) Ensure raw_days exist for the requested range
        # -------------------------------------------------
        if start and end:
            universe = load_universe()
            if not universe:
                _set(job_id, status="error", error="Universe empty or missing.")
                return

            log(
                f"[replay_job] job_id={job_id} fetching raw bars "
                f"start={start} end={end} mode={mode} compute={compute_mode}"
            )
            fetch_range(start, end, universe=universe)

        # -------------------------------------------------
        # 2) Discover available days and filter by range
        # -------------------------------------------------
        all_dates = _discover_dates()
        usable = all_dates

        if start:
            usable = [d for d in usable if d >= start]
        if end:
            usable = [d for d in usable if d <= end]

        total = len(usable)
        if total == 0:
            _set(job_id, status="error", error="No dates available in requested range.")
            return

        log(
            f"[replay_job] job_id={job_id} running replay "
            f"days={total} ({usable[0]} → {usable[-1]})"
        )

        # -------------------------------------------------
        # 3) Run replay day by day, tracking progress
        # -------------------------------------------------
        done = 0
        for day in usable:
            # Check for cancellation between days
            if JOBS.get(job_id, {}).get("cancel_requested"):
                _set(job_id, status="cancelled")
                log(f"[replay_job] job_id={job_id} cancelled at day={day}")
                return

            # Run a single-day replay; manager writes replay_log.json
            run_replay_range(start=day, end=day)

            done += 1
            _set(job_id, progress=done / total)

        _set(
            job_id,
            status="done",
            progress=1.0,
            result={"days": total, "start": start, "end": end},
        )
        log(f"[replay_job] ✅ job_id={job_id} finished, days={total}")

    except Exception as e:
        _set(job_id, status="error", error=str(e))
        log(f"[replay_job] ❌ job_id={job_id} error={e}")
