"""
Unit tests for rolling optimizer section-based updates.

Tests:
1. Swing section updates don't affect DT section
2. DT section updates don't affect swing section  
3. Atomic writes work correctly
4. File format is correct
"""

import gzip
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def create_test_rolling_body(path: Path):
    """Create a test rolling_body.json.gz file with swing predictions."""
    data = {
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
        }
    }
    
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)


def create_test_rolling_intraday(path: Path):
    """Create a test rolling_intraday.json.gz file with DT data."""
    data = {
        "TSLA": {
            "predictions_dt": {
                "label": "BUY",
                "proba": {"BUY": 0.7, "HOLD": 0.2, "SELL": 0.1},
                "confidence": 0.7
            }
        },
        "MSFT": {
            "predictions_dt": {
                "label": "HOLD",
                "proba": {"BUY": 0.3, "HOLD": 0.5, "SELL": 0.2},
                "confidence": 0.5
            }
        }
    }
    
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)


@pytest.mark.unit
def test_swing_section_initialization(temp_dir, mock_paths):
    """Test that swing section initializes with rolling_body input."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing")
        
        assert optimizer.section == "swing"
        assert optimizer.rolling_input.name == "rolling_body.json.gz"
        assert "rolling_body.json.gz" in str(optimizer.rolling_input)


@pytest.mark.unit
def test_dt_section_initialization(temp_dir, mock_paths):
    """Test that DT section initializes with rolling_intraday input."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="dt")
        
        assert optimizer.section == "dt"
        assert optimizer.rolling_input.name == "rolling_intraday.json.gz"
        assert "rolling_intraday.json.gz" in str(optimizer.rolling_input)


@pytest.mark.unit
def test_load_existing_file_creates_base_structure(temp_dir, mock_paths):
    """Test that loading non-existent file creates base structure."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing")
        
        data = optimizer._load_existing_file()
        
        assert "timestamp" in data
        assert "swing" in data
        assert "dt" in data
        assert "swing_bots" in data
        assert data["swing"] == {}
        assert data["dt"] == {}
        assert data["swing_bots"] == {}


@pytest.mark.unit
def test_swing_update_preserves_dt_section(temp_dir, mock_paths):
    """Test that swing update doesn't overwrite DT section."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        # Create initial file with DT data
        optimizer = RollingOptimizer(section="swing")
        da_brains = temp_dir / "da_brains"
        
        initial_data = {
            "timestamp": "2024-01-27T10:00:00Z",
            "swing": {},
            "dt": {
                "bots": {"dt_bot_1": {"equity": 50000}},
                "portfolio": {"holdings": [{"symbol": "TSLA", "qty": 100}]},
                "timestamp": "2024-01-27T10:00:00Z"
            },
            "swing_bots": {}
        }
        
        with gzip.open(optimizer.rolling_optimized, "wt", encoding="utf-8") as f:
            json.dump(initial_data, f)
        
        # Create test rolling_body
        create_test_rolling_body(optimizer.rolling_input)
        
        # Run swing optimization
        result = optimizer.stream_and_optimize()
        
        # Verify swing section was updated
        assert result["status"] == "success"
        assert result["section"] == "swing"
        
        # Load and verify DT section was preserved
        with gzip.open(optimizer.rolling_optimized, "rt", encoding="utf-8") as f:
            data = json.load(f)
        
        # Check DT section unchanged
        assert data["dt"]["bots"]["dt_bot_1"]["equity"] == 50000
        assert data["dt"]["portfolio"]["holdings"][0]["symbol"] == "TSLA"
        
        # Check swing section was updated
        assert "swing" in data
        assert "predictions" in data["swing"]
        assert len(data["swing"]["predictions"]) > 0


@pytest.mark.unit
def test_dt_update_preserves_swing_section(temp_dir, mock_paths):
    """Test that DT update doesn't overwrite swing section."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        # Create initial file with swing data
        optimizer = RollingOptimizer(section="dt")
        da_brains = temp_dir / "da_brains"
        intraday_dir = da_brains / "intraday"
        intraday_dir.mkdir(parents=True, exist_ok=True)
        
        initial_data = {
            "timestamp": "2024-01-27T04:00:00Z",
            "swing": {
                "predictions": [{"symbol": "AAPL", "confidence": 0.85}],
                "timestamp": "2024-01-27T04:00:00Z",
                "count": 1
            },
            "dt": {},
            "swing_bots": {
                "bots": {"swing_bot_1": {"equity": 100000}},
                "timestamp": "2024-01-27T04:00:00Z"
            }
        }
        
        with gzip.open(optimizer.rolling_optimized, "wt", encoding="utf-8") as f:
            json.dump(initial_data, f)
        
        # Create test rolling_intraday
        create_test_rolling_intraday(optimizer.rolling_input)
        
        # Run DT optimization (mocking bot extraction since we don't have bot files)
        with patch.object(optimizer, '_extract_bots_data', return_value={"bots": {}, "timestamp": "2024-01-27T15:00:00Z"}):
            with patch.object(optimizer, '_extract_portfolio_data', return_value={"holdings": [], "timestamp": "2024-01-27T15:00:00Z"}):
                result = optimizer.stream_and_optimize()
        
        # Verify DT section was updated
        assert result["status"] == "success"
        assert result["section"] == "dt"
        
        # Load and verify swing section was preserved
        with gzip.open(optimizer.rolling_optimized, "rt", encoding="utf-8") as f:
            data = json.load(f)
        
        # Check swing section unchanged
        assert data["swing"]["predictions"][0]["symbol"] == "AAPL"
        assert data["swing"]["predictions"][0]["confidence"] == 0.85
        assert data["swing_bots"]["bots"]["swing_bot_1"]["equity"] == 100000
        
        # Check DT section was updated
        assert "dt" in data
        assert data["dt"]["timestamp"] is not None


@pytest.mark.unit
def test_atomic_write_creates_temp_file(temp_dir, mock_paths):
    """Test that atomic write uses temp file."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing")
        
        test_data = {
            "timestamp": "2024-01-27T15:00:00Z",
            "swing": {"test": "data"},
            "dt": {},
            "swing_bots": {}
        }
        
        # Write should succeed
        optimizer._write_atomically(test_data)
        
        # Verify file was created
        assert optimizer.rolling_optimized.exists()
        
        # Verify content
        with gzip.open(optimizer.rolling_optimized, "rt", encoding="utf-8") as f:
            data = json.load(f)
        
        assert data["swing"]["test"] == "data"


@pytest.mark.unit
def test_optimize_rolling_data_function_swing(temp_dir, mock_paths):
    """Test main entry point with swing section."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        da_brains = temp_dir / "da_brains"
        rolling_body = da_brains / "rolling_body.json.gz"
        
        create_test_rolling_body(rolling_body)
        
        result = optimize_rolling_data(section="swing")
        
        assert result["status"] == "success"
        assert result["section"] == "swing"
        assert "stats" in result


@pytest.mark.unit
def test_optimize_rolling_data_function_dt(temp_dir, mock_paths):
    """Test main entry point with DT section."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        da_brains = temp_dir / "da_brains"
        intraday_dir = da_brains / "intraday"
        intraday_dir.mkdir(parents=True, exist_ok=True)
        rolling_intraday = intraday_dir / "rolling_intraday.json.gz"
        
        create_test_rolling_intraday(rolling_intraday)
        
        # Mock bot extraction
        with patch("backend.services.rolling_optimizer.RollingOptimizer._extract_bots_data") as mock_bots:
            with patch("backend.services.rolling_optimizer.RollingOptimizer._extract_portfolio_data") as mock_portfolio:
                mock_bots.return_value = {"bots": {}, "timestamp": "2024-01-27T15:00:00Z"}
                mock_portfolio.return_value = {"holdings": [], "timestamp": "2024-01-27T15:00:00Z"}
                
                result = optimize_rolling_data(section="dt")
        
        assert result["status"] == "success"
        assert result["section"] == "dt"
        assert "stats" in result


@pytest.mark.unit
def test_unified_format_structure(temp_dir, mock_paths):
    """Test that the unified format has correct structure."""
    with patch("backend.services.rolling_optimizer.PATHS", mock_paths):
        optimizer = RollingOptimizer(section="swing")
        da_brains = temp_dir / "da_brains"
        
        create_test_rolling_body(optimizer.rolling_input)
        
        result = optimizer.stream_and_optimize()
        assert result["status"] == "success"
        
        # Load and verify structure
        with gzip.open(optimizer.rolling_optimized, "rt", encoding="utf-8") as f:
            data = json.load(f)
        
        # Check top-level structure
        assert "timestamp" in data
        assert "swing" in data
        assert "dt" in data
        assert "swing_bots" in data
        
        # Check swing section structure
        assert "predictions" in data["swing"]
        assert "timestamp" in data["swing"]
        assert "count" in data["swing"]
        
        # Check swing_bots section structure
        assert "bots" in data["swing_bots"]
        assert "timestamp" in data["swing_bots"]
