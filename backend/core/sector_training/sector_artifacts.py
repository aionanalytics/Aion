from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.core.config import PATHS

ML_DATA_ROOT: Path = PATHS.get("ml_data", Path("ml_data"))
MODEL_ROOT: Path = PATHS.get("ml_models", ML_DATA_ROOT / "nightly" / "models")
METRICS_ROOT: Path = ML_DATA_ROOT / "metrics" / "sector"

@dataclass(frozen=True)
class SectorPaths:
    sector: str
    model_dir: Path
    metrics_dir: Path

def normalize_sector(sector: str) -> str:
    s = (sector or "UNKNOWN").strip().upper()
    s = s.replace(" ", "_").replace("/", "_")
    return s or "UNKNOWN"

def sector_paths(sector: str) -> SectorPaths:
    sec = normalize_sector(sector)
    model_dir = MODEL_ROOT / "sector" / sec
    metrics_dir = METRICS_ROOT / sec
    model_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    return SectorPaths(sector=sec, model_dir=model_dir, metrics_dir=metrics_dir)

def env_max_workers(default: int = 8) -> int:
    try:
        return int(os.getenv("AION_TRAIN_MAX_WORKERS", str(default)))
    except Exception:
        return int(default)
