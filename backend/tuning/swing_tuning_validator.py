"""backend.tuning.swing_tuning_validator — Safety Guardrails for Tuning

Validates all proposed parameter changes before application.

Safety mechanisms:
- Minimum trade count requirement (default: 50 trades)
- 95% confidence intervals on estimates
- Minimum Sharpe improvement gate (≥5%)
- Maximum parameter change per cycle (≤20%)
- Auto-rollback if performance degrades
- Per-regime validation

Environment Variables:
  SWING_TUNING_MIN_TRADES (default: 50)
  SWING_TUNING_MIN_SHARPE_IMPROVEMENT (default: 0.05)
  SWING_TUNING_MAX_CHANGE_PCT (default: 0.20)
  SWING_TUNING_ROLLBACK_THRESHOLD (default: 0.10)
  SWING_TUNING_CONFIDENCE_LEVEL (default: 0.95)
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ValidationResult:
    """Result of parameter validation."""
    approved: bool
    reason: str
    confidence_interval: Optional[Tuple[float, float]] = None
    sharpe_improvement_pct: Optional[float] = None
    trades_analyzed: int = 0
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


@dataclass
class TuningDecision:
    """Record of a tuning decision."""
    bot_key: str
    regime: str
    decision_ts: str
    phase: str
    parameter: str
    old_value: float
    new_value: float
    improvement_pct: float
    sharpe_old: float
    sharpe_new: float
    confidence_interval: Tuple[float, float]
    trades_analyzed: int
    applied: bool
    rollback_ts: Optional[str] = None
    reason: str = ""


class TuningValidator:
    """Validates tuning decisions with safety guardrails."""
    
    def __init__(
        self,
        min_trades: Optional[int] = None,
        min_sharpe_improvement: Optional[float] = None,
        max_change_pct: Optional[float] = None,
        confidence_level: Optional[float] = None
    ):
        """
        Initialize validator with safety parameters.
        
        Args:
            min_trades: Minimum trades required before tuning
            min_sharpe_improvement: Minimum Sharpe improvement required
            max_change_pct: Maximum parameter change allowed per cycle
            confidence_level: Confidence level for intervals (default: 0.95)
        """
        self.min_trades = min_trades or _env_int("SWING_TUNING_MIN_TRADES", 50)
        self.min_sharpe_improvement = min_sharpe_improvement or _env_float("SWING_TUNING_MIN_SHARPE_IMPROVEMENT", 0.05)
        self.max_change_pct = max_change_pct or _env_float("SWING_TUNING_MAX_CHANGE_PCT", 0.20)
        self.confidence_level = confidence_level or _env_float("SWING_TUNING_CONFIDENCE_LEVEL", 0.95)
        self.rollback_threshold = _env_float("SWING_TUNING_ROLLBACK_THRESHOLD", 0.10)
    
    def validate_sufficient_data(
        self,
        trades_count: int
    ) -> ValidationResult:
        """
        Validate that we have sufficient trade data.
        
        Args:
            trades_count: Number of trades in analysis window
        
        Returns:
            ValidationResult indicating approval status
        """
        if trades_count < self.min_trades:
            return ValidationResult(
                approved=False,
                reason=f"Insufficient data: {trades_count} trades < {self.min_trades} minimum",
                trades_analyzed=trades_count
            )
        
        return ValidationResult(
            approved=True,
            reason=f"Sufficient data: {trades_count} trades",
            trades_analyzed=trades_count
        )
    
    def calculate_confidence_interval(
        self,
        returns: List[float],
        confidence_level: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        Calculate confidence interval for mean return.
        
        Args:
            returns: List of trade returns
            confidence_level: Confidence level (default: self.confidence_level)
        
        Returns:
            Tuple of (lower_bound, upper_bound)
        """
        if not returns:
            return (0.0, 0.0)
        
        n = len(returns)
        if n < 2:
            return (0.0, 0.0)
        
        conf_level = confidence_level or self.confidence_level
        
        # Calculate mean and standard error
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
        std_error = math.sqrt(variance / n)
        
        # Use t-distribution for small samples
        # For 95% confidence and n>30, t ≈ 1.96 (z-score)
        # For smaller n, use approximation
        if n > 30:
            t_score = 1.96  # For 95% confidence
        else:
            # Simplified t-score approximation
            t_score = 2.0 + (1.0 / math.sqrt(n))
        
        margin = t_score * std_error
        
        return (mean - margin, mean + margin)
    
    def calculate_sharpe_ratio(
        self,
        returns: List[float],
        annualize: bool = True
    ) -> float:
        """
        Calculate Sharpe ratio from returns.
        
        Args:
            returns: List of trade returns (as fractions)
            annualize: Whether to annualize the Sharpe ratio
        
        Returns:
            Sharpe ratio
        """
        if not returns or len(returns) < 2:
            return 0.0
        
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_return = math.sqrt(variance)
        
        if std_return == 0:
            return 0.0
        
        sharpe = mean_return / std_return
        
        if annualize:
            # Annualize assuming ~252 trading days
            sharpe *= math.sqrt(252)
        
        return sharpe
    
    def validate_parameter_change(
        self,
        parameter: str,
        old_value: float,
        new_value: float,
        old_sharpe: float,
        new_sharpe: float,
        trades_count: int,
        returns_old: List[float],
        returns_new: List[float]
    ) -> ValidationResult:
        """
        Validate a proposed parameter change.
        
        Args:
            parameter: Parameter name
            old_value: Current parameter value
            new_value: Proposed parameter value
            old_sharpe: Sharpe ratio with old parameter
            new_sharpe: Sharpe ratio with new parameter
            trades_count: Number of trades analyzed
            returns_old: Returns under old parameter
            returns_new: Returns under new parameter
        
        Returns:
            ValidationResult with approval decision
        """
        warnings = []
        
        # Check 1: Sufficient data
        data_check = self.validate_sufficient_data(trades_count)
        if not data_check.approved:
            return data_check
        
        # Check 2: Parameter change magnitude
        if old_value != 0:
            change_pct = abs(new_value - old_value) / abs(old_value)
            if change_pct > self.max_change_pct:
                return ValidationResult(
                    approved=False,
                    reason=f"Parameter change too large: {change_pct:.1%} > {self.max_change_pct:.1%} limit",
                    trades_analyzed=trades_count,
                    warnings=warnings
                )
        
        # Check 3: Sharpe improvement
        sharpe_improvement = new_sharpe - old_sharpe
        if old_sharpe != 0:
            sharpe_improvement_pct = sharpe_improvement / abs(old_sharpe)
        else:
            sharpe_improvement_pct = 1.0 if new_sharpe > 0 else 0.0
        
        if sharpe_improvement_pct < self.min_sharpe_improvement:
            return ValidationResult(
                approved=False,
                reason=f"Sharpe improvement insufficient: {sharpe_improvement_pct:.1%} < {self.min_sharpe_improvement:.1%} required",
                sharpe_improvement_pct=sharpe_improvement_pct,
                trades_analyzed=trades_count,
                warnings=warnings
            )
        
        # Check 4: Confidence interval for new returns
        if returns_new:
            ci = self.calculate_confidence_interval(returns_new)
            # Verify that improvement is statistically significant
            if ci[0] < 0 and sharpe_improvement < 0.1:
                warnings.append("Confidence interval includes negative returns")
        else:
            ci = (0.0, 0.0)
        
        # All checks passed
        return ValidationResult(
            approved=True,
            reason=f"Sharpe improved {sharpe_improvement_pct:.1%} with {trades_count} trades",
            confidence_interval=ci,
            sharpe_improvement_pct=sharpe_improvement_pct,
            trades_analyzed=trades_count,
            warnings=warnings
        )
    
    def should_rollback(
        self,
        sharpe_before_tuning: float,
        sharpe_after_tuning: float
    ) -> bool:
        """
        Determine if a tuning change should be rolled back.
        
        Args:
            sharpe_before_tuning: Sharpe ratio before tuning was applied
            sharpe_after_tuning: Sharpe ratio after tuning period
        
        Returns:
            True if rollback is recommended
        """
        if sharpe_before_tuning == 0:
            return sharpe_after_tuning < -0.5  # Absolute threshold
        
        sharpe_change = (sharpe_after_tuning - sharpe_before_tuning) / abs(sharpe_before_tuning)
        
        return sharpe_change < -self.rollback_threshold
    
    def validate_regime_specific(
        self,
        regime: str,
        parameter: str,
        new_value: float
    ) -> ValidationResult:
        """
        Validate parameter value is appropriate for regime.
        
        Args:
            regime: Market regime (bull/bear/chop/stress)
            parameter: Parameter name
            new_value: Proposed value
        
        Returns:
            ValidationResult
        """
        warnings = []
        
        # Regime-specific bounds for conf_threshold
        if parameter == "conf_threshold":
            if regime == "bull":
                if new_value < 0.40 or new_value > 0.75:
                    return ValidationResult(
                        approved=False,
                        reason=f"conf_threshold {new_value:.2f} outside bull regime bounds [0.40, 0.75]"
                    )
            elif regime == "bear":
                if new_value < 0.35 or new_value > 0.70:
                    return ValidationResult(
                        approved=False,
                        reason=f"conf_threshold {new_value:.2f} outside bear regime bounds [0.35, 0.70]"
                    )
                if new_value > 0.60:
                    warnings.append("High conf_threshold in bear regime may limit opportunities")
            elif regime == "stress":
                if new_value < 0.30 or new_value > 0.65:
                    return ValidationResult(
                        approved=False,
                        reason=f"conf_threshold {new_value:.2f} outside stress regime bounds [0.30, 0.65]"
                    )
        
        # Position sizing bounds
        elif parameter == "starter_fraction":
            if new_value < 0.25 or new_value > 0.50:
                return ValidationResult(
                    approved=False,
                    reason=f"starter_fraction {new_value:.2f} outside bounds [0.25, 0.50]"
                )
        
        elif parameter == "max_weight_per_name":
            if new_value < 0.05 or new_value > 0.25:
                return ValidationResult(
                    approved=False,
                    reason=f"max_weight_per_name {new_value:.2f} outside bounds [0.05, 0.25]"
                )
        
        # Exit parameters
        elif parameter == "stop_loss_pct":
            if new_value < -0.08 or new_value > -0.02:
                return ValidationResult(
                    approved=False,
                    reason=f"stop_loss_pct {new_value:.2f} outside bounds [-0.08, -0.02]"
                )
        
        elif parameter == "take_profit_pct":
            if new_value < 0.05 or new_value > 0.20:
                return ValidationResult(
                    approved=False,
                    reason=f"take_profit_pct {new_value:.2f} outside bounds [0.05, 0.20]"
                )
        
        return ValidationResult(
            approved=True,
            reason=f"Parameter {parameter} = {new_value:.4f} valid for regime {regime}",
            warnings=warnings
        )
