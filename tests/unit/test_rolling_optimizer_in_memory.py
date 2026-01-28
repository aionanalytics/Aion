"""
Unit tests for rolling optimizer in-memory data feature.

Tests the new functionality that allows passing rolling data directly
to the optimizer instead of forcing a disk re-read, preventing race conditions.
"""

import gzip
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.rolling_optimizer import RollingOptimizer, optimize_rolling_data


@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory for test files."""
    da_brains = tmp_path / "da_brains"
    da_brains.mkdir(parents=True, exist_ok=True)
    
    intraday_dir = da_brains / "intraday"
    intraday_dir.mkdir(parents=True, exist_ok=True)
    
    return tmp_path


@pytest.fixture
def mock_paths(temp_dir):
    """Mock PATHS configuration."""
    return {
        "da_brains": temp_dir / "da_brains",
        "stock_cache": temp_dir / "data" / "stock_cache",
    }


@pytest.fixture
def sample_rolling_data():
    """Sample rolling data structure."""
    return {
        "AAPL": {
            "prediction": 1.5,
            "confidence": 0.85,
            "price": 150.0,
            "last": 150.0,
            "sentiment": "bullish",
            "target_price": 160.0,
            "stop_loss": 145.0,
            "timestamp": "2024-01-27T04:00:00Z"
        },
        "GOOGL": {
            "prediction": 0.8,
            "confidence": 0.72,
            "price": 140.0,
            "last": 140.0,
            "sentiment": "neutral",
            "timestamp": "2024-01-27T04:00:00Z"
        },
        "TSLA": {
            "prediction": -0.5,
            "confidence": 0.68,
            "price": 200.0,
            "last": 200.0,
            "sentiment": "bearish",
            "timestamp": "2024-01-27T04:00:00Z"
        }
    }


@pytest.mark.unit
def test_rolling_optimizer_accepts_in_memory_data(temp_dir, mock_paths, sample_rolling_data):
    """Test that RollingOptimizer accepts in-memory rolling data."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing", rolling_data=sample_rolling_data)
        
        assert optimizer.section == "swing"
        assert optimizer.rolling_data == sample_rolling_data
        assert optimizer.rolling_data is not None


@pytest.mark.unit
def test_rolling_optimizer_without_in_memory_data(temp_dir, mock_paths):
    """Test that RollingOptimizer works without in-memory data (backward compatibility)."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing")
        
        assert optimizer.section == "swing"
        assert optimizer.rolling_data is None


@pytest.mark.unit
def test_extract_predictions_uses_in_memory_data(temp_dir, mock_paths, sample_rolling_data):
    """Test that _extract_predictions uses in-memory data when provided."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing", rolling_data=sample_rolling_data)
        
        predictions = optimizer._extract_predictions()
        
        # Should extract predictions from in-memory data
        assert len(predictions) == 3
        
        # Verify predictions are sorted by confidence
        assert predictions[0]["symbol"] == "AAPL"
        assert predictions[0]["confidence"] == 0.85
        assert predictions[1]["symbol"] == "GOOGL"
        assert predictions[1]["confidence"] == 0.72
        assert predictions[2]["symbol"] == "TSLA"
        assert predictions[2]["confidence"] == 0.68


@pytest.mark.unit
def test_extract_predictions_reads_from_disk_when_no_in_memory_data(temp_dir, mock_paths):
    """Test that _extract_predictions reads from disk when no in-memory data provided."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing")
        da_brains = temp_dir / "da_brains"
        rolling_body = da_brains / "rolling_body.json.gz"
        
        # Create test file
        test_data = {
            "MSFT": {
                "prediction": 1.2,
                "confidence": 0.90,
                "price": 300.0,
                "last": 300.0,
                "sentiment": "bullish",
                "timestamp": "2024-01-27T04:00:00Z"
            }
        }
        
        with gzip.open(rolling_body, "wt", encoding="utf-8") as f:
            json.dump(test_data, f)
        
        predictions = optimizer._extract_predictions()
        
        # Should extract predictions from disk
        assert len(predictions) == 1
        assert predictions[0]["symbol"] == "MSFT"
        assert predictions[0]["confidence"] == 0.90


@pytest.mark.unit
def test_optimize_rolling_data_with_in_memory_data(temp_dir, mock_paths, sample_rolling_data):
    """Test optimize_rolling_data function with in-memory data."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        result = optimize_rolling_data(section="swing", rolling_data=sample_rolling_data)
        
        assert result["status"] == "success"
        assert result["section"] == "swing"
        assert "stats" in result
        assert result["stats"]["predictions_extracted"] == 3


@pytest.mark.unit
def test_optimize_rolling_data_without_in_memory_data(temp_dir, mock_paths):
    """Test optimize_rolling_data function without in-memory data (backward compatibility)."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        da_brains = temp_dir / "da_brains"
        rolling_body = da_brains / "rolling_body.json.gz"
        
        # Create test file
        test_data = {
            "NVDA": {
                "prediction": 2.0,
                "confidence": 0.95,
                "price": 500.0,
                "last": 500.0,
                "sentiment": "bullish",
                "timestamp": "2024-01-27T04:00:00Z"
            }
        }
        
        with gzip.open(rolling_body, "wt", encoding="utf-8") as f:
            json.dump(test_data, f)
        
        result = optimize_rolling_data(section="swing")
        
        assert result["status"] == "success"
        assert result["section"] == "swing"
        assert "stats" in result
        assert result["stats"]["predictions_extracted"] == 1


@pytest.mark.unit
def test_in_memory_data_prevents_disk_read(temp_dir, mock_paths, sample_rolling_data):
    """Test that providing in-memory data prevents reading from disk file."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        # Create a different file on disk
        da_brains = temp_dir / "da_brains"
        rolling_body = da_brains / "rolling_body.json.gz"
        
        disk_data = {
            "WRONG": {
                "prediction": 0.0,
                "confidence": 0.5,
                "price": 1.0,
                "last": 1.0,
                "sentiment": "neutral",
                "timestamp": "2024-01-27T04:00:00Z"
            }
        }
        
        with gzip.open(rolling_body, "wt", encoding="utf-8") as f:
            json.dump(disk_data, f)
        
        # Use in-memory data instead
        optimizer = RollingOptimizer(section="swing", rolling_data=sample_rolling_data)
        predictions = optimizer._extract_predictions()
        
        # Should use in-memory data, not disk data
        assert len(predictions) == 3
        assert all(p["symbol"] != "WRONG" for p in predictions)
        assert predictions[0]["symbol"] == "AAPL"


@pytest.mark.unit
def test_in_memory_data_with_empty_dict(temp_dir, mock_paths):
    """Test that providing empty in-memory data returns no predictions."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing", rolling_data={})
        predictions = optimizer._extract_predictions()
        
        assert len(predictions) == 0


@pytest.mark.unit
def test_full_workflow_with_in_memory_data(temp_dir, mock_paths, sample_rolling_data):
    """Test the complete workflow: in-memory data -> optimize -> write optimized file."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        da_brains = temp_dir / "da_brains"
        
        result = optimize_rolling_data(section="swing", rolling_data=sample_rolling_data)
        
        assert result["status"] == "success"
        assert result["stats"]["predictions_extracted"] == 3
        
        # Verify optimized file was created
        rolling_optimized = da_brains / "rolling_optimized.json.gz"
        assert rolling_optimized.exists()
        
        # Load and verify content
        with gzip.open(rolling_optimized, "rt", encoding="utf-8") as f:
            data = json.load(f)
        
        assert "swing" in data
        assert "predictions" in data["swing"]
        assert data["swing"]["count"] == 3
        assert len(data["swing"]["predictions"]) == 3
        
        # Verify predictions are from in-memory data
        symbols = {p["symbol"] for p in data["swing"]["predictions"]}
        assert symbols == {"AAPL", "GOOGL", "TSLA"}
