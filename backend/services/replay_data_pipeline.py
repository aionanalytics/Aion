"""
Replay-safe data loaders that use snapshots instead of live fetching.

When AION_RUN_MODE=replay, all data comes from snapshots.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd


def is_replay_mode() -> bool:
    """Check if running in replay mode."""
    return os.getenv("AION_RUN_MODE", "") == "replay"


def get_replay_date() -> Optional[str]:
    """Get as-of-date for replay."""
    return os.getenv("AION_ASOF_DATE")


def load_bars_for_replay(date: str) -> pd.DataFrame:
    """Load bars from snapshot (not live)."""
    from backend.core.config import PATHS
    # Lazy import to avoid circular dependency
    from backend.historical_replay_swing.snapshot_manager import SnapshotManager
    
    manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
    snapshot = manager.load_snapshot(date)
    return snapshot.bars


def load_fundamentals_for_replay(date: str) -> Dict[str, Any]:
    """Load fundamentals from snapshot."""
    from backend.core.config import PATHS
    # Lazy import to avoid circular dependency
    from backend.historical_replay_swing.snapshot_manager import SnapshotManager
    
    manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
    snapshot = manager.load_snapshot(date)
    return snapshot.fundamentals


def load_macro_for_replay(date: str) -> Dict[str, Any]:
    """Load macro from snapshot."""
    from backend.core.config import PATHS
    # Lazy import to avoid circular dependency
    from backend.historical_replay_swing.snapshot_manager import SnapshotManager
    
    manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
    snapshot = manager.load_snapshot(date)
    return snapshot.macro


def load_rolling_for_replay(date: str) -> Dict[str, Any]:
    """Load rolling cache from snapshot."""
    from backend.core.config import PATHS
    # Lazy import to avoid circular dependency
    from backend.historical_replay_swing.snapshot_manager import SnapshotManager
    
    manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
    snapshot = manager.load_snapshot(date)
    return snapshot.rolling_state


__all__ = [
    "is_replay_mode",
    "get_replay_date",
    "load_bars_for_replay",
    "load_fundamentals_for_replay",
    "load_macro_for_replay",
    "load_rolling_for_replay",
]
