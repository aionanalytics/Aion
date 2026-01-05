from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any, Dict

from fastapi import APIRouter, Request, HTTPException, Depends

from backend.admin.auth import login_admin, issue_token, require_admin
from backend.historical_replay_swing.job_manager import (
    start_replay_legacy,
    get_replay_status,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _extract_token(req: Request) -> str:
    auth = (req.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    if auth:
        return auth
    return (req.headers.get("x-admin-token") or "").strip()


def admin_required_req(req: Request) -> str:
    """
    FastAPI dependency that validates admin token extracted from headers.
    Returns the token (handy for logging/auditing if needed).
    """
    token = _extract_token(req)
    if not token or not require_admin(token):
        raise HTTPException(status_code=403, detail="forbidden")
    return token


# --- Auth ---
@router.post("/login")
async def admin_login(req: Request) -> Dict[str, Any]:
    try:
        data = await req.json()
    except Exception:
        data = {}

    password = str((data or {}).get("password", "")).strip()
    if not password:
        raise HTTPException(status_code=400, detail="missing_password")

    if not login_admin(password):
        raise HTTPException(status_code=403, detail="invalid_password")

    return {"token": issue_token()}


# --- Replay control ---
@router.post("/replay/start")
async def replay_start(req: Request, _: str = Depends(admin_required_req)) -> Dict[str, Any]:
    try:
        payload = await req.json()
    except Exception:
        payload = {}

    weeks = payload.get("weeks", 4)
    version = payload.get("version", "v1")

    return start_replay_legacy(weeks=int(weeks), version=str(version))


@router.get("/replay/status")
async def replay_status(_: str = Depends(admin_required_req)) -> Dict[str, Any]:
    return get_replay_status()


# --- Restart services ---
@router.post("/system/restart")
def restart_services(_: str = Depends(admin_required_req)):
    """
    NOTE:
      In multi-worker uvicorn, this only terminates *this* worker process.
      Your process manager (systemd/supervisor/docker) should restart it.
    """
    def delayed_exit():
        time.sleep(1.5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=delayed_exit, daemon=True).start()
    return {"status": "ok", "message": "Terminating this process for restart"}
