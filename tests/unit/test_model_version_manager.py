"""Unit tests for model version manager."""

import pytest
import json
from pathlib import Path
from datetime import datetime
from dt_backend.ml.model_version_manager import (
    save_model_version,
    load_model_version,
    list_model_versions,
    get_model_metadata,
    cleanup_old_versions,
)


@pytest.fixture
def temp_model_files(tmp_path):
    """Create temporary model files for testing."""
    model_file = tmp_path / "test_model.txt"
    model_file.write_text("mock model data")
    
    feature_map = tmp_path / "feature_map.json"
    feature_map.write_text(json.dumps(["feature1", "feature2", "feature3"]))
    
    return {
        "model.txt": model_file,
        "feature_map.json": feature_map,
    }


@pytest.fixture
def mock_dt_paths(monkeypatch, tmp_path):
    """Mock DT_PATHS for testing."""
    from dt_backend.ml import model_version_manager
    
    mock_paths = {
        "ml_data_dt": tmp_path / "ml_data_dt",
        "models_root": tmp_path / "models",
    }
    
    monkeypatch.setattr(model_version_manager, "get_models_root", lambda: mock_paths["models_root"])
    return mock_paths


def test_save_model_version(temp_model_files, mock_dt_paths):
    """Test saving a model version."""
    date_str = "2025-01-15"
    metadata = {"accuracy": 0.85, "samples": 10000}
    
    result = save_model_version(
        "lightgbm_intraday",
        date_str,
        temp_model_files,
        metadata,
    )
    
    assert result is True
    
    # Verify version directory was created
    versions_dir = mock_dt_paths["models_root"] / "versions" / "lightgbm_intraday"
    version_dir = versions_dir / date_str
    assert version_dir.exists()
    
    # Verify files were copied
    assert (version_dir / "model.txt").exists()
    assert (version_dir / "feature_map.json").exists()
    
    # Verify index was updated
    index_path = versions_dir / "version_index.json"
    assert index_path.exists()
    
    with index_path.open("r") as f:
        index = json.load(f)
    
    assert date_str in index["versions"]
    assert index["latest"] == date_str
    assert index["versions"][date_str]["metadata"] == metadata


def test_load_model_version(temp_model_files, mock_dt_paths):
    """Test loading a model version."""
    date_str = "2025-01-15"
    
    # Save a version first
    save_model_version("lightgbm_intraday", date_str, temp_model_files)
    
    # Load the version
    version_dir = load_model_version("lightgbm_intraday", date_str)
    
    assert version_dir is not None
    assert version_dir.exists()
    assert (version_dir / "model.txt").exists()


def test_load_nonexistent_version_with_fallback(temp_model_files, mock_dt_paths):
    """Test loading a nonexistent version with fallback to latest."""
    # Save a version
    save_model_version("lightgbm_intraday", "2025-01-15", temp_model_files)
    
    # Try to load a different date
    version_dir = load_model_version("lightgbm_intraday", "2025-01-20", fallback_to_latest=True)
    
    # Should fall back to the saved version
    assert version_dir is not None
    assert version_dir.name == "2025-01-15"


def test_load_nonexistent_version_without_fallback(mock_dt_paths):
    """Test loading a nonexistent version without fallback."""
    version_dir = load_model_version("lightgbm_intraday", "2025-01-20", fallback_to_latest=False)
    
    assert version_dir is None


def test_list_model_versions(temp_model_files, mock_dt_paths):
    """Test listing model versions."""
    # Save multiple versions
    dates = ["2025-01-10", "2025-01-15", "2025-01-20"]
    for date in dates:
        save_model_version("lightgbm_intraday", date, temp_model_files)
    
    # List versions
    versions = list_model_versions("lightgbm_intraday")
    
    assert versions == sorted(dates)


def test_get_model_metadata(temp_model_files, mock_dt_paths):
    """Test getting model metadata."""
    date_str = "2025-01-15"
    metadata = {"accuracy": 0.85, "samples": 10000}
    
    save_model_version("lightgbm_intraday", date_str, temp_model_files, metadata)
    
    retrieved_metadata = get_model_metadata("lightgbm_intraday", date_str)
    
    assert retrieved_metadata == metadata


def test_cleanup_old_versions(temp_model_files, mock_dt_paths):
    """Test cleanup of old versions."""
    # Save multiple versions
    dates = ["2025-01-01", "2025-01-05", "2025-01-10", "2025-01-15", "2025-01-20"]
    for date in dates:
        save_model_version("lightgbm_intraday", date, temp_model_files)
    
    # Cleanup, keeping only latest 3
    deleted_count = cleanup_old_versions("lightgbm_intraday", keep_latest_n=3, keep_days=0)
    
    # Should delete 2 versions (keeping latest 3)
    assert deleted_count == 2
    
    # Verify remaining versions
    versions = list_model_versions("lightgbm_intraday")
    assert len(versions) == 3
    assert versions == ["2025-01-10", "2025-01-15", "2025-01-20"]
