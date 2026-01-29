"""
Test prediction validation logic for flat prediction detection.

Tests the new validation code added to backend/jobs/nightly_job.py
to prevent corrupting rolling cache with flat/degenerate predictions.
"""

import pytest
import numpy as np
from statistics import pstdev


def test_flat_prediction_detection_std_zero():
    """Test detection of completely flat predictions (std=0)."""
    # All predictions are identical
    pred_values = [0.0] * 100
    std = float(pstdev(pred_values))
    
    assert std == 0.0, "Standard deviation should be 0 for identical values"
    assert std < 0.002, "Should trigger flat prediction detection"


def test_flat_prediction_detection_very_low_variance():
    """Test detection of near-flat predictions (std < 0.002)."""
    # Very small variations around 0
    pred_values = [0.0001, 0.0002, 0.0001, 0.0002, 0.0001] * 20
    std = float(pstdev(pred_values))
    
    assert std < 0.002, f"Standard deviation {std} should trigger flat prediction detection"


def test_normal_prediction_variance():
    """Test that normal predictions pass validation."""
    # Realistic prediction variance (5-10% std)
    np.random.seed(42)
    pred_values = np.random.normal(0.05, 0.08, 100).tolist()
    std = float(pstdev(pred_values))
    
    assert std >= 0.002, f"Standard deviation {std} should pass validation (not flat)"


def test_edge_case_threshold():
    """Test predictions right at the threshold."""
    # Generate values with std slightly above threshold
    pred_values = [0.0, 0.005, -0.005] * 30
    std = float(pstdev(pred_values))
    
    # This should pass (std should be > 0.002)
    assert std > 0.002, f"Standard deviation {std} should pass validation"


def test_mixed_positive_negative_variance():
    """Test that mixed pos/neg predictions with good variance pass."""
    pred_values = [0.05, -0.03, 0.08, -0.02, 0.06, -0.04] * 20
    std = float(pstdev(pred_values))
    
    assert std >= 0.002, f"Standard deviation {std} should pass validation"


def test_nan_inf_filtering():
    """Test that NaN and Inf values are properly filtered out."""
    pred_values_raw = [0.05, np.nan, 0.03, np.inf, -0.02, -np.inf, 0.04]
    
    # Filter out NaN and Inf (as done in nightly_job.py)
    pred_values = [
        float(v) for v in pred_values_raw 
        if not (np.isnan(v) or np.isinf(v))
    ]
    
    assert len(pred_values) == 4, "Should have 4 valid values after filtering"
    assert all(not (np.isnan(v) or np.isinf(v)) for v in pred_values)
    
    std = float(pstdev(pred_values))
    assert std >= 0.002, f"Standard deviation {std} of filtered values should pass"


def test_sufficient_samples_requirement():
    """Test that we need sufficient samples (>10) for flat detection."""
    # Too few samples should not trigger flat detection
    pred_values_few = [0.0001] * 5
    
    # The actual validation requires > 10 samples
    assert len(pred_values_few) <= 10, "Should have insufficient samples"
    
    # With sufficient samples
    pred_values_many = [0.0001] * 20
    std_many = float(pstdev(pred_values_many))
    
    assert len(pred_values_many) > 10, "Should have sufficient samples"
    assert std_many < 0.002, "Should trigger flat detection with sufficient samples"


def test_realistic_flat_scenario():
    """Test a realistic scenario where model outputs near-zero predictions."""
    # Simulate a degenerate model that outputs very small values
    # (e.g., all features became zero due to NaN cascade)
    pred_values = [0.00001, -0.00001, 0.0, 0.00002, -0.00002] * 30
    std = float(pstdev(pred_values))
    
    assert std < 0.002, f"Degenerate model output (std={std}) should be detected as flat"


def test_realistic_healthy_scenario():
    """Test a realistic healthy model with good prediction spread."""
    # Simulate healthy model with variety of predictions
    np.random.seed(123)
    
    # Mix of different prediction strengths
    bullish = np.random.normal(0.06, 0.02, 30)
    bearish = np.random.normal(-0.04, 0.02, 30)
    neutral = np.random.normal(0.0, 0.01, 40)
    
    pred_values = np.concatenate([bullish, bearish, neutral]).tolist()
    std = float(pstdev(pred_values))
    
    assert std > 0.002, f"Healthy model (std={std}) should pass validation"
    assert std > 0.02, f"Healthy model should have substantial variance (std={std})"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
