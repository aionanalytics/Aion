"""backend.routers.swing_replay_router

Admin router for the Swing historical replay manager.

Endpoints:
  GET  /admin/swing-replay/status
  POST /admin/swing-replay/start
  POST /admin/swing-replay/stop
  POST /admin/swing-replay/reset
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter

from backend.historical_replay_swing.job_manager import (
    get_state,
    start_replay,
    stop_replay,
    reset_replay_state,
)


router = APIRouter(prefix="/admin/swing-replay", tags=["admin", "swing-replay"])


@router.get("/status")
def status() -> Dict[str, Any]:
    return get_state()


@router.post("/start")
def start(
    lookback_days: int = 28,
    version: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    return start_replay(lookback_days=lookback_days, version=version, force=force)


@router.post("/stop")
def stop() -> Dict[str, Any]:
    return stop_replay()


@router.post("/reset")
def reset(force: bool = False) -> Dict[str, Any]:
    return reset_replay_state(force=force)
