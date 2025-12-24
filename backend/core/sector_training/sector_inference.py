"""backend.core.sector_training.sector_inference

Sector-aware inference wrapper.

Resolution order per (symbol, horizon):
  1) sector model (if trained + horizon valid)
  2) global model (if trained + horizon valid)
  3) None (caller can mark invalid/silent)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from pathlib import Path

from backend.core import ai_model
from backend.core.sector_training.sector_registry import load_symbol_sector_map, normalize_sector_name, sector_slug, UNKNOWN_SECTOR
from backend.core.sector_training.sector_validator import load_return_stats, horizon_valid

@dataclass
class SectorModelBundle:
    sector: str
    model_root: Path
    stats_path: Path
    models: Dict[str, Any]
    stats: Dict[str, Any]

class SectorModelStore:
    def __init__(self):
        self._symbol_sector = load_symbol_sector_map()
        self._bundle_cache: Dict[str, SectorModelBundle] = {}
        self._global_models: Optional[Dict[str, Any]] = None
        self._global_stats: Optional[Dict[str, Any]] = None

    def refresh_sector_map(self):
        self._symbol_sector = load_symbol_sector_map()

    def sector_for(self, symbol: str) -> str:
        return normalize_sector_name(self._symbol_sector.get(str(symbol).upper()))

    def _sector_paths(self, sector: str) -> Tuple[Path, Path]:
        sec = normalize_sector_name(sector)
        model_root = ai_model.MODEL_ROOT / "sector" / sector_slug(sec)
        stats_path = ai_model.METRICS_ROOT / "sector" / sector_slug(sec) / "return_stats.json"
        return model_root, stats_path

    def _load_sector_bundle(self, sector: str) -> Optional[SectorModelBundle]:
        sec = normalize_sector_name(sector)
        if sec in self._bundle_cache:
            return self._bundle_cache[sec]

        model_root, stats_path = self._sector_paths(sec)
        if not model_root.exists():
            return None

        models = ai_model._load_regressors(model_root=model_root)
        stats = load_return_stats(stats_path)
        bundle = SectorModelBundle(sector=sec, model_root=model_root, stats_path=stats_path, models=models, stats=stats)
        self._bundle_cache[sec] = bundle
        return bundle

    def _ensure_global(self):
        if self._global_models is None:
            self._global_models = ai_model._load_regressors(model_root=ai_model.MODEL_ROOT)
        if self._global_stats is None:
            self._global_stats = ai_model._load_return_stats()

    def resolve_model(self, symbol: str, horizon: str) -> Tuple[Optional[Any], Dict[str, Any]]:
        """Return (model, meta). meta includes coverage level + reasons."""
        sec = self.sector_for(symbol)
        # sector model first
        bundle = self._load_sector_bundle(sec)
        if bundle is not None:
            ok, reason = horizon_valid(bundle.stats, horizon)
            if ok and horizon in bundle.models:
                return bundle.models[horizon], {"coverage": "sector", "sector": sec, "reason": "ok"}

        # global fallback
        self._ensure_global()
        ok_g, reason_g = horizon_valid(self._global_stats or {}, horizon)
        if ok_g and self._global_models and horizon in self._global_models:
            return self._global_models[horizon], {"coverage": "global", "sector": sec, "reason": "ok"}

        return None, {"coverage": "none", "sector": sec, "reason": reason_g if not ok_g else "missing_model"}
