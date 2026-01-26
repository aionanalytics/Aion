# backend/routers/bots_router.py
"""
Consolidated Bots Router — AION Analytics

Consolidates bot-related endpoints from:
  - bots_page_router.py
  - bots_hub_router.py
  - eod_bots_router.py

Endpoints:
  - GET /api/bots/page         (unified bundle for UI)
  - GET /api/bots/overview     (aggregated status - alias for page)
  - GET /api/bots/status       (swing + intraday status)
  - GET /api/bots/configs      (all bot configurations)
  - GET /api/bots/signals      (latest signals)
  - GET /api/bots/equity       (portfolio equity)
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter

from backend.core.config import TIMEZONE

# Import existing router modules to delegate functionality
try:
    from backend.routers import bots_page_router
except ImportError:
    bots_page_router = None

try:
    from backend.routers import bots_hub_router
except ImportError:
    bots_hub_router = None

try:
    from backend.routers import eod_bots_router
except ImportError:
    eod_bots_router = None

try:
    from backend.routers import intraday_logs_router
except ImportError:
    intraday_logs_router = None

router = APIRouter(prefix="/api/bots", tags=["bots"])


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
# ENDPOINTS
# =========================================================================

@router.get("/page")
async def get_bots_page() -> Dict[str, Any]:
    """
    Unified bundle for bots page UI.
    
    Returns combined data for swing (EOD) and intraday bots including:
      - status
      - configs  
      - logs
      - signals
      - PnL
    """
    # Try to use existing bots_page_router first
    if bots_page_router:
        result = await _call_if_exists(bots_page_router, "get_bots_page")
        if result:
            return result
    
    # Fallback: aggregate from individual routers
    as_of = datetime.now(TIMEZONE).isoformat()
    
    # Swing/EOD data
    eod_status = await _call_if_exists(eod_bots_router, "eod_status") if eod_bots_router else {}
    eod_configs = await _call_if_exists(eod_bots_router, "list_eod_bot_configs") if eod_bots_router else {}
    eod_days = await _call_if_exists(eod_bots_router, "eod_log_days") if eod_bots_router else []
    
    # Intraday data
    intraday_status = await _call_if_exists(intraday_logs_router, "intraday_status") if intraday_logs_router else {}
    intraday_configs = await _call_if_exists(intraday_logs_router, "intraday_configs") if intraday_logs_router else {}
    intraday_days = await _call_if_exists(intraday_logs_router, "list_log_days") if intraday_logs_router else []
    
    # PnL (optional)
    pnl_last_day = None
    try:
        if intraday_logs_router:
            pnl_last_day = await _call_if_exists(intraday_logs_router, "get_last_day_pnl_summary")
    except Exception:
        pass
    
    # Signals (optional)
    signals_latest = None
    try:
        if intraday_logs_router:
            signals_latest = await _call_if_exists(intraday_logs_router, "get_latest_signals")
    except Exception:
        pass
    
    # Recent fills (optional)
    fills_recent = None
    try:
        if intraday_logs_router:
            fills_recent = await _call_if_exists(intraday_logs_router, "get_recent_fills")
    except Exception:
        pass
    
    return {
        "as_of": as_of,
        "swing": {
            "status": eod_status,
            "configs": eod_configs,
            "log_days": eod_days,
        },
        "intraday": {
            "status": intraday_status,
            "configs": intraday_configs,
            "log_days": intraday_days,
            "pnl_last_day": pnl_last_day,
            "signals_latest": signals_latest,
            "fills_recent": fills_recent,
        },
    }


@router.get("/overview")
async def get_bots_overview() -> Dict[str, Any]:
    """
    Aggregated bot status overview (alias for /page).
    """
    # Try bots_hub_router first
    if bots_hub_router:
        result = await _call_if_exists(bots_hub_router, "bots_overview")
        if result:
            return result
    
    # Fallback to page endpoint
    return await get_bots_page()


@router.get("/status")
async def get_bots_status() -> Dict[str, Any]:
    """
    Get status for all bots (swing + intraday).
    """
    eod_status = await _call_if_exists(eod_bots_router, "eod_status") if eod_bots_router else {}
    intraday_status = await _call_if_exists(intraday_logs_router, "intraday_status") if intraday_logs_router else {}
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "swing": eod_status,
        "intraday": intraday_status,
    }


@router.get("/configs")
async def get_bots_configs() -> Dict[str, Any]:
    """
    Get configurations for all bots.
    """
    eod_configs = await _call_if_exists(eod_bots_router, "list_eod_bot_configs") if eod_bots_router else {}
    intraday_configs = await _call_if_exists(intraday_logs_router, "intraday_configs") if intraday_logs_router else {}
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "swing": eod_configs,
        "intraday": intraday_configs,
    }


@router.get("/signals")
async def get_bots_signals() -> Dict[str, Any]:
    """
    Get latest signals from all bots.
    """
    signals = None
    try:
        if intraday_logs_router:
            signals = await _call_if_exists(intraday_logs_router, "get_latest_signals")
    except Exception:
        pass
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "signals": signals or [],
    }


@router.get("/equity")
async def get_bots_equity() -> Dict[str, Any]:
    """
    Get portfolio equity from all bots.
    """
    eod_status = await _call_if_exists(eod_bots_router, "eod_status") if eod_bots_router else {}
    
    # Extract equity from EOD bot status
    equity_data = {}
    if isinstance(eod_status, dict) and "bots" in eod_status:
        for bot_name, bot_data in eod_status.get("bots", {}).items():
            if isinstance(bot_data, dict) and "equity" in bot_data:
                equity_data[bot_name] = bot_data["equity"]
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "equity": equity_data,
    }
