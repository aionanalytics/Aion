"""Unit tests for DT replay validation."""

import pytest
import json
from pathlib import Path
from dt_backend.historical_replay.validation_dt import (
    validate_data_integrity,
    validate_predictions,
    validate_results_consistency,
    validate_pipeline_stages,
    validate_replay_result,
)


@pytest.fixture
def mock_dt_paths(monkeypatch, tmp_path):
    """Mock DT_PATHS for testing."""
    from dt_backend.historical_replay import validation_dt
    
    mock_paths = {
        "ml_data_dt": tmp_path / "ml_data_dt",
    }
    
    monkeypatch.setattr(validation_dt, "DT_PATHS", mock_paths)
    
    # Create necessary directories
    replay_dir = tmp_path / "ml_data_dt" / "intraday" / "replay"
    (replay_dir / "raw_days").mkdir(parents=True, exist_ok=True)
    (replay_dir / "replay_results").mkdir(parents=True, exist_ok=True)
    
    return mock_paths


@pytest.fixture
def sample_raw_day():
    """Sample raw day data."""
    return [
        {
            "symbol": "AAPL",
            "bars": [
                {"c": 150.0, "t": 1642000000, "v": 1000},
                {"c": 151.0, "t": 1642000060, "v": 1100},
                {"c": 152.0, "t": 1642000120, "v": 1200},
            ],
        },
        {
            "symbol": "MSFT",
            "bars": [
                {"c": 300.0, "t": 1642000000, "v": 800},
                {"c": 301.0, "t": 1642000060, "v": 900},
            ],
        },
    ]


@pytest.fixture
def sample_replay_result():
    """Sample replay result data."""
    return {
        "date": "2025-01-15",
        "n_symbols": 2,
        "n_trades": 5,
        "gross_pnl": 150.0,
        "avg_pnl_per_trade": 30.0,
        "hit_rate": 0.6,
        "meta": {
            "regime_dt": {
                "label": "TREND_UP",
                "confidence": 0.75,
            }
        },
    }


def test_validate_data_integrity_valid(sample_raw_day):
    """Test data integrity validation with valid data."""
    passed, errors, warnings = validate_data_integrity("2025-01-15", sample_raw_day)
    
    assert passed is True
    assert len(errors) == 0


def test_validate_data_integrity_empty():
    """Test data integrity validation with empty data."""
    passed, errors, warnings = validate_data_integrity("2025-01-15", [])
    
    assert passed is True
    assert len(warnings) > 0


def test_validate_data_integrity_missing_data():
    """Test data integrity validation with missing data."""
    passed, errors, warnings = validate_data_integrity("2025-01-15", None)
    
    assert passed is False
    assert len(errors) > 0


def test_validate_data_integrity_duplicate_symbols():
    """Test data integrity validation with duplicate symbols."""
    raw_day = [
        {"symbol": "AAPL", "bars": [{"c": 150.0}]},
        {"symbol": "AAPL", "bars": [{"c": 151.0}]},
    ]
    
    passed, errors, warnings = validate_data_integrity("2025-01-15", raw_day)
    
    assert passed is True
    assert any("duplicate" in w.lower() for w in warnings)


def test_validate_predictions_valid(sample_replay_result):
    """Test prediction validation with valid data."""
    passed, errors, warnings = validate_predictions("2025-01-15", sample_replay_result)
    
    assert passed is True
    assert len(errors) == 0


def test_validate_predictions_missing_result():
    """Test prediction validation with missing result."""
    passed, errors, warnings = validate_predictions("2025-01-15", None)
    
    assert passed is False
    assert len(errors) > 0


def test_validate_results_consistency_valid(sample_replay_result):
    """Test results consistency validation with valid data."""
    passed, errors, warnings = validate_results_consistency("2025-01-15", sample_replay_result)
    
    assert passed is True
    assert len(errors) == 0


def test_validate_results_consistency_invalid_hit_rate():
    """Test results consistency validation with invalid hit rate."""
    result = {
        "n_trades": 5,
        "gross_pnl": 100.0,
        "avg_pnl_per_trade": 20.0,
        "hit_rate": 1.5,  # Invalid: > 1.0
    }
    
    passed, errors, warnings = validate_results_consistency("2025-01-15", result)
    
    assert passed is False
    assert any("hit rate" in e.lower() for e in errors)


def test_validate_results_consistency_inconsistent_avg_pnl():
    """Test results consistency validation with inconsistent average PnL."""
    result = {
        "n_trades": 5,
        "gross_pnl": 100.0,
        "avg_pnl_per_trade": 50.0,  # Should be 20.0
        "hit_rate": 0.6,
    }
    
    passed, errors, warnings = validate_results_consistency("2025-01-15", result)
    
    # Should pass but with warnings
    assert len(warnings) > 0


def test_validate_results_consistency_zero_trades():
    """Test results consistency validation with zero trades."""
    result = {
        "n_trades": 0,
        "gross_pnl": 0.0,
        "avg_pnl_per_trade": 0.0,
        "hit_rate": 0.0,
    }
    
    passed, errors, warnings = validate_results_consistency("2025-01-15", result)
    
    assert passed is True


def test_validate_pipeline_stages(mock_dt_paths, sample_raw_day, sample_replay_result):
    """Test pipeline stages validation."""
    date_str = "2025-01-15"
    
    # Create raw day file
    raw_path = mock_dt_paths["ml_data_dt"] / "intraday" / "replay" / "raw_days" / f"{date_str}.json"
    with raw_path.open("w") as f:
        json.dump(sample_raw_day, f)
    
    # Create replay result file
    result_path = mock_dt_paths["ml_data_dt"] / "intraday" / "replay" / "replay_results" / f"{date_str}.json"
    with result_path.open("w") as f:
        json.dump(sample_replay_result, f)
    
    passed, errors, warnings = validate_pipeline_stages(date_str)
    
    assert passed is True
    assert len(errors) == 0


def test_validate_replay_result_complete(mock_dt_paths, sample_raw_day, sample_replay_result):
    """Test complete replay validation."""
    date_str = "2025-01-15"
    
    # Create raw day file
    raw_path = mock_dt_paths["ml_data_dt"] / "intraday" / "replay" / "raw_days" / f"{date_str}.json"
    with raw_path.open("w") as f:
        json.dump(sample_raw_day, f)
    
    # Create replay result file
    result_path = mock_dt_paths["ml_data_dt"] / "intraday" / "replay" / "replay_results" / f"{date_str}.json"
    with result_path.open("w") as f:
        json.dump(sample_replay_result, f)
    
    validation = validate_replay_result(date_str, save_to_file=False)
    
    assert validation.date == date_str
    assert validation.passed is True
    assert validation.checks_passed > 0
    assert validation.checks_failed == 0
