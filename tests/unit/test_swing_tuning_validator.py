"""Unit tests for swing tuning validator (backend.tuning.swing_tuning_validator)

Tests safety guardrails and validation logic.
"""

import pytest
import math

from backend.tuning.swing_tuning_validator import (
    TuningValidator,
    ValidationResult,
    TuningDecision
)


class TestTuningValidator:
    """Test tuning validator functionality."""
    
    @pytest.fixture
    def validator(self):
        """Create a validator with default settings."""
        return TuningValidator(
            min_trades=50,
            min_sharpe_improvement=0.05,
            max_change_pct=0.20,
            confidence_level=0.95
        )
    
    def test_validate_sufficient_data_pass(self, validator):
        """Test sufficient data validation passes."""
        result = validator.validate_sufficient_data(trades_count=100)
        
        assert result.approved is True
        assert result.trades_analyzed == 100
        assert "100 trades" in result.reason
    
    def test_validate_sufficient_data_fail(self, validator):
        """Test insufficient data validation fails."""
        result = validator.validate_sufficient_data(trades_count=30)
        
        assert result.approved is False
        assert result.trades_analyzed == 30
        assert "30 trades" in result.reason
        assert "50 minimum" in result.reason
    
    def test_calculate_confidence_interval(self, validator):
        """Test confidence interval calculation."""
        returns = [0.05, 0.03, -0.02, 0.08, 0.04, 0.06, -0.01, 0.07]
        
        ci = validator.calculate_confidence_interval(returns)
        
        assert len(ci) == 2
        lower, upper = ci
        
        # Mean should be within interval
        mean = sum(returns) / len(returns)
        assert lower < mean < upper
        
        # Interval should be reasonable
        assert upper - lower > 0
        assert upper - lower < 0.2  # Not too wide
    
    def test_calculate_confidence_interval_empty(self, validator):
        """Test confidence interval with empty data."""
        ci = validator.calculate_confidence_interval([])
        assert ci == (0.0, 0.0)
    
    def test_calculate_sharpe_ratio(self, validator):
        """Test Sharpe ratio calculation."""
        # Positive returns with low volatility
        returns = [0.05, 0.06, 0.04, 0.05, 0.05]
        sharpe = validator.calculate_sharpe_ratio(returns, annualize=False)
        
        assert sharpe > 0
        
        # Annualized should be higher
        sharpe_annual = validator.calculate_sharpe_ratio(returns, annualize=True)
        assert sharpe_annual > sharpe
    
    def test_calculate_sharpe_ratio_negative(self, validator):
        """Test Sharpe with negative returns."""
        returns = [-0.05, -0.03, -0.04, -0.02]
        sharpe = validator.calculate_sharpe_ratio(returns)
        
        assert sharpe < 0
    
    def test_calculate_sharpe_ratio_zero_volatility(self, validator):
        """Test Sharpe with zero volatility."""
        returns = [0.05, 0.05, 0.05, 0.05]
        sharpe = validator.calculate_sharpe_ratio(returns)
        
        # Should handle zero std gracefully
        assert sharpe == 0.0
    
    def test_validate_parameter_change_approved(self, validator):
        """Test parameter change approval."""
        old_returns = [0.03, 0.02, -0.01, 0.04, 0.03]
        new_returns = [0.06, 0.05, 0.04, 0.07, 0.06]
        
        old_sharpe = validator.calculate_sharpe_ratio(old_returns)
        new_sharpe = validator.calculate_sharpe_ratio(new_returns)
        
        result = validator.validate_parameter_change(
            parameter="conf_threshold",
            old_value=0.55,
            new_value=0.60,
            old_sharpe=old_sharpe,
            new_sharpe=new_sharpe,
            trades_count=100,
            returns_old=old_returns,
            returns_new=new_returns
        )
        
        assert result.approved is True
        assert result.sharpe_improvement_pct is not None
        assert result.sharpe_improvement_pct > 0
    
    def test_validate_parameter_change_insufficient_data(self, validator):
        """Test rejection due to insufficient data."""
        old_returns = [0.03, 0.02]
        new_returns = [0.06, 0.05]
        
        result = validator.validate_parameter_change(
            parameter="conf_threshold",
            old_value=0.55,
            new_value=0.60,
            old_sharpe=1.0,
            new_sharpe=1.5,
            trades_count=30,  # Below minimum
            returns_old=old_returns,
            returns_new=new_returns
        )
        
        assert result.approved is False
        assert "Insufficient data" in result.reason
    
    def test_validate_parameter_change_too_large(self, validator):
        """Test rejection due to large parameter change."""
        old_returns = [0.03] * 100
        new_returns = [0.06] * 100
        
        result = validator.validate_parameter_change(
            parameter="conf_threshold",
            old_value=0.50,
            new_value=0.75,  # 50% change, exceeds 20% limit
            old_sharpe=1.0,
            new_sharpe=1.5,
            trades_count=100,
            returns_old=old_returns,
            returns_new=new_returns
        )
        
        assert result.approved is False
        assert "too large" in result.reason
    
    def test_validate_parameter_change_insufficient_improvement(self, validator):
        """Test rejection due to small Sharpe improvement."""
        old_returns = [0.03] * 100
        new_returns = [0.031] * 100  # Tiny improvement
        
        old_sharpe = validator.calculate_sharpe_ratio(old_returns)
        new_sharpe = validator.calculate_sharpe_ratio(new_returns)
        
        result = validator.validate_parameter_change(
            parameter="conf_threshold",
            old_value=0.55,
            new_value=0.56,
            old_sharpe=old_sharpe,
            new_sharpe=new_sharpe,
            trades_count=100,
            returns_old=old_returns,
            returns_new=new_returns
        )
        
        # Should be rejected due to < 5% Sharpe improvement
        assert result.approved is False
        assert "improvement insufficient" in result.reason
    
    def test_should_rollback_true(self, validator):
        """Test rollback recommendation when performance degrades."""
        sharpe_before = 1.5
        sharpe_after = 1.0  # Dropped more than 10%
        
        should_rollback = validator.should_rollback(sharpe_before, sharpe_after)
        
        assert should_rollback is True
    
    def test_should_rollback_false(self, validator):
        """Test no rollback when performance acceptable."""
        sharpe_before = 1.5
        sharpe_after = 1.4  # Small drop, within threshold
        
        should_rollback = validator.should_rollback(sharpe_before, sharpe_after)
        
        assert should_rollback is False
    
    def test_validate_regime_specific_conf_threshold_bull(self, validator):
        """Test regime-specific validation for bull market."""
        # Valid value for bull
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="conf_threshold",
            new_value=0.60
        )
        
        assert result.approved is True
        
        # Too high for bull
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="conf_threshold",
            new_value=0.80
        )
        
        assert result.approved is False
        assert "outside bull regime bounds" in result.reason
    
    def test_validate_regime_specific_conf_threshold_bear(self, validator):
        """Test regime-specific validation for bear market."""
        # Valid value for bear
        result = validator.validate_regime_specific(
            regime="bear",
            parameter="conf_threshold",
            new_value=0.50
        )
        
        assert result.approved is True
        
        # Warning for high threshold in bear
        result = validator.validate_regime_specific(
            regime="bear",
            parameter="conf_threshold",
            new_value=0.65
        )
        
        assert result.approved is True
        assert len(result.warnings) > 0
    
    def test_validate_regime_specific_stop_loss(self, validator):
        """Test validation for stop_loss_pct."""
        # Valid stop loss
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="stop_loss_pct",
            new_value=-0.05
        )
        
        assert result.approved is True
        
        # Too tight
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="stop_loss_pct",
            new_value=-0.01
        )
        
        assert result.approved is False
        
        # Too wide
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="stop_loss_pct",
            new_value=-0.10
        )
        
        assert result.approved is False
    
    def test_validate_regime_specific_take_profit(self, validator):
        """Test validation for take_profit_pct."""
        # Valid take profit
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="take_profit_pct",
            new_value=0.10
        )
        
        assert result.approved is True
        
        # Too low
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="take_profit_pct",
            new_value=0.03
        )
        
        assert result.approved is False
        
        # Too high
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="take_profit_pct",
            new_value=0.25
        )
        
        assert result.approved is False
    
    def test_validate_regime_specific_position_sizing(self, validator):
        """Test validation for position sizing parameters."""
        # Valid starter_fraction
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="starter_fraction",
            new_value=0.35
        )
        
        assert result.approved is True
        
        # Valid max_weight_per_name
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="max_weight_per_name",
            new_value=0.15
        )
        
        assert result.approved is True
        
        # Invalid starter_fraction
        result = validator.validate_regime_specific(
            regime="bull",
            parameter="starter_fraction",
            new_value=0.60  # Too high
        )
        
        assert result.approved is False
    
    def test_tuning_decision_dataclass(self):
        """Test TuningDecision dataclass."""
        decision = TuningDecision(
            bot_key="swing_1w",
            regime="bull",
            decision_ts="2026-01-27T22:00:00Z",
            phase="threshold_optimization",
            parameter="conf_threshold",
            old_value=0.55,
            new_value=0.58,
            improvement_pct=0.072,
            sharpe_old=1.45,
            sharpe_new=1.55,
            confidence_interval=(0.56, 0.60),
            trades_analyzed=67,
            applied=True,
            reason="Sharpe improved >5% with 95% confidence"
        )
        
        assert decision.bot_key == "swing_1w"
        assert decision.regime == "bull"
        assert decision.parameter == "conf_threshold"
        assert decision.applied is True
        assert decision.rollback_ts is None
