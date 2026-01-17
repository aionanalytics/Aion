"""
Tests for ML Pipeline Adaptive Features

Tests:
- Feature importance tracking (compute, save, load)
- Regime-specific feature selection
- Model performance tracking (prediction, outcome, accuracy, Sharpe)
- Parallel feature building (if implemented)
- Retraining trigger logic
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock


class TestFeatureImportanceTracker:
    """Test feature importance tracking functionality."""
    
    def test_compute_importance_basic(self, tmp_path):
        """Test basic feature importance computation."""
        from backend.core.ai_model.feature_importance import FeatureImportanceTracker
        
        # Mock model with feature importances
        mock_model = Mock()
        mock_model.feature_importances_ = [0.5, 0.3, 0.15, 0.05]
        
        feature_names = ["feature_a", "feature_b", "feature_c", "feature_d"]
        
        with patch("backend.core.ai_model.feature_importance.PATHS", {"ml_data": tmp_path}):
            tracker = FeatureImportanceTracker()
            importance = tracker.compute_importance(
                mock_model,
                feature_names,
                horizon="1d",
                top_n=3
            )
        
        # Should return top 3 features
        assert len(importance) == 3
        assert "feature_a" in importance
        assert "feature_b" in importance
        assert "feature_c" in importance
        assert importance["feature_a"] > importance["feature_b"]
    
    def test_save_and_load_importance(self, tmp_path):
        """Test saving and loading feature importance."""
        from backend.core.ai_model.feature_importance import FeatureImportanceTracker
        
        mock_model = Mock()
        mock_model.feature_importances_ = [0.6, 0.3, 0.1]
        feature_names = ["momentum", "volume", "volatility"]
        
        with patch("backend.core.ai_model.feature_importance.PATHS", {"ml_data": tmp_path}):
            tracker = FeatureImportanceTracker()
            
            # Compute and save
            tracker.compute_importance(mock_model, feature_names, "5d", top_n=2)
            
            # Load back
            loaded = tracker.get_top_features("5d", top_n=2)
        
        assert len(loaded) == 2
        assert "momentum" in loaded
        assert "volume" in loaded
    
    def test_get_low_importance_features(self, tmp_path):
        """Test identifying low-importance features."""
        from backend.core.ai_model.feature_importance import FeatureImportanceTracker
        
        with patch("backend.core.ai_model.feature_importance.PATHS", {"ml_data": tmp_path}):
            tracker = FeatureImportanceTracker()
            
            # Manually create importance file
            importance_file = tracker.importance_dir / "importance_1d.json"
            data = {
                "horizon": "1d",
                "features": {
                    "high_importance": 0.5,
                    "medium_importance": 0.3,
                    "low_importance_1": 0.005,
                    "low_importance_2": 0.002,
                },
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            importance_file.write_text(json.dumps(data))
            
            # Get low importance features
            low_features = tracker.get_low_importance_features("1d", threshold=0.01)
        
        assert len(low_features) == 2
        assert "low_importance_1" in low_features
        assert "low_importance_2" in low_features
    
    def test_no_feature_importances_attribute(self, tmp_path):
        """Test handling model without feature_importances_."""
        from backend.core.ai_model.feature_importance import FeatureImportanceTracker
        
        mock_model = Mock(spec=[])  # No feature_importances_ attribute
        
        with patch("backend.core.ai_model.feature_importance.PATHS", {"ml_data": tmp_path}):
            tracker = FeatureImportanceTracker()
            importance = tracker.compute_importance(
                mock_model,
                ["feature_a"],
                horizon="1d"
            )
        
        assert importance == {}


class TestRegimeFeatureSelector:
    """Test regime-specific feature selection."""
    
    def test_get_features_for_bull_regime(self):
        """Test feature selection for bull regime."""
        from backend.core.ai_model.regime_features import RegimeFeatureSelector
        
        features = RegimeFeatureSelector.get_features_for_regime("bull")
        
        # Should include trend and momentum features
        assert "adx" in features or "slope_20" in features  # Trend feature
        assert "rsi_14" in features or "macd" in features  # Momentum feature
        assert "volume" in features  # Base feature
    
    def test_get_features_for_bear_regime(self):
        """Test feature selection for bear regime."""
        from backend.core.ai_model.regime_features import RegimeFeatureSelector
        
        features = RegimeFeatureSelector.get_features_for_regime("bear")
        
        # Should include trend, momentum, and volatility features
        assert any(f.startswith("atr") or f == "true_range" for f in features)  # Volatility
    
    def test_get_features_for_chop_regime(self):
        """Test feature selection for range/chop regime."""
        from backend.core.ai_model.regime_features import RegimeFeatureSelector
        
        features = RegimeFeatureSelector.get_features_for_regime("chop")
        
        # Should include mean reversion features
        assert "bb_width" in features or "stoch_k" in features
    
    def test_get_features_for_volatile_regime(self):
        """Test feature selection for volatile regime."""
        from backend.core.ai_model.regime_features import RegimeFeatureSelector
        
        features = RegimeFeatureSelector.get_features_for_regime("volatile")
        
        # Should include volatility features
        assert "atr_14" in features or "true_range" in features
    
    def test_unknown_regime_falls_back_to_transitioning(self):
        """Test that unknown regime uses transitioning (all features)."""
        from backend.core.ai_model.regime_features import RegimeFeatureSelector
        
        features_unknown = RegimeFeatureSelector.get_features_for_regime("unknown_regime")
        features_transitioning = RegimeFeatureSelector.get_features_for_regime("transitioning")
        
        # Should be the same
        assert set(features_unknown) == set(features_transitioning)
    
    def test_get_available_regimes(self):
        """Test getting list of available regimes."""
        from backend.core.ai_model.regime_features import RegimeFeatureSelector
        
        regimes = RegimeFeatureSelector.get_available_regimes()
        
        assert "bull" in regimes
        assert "bear" in regimes
        assert "chop" in regimes
        assert "volatile" in regimes
        assert "transitioning" in regimes
    
    def test_get_feature_groups(self):
        """Test getting feature groups."""
        from backend.core.ai_model.regime_features import RegimeFeatureSelector
        
        groups = RegimeFeatureSelector.get_feature_groups()
        
        assert "momentum" in groups
        assert "trend" in groups
        assert "volatility" in groups
        assert "mean_reversion" in groups
        assert "base" in groups


class TestModelPerformanceTracker:
    """Test model performance tracking."""
    
    def test_record_prediction(self, tmp_path):
        """Test recording a prediction."""
        from backend.services.model_performance_tracker import ModelPerformanceTracker
        
        with patch("backend.services.model_performance_tracker.PATHS", {"brains_root": tmp_path}):
            tracker = ModelPerformanceTracker()
            
            tracker.record_prediction(
                symbol="AAPL",
                horizon="1d",
                prediction=0.05,
                confidence=0.85,
                ts="2024-01-01T10:00:00+00:00"
            )
        
        # Check file was created
        assert tracker.tracking_file.exists()
        
        # Check content
        with open(tracker.tracking_file, "r") as f:
            line = f.readline()
            record = json.loads(line)
        
        assert record["type"] == "prediction"
        assert record["symbol"] == "AAPL"
        assert record["prediction"] == 0.05
        assert record["confidence"] == 0.85
    
    def test_record_outcome(self, tmp_path):
        """Test recording an outcome."""
        from backend.services.model_performance_tracker import ModelPerformanceTracker
        
        with patch("backend.services.model_performance_tracker.PATHS", {"brains_root": tmp_path}):
            tracker = ModelPerformanceTracker()
            
            tracker.record_outcome(
                symbol="AAPL",
                realized_return=0.03,
                ts="2024-01-01T10:00:00+00:00"
            )
        
        # Check content
        with open(tracker.tracking_file, "r") as f:
            line = f.readline()
            record = json.loads(line)
        
        assert record["type"] == "outcome"
        assert record["symbol"] == "AAPL"
        assert record["realized_return"] == 0.03
    
    def test_get_accuracy_per_symbol(self, tmp_path):
        """Test calculating prediction accuracy."""
        from backend.services.model_performance_tracker import ModelPerformanceTracker
        
        with patch("backend.services.model_performance_tracker.PATHS", {"brains_root": tmp_path}):
            tracker = ModelPerformanceTracker()
            
            # Create test data: 3 predictions, 2 correct
            ts1 = datetime.now(timezone.utc).isoformat()
            ts2 = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            ts3 = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            
            # Prediction 1: predicted up, realized up (correct)
            tracker.record_prediction("AAPL", "1d", 0.05, 0.8, ts=ts1)
            tracker.record_outcome("AAPL", 0.03, ts1)
            
            # Prediction 2: predicted down, realized down (correct)
            tracker.record_prediction("AAPL", "1d", -0.02, 0.7, ts=ts2)
            tracker.record_outcome("AAPL", -0.01, ts2)
            
            # Prediction 3: predicted up, realized down (wrong)
            tracker.record_prediction("AAPL", "1d", 0.03, 0.75, ts=ts3)
            tracker.record_outcome("AAPL", -0.02, ts3)
            
            accuracy = tracker.get_accuracy_per_symbol("AAPL", days=1)
        
        assert accuracy["predictions_total"] == 3
        assert accuracy["predictions_with_outcomes"] == 3
        assert accuracy["accuracy"] == 2/3  # 2 correct out of 3
    
    def test_get_rolling_sharpe(self, tmp_path):
        """Test calculating rolling Sharpe ratio."""
        from backend.services.model_performance_tracker import ModelPerformanceTracker
        
        # Skip if numpy not available
        try:
            import numpy as np
        except ImportError:
            pytest.skip("NumPy not available")
        
        with patch("backend.services.model_performance_tracker.PATHS", {"brains_root": tmp_path}):
            tracker = ModelPerformanceTracker()
            
            # Create test data with outcomes
            base_ts = datetime.now(timezone.utc)
            for i in range(10):
                ts = (base_ts - timedelta(hours=i)).isoformat()
                tracker.record_prediction("AAPL", "1d", 0.01, 0.8, ts=ts)
                # Vary returns slightly
                realized = 0.01 + (i % 3 - 1) * 0.005
                tracker.record_outcome("AAPL", realized, ts)
            
            sharpe = tracker.get_rolling_sharpe("AAPL", days=1)
        
        # Should calculate a Sharpe ratio
        assert isinstance(sharpe, float)
        # Sharpe should be reasonable (not NaN, not extreme)
        assert not (sharpe != sharpe)  # Check not NaN
    
    def test_empty_tracking_file(self, tmp_path):
        """Test handling empty tracking file."""
        from backend.services.model_performance_tracker import ModelPerformanceTracker
        
        with patch("backend.services.model_performance_tracker.PATHS", {"brains_root": tmp_path}):
            tracker = ModelPerformanceTracker()
            
            accuracy = tracker.get_accuracy_per_symbol("AAPL")
            sharpe = tracker.get_rolling_sharpe("AAPL")
        
        assert accuracy["accuracy"] == 0.0
        assert accuracy["predictions_total"] == 0
        assert sharpe == 0.0


class TestAdaptiveRetraining:
    """Test adaptive retraining trigger logic."""
    
    def test_should_retrain_when_many_symbols_underperforming(self):
        """Test retraining trigger when >30% symbols have low Sharpe."""
        # This will be tested once we add the function to continuous_learning.py
        # For now, this is a placeholder test structure
        pass
    
    def test_no_retrain_when_symbols_performing_well(self):
        """Test no retraining when symbols perform well."""
        # Placeholder for future implementation
        pass
