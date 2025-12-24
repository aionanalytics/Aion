
from __future__ import annotations

"""
Sector-specific feature pruning policy (v1).

Goal:
  - For sector models, remove feature families that are very likely irrelevant or misleading
    for that sector, while preserving core market, volatility, drift, macro, and context features.
  - Conservative-by-default. Refuses to over-prune.

Design principles:
  1) Subtractive only: start from global feature list, remove a small set of patterns.
  2) Protected core: never prune protected prefixes/features (price/vol/drift/macro/ctx/regime/risk/sector features).
  3) Safety rails: cap pruning fraction; require minimum feature count after pruning; fallback to original if unsafe.
  4) Transparent: returns (kept, removed, meta) and can log a concise summary.
"""

import os
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Dict, List, Optional, Sequence, Tuple

from utils.logger import log

def _env_bool(key: str, default: bool) -> bool:
    v = str(os.getenv(key, "1" if default else "0")).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return int(default)

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return float(default)

ENABLE_PRUNING_DEFAULT = True
AION_ENABLE_SECTOR_FEATURE_PRUNING = "AION_ENABLE_SECTOR_FEATURE_PRUNING"
AION_SECTOR_MIN_FEATURES = "AION_SECTOR_MIN_FEATURES"
AION_SECTOR_MAX_PRUNE_FRAC = "AION_SECTOR_MAX_PRUNE_FRAC"
AION_SECTOR_PRUNE_LOG = "AION_SECTOR_PRUNE_LOG"

DEFAULT_MIN_FEATURES = 140
DEFAULT_MAX_PRUNE_FRAC = 0.18
DEFAULT_LOG = True

PROTECTED_PREFIXES: Tuple[str, ...] = (
    "ret_", "logret_", "rsi_", "atr_", "vol_", "volatility_", "beta_", "corr_",
    "drawdown_", "range_", "momentum_", "momo_", "trend_", "ema_", "sma_", "macd_",
    "drift_", "hit_ratio_", "mae_", "perf_",
    "macro_", "ctx_", "regime_", "risk_",
    "sector_",
    "market_", "spy_", "qqq_", "vix_", "breadth_",
)

PROTECTED_EXACT: Tuple[str, ...] = (
    "symbol", "asof_date",
    "close", "open", "high", "low", "volume",
)

def _is_protected(feature: str) -> bool:
    if feature in PROTECTED_EXACT:
        return True
    return any(feature.startswith(p) for p in PROTECTED_PREFIXES)

SECTOR_EXCLUDE_PATTERNS: Dict[str, List[str]] = {
    "ENERGY": ["bank_*", "credit_spread_*", "yield_spread_*", "swap_spread_*", "consumer_*", "retail_*", "housing_*", "crypto_*"],
    "FINANCIALS": ["oil_*", "*_wti_*", "*_brent_*", "natural_gas_*", "*_henryhub_*", "metals_*", "gold_*", "silver_*", "copper_*", "agri_*", "grain_*", "corn_*", "wheat_*", "soy_*", "shipping_*", "freight_*", "semis_*", "chip_*"],
    "TECHNOLOGY": ["oil_*", "*_wti_*", "*_brent_*", "natural_gas_*", "*_henryhub_*", "metals_*", "gold_*", "silver_*", "copper_*", "agri_*", "grain_*", "corn_*", "wheat_*", "soy_*", "shipping_*", "freight_*"],
    "HEALTHCARE": ["oil_*", "*_wti_*", "*_brent_*", "natural_gas_*", "*_henryhub_*", "metals_*", "gold_*", "silver_*", "copper_*", "agri_*", "grain_*", "corn_*", "wheat_*", "soy_*", "shipping_*", "freight_*"],
}

SECTOR_ALIASES = {
    "COMMUNICATIONS": "COMMUNICATION_SERVICES",
    "REAL ESTATE": "REAL_ESTATE",
}

@dataclass
class PruneResult:
    sector: str
    enabled: bool
    kept: List[str]
    removed: List[str]
    protected_kept: int
    removable_total: int
    removed_count: int
    removed_frac: float
    reason: Optional[str] = None

def normalize_sector(sector: str) -> str:
    s = (sector or "UNKNOWN").strip().upper()
    s = s.replace("/", "_").replace("-", "_")
    s = re.sub(r"\s+", "_", s)
    return SECTOR_ALIASES.get(s, s) or "UNKNOWN"

def prune_features_for_sector(
    sector: str,
    feature_cols: Sequence[str],
    *,
    enabled: Optional[bool] = None,
    min_features: Optional[int] = None,
    max_prune_frac: Optional[float] = None,
    log_summary: Optional[bool] = None,
) -> PruneResult:
    sec = normalize_sector(sector)
    if enabled is None:
        enabled = _env_bool(AION_ENABLE_SECTOR_FEATURE_PRUNING, ENABLE_PRUNING_DEFAULT)
    if min_features is None:
        min_features = _env_int(AION_SECTOR_MIN_FEATURES, DEFAULT_MIN_FEATURES)
    if max_prune_frac is None:
        max_prune_frac = _env_float(AION_SECTOR_MAX_PRUNE_FRAC, DEFAULT_MAX_PRUNE_FRAC)
    if log_summary is None:
        log_summary = _env_bool(AION_SECTOR_PRUNE_LOG, DEFAULT_LOG)

    feats = [str(f) for f in feature_cols if f is not None]
    if not enabled or sec not in SECTOR_EXCLUDE_PATTERNS:
        return PruneResult(sec, enabled, feats, [], sum(_is_protected(f) for f in feats),
                           sum(not _is_protected(f) for f in feats), 0, 0.0, "noop")

    protected = [f for f in feats if _is_protected(f)]
    removable = [f for f in feats if not _is_protected(f)]

    to_remove = [f for f in removable if any(fnmatch(f, pat) for pat in SECTOR_EXCLUDE_PATTERNS[sec])]
    kept = protected + [f for f in removable if f not in to_remove]

    if len(kept) < min_features:
        return PruneResult(sec, enabled, feats, [], len(protected), len(removable), 0, 0.0, "refused_min_features")

    removed_frac = len(to_remove) / max(1, len(removable))
    if removed_frac > max_prune_frac:
        return PruneResult(sec, enabled, feats, [], len(protected), len(removable), 0, 0.0, "refused_overprune")

    if log_summary:
        log(f"[sector_prune] sector={sec} kept={len(kept)} removed={len(to_remove)}")

    return PruneResult(sec, enabled, kept, to_remove, len(protected), len(removable), len(to_remove), removed_frac, "ok")
