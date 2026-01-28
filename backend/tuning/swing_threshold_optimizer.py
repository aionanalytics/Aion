"""backend.tuning.swing_threshold_optimizer — Confidence Threshold Optimizer

Analyzes trade outcomes to find optimal confidence threshold per regime.

Algorithm:
1. Load recent trade outcomes (30-day window by default)
2. Group trades by regime
3. For each regime, test confidence thresholds from 0.40 to 0.75
4. Calculate Sharpe ratio at each threshold
5. Find threshold that maximizes Sharpe
6. Validate improvement ≥5% with statistical significance
7. Apply if validation passes

Environment Variables:
  SWING_TUNING_WINDOW_DAYS (default: 30)
  SWING_THRESHOLD_TEST_STEP (default: 0.05)
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Tuple

from backend.services.swing_outcome_logger import load_recent_outcomes
from backend.tuning.swing_tuning_validator import TuningValidator, ValidationResult

try:
    from backend.core.data_pipeline import log  # type: ignore
except Exception:  # pragma: no cover
    def log(msg: str) -> None:  # type: ignore
        print(msg)


def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.getenv(name, "") or "").strip()
        return int(float(raw)) if raw else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.getenv(name, "") or "").strip()
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


class ThresholdOptimizer:
    """Optimizes confidence threshold based on trade outcomes."""
    
    def __init__(
        self,
        window_days: Optional[int] = None,
        test_step: Optional[float] = None,
        validator: Optional[TuningValidator] = None
    ):
        """
        Initialize threshold optimizer.
        
        Args:
            window_days: Days of outcomes to analyze
            test_step: Step size for threshold testing
            validator: TuningValidator instance for safety checks
        """
        self.window_days = window_days or _env_int("SWING_TUNING_WINDOW_DAYS", 30)
        self.test_step = test_step or _env_float("SWING_THRESHOLD_TEST_STEP", 0.05)
        self.validator = validator or TuningValidator()
    
    def _calculate_sharpe_at_threshold(
        self,
        outcomes: List[Dict[str, Any]],
        threshold: float
    ) -> Tuple[float, List[float]]:
        """
        Calculate Sharpe ratio if we had used this threshold.
        
        Simulates: only take trades with entry_confidence >= threshold
        
        Args:
            outcomes: List of trade outcomes
            threshold: Confidence threshold to test
        
        Returns:
            Tuple of (sharpe_ratio, returns_list)
        """
        # Filter to trades that would have been taken
        filtered_outcomes = [
            o for o in outcomes
            if o.get("entry_confidence", 0.0) >= threshold
        ]
        
        if not filtered_outcomes:
            return (0.0, [])
        
        # Extract returns
        returns = [o.get("actual_return", 0.0) for o in filtered_outcomes]
        
        # Calculate Sharpe
        sharpe = self.validator.calculate_sharpe_ratio(returns, annualize=True)
        
        return (sharpe, returns)
    
    def optimize_threshold_for_regime(
        self,
        bot_key: str,
        regime: str,
        current_threshold: float
    ) -> Optional[Dict[str, Any]]:
        """
        Find optimal confidence threshold for a regime.
        
        Args:
            bot_key: Bot identifier (swing_1w, swing_2w, swing_4w)
            regime: Market regime (bull/bear/chop/stress)
            current_threshold: Current confidence threshold
        
        Returns:
            Dictionary with optimization results or None if no improvement
            {
                "new_threshold": float,
                "old_sharpe": float,
                "new_sharpe": float,
                "improvement_pct": float,
                "trades_analyzed": int,
                "validation": ValidationResult
            }
        """
        try:
            # Load recent outcomes for this bot and regime
            all_outcomes = load_recent_outcomes(bot_key=bot_key, days=self.window_days)
            regime_outcomes = [
                o for o in all_outcomes
                if o.get("regime_entry") == regime
            ]
            
            if not regime_outcomes:
                log(f"[threshold_optimizer] No outcomes for {bot_key} regime {regime}")
                return None
            
            # Check if we have sufficient data
            data_check = self.validator.validate_sufficient_data(len(regime_outcomes))
            if not data_check.approved:
                log(f"[threshold_optimizer] {data_check.reason}")
                return None
            
            # Calculate current Sharpe
            current_sharpe, current_returns = self._calculate_sharpe_at_threshold(
                regime_outcomes, current_threshold
            )
            
            log(f"[threshold_optimizer] {bot_key} {regime}: current threshold={current_threshold:.2f}, Sharpe={current_sharpe:.2f}")
            
            # Test thresholds from 0.40 to 0.75 in steps
            best_threshold = current_threshold
            best_sharpe = current_sharpe
            best_returns = current_returns
            
            test_min = 0.40
            test_max = 0.75
            
            # Regime-specific bounds
            if regime == "bear":
                test_min = 0.35
                test_max = 0.70
            elif regime == "stress":
                test_min = 0.30
                test_max = 0.65
            
            threshold = test_min
            while threshold <= test_max:
                sharpe, returns = self._calculate_sharpe_at_threshold(
                    regime_outcomes, threshold
                )
                
                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_threshold = threshold
                    best_returns = returns
                
                threshold += self.test_step
            
            # Round to 2 decimals
            best_threshold = round(best_threshold, 2)
            
            # Validate the proposed change
            validation = self.validator.validate_parameter_change(
                parameter="conf_threshold",
                old_value=current_threshold,
                new_value=best_threshold,
                old_sharpe=current_sharpe,
                new_sharpe=best_sharpe,
                trades_count=len(regime_outcomes),
                returns_old=current_returns,
                returns_new=best_returns
            )
            
            # Additional regime-specific validation
            if validation.approved:
                regime_validation = self.validator.validate_regime_specific(
                    regime=regime,
                    parameter="conf_threshold",
                    new_value=best_threshold
                )
                if not regime_validation.approved:
                    validation = regime_validation
            
            if validation.approved:
                improvement_pct = validation.sharpe_improvement_pct or 0.0
                log(f"[threshold_optimizer] ✓ {bot_key} {regime}: {current_threshold:.2f} → {best_threshold:.2f} "
                    f"(Sharpe {current_sharpe:.2f} → {best_sharpe:.2f}, +{improvement_pct:.1%})")
            else:
                log(f"[threshold_optimizer] ✗ {bot_key} {regime}: {validation.reason}")
            
            return {
                "new_threshold": best_threshold,
                "old_threshold": current_threshold,
                "old_sharpe": current_sharpe,
                "new_sharpe": best_sharpe,
                "improvement_pct": validation.sharpe_improvement_pct or 0.0,
                "trades_analyzed": len(regime_outcomes),
                "validation": validation
            }
            
        except Exception as e:
            log(f"[threshold_optimizer] Error optimizing threshold: {e}")
            return None
    
    def optimize_all_regimes(
        self,
        bot_key: str,
        current_thresholds: Dict[str, float]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Optimize thresholds for all regimes.
        
        Args:
            bot_key: Bot identifier
            current_thresholds: Dict mapping regime -> current threshold
        
        Returns:
            Dict mapping regime -> optimization result
        """
        results = {}
        
        for regime in ["bull", "bear", "chop", "stress"]:
            current_threshold = current_thresholds.get(regime, 0.55)
            result = self.optimize_threshold_for_regime(
                bot_key=bot_key,
                regime=regime,
                current_threshold=current_threshold
            )
            results[regime] = result
        
        return results


def optimize_confidence_threshold(
    bot_key: str,
    regime: str,
    current_threshold: float,
    window_days: int = 30
) -> Optional[float]:
    """
    Convenience function to optimize threshold for a single regime.
    
    Args:
        bot_key: Bot identifier
        regime: Market regime
        current_threshold: Current threshold
        window_days: Days of data to analyze
    
    Returns:
        New threshold if improvement found, None otherwise
    """
    optimizer = ThresholdOptimizer(window_days=window_days)
    result = optimizer.optimize_threshold_for_regime(
        bot_key=bot_key,
        regime=regime,
        current_threshold=current_threshold
    )
    
    if result and result["validation"].approved:
        return result["new_threshold"]
    
    return None
