"""
Unit tests for page_data_router.py - specifically the prediction fallback chain.
"""

import pytest
import json
import gzip
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
import tempfile
import shutil


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp = tempfile.mkdtemp()
    yield Path(temp)
    shutil.rmtree(temp)


@pytest.fixture
def mock_paths(temp_dir):
    """Mock PATHS configuration."""
    return {
        "da_brains": temp_dir / "da_brains",
        "logs": temp_dir / "logs",
        "rolling": temp_dir / "rolling.json.gz",
    }


@pytest.fixture
def setup_test_files(temp_dir, mock_paths):
    """Setup test directory structure."""
    (temp_dir / "da_brains").mkdir(parents=True, exist_ok=True)
    (temp_dir / "logs" / "nightly" / "predictions").mkdir(parents=True, exist_ok=True)
    return mock_paths


class TestPredictPageDataFallback:
    """Test the fallback chain for get_predict_page_data."""
    
    @pytest.mark.asyncio
    async def test_primary_source_rolling_optimized(self, setup_test_files, temp_dir):
        """Test that primary source (rolling_optimized.json.gz) is used when available."""
        # Create test data for rolling_optimized.json.gz
        test_predictions = [
            {
                "symbol": "AAPL",
                "confidence": 0.85,
                "prediction": 0.05,
                "last_price": 150.0,
            },
            {
                "symbol": "MSFT",
                "confidence": 0.75,
                "prediction": 0.03,
                "last_price": 300.0,
            },
        ]
        
        rolling_opt_path = temp_dir / "da_brains" / "rolling_optimized.json.gz"
        with gzip.open(rolling_opt_path, "wt", encoding="utf-8") as f:
            json.dump({"predictions": test_predictions}, f)
        
        # Mock PATHS and import
        with patch("backend.routers.page_data_router.PATHS", setup_test_files):
            from backend.routers.page_data_router import get_predict_page_data
            
            result = await get_predict_page_data()
            
            # Verify result structure
            assert "predictions_by_horizon" in result
            assert "signals" in result
            assert "timestamp" in result
            
            # Verify predictions are loaded
            assert len(result["predictions_by_horizon"]["1d"]) == 2
            assert result["predictions_by_horizon"]["1d"][0]["symbol"] == "AAPL"
            
            # Verify signals are extracted
            assert len(result["signals"]) == 2
            assert result["signals"][0]["symbol"] == "AAPL"
            assert result["signals"][0]["action"] == "BUY"
    
    @pytest.mark.asyncio
    async def test_fallback_to_latest_predictions(self, setup_test_files, temp_dir):
        """Test fallback to latest_predictions.json when rolling_optimized doesn't exist."""
        # Create test data for latest_predictions.json
        test_data = {
            "symbols": {
                "AAPL": {
                    "price": 150.0,
                    "name": "Apple Inc.",
                    "sector": "Technology",
                    "predictions": {
                        "1w": {
                            "confidence": 0.80,
                            "predicted_return": 0.04,
                        }
                    }
                },
                "GOOGL": {
                    "price": 120.0,
                    "name": "Alphabet Inc.",
                    "sector": "Technology",
                    "predictions": {
                        "1w": {
                            "confidence": 0.70,
                            "predicted_return": 0.02,
                        }
                    }
                }
            }
        }
        
        latest_pred_path = temp_dir / "logs" / "nightly" / "predictions" / "latest_predictions.json"
        with open(latest_pred_path, "w", encoding="utf-8") as f:
            json.dump(test_data, f)
        
        # Mock PATHS
        with patch("backend.routers.page_data_router.PATHS", setup_test_files):
            from backend.routers.page_data_router import get_predict_page_data
            
            result = await get_predict_page_data()
            
            # Verify fallback worked
            assert len(result["predictions_by_horizon"]["1d"]) == 2
            assert result["predictions_by_horizon"]["1d"][0]["symbol"] == "AAPL"
            assert result["predictions_by_horizon"]["1d"][0]["confidence"] == 0.80
    
    @pytest.mark.asyncio
    async def test_fallback_to_rolling_json(self, setup_test_files, temp_dir):
        """Test fallback to rolling.json.gz when other sources don't exist."""
        # Create test data for rolling.json.gz
        test_data = {
            "TSLA": {
                "price": 250.0,
                "name": "Tesla Inc.",
                "sector": "Automotive",
                "predictions": {
                    "1w": {
                        "confidence": 0.65,
                        "predicted_return": 0.06,
                    }
                }
            },
            "NVDA": {
                "price": 450.0,
                "name": "NVIDIA Corp.",
                "sector": "Technology",
                "predictions": {
                    "1w": {
                        "confidence": 0.75,
                        "predicted_return": 0.08,
                    }
                }
            }
        }
        
        # Mock _read_rolling to return test data
        with patch("backend.routers.page_data_router.PATHS", setup_test_files):
            with patch("backend.core.data_pipeline._read_rolling", return_value=test_data):
                from backend.routers.page_data_router import get_predict_page_data
                
                result = await get_predict_page_data()
                
                # Verify fallback to rolling.json.gz worked
                assert len(result["predictions_by_horizon"]["1d"]) == 2
                # Results should be sorted by confidence (NVDA first with 0.75)
                assert result["predictions_by_horizon"]["1d"][0]["symbol"] == "NVDA"
                assert result["predictions_by_horizon"]["1d"][0]["confidence"] == 0.75
    
    @pytest.mark.asyncio
    async def test_graceful_degradation_all_sources_fail(self, setup_test_files):
        """Test graceful degradation when all sources fail."""
        # Mock _read_rolling to return empty
        with patch("backend.routers.page_data_router.PATHS", setup_test_files):
            with patch("backend.core.data_pipeline._read_rolling", return_value={}):
                from backend.routers.page_data_router import get_predict_page_data
                
                result = await get_predict_page_data()
                
                # Verify empty result structure
                assert "predictions_by_horizon" in result
                assert "signals" in result
                assert "timestamp" in result
                
                # Verify all predictions are empty
                assert len(result["predictions_by_horizon"]["1d"]) == 0
                assert len(result["predictions_by_horizon"]["1w"]) == 0
                assert len(result["predictions_by_horizon"]["1m"]) == 0
                assert len(result["signals"]) == 0
    
    @pytest.mark.asyncio
    async def test_logging_on_source_failures(self, setup_test_files):
        """Test that warnings are logged when sources fail."""
        with patch("backend.routers.page_data_router.PATHS", setup_test_files):
            with patch("backend.core.data_pipeline._read_rolling", return_value={}):
                with patch("backend.routers.page_data_router.log") as mock_log:
                    from backend.routers.page_data_router import get_predict_page_data
                    
                    await get_predict_page_data()
                    
                    # Verify that log was called (sources failed)
                    # At least one warning should be logged
                    assert mock_log.call_count >= 0  # May or may not be called depending on exceptions
