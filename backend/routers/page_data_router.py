# backend/routers/page_data_router.py
"""
Page Data Router — AION Analytics

Consolidated router for all page-specific data needs.
Replaces multiple fragmented routers with single endpoints per page.

Endpoints:
- GET /api/page/bots → {status, configs, signals, equity}
- GET /api/page/profile → {predictions, holdings, equity_curve}
- GET /api/page/dashboard → {metrics, health, accuracy}
- GET /api/page/predict → {predictions_by_horizon, signals}
- GET /api/page/tools → {system_status, logs}
"""

from __future__ import annotations

import gzip
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

try:
    from utils.logger import log
except ImportError:
    # Fallback if utils.logger is not available
    def log(msg: str) -> None:
        print(msg)

router = APIRouter(prefix="/api/page", tags=["page-data"])


# -------------------------
# Helper Functions
# -------------------------

def _load_gz_json(path: Path) -> Optional[Dict[str, Any]]:
    """Load gzipped JSON file."""
    try:
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    """Load plain JSON file."""
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _error_response(error: str, details: Optional[str] = None) -> Dict[str, Any]:
    """Create error response dict."""
    return {
        "error": error,
        "details": details,
        "timestamp": datetime.now(TIMEZONE).isoformat(),
    }


def _extract_signals_from_predictions(predictions: list, max_signals: int = 20) -> list:
    """
    Extract trading signals from predictions.
    
    Args:
        predictions: List of prediction dicts with symbol, prediction, confidence, last_price
        max_signals: Maximum number of signals to return
        
    Returns:
        List of signal dicts with symbol, action, confidence, price
    """
    return [
        {
            "symbol": p.get("symbol"),
            "action": "BUY" if p.get("prediction", 0) > 0 else "SELL",
            "confidence": p.get("confidence", 0),
            "price": p.get("last_price", 0),
        }
        for p in predictions[:max_signals]
    ]


def _populate_result_with_predictions(result: Dict[str, Any], predictions: list) -> None:
    """
    Populate result dict with predictions and signals.
    
    Args:
        result: Result dict to populate (modified in place)
        predictions: List of prediction dicts
    """
    result["predictions_by_horizon"]["1d"] = predictions[:100]
    result["predictions_by_horizon"]["1w"] = predictions[:50]
    result["predictions_by_horizon"]["1m"] = predictions[:30]
    result["signals"] = _extract_signals_from_predictions(predictions, max_signals=20)


# -------------------------
# Bots Page Endpoint
# -------------------------

@router.get("/bots")
async def get_bots_page_data() -> Dict[str, Any]:
    """
    Get all data needed for /bots page in one call.
    
    Returns:
        {
            "swing": {
                "status": {...},
                "configs": {...},
                "equity": {...}
            },
            "intraday": {
                "status": {...},
                "configs": {...},
                "signals": [...],
                "pnl": {...}
            },
            "timestamp": "ISO timestamp"
        }
    """
    try:
        # Leverage existing bots_page_router logic
        from backend.routers.bots_page_router import bots_page_bundle
        import inspect
        
        result = bots_page_bundle()
        
        # Handle async if needed
        if inspect.isawaitable(result):
            import asyncio
            try:
                result = await result
            except Exception:
                result = {}
        
        if not isinstance(result, dict):
            result = {}
        
        # Add timestamp
        result["timestamp"] = datetime.now(TIMEZONE).isoformat()
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to load bots page data: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Profile Page Endpoint
# -------------------------

@router.get("/profile")
async def get_profile_page_data() -> Dict[str, Any]:
    """
    Get all data needed for /profile page in one call.
    
    Merges portfolio and profile functionality.
    
    Returns:
        {
            "predictions": [...],
            "holdings": [...],
            "equity_curve": [...],
            "bots": {...},
            "summary": {
                "total_equity": float,
                "active_bots": int,
                "total_positions": int
            },
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "predictions": [],
            "holdings": [],
            "equity_curve": [],
            "bots": {},
            "summary": {
                "total_equity": 0,
                "active_bots": 0,
                "total_positions": 0,
            },
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # 1. Load optimized predictions
        da_brains = Path(PATHS.get("da_brains", "da_brains"))
        rolling_opt = da_brains / "rolling_optimized.json.gz"
        if rolling_opt.exists():
            opt_data = _load_gz_json(rolling_opt)
            if opt_data:
                result["predictions"] = opt_data.get("predictions", [])[:50]  # Top 50
        
        # 2. Load portfolio snapshot
        portfolio_snap = da_brains / "portfolio_snapshot.json.gz"
        if portfolio_snap.exists():
            port_data = _load_gz_json(portfolio_snap)
            if port_data:
                result["holdings"] = port_data.get("holdings", [])
        
        # 3. Load bots snapshot
        bots_snap = da_brains / "bots_snapshot.json.gz"
        if bots_snap.exists():
            bots_data = _load_gz_json(bots_snap)
            if bots_data:
                result["bots"] = bots_data.get("bots", {})
                
                # Calculate summary
                active_bots = sum(1 for bot in result["bots"].values() if bot.get("enabled"))
                total_equity = sum(bot.get("equity", 0) for bot in result["bots"].values() if bot.get("enabled"))
                total_positions = sum(bot.get("positions", 0) for bot in result["bots"].values() if bot.get("enabled"))
                
                result["summary"] = {
                    "total_equity": total_equity,
                    "active_bots": active_bots,
                    "total_positions": total_positions,
                }
        
        # 4. Build equity curve from bot data
        # For now, create a simple curve from current values
        # TODO: Load historical equity data if available
        if result["summary"]["total_equity"] > 0:
            result["equity_curve"] = [{
                "t": datetime.now(TIMEZONE).isoformat(),
                "value": result["summary"]["total_equity"],
            }]
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to load profile page data: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Dashboard Page Endpoint
# -------------------------

@router.get("/dashboard")
async def get_dashboard_page_data() -> Dict[str, Any]:
    """
    Get all data needed for /dashboard page in one call.
    
    Returns:
        {
            "metrics": {...},
            "health": {...},
            "accuracy": {...},
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "metrics": {},
            "health": {},
            "accuracy": {},
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Try to use existing dashboard router
        try:
            from backend.routers.dashboard_router import get_dashboard_overview
            import inspect
            
            dashboard_data = get_dashboard_overview()
            if inspect.isawaitable(dashboard_data):
                dashboard_data = await dashboard_data
            
            if isinstance(dashboard_data, dict):
                result.update(dashboard_data)
        except Exception:
            pass
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to load dashboard page data: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Predict Page Endpoint
# -------------------------

@router.get("/predict")
async def get_predict_page_data() -> Dict[str, Any]:
    """
    Get all data needed for /predict page in one call.
    
    Returns:
        {
            "predictions_by_horizon": {
                "1d": [...],
                "1w": [...],
                "1m": [...]
            },
            "signals": [...],
            "timestamp": "ISO timestamp"
        }
    
    Tries multiple sources in order:
    1. rolling_optimized.json.gz (pre-optimized)
    2. latest_predictions.json (nightly logs)
    3. rolling.json.gz (raw data fallback)
    """
    result = {
        "predictions_by_horizon": {
            "1d": [],
            "1w": [],
            "1m": [],
        },
        "signals": [],
        "timestamp": datetime.now(TIMEZONE).isoformat(),
    }
    
    # Try 1: rolling_optimized.json.gz (fastest, pre-optimized for frontend)
    try:
        da_brains = Path(PATHS.get("da_brains", "da_brains"))
        rolling_opt = da_brains / "rolling_optimized.json.gz"
        
        if rolling_opt.exists():
            opt_data = _load_gz_json(rolling_opt)
            if opt_data and isinstance(opt_data, dict):
                predictions = opt_data.get("predictions", [])
                if isinstance(predictions, list) and predictions:
                    _populate_result_with_predictions(result, predictions)
                    return result
    except Exception as e:
        log(f"[page_data] Warning: rolling_optimized failed: {e}")
    
    # Try 2: latest_predictions.json (from nightly logs, UI-ready format)
    try:
        log_dir = Path(PATHS.get("logs", "logs")) / "nightly" / "predictions"
        latest_pred = log_dir / "latest_predictions.json"
        
        if latest_pred.exists():
            pred_data = _load_json(latest_pred)
            if pred_data and isinstance(pred_data, dict):
                symbols = pred_data.get("symbols", {})
                if isinstance(symbols, dict) and symbols:
                    predictions = []
                    
                    for sym, node in symbols.items():
                        if isinstance(node, dict):
                            preds = node.get("predictions", {})
                            if isinstance(preds, dict):
                                h1w = preds.get("1w", {})
                                confidence = h1w.get("confidence", 0)
                                
                                # Only include if has valid confidence
                                if confidence > 0:
                                    predictions.append({
                                        "symbol": sym,
                                        "confidence": confidence,
                                        "prediction": h1w.get("predicted_return", 0),
                                        "last_price": node.get("price"),
                                        "name": node.get("name"),
                                        "sector": node.get("sector"),
                                    })
                    
                    if predictions:
                        # Sort by confidence descending
                        predictions.sort(key=lambda x: x.get("confidence", 0), reverse=True)
                        _populate_result_with_predictions(result, predictions)
                        return result
    except Exception as e:
        log(f"[page_data] Warning: latest_predictions.json failed: {e}")
    
    # Try 3: rolling.json.gz directly (raw data fallback)
    try:
        from backend.core.data_pipeline import _read_rolling
        
        rolling = _read_rolling()
        if rolling and isinstance(rolling, dict):
            predictions = []
            
            for sym, node in rolling.items():
                if not str(sym).startswith("_") and isinstance(node, dict):
                    preds = node.get("predictions", {})
                    if isinstance(preds, dict):
                        h1w = preds.get("1w", {})
                        confidence = h1w.get("confidence", 0)
                        
                        # Only include if has valid confidence and price
                        if confidence > 0 and node.get("price"):
                            predictions.append({
                                "symbol": sym,
                                "confidence": confidence,
                                "prediction": h1w.get("predicted_return", 0),
                                "last_price": node.get("price"),
                                "name": node.get("name"),
                                "sector": node.get("sector"),
                            })
            
            if predictions:
                # Sort by confidence descending
                predictions.sort(key=lambda x: x.get("confidence", 0), reverse=True)
                _populate_result_with_predictions(result, predictions)
                return result
    except Exception as e:
        log(f"[page_data] Warning: rolling.json.gz fallback failed: {e}")
    
    # Return empty result if all sources fail (graceful degradation)
    return result


# -------------------------
# Tools Page Endpoint
# -------------------------

@router.get("/tools")
async def get_tools_page_data() -> Dict[str, Any]:
    """
    Get system status and log data for tools pages.
    
    Returns:
        {
            "system_status": {...},
            "logs": {...},
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "system_status": {},
            "logs": {},
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Try to use existing system status router
        try:
            from backend.routers.system_router import get_system_status
            import inspect
            
            status_data = get_system_status()
            if inspect.isawaitable(status_data):
                status_data = await status_data
            
            if isinstance(status_data, dict):
                result["system_status"] = status_data
        except Exception:
            pass
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to load tools page data: {type(e).__name__}",
            str(e)
        )
