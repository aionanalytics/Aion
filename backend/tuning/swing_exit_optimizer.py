"""backend.tuning.swing_exit_optimizer — Exit Strategy Optimizer

Optimizes exit discipline parameters based on trade outcomes.

Parameters optimized:
- stop_loss_pct: Stop loss threshold (-2% to -8%)
- take_profit_pct: Take profit threshold (+5% to +20%)
- min_hold_days: Minimum hold period (1-5 days)
- time_stop_days: Maximum hold period (7-21 days)
- exit_confirmations: Exit signal confirmations (1-3)

Algorithm:
1. Load outcomes and analyze exit reason distribution
2. For each exit parameter, test ranges
3. Simulate what returns would have been with different exits
4. Calculate Sharpe for each setting
5. Find optimal combination
6. Validate improvements
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


class ExitOptimizer:
    """Optimizes exit strategy parameters."""
    
    def __init__(
        self,
        window_days: Optional[int] = None,
        validator: Optional[TuningValidator] = None
    ):
        """
        Initialize exit optimizer.
        
        Args:
            window_days: Days of outcomes to analyze
            validator: TuningValidator for safety checks
        """
        self.window_days = window_days or _env_int("SWING_TUNING_WINDOW_DAYS", 30)
        self.validator = validator or TuningValidator()
    
    def analyze_exit_reasons(
        self,
        outcomes: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Analyze distribution of exit reasons.
        
        Args:
            outcomes: Trade outcomes
        
        Returns:
            Dict mapping exit_reason -> count
        """
        distribution = {}
        for outcome in outcomes:
            reason = outcome.get("exit_reason", "UNKNOWN")
            distribution[reason] = distribution.get(reason, 0) + 1
        return distribution
    
    def _simulate_returns_with_stops(
        self,
        outcomes: List[Dict[str, Any]],
        stop_loss_pct: float,
        take_profit_pct: float
    ) -> List[float]:
        """
        Simulate returns with different stop/take levels.
        
        This is a simplified simulation that caps returns at the stop/take levels.
        
        Args:
            outcomes: Trade outcomes
            stop_loss_pct: Stop loss percentage (negative)
            take_profit_pct: Take profit percentage (positive)
        
        Returns:
            List of adjusted returns
        """
        adjusted_returns = []
        
        for outcome in outcomes:
            actual_return = outcome.get("actual_return", 0.0)
            exit_reason = outcome.get("exit_reason", "")
            
            # If it was a stop loss hit, check if new stop would change outcome
            if "STOP" in exit_reason and actual_return < 0:
                # Cap loss at new stop level
                adjusted_return = max(actual_return, stop_loss_pct)
            
            # If it was a take profit, check if new target would change
            elif "PROFIT" in exit_reason and actual_return > 0:
                # Cap gain at new take profit level
                adjusted_return = min(actual_return, take_profit_pct)
            
            # For other exits, returns stay same
            else:
                adjusted_return = actual_return
            
            adjusted_returns.append(adjusted_return)
        
        return adjusted_returns
    
    def optimize_stop_loss(
        self,
        bot_key: str,
        regime: str,
        current_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Optimize stop_loss_pct parameter.
        
        Args:
            bot_key: Bot identifier
            regime: Market regime
            current_value: Current stop_loss_pct (negative value)
        
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
            
            # Current Sharpe
            current_returns = [o.get("actual_return", 0.0) for o in regime_outcomes]
            current_sharpe = self.validator.calculate_sharpe_ratio(current_returns)
            
            # Analyze stop loss exits
            exit_dist = self.analyze_exit_reasons(regime_outcomes)
            stop_count = sum(v for k, v in exit_dist.items() if "STOP" in k)
            
            log(f"[exit_optimizer] {bot_key} {regime}: {stop_count} stop loss exits out of {len(regime_outcomes)} trades")
            
            # Test different stop levels: -2% to -8%
            best_value = current_value
            best_sharpe = current_sharpe
            best_returns = current_returns
            
            # Tighter stops for volatile regimes
            if regime in ["stress", "chop"]:
                test_stops = [-0.02, -0.03, -0.04, -0.05, -0.06, -0.08]
            else:
                test_stops = [-0.03, -0.04, -0.05, -0.06, -0.07, -0.08]
            
            for test_stop in test_stops:
                test_returns = self._simulate_returns_with_stops(
                    regime_outcomes,
                    stop_loss_pct=test_stop,
                    take_profit_pct=0.10  # Keep constant
                )
                
                test_sharpe = self.validator.calculate_sharpe_ratio(test_returns)
                
                if test_sharpe > best_sharpe:
                    best_sharpe = test_sharpe
                    best_value = test_stop
                    best_returns = test_returns
            
            # Validate
            validation = self.validator.validate_parameter_change(
                parameter="stop_loss_pct",
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
                    parameter="stop_loss_pct",
                    new_value=best_value
                )
                if not regime_validation.approved:
                    validation = regime_validation
            
            if validation.approved:
                log(f"[exit_optimizer] ✓ {bot_key} {regime}: stop_loss {current_value:.1%} → {best_value:.1%}")
            else:
                log(f"[exit_optimizer] ✗ {bot_key} {regime}: {validation.reason}")
            
            return {
                "parameter": "stop_loss_pct",
                "new_value": best_value,
                "old_value": current_value,
                "old_sharpe": current_sharpe,
                "new_sharpe": best_sharpe,
                "improvement_pct": validation.sharpe_improvement_pct or 0.0,
                "trades_analyzed": len(regime_outcomes),
                "validation": validation
            }
            
        except Exception as e:
            log(f"[exit_optimizer] Error optimizing stop_loss: {e}")
            return None
    
    def optimize_take_profit(
        self,
        bot_key: str,
        regime: str,
        current_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Optimize take_profit_pct parameter.
        
        Args:
            bot_key: Bot identifier
            regime: Market regime
            current_value: Current take_profit_pct (positive value)
        
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
            
            # Current Sharpe
            current_returns = [o.get("actual_return", 0.0) for o in regime_outcomes]
            current_sharpe = self.validator.calculate_sharpe_ratio(current_returns)
            
            # Analyze take profit exits
            exit_dist = self.analyze_exit_reasons(regime_outcomes)
            tp_count = sum(v for k, v in exit_dist.items() if "PROFIT" in k)
            
            log(f"[exit_optimizer] {bot_key} {regime}: {tp_count} take profit exits out of {len(regime_outcomes)} trades")
            
            # Test different take profit levels: 5% to 20%
            best_value = current_value
            best_sharpe = current_sharpe
            best_returns = current_returns
            
            # More aggressive targets in bull, conservative in bear
            if regime == "bull":
                test_targets = [0.05, 0.07, 0.10, 0.12, 0.15, 0.18, 0.20]
            elif regime == "bear":
                test_targets = [0.05, 0.06, 0.07, 0.08, 0.10, 0.12]
            else:
                test_targets = [0.05, 0.08, 0.10, 0.12, 0.15, 0.18]
            
            for test_tp in test_targets:
                test_returns = self._simulate_returns_with_stops(
                    regime_outcomes,
                    stop_loss_pct=-0.05,  # Keep constant
                    take_profit_pct=test_tp
                )
                
                test_sharpe = self.validator.calculate_sharpe_ratio(test_returns)
                
                if test_sharpe > best_sharpe:
                    best_sharpe = test_sharpe
                    best_value = test_tp
                    best_returns = test_returns
            
            # Validate
            validation = self.validator.validate_parameter_change(
                parameter="take_profit_pct",
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
                    parameter="take_profit_pct",
                    new_value=best_value
                )
                if not regime_validation.approved:
                    validation = regime_validation
            
            if validation.approved:
                log(f"[exit_optimizer] ✓ {bot_key} {regime}: take_profit {current_value:.1%} → {best_value:.1%}")
            else:
                log(f"[exit_optimizer] ✗ {bot_key} {regime}: {validation.reason}")
            
            return {
                "parameter": "take_profit_pct",
                "new_value": best_value,
                "old_value": current_value,
                "old_sharpe": current_sharpe,
                "new_sharpe": best_sharpe,
                "improvement_pct": validation.sharpe_improvement_pct or 0.0,
                "trades_analyzed": len(regime_outcomes),
                "validation": validation
            }
            
        except Exception as e:
            log(f"[exit_optimizer] Error optimizing take_profit: {e}")
            return None
    
    def optimize_all_exits(
        self,
        bot_key: str,
        regime: str,
        current_params: Dict[str, float]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Optimize all exit parameters.
        
        Args:
            bot_key: Bot identifier
            regime: Market regime
            current_params: Dict with current parameter values
        
        Returns:
            Dict mapping parameter -> optimization result
        """
        results = {}
        
        # Optimize stop_loss_pct
        if "stop_loss_pct" in current_params:
            results["stop_loss_pct"] = self.optimize_stop_loss(
                bot_key=bot_key,
                regime=regime,
                current_value=current_params["stop_loss_pct"]
            )
        
        # Optimize take_profit_pct
        if "take_profit_pct" in current_params:
            results["take_profit_pct"] = self.optimize_take_profit(
                bot_key=bot_key,
                regime=regime,
                current_value=current_params["take_profit_pct"]
            )
        
        return results
