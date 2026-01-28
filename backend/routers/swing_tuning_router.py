"""backend.routers.swing_tuning_router — Swing Tuning API Router

API endpoints for autonomous tuning visibility and control.

Endpoints:
  GET  /api/eod/tuning/{bot_key}/history - Tuning decision history
  GET  /api/eod/tuning/{bot_key}/metrics - Current metrics and Sharpe
  GET  /api/eod/tuning/{bot_key}/outcomes - Recent trade outcomes
  GET  /api/eod/tuning/{bot_key}/calibration - P(hit) calibration table
  POST /api/eod/tuning/{bot_key}/enable - Enable/disable tuning
  POST /api/eod/tuning/{bot_key}/override - Manually override parameters
  POST /api/eod/tuning/{bot_key}/rollback - Rollback to previous config
  POST /api/eod/tuning/run - Manually trigger tuning run
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.swing_outcome_logger import (
    load_recent_outcomes,
    get_outcome_statistics,
    outcomes_path
)
from backend.calibration.phit_calibrator_swing import (
    load_calibration_table,
    _calibration_table_path
)
from backend.tuning.swing_tuning_orchestrator import (
    load_bot_configs,
    save_bot_configs,
    _tuning_history_path,
    TuningOrchestrator
)

try:
    from backend.core.data_pipeline import log  # type: ignore
except Exception:  # pragma: no cover
    def log(msg: str) -> None:  # type: ignore
        print(msg)


router = APIRouter(prefix="/api/eod/tuning", tags=["Swing Tuning"])


# Request/Response models
class TuningEnableRequest(BaseModel):
    """Request to enable/disable tuning."""
    enabled: bool = Field(..., description="Enable tuning")


class ParameterOverrideRequest(BaseModel):
    """Request to override a parameter."""
    parameter: str = Field(..., description="Parameter name")
    value: float = Field(..., description="New value")
    regime: Optional[str] = Field(None, description="Regime (if regime-specific)")


class RollbackRequest(BaseModel):
    """Request to rollback to previous config."""
    steps: int = Field(1, description="Number of tuning steps to rollback", ge=1, le=10)


# ============================================================
# GET Endpoints
# ============================================================

@router.get("/{bot_key}/history")
async def get_tuning_history(
    bot_key: str,
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    regime: Optional[str] = Query(None, description="Filter by regime"),
    parameter: Optional[str] = Query(None, description="Filter by parameter")
) -> Dict[str, Any]:
    """
    Get tuning decision history for a bot.
    
    Args:
        bot_key: Bot identifier (swing_1w, swing_2w, swing_4w)
        limit: Maximum records to return
        regime: Filter by regime
        parameter: Filter by parameter name
    
    Returns:
        {
            "bot_key": str,
            "total_decisions": int,
            "decisions": [...]
        }
    """
    try:
        path = _tuning_history_path()
        
        if not path.exists():
            return {
                "bot_key": bot_key,
                "total_decisions": 0,
                "decisions": []
            }
        
        decisions = []
        
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    decision = json.loads(line)
                    
                    # Filter by bot_key
                    if decision.get("bot_key") != bot_key:
                        continue
                    
                    # Filter by regime if specified
                    if regime and decision.get("regime") != regime:
                        continue
                    
                    # Filter by parameter if specified
                    if parameter and decision.get("parameter") != parameter:
                        continue
                    
                    decisions.append(decision)
                    
                except Exception:
                    continue
        
        # Most recent first
        decisions = sorted(decisions, key=lambda d: d.get("decision_ts", ""), reverse=True)
        
        # Limit results
        decisions = decisions[:limit]
        
        return {
            "bot_key": bot_key,
            "total_decisions": len(decisions),
            "decisions": decisions
        }
        
    except Exception as e:
        log(f"[tuning_router] Error getting history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{bot_key}/metrics")
async def get_tuning_metrics(
    bot_key: str,
    days: int = Query(30, ge=1, le=365, description="Days of data")
) -> Dict[str, Any]:
    """
    Get current metrics and Sharpe ratio for a bot.
    
    Args:
        bot_key: Bot identifier
        days: Days of data to analyze
    
    Returns:
        {
            "bot_key": str,
            "total_trades": int,
            "win_rate": float,
            "avg_return": float,
            "sharpe_ratio": float,
            "avg_hold_hours": float,
            "exit_reasons": {...},
            "regime_breakdown": {...}
        }
    """
    try:
        # Overall statistics
        stats = get_outcome_statistics(bot_key=bot_key, days=days)
        
        # Regime breakdown
        regime_breakdown = {}
        for regime in ["bull", "bear", "chop", "stress"]:
            regime_stats = get_outcome_statistics(
                bot_key=bot_key,
                regime=regime,
                days=days
            )
            if regime_stats["total_trades"] > 0:
                regime_breakdown[regime] = regime_stats
        
        return {
            "bot_key": bot_key,
            "days_analyzed": days,
            **stats,
            "regime_breakdown": regime_breakdown
        }
        
    except Exception as e:
        log(f"[tuning_router] Error getting metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{bot_key}/outcomes")
async def get_recent_outcomes(
    bot_key: str,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=1000)
) -> Dict[str, Any]:
    """
    Get recent trade outcomes for a bot.
    
    Args:
        bot_key: Bot identifier
        days: Days to look back
        limit: Max outcomes to return
    
    Returns:
        {
            "bot_key": str,
            "total_outcomes": int,
            "outcomes": [...]
        }
    """
    try:
        outcomes = load_recent_outcomes(bot_key=bot_key, days=days)
        
        # Most recent first
        outcomes = sorted(outcomes, key=lambda o: o.get("exit_ts", ""), reverse=True)
        
        # Limit
        outcomes = outcomes[:limit]
        
        return {
            "bot_key": bot_key,
            "total_outcomes": len(outcomes),
            "outcomes": outcomes
        }
        
    except Exception as e:
        log(f"[tuning_router] Error getting outcomes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{bot_key}/calibration")
async def get_calibration_table(bot_key: str) -> Dict[str, Any]:
    """
    Get P(hit) calibration table for a bot.
    
    Args:
        bot_key: Bot identifier
    
    Returns:
        {
            "bot_key": str,
            "calibration": {...}
        }
    """
    try:
        table = load_calibration_table()
        bot_calibration = table.get(bot_key, {})
        
        return {
            "bot_key": bot_key,
            "calibration": bot_calibration,
            "total_buckets": len(bot_calibration)
        }
        
    except Exception as e:
        log(f"[tuning_router] Error getting calibration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# POST Endpoints (Control)
# ============================================================

@router.post("/{bot_key}/enable")
async def enable_tuning(
    bot_key: str,
    request: TuningEnableRequest
) -> Dict[str, Any]:
    """
    Enable or disable tuning for a bot.
    
    Args:
        bot_key: Bot identifier
        request: Enable/disable request
    
    Returns:
        {
            "bot_key": str,
            "tuning_enabled": bool,
            "success": bool
        }
    """
    try:
        configs = load_bot_configs()
        
        if bot_key not in configs:
            raise HTTPException(status_code=404, detail=f"Bot {bot_key} not found")
        
        configs[bot_key]["tuning_enabled"] = request.enabled
        
        if save_bot_configs(configs):
            log(f"[tuning_router] Tuning {'enabled' if request.enabled else 'disabled'} for {bot_key}")
            return {
                "bot_key": bot_key,
                "tuning_enabled": request.enabled,
                "success": True
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to save config")
        
    except HTTPException:
        raise
    except Exception as e:
        log(f"[tuning_router] Error enabling tuning: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{bot_key}/override")
async def override_parameter(
    bot_key: str,
    request: ParameterOverrideRequest
) -> Dict[str, Any]:
    """
    Manually override a parameter.
    
    Args:
        bot_key: Bot identifier
        request: Override request
    
    Returns:
        {
            "bot_key": str,
            "parameter": str,
            "new_value": float,
            "success": bool
        }
    """
    try:
        configs = load_bot_configs()
        
        if bot_key not in configs:
            raise HTTPException(status_code=404, detail=f"Bot {bot_key} not found")
        
        # Apply override
        if request.regime:
            # Regime-specific override
            if "regime_overrides" not in configs[bot_key]:
                configs[bot_key]["regime_overrides"] = {}
            if request.regime not in configs[bot_key]["regime_overrides"]:
                configs[bot_key]["regime_overrides"][request.regime] = {}
            configs[bot_key]["regime_overrides"][request.regime][request.parameter] = request.value
        else:
            # Global override
            configs[bot_key][request.parameter] = request.value
        
        if save_bot_configs(configs):
            log(f"[tuning_router] Override applied: {bot_key}.{request.parameter} = {request.value}")
            return {
                "bot_key": bot_key,
                "parameter": request.parameter,
                "new_value": request.value,
                "regime": request.regime,
                "success": True
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to save config")
        
    except HTTPException:
        raise
    except Exception as e:
        log(f"[tuning_router] Error overriding parameter: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{bot_key}/rollback")
async def rollback_tuning(
    bot_key: str,
    request: RollbackRequest
) -> Dict[str, Any]:
    """
    Rollback to previous configuration.
    
    Args:
        bot_key: Bot identifier
        request: Rollback request
    
    Returns:
        {
            "bot_key": str,
            "rolled_back_decisions": int,
            "success": bool
        }
    """
    try:
        # Load tuning history
        path = _tuning_history_path()
        
        if not path.exists():
            raise HTTPException(status_code=404, detail="No tuning history found")
        
        # Find recent decisions for this bot
        decisions = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    decision = json.loads(line)
                    if decision.get("bot_key") == bot_key and decision.get("applied"):
                        decisions.append(decision)
                except Exception:
                    continue
        
        # Sort by timestamp (most recent first)
        decisions = sorted(decisions, key=lambda d: d.get("decision_ts", ""), reverse=True)
        
        if not decisions:
            raise HTTPException(status_code=404, detail="No applied decisions found to rollback")
        
        # Get decisions to rollback
        to_rollback = decisions[:request.steps]
        
        # Load current config
        configs = load_bot_configs()
        
        if bot_key not in configs:
            raise HTTPException(status_code=404, detail=f"Bot {bot_key} not found")
        
        # Revert each decision
        rolled_back = 0
        for decision in to_rollback:
            parameter = decision.get("parameter")
            old_value = decision.get("old_value")
            regime = decision.get("regime")
            
            # Revert to old value
            if regime and "regime_overrides" in configs[bot_key]:
                if regime in configs[bot_key]["regime_overrides"]:
                    configs[bot_key]["regime_overrides"][regime][parameter] = old_value
            else:
                configs[bot_key][parameter] = old_value
            
            rolled_back += 1
            log(f"[tuning_router] Rolled back {bot_key}.{parameter} to {old_value}")
        
        # Save updated config
        if save_bot_configs(configs):
            return {
                "bot_key": bot_key,
                "rolled_back_decisions": rolled_back,
                "success": True
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to save config")
        
    except HTTPException:
        raise
    except Exception as e:
        log(f"[tuning_router] Error rolling back: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
async def manually_run_tuning(
    phase: Optional[str] = Query("full", description="Tuning phase to run")
) -> Dict[str, Any]:
    """
    Manually trigger a tuning run.
    
    Args:
        phase: Tuning phase (logging_only, calibration, threshold, position, exit, full)
    
    Returns:
        Tuning summary
    """
    try:
        orchestrator = TuningOrchestrator(phase=phase)
        result = orchestrator.run_nightly_tuning()
        
        return result
        
    except Exception as e:
        log(f"[tuning_router] Error running tuning: {e}")
        raise HTTPException(status_code=500, detail=str(e))
