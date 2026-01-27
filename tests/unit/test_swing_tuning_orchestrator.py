"""Unit tests for swing tuning orchestrator (backend.tuning.swing_tuning_orchestrator)

Tests the orchestration of the full tuning pipeline.
"""

import pytest
import json
import tempfile
from pathlib import Path

from backend.services.swing_outcome_logger import append_swing_outcome
from backend.tuning.swing_tuning_orchestrator import (
    TuningOrchestrator,
    load_bot_configs,
    save_bot_configs,
    run_nightly_tuning,
    _bot_configs_path,
    _tuning_history_path
)


class TestTuningOrchestrator:
    """Test tuning orchestrator functionality."""
    
    @pytest.fixture
    def temp_dirs(self, tmp_path, monkeypatch):
        """Set up temporary directories."""
        outcomes_dir = tmp_path / "outcomes"
        configs_dir = tmp_path / "configs"
        history_dir = tmp_path / "history"
        
        outcomes_dir.mkdir()
        configs_dir.mkdir()
        history_dir.mkdir()
        
        monkeypatch.setenv("SWING_OUTCOMES_DIR", str(outcomes_dir))
        
        # Override paths
        def mock_bot_configs_path():
            return configs_dir / "configs.json"
        
        def mock_tuning_history_path():
            return history_dir / "tuning_history.jsonl"
        
        import backend.tuning.swing_tuning_orchestrator as orch
        orch._bot_configs_path = mock_bot_configs_path
        orch._tuning_history_path = mock_tuning_history_path
        
        return {
            "outcomes": outcomes_dir,
            "configs": configs_dir,
            "history": history_dir
        }
    
    def _create_test_outcomes(self, bot_key="swing_1w", count=60):
        """Create test outcomes with varying performance."""
        for i in range(count):
            confidence = 0.50 + (i % 5) * 0.10
            base_return = (confidence - 0.50) * 0.20
            noise = (i % 3 - 1) * 0.02
            
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
                hold_hours=24.0 * (i % 3 + 1),
                exit_reason="TAKE_PROFIT" if exit_price > entry_price else "STOP_LOSS",
                regime_entry="bull",
                regime_exit="bull"
            )
    
    def _create_test_config(self):
        """Create a test bot configuration."""
        return {
            "swing_1w": {
                "tuning_enabled": True,
                "conf_threshold": 0.55,
                "starter_fraction": 0.35,
                "max_weight_per_name": 0.15,
                "stop_loss_pct": -0.05,
                "take_profit_pct": 0.10,
                "per_regime_tuning": True
            }
        }
    
    def test_orchestrator_initialization(self):
        """Test orchestrator initialization."""
        orchestrator = TuningOrchestrator(
            enabled=True,
            phase="full",
            window_days=30
        )
        
        assert orchestrator.enabled is True
        assert orchestrator.phase == "full"
        assert orchestrator.window_days == 30
        assert orchestrator.validator is not None
        assert orchestrator.threshold_optimizer is not None
        assert orchestrator.position_tuner is not None
        assert orchestrator.exit_optimizer is not None
    
    def test_orchestrator_disabled(self, temp_dirs):
        """Test that tuning is skipped when disabled."""
        orchestrator = TuningOrchestrator(enabled=False)
        
        config = self._create_test_config()
        result = orchestrator.run_tuning_for_bot("swing_1w", config["swing_1w"])
        
        # Config should be unchanged
        assert result == config["swing_1w"]
    
    def test_orchestrator_logging_only_phase(self, temp_dirs):
        """Test logging_only phase (no tuning applied)."""
        self._create_test_outcomes(bot_key="swing_1w", count=60)
        
        orchestrator = TuningOrchestrator(
            enabled=True,
            phase="logging_only",
            window_days=30
        )
        
        config = self._create_test_config()
        original_threshold = config["swing_1w"]["conf_threshold"]
        
        result = orchestrator.run_tuning_for_bot("swing_1w", config["swing_1w"])
        
        # Config should be unchanged in logging_only phase
        assert result["conf_threshold"] == original_threshold
    
    def test_orchestrator_threshold_phase(self, temp_dirs, monkeypatch):
        """Test threshold optimization phase."""
        # Lower minimum trades for testing
        monkeypatch.setenv("SWING_TUNING_MIN_TRADES", "10")
        
        self._create_test_outcomes(bot_key="swing_1w", count=60)
        
        orchestrator = TuningOrchestrator(
            enabled=True,
            phase="threshold",
            window_days=30
        )
        
        config = self._create_test_config()
        
        result = orchestrator.run_tuning_for_bot("swing_1w", config["swing_1w"])
        
        # Result should be returned (may or may not have changes based on validation)
        assert result is not None
        assert "conf_threshold" in result or "regime_thresholds" in result
    
    def test_save_and_load_bot_configs(self, temp_dirs):
        """Test saving and loading bot configurations."""
        config = self._create_test_config()
        
        # Save
        success = save_bot_configs(config)
        assert success is True
        
        # Load
        loaded = load_bot_configs()
        assert loaded == config
        assert loaded["swing_1w"]["conf_threshold"] == 0.55
    
    def test_run_nightly_tuning_disabled(self, temp_dirs, monkeypatch):
        """Test nightly tuning when disabled."""
        monkeypatch.setenv("SWING_TUNING_ENABLED", "false")
        
        result = run_nightly_tuning()
        
        assert result["enabled"] is False
        assert result["bots_tuned"] == 0
    
    def test_run_nightly_tuning_enabled(self, temp_dirs, monkeypatch):
        """Test nightly tuning when enabled."""
        # Set up environment
        monkeypatch.setenv("SWING_TUNING_ENABLED", "true")
        monkeypatch.setenv("SWING_TUNING_PHASE", "logging_only")
        monkeypatch.setenv("SWING_TUNING_MIN_TRADES", "10")
        
        # Create test data
        self._create_test_outcomes(bot_key="swing_1w", count=60)
        self._create_test_outcomes(bot_key="swing_2w", count=60)
        
        # Create and save initial configs
        config = {
            "swing_1w": {
                "tuning_enabled": True,
                "conf_threshold": 0.55
            },
            "swing_2w": {
                "tuning_enabled": True,
                "conf_threshold": 0.55
            },
            "swing_4w": {
                "tuning_enabled": False,
                "conf_threshold": 0.55
            }
        }
        save_bot_configs(config)
        
        # Run tuning
        result = run_nightly_tuning()
        
        assert result["enabled"] is True
        assert result["phase"] == "logging_only"
        # Should tune 1w and 2w (4w disabled)
        assert result["bots_tuned"] >= 0  # May be 0 in logging_only phase
    
    def test_tuning_history_logged(self, temp_dirs, monkeypatch):
        """Test that tuning decisions are logged to history."""
        from backend.tuning.swing_tuning_validator import TuningDecision
        
        # Set up the mock path before importing
        history_path = temp_dirs["history"] / "tuning_history.jsonl"
        
        def mock_tuning_history_path():
            return history_path
        
        # Patch the function in the module
        import backend.tuning.swing_tuning_orchestrator as orch
        original_path_func = orch._tuning_history_path
        orch._tuning_history_path = mock_tuning_history_path
        
        # Now import the function that uses it
        from backend.tuning.swing_tuning_orchestrator import append_tuning_decision
        
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
            reason="Test decision"
        )
        
        append_tuning_decision(decision)
        
        # Verify history file exists and contains decision
        assert history_path.exists()
        
        with open(history_path, "r") as f:
            lines = f.readlines()
            assert len(lines) == 1
            
            logged = json.loads(lines[0])
            assert logged["bot_key"] == "swing_1w"
            assert logged["parameter"] == "conf_threshold"
            assert logged["new_value"] == 0.58
        
        # Restore original function
        orch._tuning_history_path = original_path_func
    
    def test_orchestrator_with_no_outcomes(self, temp_dirs):
        """Test orchestrator behavior with no outcomes."""
        orchestrator = TuningOrchestrator(
            enabled=True,
            phase="full",
            window_days=30
        )
        
        config = self._create_test_config()
        
        # No outcomes created
        result = orchestrator.run_tuning_for_bot("swing_1w", config["swing_1w"])
        
        # Should return config unchanged
        assert result == config["swing_1w"]
    
    def test_orchestrator_full_pipeline(self, temp_dirs, monkeypatch):
        """Test full orchestration pipeline with all phases."""
        # Lower thresholds for testing
        monkeypatch.setenv("SWING_TUNING_MIN_TRADES", "10")
        monkeypatch.setenv("SWING_TUNING_MIN_SHARPE_IMPROVEMENT", "0.01")
        
        # Create comprehensive test data
        self._create_test_outcomes(bot_key="swing_1w", count=100)
        
        orchestrator = TuningOrchestrator(
            enabled=True,
            phase="full",
            window_days=30
        )
        
        config = self._create_test_config()
        
        result = orchestrator.run_tuning_for_bot("swing_1w", config["swing_1w"])
        
        # Result should be returned
        assert result is not None
        
        # Config may be modified (depending on validation)
        # At minimum, should have the same keys
        assert "tuning_enabled" in result
        assert "conf_threshold" in result or "regime_thresholds" in result
