# backend/services/sentiment_engine.py
"""
Sentiment Engine v1.0

Shared logic to turn article-level sentiment into:
- Long-horizon sentiment features (for nightly / ml_data)
- Intraday shock scores (for dt_backend / ml_data_dt)

Relies primarily on:
- article["sentiment_score"]  (0..1)
- article["relevance_score"]
- recency
- simple event keywords

Optionally uses embeddings if `sentence_transformers` is installed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import math

from utils.logger import warn

# Optional embedding support
try:
    from sentence_transformers import SentenceTransformer  # type: ignore

    _EMBED_MODEL: Optional[SentenceTransformer] = SentenceTransformer(
        "all-MiniLM-L6-v2"
    )
except Exception:
    _EMBED_MODEL = None
    warn("[sentiment_engine] sentence_transformers not available; embeddings disabled.")


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _extract_base_sentiment(article: Dict[str, Any]) -> float:
    s = article.get("sentiment_score")
    if s is None:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _extract_relevance(article: Dict[str, Any]) -> float:
    r = article.get("relevance_score")
    if r is None:
        return 0.5
    try:
        return float(r)
    except Exception:
        return 0.5


def _extract_published_at(article: Dict[str, Any]) -> Optional[datetime]:
    ts = article.get("published_at")
    if not ts:
        return None
    try:
        # Already ISO from news_fetcher
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _recency_weight(published_at: Optional[datetime], now: datetime, half_life_hours: float = 24.0) -> float:
    if not published_at:
        return 0.5
    dt_hours = (now - published_at).total_seconds() / 3600.0
    if dt_hours < 0:
        dt_hours = 0
    # exponential decay
    return math.exp(-dt_hours * math.log(2) / max(0.1, half_life_hours))


def _event_boost(headline: str) -> float:
    """
    Simple keyword-based boost for strong events.
    """
    h = (headline or "").lower()
    boost = 1.0
    if any(k in h for k in ["beats estimates", "beat estimates", "raises guidance", "upgrades", "upgrade"]):
        boost *= 1.2
    if any(k in h for k in ["misses estimates", "cuts guidance", "downgrade", "downgrades"]):
        boost *= 1.2
    if any(k in h for k in ["sec investigation", "fraud", "probe", "lawsuit"]):
        boost *= 1.3
    return boost


def embed_headlines(articles: List[Dict[str, Any]]) -> Optional[List[List[float]]]:
    """
    Optional: produce embeddings for headlines.
    Returns list of vectors or None if embedding model is not available.
    """
    if _EMBED_MODEL is None or not articles:
        return None
    texts = [a.get("headline") or "" for a in articles]
    try:
        emb = _EMBED_MODEL.encode(texts, convert_to_numpy=True)
        return emb.tolist()
    except Exception as e:
        warn(f"[sentiment_engine] embedding failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Long-horizon sentiment (nightly / ml_data)
# ---------------------------------------------------------------------------

def compute_long_horizon_sentiment(
    articles_by_symbol: Dict[str, List[Dict[str, Any]]],
    now: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Aggregate article sentiment into long-horizon features per symbol.

    Returns:
      symbol -> {
        "sentiment_mean": float,
        "sentiment_weighted": float,
        "sentiment_max": float,
        "article_count": int,
        "recency_weight_sum": float,
      }
    """
    now = now or datetime.utcnow()
    out: Dict[str, Dict[str, Any]] = {}

    for symbol, arts in articles_by_symbol.items():
        if not arts:
            out[symbol] = {
                "sentiment_mean": 0.0,
                "sentiment_weighted": 0.0,
                "sentiment_max": 0.0,
                "article_count": 0,
                "recency_weight_sum": 0.0,
            }
            continue

        vals = []
        weighted_vals = []
        weight_sum = 0.0
        max_s = 0.0

        for a in arts:
            s = _extract_base_sentiment(a)
            r = _extract_relevance(a)
            t = _extract_published_at(a)
            w_rec = _recency_weight(t, now, half_life_hours=48.0)
            w = r * w_rec * _event_boost(a.get("headline") or "")
            vals.append(s)
            if w > 0:
                weighted_vals.append(s * w)
            weight_sum += w
            if abs(s) > abs(max_s):
                max_s = s

        mean_s = float(sum(vals) / max(1, len(vals)))
        weighted_s = float(sum(weighted_vals) / max(1e-6, weight_sum)) if weighted_vals else mean_s

        out[symbol] = {
            "sentiment_mean": mean_s,
            "sentiment_weighted": weighted_s,
            "sentiment_max": max_s,
            "article_count": len(arts),
            "recency_weight_sum": weight_sum,
        }

    return out


# ---------------------------------------------------------------------------
# Intraday shock sentiment (aggressive / ml_data_dt)
# ---------------------------------------------------------------------------

def compute_intraday_shock_scores(
    articles_by_symbol: Dict[str, List[Dict[str, Any]]],
    now: Optional[datetime] = None,
    window_minutes: int = 60,
) -> Dict[str, Dict[str, Any]]:
    """
    Focus on *recent* news (last window_minutes) and compute a shock-style score:
      symbol -> {
        "shock_score": float,      # magnitude of recent news shock
        "shock_direction": float,  # sign (-1..1)
        "recent_articles": int,
      }
    """
    now = now or datetime.utcnow()
    out: Dict[str, Dict[str, Any]] = {}

    cutoff = now - timedelta(minutes=window_minutes)

    for symbol, arts in articles_by_symbol.items():
        recent = []
        for a in arts:
            t = _extract_published_at(a)
            if not t or t < cutoff:
                continue
            recent.append(a)

        if not recent:
            out[symbol] = {
                "shock_score": 0.0,
                "shock_direction": 0.0,
                "recent_articles": 0,
            }
            continue

        # Shock = max(|s| * relevance * event_boost)
        max_magnitude = 0.0
        direction = 0.0

        for a in recent:
            s = _extract_base_sentiment(a)
            r = _extract_relevance(a)
            mag = abs(s) * r * _event_boost(a.get("headline") or "")
            if mag > max_magnitude:
                max_magnitude = mag
                direction = 1.0 if s >= 0 else -1.0

        out[symbol] = {
            "shock_score": float(max_magnitude),
            "shock_direction": float(direction),
            "recent_articles": len(recent),
        }

    return out
