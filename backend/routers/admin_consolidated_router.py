# backend/routers/admin_consolidated_router.py
"""
Admin Consolidated Router — AION Analytics

Consolidated router for all admin/system operations.
Replaces multiple admin-related routers with unified endpoints.

Endpoints:
- GET /api/admin/status → system health
- GET /api/admin/logs → live logs
- POST /api/admin/action/{action} → system actions (restart, stop, etc.)
- GET /api/admin/replay/{backend}/status → replay status
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException

try:
    from backend.core.config import PATHS, TIMEZONE
except ImportError:
    from backend.config import PATHS, TIMEZONE  # type: ignore

router = APIRouter(prefix="/api/admin", tags=["admin"])


# -------------------------
# Helper Functions
# -------------------------

def _error_response(error: str, details: Optional[str] = None) -> Dict[str, Any]:
    """Create error response dict."""
    return {
        "error": error,
        "details": details,
        "timestamp": datetime.now(TIMEZONE).isoformat(),
    }


# -------------------------
# System Status Endpoint
# -------------------------

@router.get("/status")
async def get_admin_status() -> Dict[str, Any]:
    """
    Get comprehensive system health and status.
    
    Returns:
        {
            "status": "ok" | "degraded" | "error",
            "services": {
                "backend": {...},
                "scheduler": {...},
                "cache": {...}
            },
            "resources": {
                "disk": {...},
                "memory": {...}
            },
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "status": "ok",
            "services": {},
            "resources": {},
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Try to use existing system status router
        try:
            from backend.routers.system_status_router import get_system_status
            import inspect
            
            status_data = get_system_status()
            if inspect.isawaitable(status_data):
                status_data = await status_data
            
            if isinstance(status_data, dict):
                result.update(status_data)
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to get admin status: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Logs Endpoint
# -------------------------

@router.get("/logs")
async def get_admin_logs(
    log_type: str = "backend",
    lines: int = 100,
) -> Dict[str, Any]:
    """
    Get live logs from various services.
    
    Args:
        log_type: Type of logs (backend, nightly, scheduler, intraday)
        lines: Number of lines to return
    
    Returns:
        {
            "logs": [
                {"timestamp": "...", "level": "...", "message": "..."},
                ...
            ],
            "log_type": "backend",
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "logs": [],
            "log_type": log_type,
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        logs_root = Path(PATHS.get("logs", "logs"))
        
        # Map log types to paths
        log_paths = {
            "backend": logs_root / "backend",
            "nightly": logs_root / "nightly",
            "scheduler": logs_root / "scheduler",
            "intraday": logs_root / "intraday",
        }
        
        log_dir = log_paths.get(log_type)
        if not log_dir or not log_dir.exists():
            return result
        
        # Get most recent log file
        log_files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not log_files:
            return result
        
        # Read last N lines
        log_file = log_files[0]
        try:
            with log_file.open("r", encoding="utf-8") as f:
                all_lines = f.readlines()
                last_lines = all_lines[-lines:]
                
                for line in last_lines:
                    line = line.strip()
                    if line:
                        result["logs"].append({
                            "message": line,
                            "raw": line,
                        })
        except Exception:
            pass
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to get logs: {type(e).__name__}",
            str(e)
        )


# -------------------------
# System Actions Endpoint
# -------------------------

@router.post("/action/{action}")
async def execute_admin_action(action: str) -> Dict[str, Any]:
    """
    Execute admin system actions.
    
    Args:
        action: Action to execute (restart, stop, clear_cache, etc.)
    
    Returns:
        {
            "status": "ok" | "error",
            "action": "restart",
            "message": "...",
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "status": "ok",
            "action": action,
            "message": "",
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Map actions to implementations
        if action == "clear_cache":
            # Clear unified cache
            try:
                from backend.services.unified_cache_service import UnifiedCacheService
                service = UnifiedCacheService()
                cache_file = service.cache_file
                if cache_file.exists():
                    cache_file.unlink()
                result["message"] = "Cache cleared successfully"
            except Exception as e:
                result["status"] = "error"
                result["message"] = f"Failed to clear cache: {e}"
        
        elif action == "refresh_cache":
            # Refresh unified cache
            try:
                from backend.services.unified_cache_service import UnifiedCacheService
                service = UnifiedCacheService()
                service.update_all()
                result["message"] = "Cache refreshed successfully"
            except Exception as e:
                result["status"] = "error"
                result["message"] = f"Failed to refresh cache: {e}"
        
        elif action == "optimize_rolling":
            # Run rolling optimizer
            try:
                from backend.services.rolling_optimizer import optimize_rolling_data
                opt_result = optimize_rolling_data()
                result["message"] = f"Rolling optimization complete: {opt_result.get('status')}"
                result["details"] = opt_result.get("stats", {})
            except Exception as e:
                result["status"] = "error"
                result["message"] = f"Failed to optimize rolling: {e}"
        
        else:
            result["status"] = "error"
            result["message"] = f"Unknown action: {action}"
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to execute action: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Replay Status Endpoints
# -------------------------

@router.get("/replay/{backend_type}/status")
async def get_replay_status(backend_type: str) -> Dict[str, Any]:
    """
    Get replay status for a specific backend.
    
    Args:
        backend_type: Backend type (swing, intraday, dt)
    
    Returns:
        {
            "backend": "swing",
            "status": {...},
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "backend": backend_type,
            "status": {},
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Try to use existing replay router
        if backend_type == "swing":
            try:
                from backend.routers.swing_replay_router import get_swing_replay_status
                import inspect
                
                status_data = get_swing_replay_status()
                if inspect.isawaitable(status_data):
                    status_data = await status_data
                
                if isinstance(status_data, dict):
                    result["status"] = status_data
            except Exception:
                pass
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to get replay status: {type(e).__name__}",
            str(e)
        )


# -------------------------
# System Metrics Endpoint
# -------------------------

@router.get("/metrics")
async def get_admin_metrics() -> Dict[str, Any]:
    """
    Get system performance metrics.
    
    Returns:
        {
            "metrics": {...},
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "metrics": {},
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Try to use existing metrics router
        try:
            from backend.routers.metrics_router import get_metrics_summary
            import inspect
            
            metrics_data = get_metrics_summary()
            if inspect.isawaitable(metrics_data):
                metrics_data = await metrics_data
            
            if isinstance(metrics_data, dict):
                result["metrics"] = metrics_data
        except Exception:
            pass
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to get metrics: {type(e).__name__}",
            str(e)
        )
