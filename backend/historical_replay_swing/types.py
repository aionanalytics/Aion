"""
Shared types and interfaces for historical replay modules.

This module breaks circular dependencies by providing common types
that are imported by snapshot_manager, backfill_history, and replay_data_pipeline.
"""

from typing import Callable, Optional


# Type alias for load_universe function
LoadUniverseFunc = Callable[[], list[str]]


def get_load_universe() -> Optional[LoadUniverseFunc]:
    """
    Lazy import of load_universe to avoid circular dependency.
    Returns the load_universe function from backfill_history.
    """
    try:
        from backend.services.backfill_history import load_universe
        return load_universe
    except ImportError:
        return None


__all__ = [
    "LoadUniverseFunc",
    "get_load_universe",
]
