"""backend.tuning.swing_position_tuner — Position Sizing Optimizer

Optimizes position sizing parameters based on trade outcomes.

Parameters optimized:
- starter_fraction: Initial entry size (25-50% of goal)
- add_fraction: Add-on size (20-50% of goal)
- max_weight_per_name: Max concentration per symbol (5-25%)

Algorithm:
1. Load recent outcomes with position sizing data
2. Analyze position survival rates and drawdowns
3. Test different sizing parameters
4. Calculate Sharpe ratio for each combination
5. Find optimal settings
6. Validate ≥5% improvement
"""

from __future__ import annotations

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


class PositionTuner:
    """Optimizes position sizing parameters."""
    
    def __init__(
        self,
        window_days: Optional[int] = None,
        validator: Optional[TuningValidator] = None
    ):
        """
        Initialize position tuner.
        
        Args:
            window_days: Days of outcomes to analyze
            validator: TuningValidator for safety checks
        """
        self.window_days = window_days or _env_int("SWING_TUNING_WINDOW_DAYS", 30)
        self.validator = validator or TuningValidator()
    
    def _simulate_returns_with_sizing(
        self,
        outcomes: List[Dict[str, Any]],
        starter_fraction: float,
        add_fraction: float,
        max_weight: float
    ) -> List[float]:
        """
        Simulate returns if we had used different position sizing.
        
        This is a simplified simulation that adjusts returns based on
        how position size would have been different.
        
        Args:
            outcomes: Trade outcomes
            starter_fraction: Starter position fraction
            add_fraction: Add position fraction
            max_weight: Maximum weight per name
        
        Returns:
            List of adjusted returns
        """
        adjusted_returns = []
        
        for outcome in outcomes:
            actual_return = outcome.get("actual_return", 0.0)
            
            # Get actual position size if available
            actual_size = outcome.get("position_size_pct")
            
            # If we don't have actual position size, use current return as-is
            if actual_size is None:
                adjusted_returns.append(actual_return)
                continue
            
            # Simple adjustment: scale return by size ratio
            # This is a heuristic - in reality sizing affects risk/reward differently
            target_size = min(starter_fraction, max_weight)
            
            if actual_size > 0:
                size_ratio = target_size / actual_size
                # Limit adjustment to avoid extreme values
                size_ratio = max(0.5, min(2.0, size_ratio))
                adjusted_return = actual_return * size_ratio
            else:
                adjusted_return = actual_return
            
            adjusted_returns.append(adjusted_return)
        
        return adjusted_returns
    
    def optimize_starter_fraction(
        self,
        bot_key: str,
        regime: str,
        current_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Optimize starter_fraction parameter.
        
        Args:
            bot_key: Bot identifier
            regime: Market regime
            current_value: Current starter_fraction
        
        Returns:
            Optimization result or None
        """
        try:
            # Load outcomes
            all_outcomes = load_recent_outcomes(bot_key=bot_key, days=self.window_days)
            regime_outcomes = [
                o for o in all_outcomes
                if o.get("regime_entry") == regime
            ]
            
            if not regime_outcomes:
                return None
            
            # Check sufficient data
            data_check = self.validator.validate_sufficient_data(len(regime_outcomes))
            if not data_check.approved:
                return None
            
            # Calculate current Sharpe
            current_returns = [o.get("actual_return", 0.0) for o in regime_outcomes]
            current_sharpe = self.validator.calculate_sharpe_ratio(current_returns)
            
            # Test different starter fractions
            best_value = current_value
            best_sharpe = current_sharpe
            best_returns = current_returns
            
            # Test range: 0.25 to 0.50 in steps of 0.05
            for test_value in [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
                test_returns = self._simulate_returns_with_sizing(
                    regime_outcomes,
                    starter_fraction=test_value,
                    add_fraction=0.30,  # Keep other params constant
                    max_weight=0.15
                )
                
                test_sharpe = self.validator.calculate_sharpe_ratio(test_returns)
                
                if test_sharpe > best_sharpe:
                    best_sharpe = test_sharpe
                    best_value = test_value
                    best_returns = test_returns
            
            # Validate change
            validation = self.validator.validate_parameter_change(
                parameter="starter_fraction",
                old_value=current_value,
                new_value=best_value,
                old_sharpe=current_sharpe,
                new_sharpe=best_sharpe,
                trades_count=len(regime_outcomes),
                returns_old=current_returns,
                returns_new=best_returns
            )
            
            # Additional bounds check
            if validation.approved:
                regime_validation = self.validator.validate_regime_specific(
                    regime=regime,
                    parameter="starter_fraction",
                    new_value=best_value
                )
                if not regime_validation.approved:
                    validation = regime_validation
            
            if validation.approved:
                log(f"[position_tuner] ✓ {bot_key} {regime}: starter_fraction {current_value:.2f} → {best_value:.2f}")
            else:
                log(f"[position_tuner] ✗ {bot_key} {regime}: {validation.reason}")
            
            return {
                "parameter": "starter_fraction",
                "new_value": best_value,
                "old_value": current_value,
                "old_sharpe": current_sharpe,
                "new_sharpe": best_sharpe,
                "improvement_pct": validation.sharpe_improvement_pct or 0.0,
                "trades_analyzed": len(regime_outcomes),
                "validation": validation
            }
            
        except Exception as e:
            log(f"[position_tuner] Error optimizing starter_fraction: {e}")
            return None
    
    def optimize_max_weight(
        self,
        bot_key: str,
        regime: str,
        current_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Optimize max_weight_per_name parameter.
        
        Args:
            bot_key: Bot identifier
            regime: Market regime
            current_value: Current max_weight_per_name
        
        Returns:
            Optimization result or None
        """
        try:
            # Load outcomes
            all_outcomes = load_recent_outcomes(bot_key=bot_key, days=self.window_days)
            regime_outcomes = [
                o for o in all_outcomes
                if o.get("regime_entry") == regime
            ]
            
            if not regime_outcomes:
                return None
            
            # Check sufficient data
            data_check = self.validator.validate_sufficient_data(len(regime_outcomes))
            if not data_check.approved:
                return None
            
            # Calculate current metrics
            current_returns = [o.get("actual_return", 0.0) for o in regime_outcomes]
            current_sharpe = self.validator.calculate_sharpe_ratio(current_returns)
            
            # Analyze concentration risk
            # Group outcomes by symbol
            symbol_returns = {}
            for outcome in regime_outcomes:
                symbol = outcome.get("symbol", "")
                ret = outcome.get("actual_return", 0.0)
                if symbol not in symbol_returns:
                    symbol_returns[symbol] = []
                symbol_returns[symbol].append(ret)
            
            # Test different max weights
            best_value = current_value
            best_sharpe = current_sharpe
            best_returns = current_returns
            
            # Test range: 0.05 to 0.25 in steps
            for test_value in [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]:
                test_returns = self._simulate_returns_with_sizing(
                    regime_outcomes,
                    starter_fraction=0.35,  # Keep constant
                    add_fraction=0.30,
                    max_weight=test_value
                )
                
                test_sharpe = self.validator.calculate_sharpe_ratio(test_returns)
                
                if test_sharpe > best_sharpe:
                    best_sharpe = test_sharpe
                    best_value = test_value
                    best_returns = test_returns
            
            # Validate
            validation = self.validator.validate_parameter_change(
                parameter="max_weight_per_name",
                old_value=current_value,
                new_value=best_value,
                old_sharpe=current_sharpe,
                new_sharpe=best_sharpe,
                trades_count=len(regime_outcomes),
                returns_old=current_returns,
                returns_new=best_returns
            )
            
            if validation.approved:
                regime_validation = self.validator.validate_regime_specific(
                    regime=regime,
                    parameter="max_weight_per_name",
                    new_value=best_value
                )
                if not regime_validation.approved:
                    validation = regime_validation
            
            if validation.approved:
                log(f"[position_tuner] ✓ {bot_key} {regime}: max_weight {current_value:.2f} → {best_value:.2f}")
            else:
                log(f"[position_tuner] ✗ {bot_key} {regime}: {validation.reason}")
            
            return {
                "parameter": "max_weight_per_name",
                "new_value": best_value,
                "old_value": current_value,
                "old_sharpe": current_sharpe,
                "new_sharpe": best_sharpe,
                "improvement_pct": validation.sharpe_improvement_pct or 0.0,
                "trades_analyzed": len(regime_outcomes),
                "validation": validation
            }
            
        except Exception as e:
            log(f"[position_tuner] Error optimizing max_weight: {e}")
            return None
    
    def optimize_all_sizing(
        self,
        bot_key: str,
        regime: str,
        current_params: Dict[str, float]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Optimize all position sizing parameters.
        
        Args:
            bot_key: Bot identifier
            regime: Market regime
            current_params: Dict with current parameter values
        
        Returns:
            Dict mapping parameter -> optimization result
        """
        results = {}
        
        # Optimize starter_fraction
        if "starter_fraction" in current_params:
            results["starter_fraction"] = self.optimize_starter_fraction(
                bot_key=bot_key,
                regime=regime,
                current_value=current_params["starter_fraction"]
            )
        
        # Optimize max_weight_per_name
        if "max_weight_per_name" in current_params:
            results["max_weight_per_name"] = self.optimize_max_weight(
                bot_key=bot_key,
                regime=regime,
                current_value=current_params["max_weight_per_name"]
            )
        
        return results
