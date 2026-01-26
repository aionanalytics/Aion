# backend/routers/logs_router.py
"""
Consolidated Logs Router — AION Analytics

Consolidates log-related endpoints from:
  - nightly_logs_router.py (deleted)
  - intraday_logs_router.py (log endpoints only)

Endpoints:
  - GET /api/logs/list?scope=nightly|intraday|scheduler|backend
  - GET /api/logs/{id}              (read log file content)
  - GET /api/logs/nightly/recent    (recent nightly log entries)
  - GET /api/logs/intraday/recent   (recent intraday log entries)
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from backend.core.config import PATHS

router = APIRouter(prefix="/api/logs", tags=["logs"])


# =========================================================================
# HELPERS
# =========================================================================

def _allowed_roots() -> List[Path]:
    """Return all allowed log directories."""
    roots: List[Path] = []
    for key in ("nightly_logs", "scheduler_logs", "backend_logs", "logs", "intraday_logs"):
        try:
            p = PATHS.get(key)
            if p:
                roots.append(Path(p))
        except Exception:
            continue

    # Legacy location
    try:
        root = Path(PATHS.get("root") or Path.cwd())
        roots.append(root / "backend" / "jobs" / "logs")
    except Exception:
        pass
    
    # DT logs
    try:
        ml_data_dt = Path(PATHS.get("ml_data_dt", "ml_data_dt"))
        roots.append(ml_data_dt / "sim_logs")
    except Exception:
        pass

    # De-dup + keep existing only
    out: List[Path] = []
    seen = set()
    for r in roots:
        try:
            rr = r.resolve()
        except Exception:
            rr = r
        if str(rr) in seen:
            continue
        seen.add(str(rr))
        out.append(rr)
    return out


def _is_within(path: Path, root: Path) -> bool:
    try:
        path = path.resolve()
        root = root.resolve()
        return path == root or root in path.parents
    except Exception:
        return False


def _encode_id(p: Path) -> str:
    raw = str(p).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_id(id_: str) -> Path:
    pad = "=" * ((4 - (len(id_) % 4)) % 4)
    raw = base64.urlsafe_b64decode((id_ + pad).encode("ascii"))
    return Path(raw.decode("utf-8"))


def _candidate_files(scope: str) -> List[Tuple[str, Path]]:
    """Return [(kind, path), ...] candidates."""
    roots = _allowed_roots()
    nightly_root = Path(PATHS.get("nightly_logs") or Path(PATHS.get("logs") or "logs") / "nightly")
    scheduler_root = Path(PATHS.get("scheduler_logs") or Path(PATHS.get("logs") or "logs") / "scheduler")
    backend_root = Path(PATHS.get("backend_logs") or Path(PATHS.get("logs") or "logs") / "backend")
    logs_root = Path(PATHS.get("logs") or "logs")
    intraday_root = Path(PATHS.get("ml_data_dt", "ml_data_dt")) / "sim_logs"

    scope = (scope or "nightly").strip().lower()

    picks: List[Tuple[str, Path]] = []

    def add_dir(kind: str, d: Path, recursive: bool = False) -> None:
        try:
            if not d.exists() or not d.is_dir():
                return
            globber = d.rglob if recursive else d.glob
            for ext in ("*.log", "*.txt", "*.out", "*.json"):
                for f in globber(ext):
                    if f.is_file():
                        picks.append((kind, f))
        except Exception:
            return

    if scope in ("nightly", "all"):
        add_dir("nightly", nightly_root, recursive=True)
        add_dir("scheduler", scheduler_root, recursive=True)
        try:
            root = Path(PATHS.get("root") or Path.cwd())
            add_dir("legacy", root / "backend" / "jobs" / "logs", recursive=True)
        except Exception:
            pass
        add_dir("daily", logs_root, recursive=False)

    if scope in ("intraday", "all"):
        add_dir("intraday", intraday_root, recursive=True)

    if scope in ("backend", "all"):
        add_dir("backend", backend_root, recursive=True)

    if scope in ("scheduler", "all"):
        add_dir("scheduler", scheduler_root, recursive=True)

    if scope in ("daily", "all"):
        add_dir("daily", logs_root, recursive=False)

    # Final safety: filter to allowed roots only
    allowed = roots
    out: List[Tuple[str, Path]] = []
    for kind, f in picks:
        if any(_is_within(f, r) for r in allowed):
            out.append((kind, f))
    return out


# =========================================================================
# ENDPOINTS
# =========================================================================

@router.get("/list")
def list_logs(scope: str = Query(default="nightly")) -> Dict[str, Any]:
    """
    List log files.
    
    Scope options:
      - nightly: nightly_logs + scheduler_logs + legacy job logs
      - intraday: intraday/DT logs
      - scheduler: scheduler logs only
      - backend: backend logs only
      - daily: root daily logs only
      - all: everything in allowed roots
    """
    files = _candidate_files(scope)
    runs: List[Dict[str, Any]] = []
    for kind, p in files:
        try:
            stat = p.stat()
            runs.append(
                {
                    "id": _encode_id(p),
                    "name": p.name,
                    "kind": kind,
                    "size_bytes": int(stat.st_size),
                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "rel": str(p),
                }
            )
        except Exception:
            continue

    # Newest first
    def _key(x: Dict[str, Any]) -> float:
        try:
            return datetime.fromisoformat(x.get("mtime") or "1970-01-01").timestamp()
        except Exception:
            return 0.0

    runs.sort(key=_key, reverse=True)
    return {"scope": scope, "count": len(runs), "runs": runs}


@router.get("/{log_id}")
def read_log(
    log_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1_000_000, ge=1, le=10_000_000),
) -> Dict[str, Any]:
    """
    Read a log file by id.
    
    Returns up to `limit` bytes starting at `offset`.
    The UI can request additional chunks if `truncated` is true.
    """
    try:
        p = _decode_id(log_id)
    except Exception:
        raise HTTPException(status_code=400, detail="bad_id")

    allowed = _allowed_roots()
    if not any(_is_within(p, r) for r in allowed):
        raise HTTPException(status_code=403, detail="forbidden")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="not_found")

    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            f.seek(int(offset))
            data = f.read(int(limit))
        text = data.decode("utf-8", errors="replace")
        next_offset = offset + len(data)
        truncated = next_offset < size
        return {
            "id": log_id,
            "name": p.name,
            "path": str(p),
            "size_bytes": int(size),
            "offset": int(offset),
            "limit": int(limit),
            "next_offset": int(next_offset) if truncated else None,
            "truncated": bool(truncated),
            "content": text,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"read_failed: {type(e).__name__}")


@router.get("/nightly/recent")
def get_nightly_recent(lines: int = Query(default=100, ge=1, le=10000)) -> Dict[str, Any]:
    """Get recent entries from nightly logs."""
    try:
        nightly_root = Path(PATHS.get("nightly_logs") or Path(PATHS.get("logs") or "logs") / "nightly")
        
        if not nightly_root.exists():
            return {"entries": [], "count": 0}
        
        # Get most recent log file
        log_files = sorted(nightly_root.glob("nightly_*.log"), reverse=True)
        if not log_files:
            return {"entries": [], "count": 0}
        
        latest_log = log_files[0]
        
        # Read last N lines
        with open(latest_log, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
        return {
            "file": str(latest_log.name),
            "entries": [line.strip() for line in recent_lines],
            "count": len(recent_lines),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read nightly logs: {str(e)}")


@router.get("/intraday/recent")
def get_intraday_recent(lines: int = Query(default=100, ge=1, le=10000)) -> Dict[str, Any]:
    """Get recent entries from intraday logs."""
    try:
        intraday_root = Path(PATHS.get("ml_data_dt", "ml_data_dt")) / "sim_logs"
        
        if not intraday_root.exists():
            return {"entries": [], "count": 0}
        
        # Get most recent log file
        log_files = sorted(intraday_root.glob("*.log"), reverse=True)
        if not log_files:
            # Try JSON files as fallback
            log_files = sorted(intraday_root.glob("*.json"), reverse=True)
        
        if not log_files:
            return {"entries": [], "count": 0}
        
        latest_log = log_files[0]
        
        # Read last N lines
        with open(latest_log, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
        return {
            "file": str(latest_log.name),
            "entries": [line.strip() for line in recent_lines],
            "count": len(recent_lines),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read intraday logs: {str(e)}")


# Backward compatibility endpoints
@router.get("/nightly/runs")
def list_nightly_runs(scope: str = Query(default="nightly")) -> Dict[str, Any]:
    """Backward compatibility for nightly/runs endpoint."""
    return list_logs(scope=scope)


@router.get("/nightly/run/{run_id}")
def read_nightly_run(
    run_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1_000_000, ge=1, le=10_000_000),
) -> Dict[str, Any]:
    """Backward compatibility for nightly/run/{run_id} endpoint."""
    return read_log(log_id=run_id, offset=offset, limit=limit)


@router.get("/nightly/{day}")
def get_nightly_by_day(day: str) -> Dict[str, Any]:
    """Get nightly log for a specific day (YYYY-MM-DD)."""
    try:
        nightly_root = Path(PATHS.get("nightly_logs") or Path(PATHS.get("logs") or "logs") / "nightly")
        
        if not nightly_root.exists():
            raise HTTPException(status_code=404, detail="Nightly logs directory not found")
        
        # Try different possible filenames
        possible_files = [
            nightly_root / f"nightly_{day}.log",
            nightly_root / f"{day}.log",
            nightly_root / f"nightly_full_{day.replace('-', '')}.log",
        ]
        
        log_file = None
        for f in possible_files:
            if f.exists():
                log_file = f
                break
        
        if not log_file:
            raise HTTPException(status_code=404, detail=f"Log file for {day} not found")
        
        # Read the file
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        return {
            "day": day,
            "file": str(log_file.name),
            "content": content,
            "size_bytes": log_file.stat().st_size,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read log: {str(e)}")
