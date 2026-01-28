"""Unit tests for swing outcome logger (backend.services.swing_outcome_logger)

Tests the trade outcome logging and statistics calculation.
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from backend.services.swing_outcome_logger import (
    append_swing_outcome,
    load_recent_outcomes,
    get_outcome_statistics,
    TradeOutcome,
    outcomes_path,
    _swing_outcomes_dir
)


class TestSwingOutcomeLogger:
    """Test swing outcome logger functionality."""
    
    @pytest.fixture
    def temp_outcomes_dir(self, tmp_path, monkeypatch):
        """Create a temporary outcomes directory."""
        monkeypatch.setenv("SWING_OUTCOMES_DIR", str(tmp_path))
        return tmp_path
    
    def test_append_swing_outcome_basic(self, temp_outcomes_dir):
        """Test appending a basic trade outcome."""
        result = append_swing_outcome(
            bot_key="swing_1w",
            symbol="AAPL",
            side="BUY",
            entry_price=150.0,
            exit_price=155.0,
            qty=100.0,
            entry_confidence=0.75,
            expected_return=0.05,
            hold_hours=48.0,
            exit_reason="TAKE_PROFIT",
            regime_entry="bull",
            regime_exit="bull"
        )
        
        assert result is True
        
        # Verify file was created
        path = outcomes_path()
        assert path.exists()
        
        # Read and verify outcome
        with open(path, "r") as f:
            lines = f.readlines()
            assert len(lines) == 1
            
            outcome = json.loads(lines[0])
            assert outcome["bot_key"] == "swing_1w"
            assert outcome["symbol"] == "AAPL"
            assert outcome["side"] == "BUY"
            assert outcome["entry_price"] == 150.0
            assert outcome["exit_price"] == 155.0
            assert outcome["qty"] == 100.0
            assert outcome["entry_confidence"] == 0.75
            assert outcome["expected_return"] == 0.05
            
            # Check calculated fields
            assert abs(outcome["actual_return"] - 0.0333) < 0.001  # (155-150)/150
            assert abs(outcome["pnl"] - 500.0) < 0.01  # (155-150)*100
            assert outcome["exit_reason"] == "TAKE_PROFIT"
    
    def test_append_swing_outcome_sell_side(self, temp_outcomes_dir):
        """Test SELL side outcome calculation."""
        result = append_swing_outcome(
            bot_key="swing_2w",
            symbol="TSLA",
            side="SELL",
            entry_price=200.0,
            exit_price=190.0,
            qty=50.0,
            entry_confidence=0.65,
            expected_return=0.03,
            hold_hours=72.0,
            exit_reason="TARGET_REBALANCE"
        )
        
        assert result is True
        
        # Read outcome
        with open(outcomes_path(), "r") as f:
            outcome = json.loads(f.readline())
            
            # For SELL: profit if exit < entry
            assert abs(outcome["actual_return"] - 0.05) < 0.001  # (200-190)/200
            assert abs(outcome["pnl"] - 500.0) < 0.01  # (200-190)*50
    
    def test_append_multiple_outcomes(self, temp_outcomes_dir):
        """Test appending multiple outcomes."""
        for i in range(5):
            append_swing_outcome(
                bot_key="swing_1w",
                symbol=f"STOCK{i}",
                side="BUY",
                entry_price=100.0,
                exit_price=105.0 + i,
                qty=10.0,
                entry_confidence=0.70 + i * 0.01,
                expected_return=0.05,
                hold_hours=24.0 * (i + 1),
                exit_reason="TAKE_PROFIT"
            )
        
        # Verify all outcomes were logged
        with open(outcomes_path(), "r") as f:
            lines = f.readlines()
            assert len(lines) == 5
    
    def test_load_recent_outcomes_all(self, temp_outcomes_dir):
        """Test loading all recent outcomes."""
        # Add some outcomes
        for i in range(3):
            append_swing_outcome(
                bot_key="swing_1w",
                symbol=f"SYM{i}",
                side="BUY",
                entry_price=100.0,
                exit_price=105.0,
                qty=10.0,
                entry_confidence=0.70,
                expected_return=0.05,
                hold_hours=24.0,
                exit_reason="TAKE_PROFIT"
            )
        
        # Load outcomes
        outcomes = load_recent_outcomes(bot_key="swing_1w", days=30)
        
        assert len(outcomes) == 3
        assert all(o["bot_key"] == "swing_1w" for o in outcomes)
    
    def test_load_recent_outcomes_filtered(self, temp_outcomes_dir):
        """Test loading outcomes with filters."""
        # Add outcomes for different bots
        append_swing_outcome(
            bot_key="swing_1w",
            symbol="AAPL",
            side="BUY",
            entry_price=100.0,
            exit_price=105.0,
            qty=10.0,
            entry_confidence=0.70,
            expected_return=0.05,
            hold_hours=24.0,
            exit_reason="TAKE_PROFIT"
        )
        
        append_swing_outcome(
            bot_key="swing_2w",
            symbol="MSFT",
            side="BUY",
            entry_price=100.0,
            exit_price=105.0,
            qty=10.0,
            entry_confidence=0.70,
            expected_return=0.05,
            hold_hours=24.0,
            exit_reason="TAKE_PROFIT"
        )
        
        # Load only swing_1w outcomes
        outcomes = load_recent_outcomes(bot_key="swing_1w", days=30)
        assert len(outcomes) == 1
        assert outcomes[0]["bot_key"] == "swing_1w"
        
        # Load all outcomes
        all_outcomes = load_recent_outcomes(days=30)
        assert len(all_outcomes) == 2
    
    def test_get_outcome_statistics(self, temp_outcomes_dir):
        """Test outcome statistics calculation."""
        # Add mix of wins and losses
        wins = [
            (100, 105, 0.05),  # +5%
            (100, 110, 0.10),  # +10%
            (100, 103, 0.03),  # +3%
        ]
        losses = [
            (100, 95, -0.05),  # -5%
            (100, 98, -0.02),  # -2%
        ]
        
        for entry, exit, _ in wins:
            append_swing_outcome(
                bot_key="swing_1w",
                symbol="WIN",
                side="BUY",
                entry_price=entry,
                exit_price=exit,
                qty=10.0,
                entry_confidence=0.70,
                expected_return=0.05,
                hold_hours=24.0,
                exit_reason="TAKE_PROFIT"
            )
        
        for entry, exit, _ in losses:
            append_swing_outcome(
                bot_key="swing_1w",
                symbol="LOSS",
                side="BUY",
                entry_price=entry,
                exit_price=exit,
                qty=10.0,
                entry_confidence=0.70,
                expected_return=0.05,
                hold_hours=24.0,
                exit_reason="STOP_LOSS"
            )
        
        # Calculate statistics
        stats = get_outcome_statistics(bot_key="swing_1w", days=30)
        
        assert stats["total_trades"] == 5
        assert abs(stats["win_rate"] - 0.60) < 0.01  # 3/5 = 60%
        assert stats["avg_return"] > 0  # Average should be positive
        assert stats["sharpe_ratio"] != 0
        assert "TAKE_PROFIT" in stats["exit_reasons"]
        assert "STOP_LOSS" in stats["exit_reasons"]
        assert stats["exit_reasons"]["TAKE_PROFIT"] == 3
        assert stats["exit_reasons"]["STOP_LOSS"] == 2
    
    def test_get_outcome_statistics_by_regime(self, temp_outcomes_dir):
        """Test statistics filtered by regime."""
        # Add outcomes for different regimes
        append_swing_outcome(
            bot_key="swing_1w",
            symbol="BULL",
            side="BUY",
            entry_price=100.0,
            exit_price=110.0,
            qty=10.0,
            entry_confidence=0.70,
            expected_return=0.10,
            hold_hours=24.0,
            exit_reason="TAKE_PROFIT",
            regime_entry="bull"
        )
        
        append_swing_outcome(
            bot_key="swing_1w",
            symbol="BEAR",
            side="BUY",
            entry_price=100.0,
            exit_price=95.0,
            qty=10.0,
            entry_confidence=0.70,
            expected_return=0.05,
            hold_hours=24.0,
            exit_reason="STOP_LOSS",
            regime_entry="bear"
        )
        
        # Bull regime stats
        bull_stats = get_outcome_statistics(bot_key="swing_1w", regime="bull", days=30)
        assert bull_stats["total_trades"] == 1
        assert bull_stats["win_rate"] == 1.0
        
        # Bear regime stats
        bear_stats = get_outcome_statistics(bot_key="swing_1w", regime="bear", days=30)
        assert bear_stats["total_trades"] == 1
        assert bear_stats["win_rate"] == 0.0
    
    def test_append_with_optional_fields(self, temp_outcomes_dir):
        """Test appending outcome with optional fields."""
        result = append_swing_outcome(
            bot_key="swing_1w",
            symbol="AAPL",
            side="BUY",
            entry_price=150.0,
            exit_price=155.0,
            qty=100.0,
            entry_confidence=0.75,
            expected_return=0.05,
            hold_hours=48.0,
            exit_reason="TAKE_PROFIT",
            phit=0.85,
            position_size_pct=5.0,
            stop_loss_used=-0.05,
            take_profit_used=0.10,
            custom_field="test_value"
        )
        
        assert result is True
        
        # Verify optional fields
        with open(outcomes_path(), "r") as f:
            outcome = json.loads(f.readline())
            assert outcome["phit"] == 0.85
            assert outcome["position_size_pct"] == 5.0
            assert outcome["stop_loss_used"] == -0.05
            assert outcome["take_profit_used"] == 0.10
            assert outcome["custom_field"] == "test_value"
    
    def test_empty_outcomes_statistics(self, temp_outcomes_dir):
        """Test statistics with no outcomes."""
        stats = get_outcome_statistics(bot_key="swing_1w", days=30)
        
        assert stats["total_trades"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["avg_return"] == 0.0
        assert stats["sharpe_ratio"] == 0.0
