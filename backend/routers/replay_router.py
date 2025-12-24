# backend/routers/replay_router.py
"""
Historical Replay Router — FINAL CLEAN VERSION

Includes:

Legacy replay-log endpoints:
  • GET  /api/replay/log
  • GET  /api/replay/days
  • GET  /api/replay/day/{day}
  • GET  /api/replay/latest
  • GET  /api/replay/requests
  • POST /api/replay/request

Run-now Job Manager endpoints (used by /replay frontend):
  • POST /api/replay/run-now
  • GET  /api/replay/jobs
  • GET  /api/replay/jobs/{job_id}
  • POST /api/replay/jobs/{job_id}/cancel
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.config import PATHS
from utils.logger import log

# -------------------------------------------------------------
# Correct Job Manager Imports
# -------------------------------------------------------------
from dt_backend.historical_replay.job_manager import (
    create_job,
    start_job,
    list_jobs,
    get_job,
    cancel_job,
    JOBS,
)

router = APIRouter(prefix="/api/replay", tags=["replay"])


# ============================================================
# Helpers
# ============================================================

def _root() -> Path:
    root = PATHS.get("root")
    return Path(root) if root else Path(".").resolve()


def _replay_dir() -> Path:
    return _root() / "ml_data_dt" / "intraday" / "replay"


def _replay_log_path() -> Path:
    return _replay_dir() / "replay_log.json"


def _requests_path() -> Path:
    return _replay_dir() / "replay_requests.json"


def _load_json(path: Path) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"[replay] ⚠ Failed to read {path}: {e}")
    return None


def _save_json(path: Path, obj: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"[replay] ⚠ Failed to write {path}: {e}")
        raise


def _parse_date(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


# ============================================================
# Models
# ============================================================

class ReplayRequest(BaseModel):
    start_date: str
    end_date: str
    mode: str = "full"


class RunNowPayload(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    mode: str = "full"
    compute_mode: str = Field(default="auto")


# ============================================================
# JOB MANAGER — RUN NOW
# ============================================================

@router.post("/run-now", summary="Run replay immediately in background")
def run_now(payload: RunNowPayload):

    # Validate dates (optional)
    if payload.start_date and not _parse_date(payload.start_date):
        raise HTTPException(status_code=400, detail="start_date invalid")

    if payload.end_date and not _parse_date(payload.end_date):
        raise HTTPException(status_code=400, detail="end_date invalid")

    if payload.start_date and payload.end_date:
        if _parse_date(payload.end_date) < _parse_date(payload.start_date):
            raise HTTPException(status_code=400, detail="end_date < start_date")

    # Create job
    job_id = create_job(
        start=payload.start_date,
        end=payload.end_date,
        mode=payload.mode,
        compute_mode=payload.compute_mode,
    )

    # Start thread
    start_job(job_id)

    log(
        f"[replay] 🚀 run-now job queued id={job_id} "
        f"start={payload.start_date} end={payload.end_date} "
        f"mode={payload.mode} compute={payload.compute_mode}"
    )

    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs", summary="List all replay jobs")
def api_list_jobs():
    return {"jobs": list_jobs()}


@router.get("/jobs/{job_id}", summary="Get job detail")
def api_get_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel", summary="Cancel running job")
def api_cancel_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] in {"done", "error", "cancelled"}:
        return {"status": "ignored", "job": job}

    cancel_job(job_id)
    return {"status": "cancel_requested", "job_id": job_id}


# ============================================================
# LEGACY REPLAY-LOG ENDPOINTS
# ============================================================

@router.get("/log", summary="Get raw replay_log.json")
def get_replay_log():
    return _load_json(_replay_log_path()) or {}


@router.get("/days", summary="List replay days with summary")
def list_replay_days():
    obj = _load_json(_replay_log_path()) or {}
    days = obj.get("days") or []
    out = []

    for d in days:
        date_str = d.get("date") or d.get("day") or "unknown"
        symbols = d.get("symbols") or d.get("universe") or []
        pnl = d.get("pnl") or {}

        out.append({
            "date": date_str,
            "num_symbols": len(symbols) if isinstance(symbols, list) else 0,
            "pnl": pnl,
        })

    return {"days": out}


@router.get("/day/{day}", summary="Get replay results for one day")
def get_day(day: str):
    obj = _load_json(_replay_log_path()) or {}
    for d in obj.get("days") or []:
        date_str = d.get("date") or d.get("day")
        if date_str == day:
            return d
    raise HTTPException(status_code=404, detail="No such day")


@router.get("/latest", summary="Get most recent replay day")
def get_latest():
    obj = _load_json(_replay_log_path()) or {}
    days = obj.get("days") or []

    best = None
    best_dt = None

    for d in days:
        ds = d.get("date") or d.get("day")
        dt = _parse_date(ds) if ds else None
        if dt and (best_dt is None or dt > best_dt):
            best_dt = dt
            best = d

    if not best:
        raise HTTPException(status_code=404, detail="No replay days found")

    return best


@router.get("/requests", summary="List legacy replay requests")
def list_requests():
    return {"requests": _load_json(_requests_path()) or []}


@router.post("/request", summary="Create legacy replay request")
def create_request(payload: ReplayRequest):
    start_dt = _parse_date(payload.start_date)
    end_dt = _parse_date(payload.end_date)

    if not start_dt or not end_dt:
        raise HTTPException(status_code=400, detail="Invalid dates")
    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="end_date < start_date")

    now = datetime.utcnow()
    req = {
        "id": now.strftime("%Y%m%dT%H%M%SZ"),
        "start_date": payload.start_date,
        "end_date": payload.end_date,
        "mode": payload.mode,
        "status": "pending",
        "created_at": now.isoformat() + "Z",
    }

    arr = _load_json(_requests_path()) or []
    arr.append(req)
    _save_json(_requests_path(), arr)

    return {"status": "queued", "request": req}
