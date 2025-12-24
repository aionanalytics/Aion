from __future__ import annotations

from typing import Any, Dict, Tuple

def horizon_valid(stats_horizons: Dict[str, Any], horizon: str) -> Tuple[bool, str | None]:
    """Validate a horizon for a given stats map (expected shape: {horizon: {...}} or nested)."""
    if not isinstance(stats_horizons, dict):
        return False, "no_stats"
    hstats = stats_horizons.get(horizon) or stats_horizons.get(horizon.lower()) or {}
    if not isinstance(hstats, dict):
        return False, "missing_horizon_stats"
    if hstats.get("valid_global") is False:
        return False, str(hstats.get("invalid_reason") or "invalid")
    # If valid_global not present, treat as unknown-but-allowed (backward compatible)
    return True, None
