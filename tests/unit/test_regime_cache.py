"""Unit tests for regime cache."""

import pytest
import json
from pathlib import Path
from dt_backend.core.regime_cache import (
    save_regime_cache,
    load_cached_regime,
    has_cached_regime,
    list_cached_dates,
    clear_regime_cache,
)


@pytest.fixture
def mock_dt_paths(monkeypatch, tmp_path):
    """Mock DT_PATHS for testing."""
    from dt_backend.core import regime_cache
    
    mock_paths = {
        "ml_data_dt": tmp_path / "ml_data_dt",
    }
    
    monkeypatch.setattr(regime_cache, "DT_PATHS", mock_paths)
    return mock_paths


@pytest.fixture
def sample_regime_data():
    """Sample regime data for testing."""
    return {
        "label": "TREND_UP",
        "day_type": "TREND_DAY",
        "confidence": 0.75,
        "mkt_trend": 0.5,
        "mkt_vol": 0.012,
        "strategy_weights": {
            "VWAP_MR": 0.1,
            "ORB": 0.35,
            "TREND_PULLBACK": 0.35,
            "SQUEEZE": 0.2,
        },
    }


def test_save_and_load_regime_cache(mock_dt_paths, sample_regime_data):
    """Test saving and loading regime cache."""
    date_str = "2025-01-15"
    
    # Save cache
    result = save_regime_cache(
        date_str,
        regime_dt=sample_regime_data,
        mkt_trend=0.5,
        mkt_vol=0.012,
    )
    
    assert result is True
    
    # Load cache
    cached_data = load_cached_regime(date_str)
    
    assert cached_data is not None
    assert cached_data["date"] == date_str
    assert cached_data["regime_dt"] == sample_regime_data
    assert cached_data["mkt_trend"] == 0.5
    assert cached_data["mkt_vol"] == 0.012
    assert "cached_at" in cached_data


def test_has_cached_regime(mock_dt_paths, sample_regime_data):
    """Test checking if regime cache exists."""
    date_str = "2025-01-15"
    
    # Should not exist initially
    assert has_cached_regime(date_str) is False
    
    # Save cache
    save_regime_cache(date_str, sample_regime_data)
    
    # Should exist now
    assert has_cached_regime(date_str) is True


def test_load_nonexistent_cache(mock_dt_paths):
    """Test loading a nonexistent cache."""
    cached_data = load_cached_regime("2025-01-20")
    
    assert cached_data is None


def test_list_cached_dates(mock_dt_paths, sample_regime_data):
    """Test listing cached dates."""
    dates = ["2025-01-10", "2025-01-15", "2025-01-20"]
    
    # Save multiple caches
    for date in dates:
        save_regime_cache(date, sample_regime_data)
    
    # List all cached dates
    cached_dates = list_cached_dates()
    
    assert cached_dates == sorted(dates)


def test_list_cached_dates_with_filters(mock_dt_paths, sample_regime_data):
    """Test listing cached dates with filters."""
    dates = ["2025-01-10", "2025-01-15", "2025-01-20", "2025-01-25"]
    
    # Save multiple caches
    for date in dates:
        save_regime_cache(date, sample_regime_data)
    
    # List with filters
    cached_dates = list_cached_dates(start_date="2025-01-15", end_date="2025-01-20")
    
    assert cached_dates == ["2025-01-15", "2025-01-20"]


def test_clear_regime_cache(mock_dt_paths, sample_regime_data):
    """Test clearing regime cache."""
    dates = ["2025-01-10", "2025-01-15", "2025-01-20"]
    
    # Save multiple caches
    for date in dates:
        save_regime_cache(date, sample_regime_data)
    
    # Clear all
    deleted_count = clear_regime_cache()
    
    assert deleted_count == 3
    assert list_cached_dates() == []


def test_clear_regime_cache_with_filters(mock_dt_paths, sample_regime_data):
    """Test clearing regime cache with filters."""
    dates = ["2025-01-10", "2025-01-15", "2025-01-20", "2025-01-25"]
    
    # Save multiple caches
    for date in dates:
        save_regime_cache(date, sample_regime_data)
    
    # Clear with filters
    deleted_count = clear_regime_cache(start_date="2025-01-15", end_date="2025-01-20")
    
    assert deleted_count == 2
    
    # Verify remaining caches
    remaining = list_cached_dates()
    assert remaining == ["2025-01-10", "2025-01-25"]


def test_cache_with_all_optional_fields(mock_dt_paths):
    """Test caching with all optional fields."""
    date_str = "2025-01-15"
    
    regime_dt = {"label": "TREND_UP"}
    micro_regime_dt = {"label": "EXPANSION"}
    daily_plan_dt = {"max_trades": 5}
    
    save_regime_cache(
        date_str,
        regime_dt=regime_dt,
        micro_regime_dt=micro_regime_dt,
        daily_plan_dt=daily_plan_dt,
        mkt_trend=0.5,
        mkt_vol=0.012,
    )
    
    cached = load_cached_regime(date_str)
    
    assert cached["regime_dt"] == regime_dt
    assert cached["micro_regime_dt"] == micro_regime_dt
    assert cached["daily_plan_dt"] == daily_plan_dt
