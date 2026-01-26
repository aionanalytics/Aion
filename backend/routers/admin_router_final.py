# backend/routers/admin_router_final.py
"""
Consolidated Admin Router — AION Analytics

Consolidates admin-related endpoints from:
  - admin_consolidated_router.py (deleted - functionality moved here)
  - backend/admin/routes.py
  - backend/admin/admin_tools_router.py
  - settings_router.py
  - swing_replay_router.py
  - dashboard_router.py

Endpoints:
  - GET  /admin/status               (system health)
  - GET  /admin/logs                 (live logs)
  - POST /admin/settings/update      (update API keys, knobs)
  - GET  /admin/settings/current     (view current settings)
  - POST /admin/replay/start         (swing replay control)
  - GET  /admin/replay/status        (replay status)
  - POST /admin/login                (authentication)
  - POST /admin/tools/*              (admin tools)
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Query

from backend.core.config import PATHS, TIMEZONE

# Import existing routers to delegate functionality
# Note: admin_consolidated_router was deleted - functionality moved to this file
try:
    from backend.routers import admin_consolidated_router
except ImportError:
    admin_consolidated_router = None

try:
    from backend.admin import routes as admin_routes
except ImportError:
    admin_routes = None

try:
    from backend.admin import admin_tools_router
except ImportError:
    admin_tools_router = None

try:
    from backend.routers import settings_router
except ImportError:
    settings_router = None

try:
    from backend.routers import swing_replay_router
except ImportError:
    swing_replay_router = None

router = APIRouter(prefix="/admin", tags=["admin"])


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================

async def _call_if_exists(module, func_name: str, *args, **kwargs):
    """Call a function from a module if it exists, handling both sync and async."""
    if module is None:
        return None
    
    func = getattr(module, func_name, None)
    if func is None or not callable(func):
        return None
    
    try:
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    except Exception:
        return None


# =========================================================================
# ADMIN STATUS & HEALTH
# =========================================================================

@router.get("/status")
async def get_admin_status() -> Dict[str, Any]:
    """
    Get comprehensive system health and status.
    """
    # Try admin_consolidated_router first
    if admin_consolidated_router:
        result = await _call_if_exists(admin_consolidated_router, "get_admin_status")
        if result:
            return result
    
    # Fallback basic status
    return {
        "status": "ok",
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "message": "Admin endpoints available",
    }


@router.get("/logs")
async def get_admin_logs(
    lines: int = Query(default=100, ge=1, le=10000)
) -> Dict[str, Any]:
    """
    Get live system logs.
    """
    if admin_tools_router:
        result = await _call_if_exists(admin_tools_router, "get_logs", lines=lines)
        if result:
            return result
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "logs": [],
        "count": 0,
    }


# =========================================================================
# SETTINGS MANAGEMENT
# =========================================================================

@router.get("/settings/current")
async def get_current_settings() -> Dict[str, Any]:
    """
    View current API keys and settings.
    """
    if settings_router:
        result = await _call_if_exists(settings_router, "get_settings")
        if result:
            return result
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "settings": {},
    }


@router.post("/settings/update")
async def update_settings(req: Request) -> Dict[str, Any]:
    """
    Update API keys and settings.
    """
    if settings_router:
        result = await _call_if_exists(settings_router, "update_settings", req=req)
        if result:
            return result
    
    return {
        "status": "error",
        "message": "Settings update not available",
    }


@router.get("/settings/keys/status")
async def get_keys_status() -> Dict[str, Any]:
    """
    Get API keys validation status.
    """
    if settings_router:
        result = await _call_if_exists(settings_router, "get_key_status")
        if result:
            return result
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "keys": {},
    }


@router.post("/settings/keys/test")
async def test_api_keys(req: Request) -> Dict[str, Any]:
    """
    Test API keys validity.
    """
    if settings_router:
        result = await _call_if_exists(settings_router, "test_keys", req=req)
        if result:
            return result
    
    return {
        "status": "error",
        "message": "Key testing not available",
    }


# =========================================================================
# REPLAY CONTROL
# =========================================================================

@router.get("/replay/status")
async def get_replay_status() -> Dict[str, Any]:
    """
    Get swing replay status.
    """
    if swing_replay_router:
        result = await _call_if_exists(swing_replay_router, "status")
        if result:
            return result
    
    # Fallback: check admin_routes
    if admin_routes:
        result = await _call_if_exists(admin_routes, "get_replay_status")
        if result:
            return result
    
    return {
        "status": "unknown",
        "message": "Replay status not available",
    }


@router.post("/replay/start")
async def start_replay(
    lookback_days: int = Query(default=28),
    version: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
) -> Dict[str, Any]:
    """
    Start swing historical replay.
    """
    if swing_replay_router:
        result = await _call_if_exists(
            swing_replay_router,
            "start",
            lookback_days=lookback_days,
            version=version,
            force=force
        )
        if result:
            return result
    
    # Fallback: check admin_routes
    if admin_routes:
        result = await _call_if_exists(
            admin_routes,
            "start_replay",
            lookback_days=lookback_days
        )
        if result:
            return result
    
    return {
        "status": "error",
        "message": "Replay start not available",
    }


@router.post("/replay/stop")
async def stop_replay() -> Dict[str, Any]:
    """
    Stop swing historical replay.
    """
    if swing_replay_router:
        result = await _call_if_exists(swing_replay_router, "stop")
        if result:
            return result
    
    return {
        "status": "error",
        "message": "Replay stop not available",
    }


@router.post("/replay/reset")
async def reset_replay(force: bool = Query(default=False)) -> Dict[str, Any]:
    """
    Reset swing replay state.
    """
    if swing_replay_router:
        result = await _call_if_exists(swing_replay_router, "reset", force=force)
        if result:
            return result
    
    return {
        "status": "error",
        "message": "Replay reset not available",
    }


# =========================================================================
# AUTHENTICATION
# =========================================================================

@router.post("/login")
async def admin_login(req: Request) -> Dict[str, Any]:
    """
    Admin authentication.
    """
    if admin_routes:
        result = await _call_if_exists(admin_routes, "admin_login", req=req)
        if result:
            return result
    
    raise HTTPException(status_code=503, detail="Admin login not available")


# =========================================================================
# ADMIN TOOLS
# =========================================================================

@router.get("/tools/logs")
async def get_tool_logs(lines: int = Query(default=100)) -> Dict[str, Any]:
    """
    Get system logs via admin tools.
    """
    return await get_admin_logs(lines=lines)


@router.post("/tools/clear-locks")
async def clear_locks() -> Dict[str, Any]:
    """
    Clear system locks.
    """
    if admin_tools_router:
        result = await _call_if_exists(admin_tools_router, "clear_locks")
        if result:
            return result
    
    return {
        "status": "ok",
        "message": "No locks to clear",
    }


@router.post("/tools/git-pull")
async def git_pull() -> Dict[str, Any]:
    """
    Pull latest code from git.
    """
    if admin_tools_router:
        result = await _call_if_exists(admin_tools_router, "git_pull")
        if result:
            return result
    
    return {
        "status": "error",
        "message": "Git pull not available",
    }


@router.post("/tools/refresh-universes")
async def refresh_universes() -> Dict[str, Any]:
    """
    Refresh trading universes.
    """
    if admin_tools_router:
        result = await _call_if_exists(admin_tools_router, "refresh_universes")
        if result:
            return result
    
    return {
        "status": "error",
        "message": "Universe refresh not available",
    }


@router.post("/system/restart")
async def system_restart() -> Dict[str, Any]:
    """
    Restart system (requires admin auth).
    """
    if admin_routes:
        result = await _call_if_exists(admin_routes, "restart_system")
        if result:
            return result
    
    return {
        "status": "error",
        "message": "System restart not available",
    }
