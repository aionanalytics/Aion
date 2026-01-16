"""
Basic tests for snapshot manager and replay pipeline.
"""

import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd


def test_snapshot_manager_basic():
    """Test basic snapshot save/load functionality."""
    from backend.historical_replay_swing.snapshot_manager import (
        EODSnapshot,
        SnapshotManager,
    )
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SnapshotManager(Path(tmpdir))
        
        # Create test snapshot
        test_bars = pd.DataFrame({
            'symbol': ['AAPL', 'GOOGL'],
            'date': ['2024-01-01', '2024-01-01'],
            'close': [150.0, 2800.0],
        })
        
        snapshot = EODSnapshot(
            date='2024-01-01',
            bars=test_bars,
            fundamentals={'AAPL': {'pe_ratio': 25.0}},
            macro={'vix_close': 15.5},
            news=[],
            sentiment={},
            rolling_state={'AAPL': {'symbol': 'AAPL', 'close': 150.0}},
        )
        
        # Save snapshot
        manager.save_snapshot(snapshot)
        
        # Verify snapshot exists
        assert manager.snapshot_exists('2024-01-01')
        
        # Load snapshot
        loaded = manager.load_snapshot('2024-01-01')
        
        # Verify data
        assert loaded.date == '2024-01-01'
        assert len(loaded.bars) == 2
        assert 'AAPL' in loaded.fundamentals
        assert loaded.macro['vix_close'] == 15.5


def test_replay_mode_detection():
    """Test replay mode detection."""
    from backend.services.replay_data_pipeline import is_replay_mode, get_replay_date
    
    # Save original env
    orig_run_mode = os.environ.get("AION_RUN_MODE")
    orig_asof = os.environ.get("AION_ASOF_DATE")
    
    try:
        # Test normal mode
        os.environ.pop("AION_RUN_MODE", None)
        assert not is_replay_mode()
        assert get_replay_date() is None
        
        # Test replay mode
        os.environ["AION_RUN_MODE"] = "replay"
        os.environ["AION_ASOF_DATE"] = "2024-01-01"
        assert is_replay_mode()
        assert get_replay_date() == "2024-01-01"
        
    finally:
        # Restore original env
        if orig_run_mode is not None:
            os.environ["AION_RUN_MODE"] = orig_run_mode
        else:
            os.environ.pop("AION_RUN_MODE", None)
        if orig_asof is not None:
            os.environ["AION_ASOF_DATE"] = orig_asof
        else:
            os.environ.pop("AION_ASOF_DATE", None)


def test_snapshot_validation():
    """Test snapshot validation."""
    from backend.historical_replay_swing.snapshot_manager import EODSnapshot
    from backend.historical_replay_swing.validation import ReplayValidator
    
    validator = ReplayValidator()
    
    # Test valid snapshot
    valid_bars = pd.DataFrame({
        'symbol': ['AAPL'],
        'date': ['2024-01-01'],
        'close': [150.0],
    })
    
    valid_snapshot = EODSnapshot(
        date='2024-01-01',
        bars=valid_bars,
        fundamentals={},
        macro={},
        news=[],
        sentiment={},
        rolling_state={},
    )
    
    result = validator.validate_snapshot('2024-01-01', valid_snapshot)
    assert result.valid
    assert len(result.errors) == 0
    
    # Test snapshot with future data (should fail)
    future_bars = pd.DataFrame({
        'symbol': ['AAPL'],
        'date': ['2024-01-05'],  # Future date
        'close': [150.0],
    })
    
    future_snapshot = EODSnapshot(
        date='2024-01-01',
        bars=future_bars,
        fundamentals={},
        macro={},
        news=[],
        sentiment={},
        rolling_state={},
    )
    
    result = validator.validate_snapshot('2024-01-01', future_snapshot)
    assert not result.valid
    assert len(result.errors) > 0
    assert 'future data' in result.errors[0].lower()


if __name__ == '__main__':
    test_snapshot_manager_basic()
    print("✅ test_snapshot_manager_basic passed")
    
    test_replay_mode_detection()
    print("✅ test_replay_mode_detection passed")
    
    test_snapshot_validation()
    print("✅ test_snapshot_validation passed")
    
    print("\n✅ All tests passed!")
