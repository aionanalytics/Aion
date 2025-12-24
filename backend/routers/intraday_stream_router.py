# backend/routers/intraday_router.py
"""
Intraday API router.

Endpoints:
    GET /api/intraday/snapshot
    GET /api/intraday/symbol/{symbol}
    GET /api/intraday/top/{side}
    POST /api/intraday/refresh
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.intraday_service import (
    get_intraday_snapshot,
    get_symbol_view,
    get_top_signals,
)

# FIXED: this import must not sit inside another function definition
from backend.intraday_runner import run_intraday_cycle

router = APIRouter(prefix="/api/intraday", tags=["intraday"])


# ------------------------------------------------------------
# /snapshot
# ------------------------------------------------------------
@router.get("/snapshot")
def api_intraday_snapshot(limit: int = Query(50, ge=1, le=200)):
    return get_intraday_snapshot(limit=limit)


# ------------------------------------------------------------
# /symbol/{symbol}
# ------------------------------------------------------------
@router.get("/symbol/{symbol}")
def api_intraday_symbol(symbol: str):
    view = get_symbol_view(symbol)
    if view is None:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found in intraday rolling.")
    return view


# ------------------------------------------------------------
# /top/{side}
# ------------------------------------------------------------
@router.get("/top/{side}")
def api_intraday_top(
    side: str,
    limit: int = Query(50, ge=1, le=200),
    min_conf: float = Query(0.20, ge=0.0, le=1.0),
):
    side = side.upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")

    rows = get_top_signals(side=side, limit=limit, min_conf=min_conf)
    return {
        "side": side,
        "limit": limit,
        "min_conf": min_conf,
        "count": len(rows),
        "results": rows,
    }


# ------------------------------------------------------------
# /refresh
# ------------------------------------------------------------
@router.post("/refresh")
def api_intraday_refresh():
    """
    Run full intraday cycle:
      • context
      • features
      • scoring
      • policy
      • execution
    """
    return run_intraday_cycle()
