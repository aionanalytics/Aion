# backend/routers/insights_router_consolidated.py
"""
Consolidated Insights Router — AION Analytics

Consolidates insight-related endpoints from:
  - insights_router.py
  - metrics_router.py
  - portfolio_router.py

Endpoints:
  - GET /api/insights/boards/{board}       (top-picks, trending, etc.)
  - GET /api/insights/top-predictions      (highest-confidence signals)
  - GET /api/insights/portfolio            (current holdings)
  - GET /api/insights/metrics              (accuracy, calibration, drift)
"""

from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.core.config import PATHS, TIMEZONE

# Import existing routers to delegate functionality
try:
    from backend.routers import insights_router
except ImportError:
    insights_router = None

try:
    from backend.routers import metrics_router
except ImportError:
    metrics_router = None

try:
    from backend.routers import portfolio_router
except ImportError:
    portfolio_router = None

router = APIRouter(prefix="/api/insights", tags=["insights"])


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

@router.get("/boards/{board}")
async def get_insight_board(
    board: str,
    sector: Optional[str] = Query(default=None),
    minConfidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
) -> Dict[str, Any]:
    """
    Get insight board by name.
    
    Boards: 1w, 2w, 4w, 52w, social, news
    
    Optional filters:
      - sector: Filter by sector
      - minConfidence: Minimum confidence threshold
    """
    if insights_router:
        # Try to call the existing insights router
        try:
            result = await _call_if_exists(
                insights_router,
                "get_board",
                board=board,
                sector=sector,
                minConfidence=minConfidence
            )
            if result:
                return result
        except Exception:
            pass
    
    # Fallback response
    return {
        "board": board,
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "entries": [],
        "error": "Insights board not available",
    }


@router.get("/top-predictions")
async def get_top_predictions(limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    """
    Get highest-confidence predictions across all horizons.
    """
    if insights_router:
        # Try predictions/latest endpoint first
        result = await _call_if_exists(insights_router, "get_latest_predictions")
        if result:
            # Extract and sort by confidence
            predictions = result.get("symbols", {})
            sorted_preds = []
            
            for symbol, data in predictions.items():
                if isinstance(data, dict) and "targets" in data:
                    for horizon, target in data.get("targets", {}).items():
                        if isinstance(target, dict) and "confidence" in target:
                            sorted_preds.append({
                                "symbol": symbol,
                                "name": data.get("name", symbol),
                                "sector": data.get("sector", "Unknown"),
                                "price": data.get("price"),
                                "horizon": horizon,
                                "expected_return": target.get("expected_return"),
                                "confidence": target.get("confidence"),
                            })
            
            # Sort by confidence descending
            sorted_preds.sort(key=lambda x: x.get("confidence", 0), reverse=True)
            
            return {
                "timestamp": datetime.now(TIMEZONE).isoformat(),
                "predictions": sorted_preds[:limit],
                "count": len(sorted_preds[:limit]),
            }
    
    # Fallback
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "predictions": [],
        "count": 0,
    }


@router.get("/portfolio")
async def get_portfolio_holdings(
    horizon: Optional[str] = Query(default=None)
) -> Dict[str, Any]:
    """
    Get current portfolio holdings.
    
    Optional:
      - horizon: Filter by horizon (1w, 1m, 3m, etc.)
    """
    if portfolio_router:
        # Try to get holdings from portfolio router
        if horizon:
            result = await _call_if_exists(
                portfolio_router,
                "get_top_holdings_by_horizon",
                horizon=horizon
            )
        else:
            # Get all holdings
            result = await _call_if_exists(
                portfolio_router,
                "get_all_holdings"
            )
        
        if result:
            return result
    
    # Fallback
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "holdings": [],
        "total_value": 0.0,
    }


@router.get("/metrics")
async def get_insights_metrics() -> Dict[str, Any]:
    """
    Get performance metrics (accuracy, calibration, drift).
    """
    metrics_data = {}
    
    if metrics_router:
        # Get accuracy metrics
        accuracy = await _call_if_exists(metrics_router, "get_accuracy")
        if accuracy:
            metrics_data["accuracy"] = accuracy
        
        # Get top performers
        top_performers = await _call_if_exists(metrics_router, "get_top_performers")
        if top_performers:
            metrics_data["top_performers"] = top_performers
        
        # Get overview if available
        overview = await _call_if_exists(metrics_router, "get_overview")
        if overview:
            metrics_data["overview"] = overview
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        **metrics_data,
    }


# =========================================================================
# BACKWARD COMPATIBILITY ENDPOINTS
# =========================================================================

@router.get("/predictions/latest")
async def get_latest_predictions() -> Dict[str, Any]:
    """
    Get latest prediction feed (backward compatibility).
    
    This is an alias that delegates to the original insights_router.
    """
    if insights_router:
        result = await _call_if_exists(insights_router, "get_latest_predictions")
        if result:
            return result
    
    return {
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "symbols": {},
    }
