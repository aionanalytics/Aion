"""
dt_backend/ml/model_version_manager.py

Point-in-time model version manager for historical replay.

Provides:
- Save model versions by date when training
- Load model versions by date for replay
- Maintain version index with metadata
- Automatic fallback to latest if version not found

Usage (training):
    >>> from dt_backend.ml.model_version_manager import save_model_version
    >>> save_model_version("lightgbm_intraday", "2025-01-15", 
    ...     model_files={"model.txt": model_path}, 
    ...     metadata={"accuracy": 0.85})

Usage (replay):
    >>> from dt_backend.ml.model_version_manager import load_model_version
    >>> model_path = load_model_version("lightgbm_intraday", "2025-01-15")
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    from dt_backend.models import get_model_dir, get_models_root
    from dt_backend.core.data_pipeline_dt import log
except Exception:
    def get_model_dir(model_type: str) -> Path:
        return Path("dt_backend") / "models" / model_type
    
    def get_models_root() -> Path:
        return Path("dt_backend") / "models"
    
    def log(msg: str) -> None:
        print(msg, flush=True)


def _get_versions_dir(model_type: str) -> Path:
    """Get the directory for versioned models."""
    root = get_models_root()
    versions_dir = root / "versions" / model_type
    versions_dir.mkdir(parents=True, exist_ok=True)
    return versions_dir


def _get_version_index_path(model_type: str) -> Path:
    """Get the path to the version index file."""
    versions_dir = _get_versions_dir(model_type)
    return versions_dir / "version_index.json"


def _load_version_index(model_type: str) -> Dict[str, Any]:
    """Load the version index."""
    index_path = _get_version_index_path(model_type)
    if not index_path.exists():
        return {"versions": {}, "latest": None}
    
    try:
        with index_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"[model_version] ⚠️ Failed to load version index: {e}")
        return {"versions": {}, "latest": None}


def _save_version_index(model_type: str, index: Dict[str, Any]) -> None:
    """Save the version index."""
    index_path = _get_version_index_path(model_type)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with index_path.open("w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"[model_version] ❌ Failed to save version index: {e}")


def save_model_version(
    model_type: str,
    date_str: str,
    model_files: Dict[str, Path],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Save a model version for a specific date.
    
    Args:
        model_type: Type of model (e.g., "lightgbm_intraday")
        date_str: Date string in ISO format (YYYY-MM-DD)
        model_files: Dict mapping destination filename to source path
        metadata: Optional metadata about the model
    
    Returns:
        True if successful, False otherwise
    
    Example:
        >>> save_model_version(
        ...     "lightgbm_intraday",
        ...     "2025-01-15",
        ...     {"model.txt": Path("/tmp/model.txt"), "feature_map.json": Path("/tmp/fmap.json")},
        ...     metadata={"accuracy": 0.85, "samples": 10000}
        ... )
    """
    try:
        # Create version directory
        versions_dir = _get_versions_dir(model_type)
        version_dir = versions_dir / date_str
        version_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy model files
        for dest_name, src_path in model_files.items():
            if not src_path.exists():
                log(f"[model_version] ⚠️ Source file not found: {src_path}")
                continue
            
            dest_path = version_dir / dest_name
            shutil.copy2(src_path, dest_path)
            log(f"[model_version] 📦 Saved {dest_name} to version {date_str}")
        
        # Update version index
        index = _load_version_index(model_type)
        index["versions"][date_str] = {
            "date": date_str,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "files": list(model_files.keys()),
            "metadata": metadata or {},
        }
        index["latest"] = date_str
        _save_version_index(model_type, index)
        
        log(f"[model_version] ✅ Saved {model_type} version {date_str}")
        return True
        
    except Exception as e:
        log(f"[model_version] ❌ Failed to save model version: {e}")
        return False


def load_model_version(
    model_type: str,
    date_str: str,
    fallback_to_latest: bool = True,
) -> Optional[Path]:
    """
    Load a model version for a specific date.
    
    Args:
        model_type: Type of model (e.g., "lightgbm_intraday")
        date_str: Date string in ISO format (YYYY-MM-DD)
        fallback_to_latest: If True, fall back to latest version if date not found
    
    Returns:
        Path to the versioned model directory, or None if not found
    
    Example:
        >>> version_dir = load_model_version("lightgbm_intraday", "2025-01-15")
        >>> if version_dir:
        ...     model_path = version_dir / "model.txt"
    """
    try:
        versions_dir = _get_versions_dir(model_type)
        version_dir = versions_dir / date_str
        
        if version_dir.exists():
            log(f"[model_version] ✅ Loaded {model_type} version {date_str}")
            return version_dir
        
        # Try fallback to latest
        if fallback_to_latest:
            index = _load_version_index(model_type)
            latest = index.get("latest")
            
            if latest and latest != date_str:
                latest_dir = versions_dir / latest
                if latest_dir.exists():
                    log(f"[model_version] ⚠️ Version {date_str} not found, using latest: {latest}")
                    return latest_dir
        
        log(f"[model_version] ⚠️ No version found for {model_type} on {date_str}")
        return None
        
    except Exception as e:
        log(f"[model_version] ❌ Failed to load model version: {e}")
        return None


def list_model_versions(model_type: str) -> List[str]:
    """
    List all available versions for a model type.
    
    Args:
        model_type: Type of model
    
    Returns:
        List of date strings for available versions
    """
    try:
        index = _load_version_index(model_type)
        return sorted(index.get("versions", {}).keys())
    except Exception as e:
        log(f"[model_version] ❌ Failed to list versions: {e}")
        return []


def get_model_metadata(model_type: str, date_str: str) -> Optional[Dict[str, Any]]:
    """
    Get metadata for a specific model version.
    
    Args:
        model_type: Type of model
        date_str: Date string
    
    Returns:
        Metadata dict or None if not found
    """
    try:
        index = _load_version_index(model_type)
        version_info = index.get("versions", {}).get(date_str)
        if version_info:
            return version_info.get("metadata", {})
        return None
    except Exception as e:
        log(f"[model_version] ❌ Failed to get metadata: {e}")
        return None


def cleanup_old_versions(
    model_type: str,
    keep_latest_n: int = 30,
    keep_days: int = 90,
) -> int:
    """
    Clean up old model versions.
    
    Args:
        model_type: Type of model
        keep_latest_n: Keep this many most recent versions
        keep_days: Keep versions from the last N days
    
    Returns:
        Number of versions deleted
    """
    try:
        index = _load_version_index(model_type)
        versions = index.get("versions", {})
        
        if not versions:
            return 0
        
        # Sort by date (newest first)
        sorted_dates = sorted(versions.keys(), reverse=True)
        
        # Determine cutoff date
        cutoff_date = (
            datetime.now(timezone.utc).date().isoformat()
        )
        from datetime import timedelta
        cutoff_date = (
            datetime.now(timezone.utc).date() - timedelta(days=keep_days)
        ).isoformat()
        
        deleted_count = 0
        versions_dir = _get_versions_dir(model_type)
        
        for i, date_str in enumerate(sorted_dates):
            # Keep latest N versions
            if i < keep_latest_n:
                continue
            
            # Keep versions within keep_days
            if date_str >= cutoff_date:
                continue
            
            # Delete this version
            version_dir = versions_dir / date_str
            if version_dir.exists():
                shutil.rmtree(version_dir)
                deleted_count += 1
                log(f"[model_version] 🗑️ Deleted old version: {date_str}")
            
            # Remove from index
            del versions[date_str]
        
        # Update index
        if deleted_count > 0:
            _save_version_index(model_type, index)
        
        return deleted_count
        
    except Exception as e:
        log(f"[model_version] ❌ Failed to cleanup versions: {e}")
        return 0


__all__ = [
    "save_model_version",
    "load_model_version",
    "list_model_versions",
    "get_model_metadata",
    "cleanup_old_versions",
]
