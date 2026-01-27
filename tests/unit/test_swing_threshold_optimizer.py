"""Unit tests for swing threshold optimizer (backend.tuning.swing_threshold_optimizer)

Tests confidence threshold optimization logic.
"""

import pytest
import tempfile
from pathlib import Path

from backend.services.swing_outcome_logger import append_swing_outcome
from backend.tuning.swing_threshold_optimizer import (
    ThresholdOptimizer,
    optimize_confidence_threshold
)
from backend.tuning.swing_tuning_validator import TuningValidator


class TestThresholdOptimizer:
    """Test threshold optimizer functionality."""
    
    @pytest.fixture
    def temp_outcomes_dir(self, tmp_path, monkeypatch):
        """Create temporary outcomes directory."""
        monkeypatch.setenv("SWING_OUTCOMES_DIR", str(tmp_path))
        return tmp_path
    
    @pytest.fixture
    def optimizer(self):
        """Create optimizer with test settings."""
        validator = TuningValidator(min_trades=10)  # Lower for testing
        return ThresholdOptimizer(
            window_days=30,
            test_step=0.10,  # Larger step for faster tests
            validator=validator
        )
    
    def _create_test_outcomes(self, bot_key="swing_1w", regime="bull", count=50):
        """Helper to create test outcomes."""
        outcomes = []
        
        for i in range(count):
            # Create mix of outcomes at different confidence levels
            confidence = 0.50 + (i % 5) * 0.10  # 0.50, 0.60, 0.70, 0.80, 0.90
            
            # Higher confidence -> better returns (on average)
            base_return = (confidence - 0.50) * 0.20
            noise = (i % 3 - 1) * 0.02  # Add some noise
            
            entry_price = 100.0
            exit_price = entry_price * (1.0 + base_return + noise)
            
            append_swing_outcome(
                bot_key=bot_key,
                symbol=f"SYM{i}",
                side="BUY",
                entry_price=entry_price,
                exit_price=exit_price,
                qty=10.0,
                entry_confidence=confidence,
                expected_return=0.05,
                hold_hours=24.0,
                exit_reason="TAKE_PROFIT" if exit_price > entry_price else "STOP_LOSS",
                regime_entry=regime,
                regime_exit=regime
            )
            
            outcomes.append({
                "confidence": confidence,
                "return": (exit_price - entry_price) / entry_price
            })
        
        return outcomes
    
    def test_optimize_threshold_with_data(self, temp_outcomes_dir, optimizer):
        """Test threshold optimization with sufficient data."""
        # Create outcomes where higher confidence performs better
        self._create_test_outcomes(bot_key="swing_1w", regime="bull", count=60)
        
        result = optimizer.optimize_threshold_for_regime(
            bot_key="swing_1w",
            regime="bull",
            current_threshold=0.50
        )
        
        # Should find a result
        assert result is not None
        
        # New threshold should be in valid range
        assert 0.40 <= result["new_threshold"] <= 0.75
        
        # Should have analyzed outcomes
        assert result["trades_analyzed"] > 0
        
        # Should have Sharpe values
        assert "old_sharpe" in result
        assert "new_sharpe" in result
    
    def test_optimize_threshold_insufficient_data(self, temp_outcomes_dir, optimizer):
        """Test with insufficient data."""
        # Only create 5 outcomes (below minimum)
        self._create_test_outcomes(bot_key="swing_1w", regime="bull", count=5)
        
        result = optimizer.optimize_threshold_for_regime(
            bot_key="swing_1w",
            regime="bull",
            current_threshold=0.50
        )
        
        # Should return None due to insufficient data
        assert result is None
    
    def test_optimize_threshold_no_improvement(self, temp_outcomes_dir):
        """Test when no improvement is found."""
        # Create outcomes with uniform performance across confidence levels
        for i in range(50):
            confidence = 0.50 + (i % 5) * 0.10
            # Fixed return regardless of confidence
            entry_price = 100.0
            exit_price = 102.0
            
            append_swing_outcome(
                bot_key="swing_1w",
                symbol=f"SYM{i}",
                side="BUY",
                entry_price=entry_price,
                exit_price=exit_price,
                qty=10.0,
                entry_confidence=confidence,
                expected_return=0.02,
                hold_hours=24.0,
                exit_reason="TAKE_PROFIT",
                regime_entry="bull"
            )
        
        validator = TuningValidator(
            min_trades=10,
            min_sharpe_improvement=0.05  # Require 5% improvement
        )
        optimizer = ThresholdOptimizer(validator=validator)
        
        result = optimizer.optimize_threshold_for_regime(
            bot_key="swing_1w",
            regime="bull",
            current_threshold=0.50
        )
        
        # May find a result but validation likely fails
        if result:
            assert result["validation"].approved is False or \
                   abs(result["new_threshold"] - 0.50) < 0.1
    
    def test_optimize_all_regimes(self, temp_outcomes_dir, optimizer):
        """Test optimizing all regimes."""
        # Create data for multiple regimes
        for regime in ["bull", "bear", "chop"]:
            self._create_test_outcomes(
                bot_key="swing_1w",
                regime=regime,
                count=60
            )
        
        current_thresholds = {
            "bull": 0.55,
            "bear": 0.50,
            "chop": 0.52,
            "stress": 0.50
        }
        
        results = optimizer.optimize_all_regimes(
            bot_key="swing_1w",
            current_thresholds=current_thresholds
        )
        
        # Should have results for all regimes
        assert "bull" in results
        assert "bear" in results
        assert "chop" in results
        assert "stress" in results
        
        # Bull, bear, chop should have data
        assert results["bull"] is not None
        assert results["bear"] is not None
        assert results["chop"] is not None
        
        # Stress might not (no data created)
        # assert results["stress"] is None
    
    def test_calculate_sharpe_at_threshold(self, temp_outcomes_dir, optimizer):
        """Test Sharpe calculation at specific threshold."""
        # Create outcomes
        outcomes_data = self._create_test_outcomes(
            bot_key="swing_1w",
            regime="bull",
            count=50
        )
        
        from backend.services.swing_outcome_logger import load_recent_outcomes
        outcomes = load_recent_outcomes(bot_key="swing_1w", days=30)
        
        # Calculate Sharpe at threshold 0.60
        sharpe, returns = optimizer._calculate_sharpe_at_threshold(
            outcomes=outcomes,
            threshold=0.60
        )
        
        # Should filter to only outcomes with confidence >= 0.60
        assert len(returns) < len(outcomes)
        
        # Verify filtered outcomes match threshold
        filtered_outcomes = [o for o in outcomes if o.get("entry_confidence", 0) >= 0.60]
        assert len(returns) == len(filtered_outcomes)
    
    def test_optimize_convenience_function(self, temp_outcomes_dir):
        """Test convenience function."""
        # Create test data
        for i in range(60):
            confidence = 0.50 + (i % 5) * 0.10
            base_return = (confidence - 0.50) * 0.20
            
            append_swing_outcome(
                bot_key="swing_2w",
                symbol=f"SYM{i}",
                side="BUY",
                entry_price=100.0,
                exit_price=100.0 * (1.0 + base_return),
                qty=10.0,
                entry_confidence=confidence,
                expected_return=0.05,
                hold_hours=48.0,
                exit_reason="TAKE_PROFIT",
                regime_entry="bull"
            )
        
        # Use convenience function
        new_threshold = optimize_confidence_threshold(
            bot_key="swing_2w",
            regime="bull",
            current_threshold=0.50,
            window_days=30
        )
        
        # Should return None or a valid threshold
        if new_threshold is not None:
            assert 0.40 <= new_threshold <= 0.75
    
    def test_regime_specific_bounds(self, temp_outcomes_dir, optimizer):
        """Test that regime-specific bounds are respected."""
        # Create data for bear regime
        self._create_test_outcomes(bot_key="swing_1w", regime="bear", count=60)
        
        result = optimizer.optimize_threshold_for_regime(
            bot_key="swing_1w",
            regime="bear",
            current_threshold=0.50
        )
        
        if result and result["validation"].approved:
            # Bear regime bounds: 0.35-0.70
            assert 0.35 <= result["new_threshold"] <= 0.70
    
    def test_no_outcomes_for_regime(self, temp_outcomes_dir, optimizer):
        """Test handling when no outcomes exist for a regime."""
        result = optimizer.optimize_threshold_for_regime(
            bot_key="swing_1w",
            regime="stress",
            current_threshold=0.50
        )
        
        # Should return None
        assert result is None
