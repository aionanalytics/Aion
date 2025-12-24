# backend/services/buzz_engine.py
"""
Buzz Engine v1.0

Purpose:
- Turn raw counts of news/social mentions into a "buzz score" per symbol.
- For now:
    * Uses news article counts as primary buzz
    * Optionally pulls from social data (future extension)
- Outputs:
    symbol -> {
        "buzz_count": int,
        "buzz_score": float,    # normalized 0..1-ish
    }

This can be used for:
- Volatility expectation
- Attention regime
- Policy tweaks (avoid overreacting to meme spikes)
"""

from __future__ import annotations

from typing import Dict, Any, List

import math

from utils.logger import warn


def compute_news_buzz(articles_by_symbol: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """
    Simple buzz: count of articles per symbol, normalized against max.
    """
    counts: Dict[str, int] = {sym: len(arts or []) for sym, arts in articles_by_symbol.items()}
    if not counts:
        return {}

    max_count = max(counts.values()) or 1
    out: Dict[str, Dict[str, Any]] = {}
    for sym, c in counts.items():
        # non-linear scaling so big spikes stand out
        score = math.log1p(c) / math.log1p(max_count)
        out[sym] = {
            "buzz_count": int(c),
            "buzz_score": float(score),
        }
    return out
