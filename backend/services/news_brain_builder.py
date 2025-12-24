# backend/services/news_brain_builder.py
"""
News Brain Builder v1.1 — Builds "news_n_buzz_brain" from news_cache

FIX v1.1:
  ✅ Correctly aggregates articles with MULTIPLE symbols
  ✅ Uses art["symbols"] instead of nonexistent art["symbol"]
  ✅ One article can contribute to many tickers (correct behavior)

Everything else remains intentionally stable.
"""

from __future__ import annotations

import json
import gzip
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Iterable, Tuple, List

from backend.core.config import PATHS, TIMEZONE
from utils.logger import log, warn, error
from utils.time_utils import ts

from backend.services import news_cache


# ==============================================================
# Paths
# ==============================================================

ROOT = Path(PATHS.get("root") or Path("."))

BRAINS_ROOT = ROOT / "da_brains"
BRAINS_ROOT.mkdir(parents=True, exist_ok=True)

BRAIN_DIR = BRAINS_ROOT / "news_n_buzz_brain"
BRAIN_DIR.mkdir(parents=True, exist_ok=True)

OUT_ROLLING = BRAIN_DIR / "news_brain_rolling.json.gz"
OUT_INTRADAY = BRAIN_DIR / "news_brain_intraday.json.gz"
META_FILE = BRAIN_DIR / "meta.json"


# ==============================================================
# Defaults / Tunables
# ==============================================================

DEFAULT_ROLLING_DAYS = int(PATHS.get("news_brain_rolling_days") or 7)
DEFAULT_INTRADAY_MINUTES = int(PATHS.get("news_brain_intraday_minutes") or 180)

ROLLING_DECAY_DAYS = float(PATHS.get("news_brain_rolling_decay_days") or 7.0)
INTRADAY_DECAY_MIN = float(PATHS.get("news_brain_intraday_decay_minutes") or 90.0)

W_COUNT = 1.0
W_RECENCY = 1.0
W_SENTIMENT = 0.75
W_RELEVANCE = 0.50

MAX_PER_SYMBOL_LATEST = 8


# ==============================================================
# Helpers
# ==============================================================

def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _now_utc() -> datetime:
    now_local = datetime.now(TIMEZONE)
    if now_local.tzinfo is None:
        now_local = now_local.replace(tzinfo=timezone.utc)
    return now_local.astimezone(timezone.utc)


def _exp_decay(age: float, scale: float) -> float:
    return float(math.exp(-age / scale)) if scale > 0 else 0.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return default if x is None else float(x)
    except Exception:
        return default


# ==============================================================
# Aggregator
# ==============================================================

@dataclass
class _Agg:
    count: int = 0
    sent_sum: float = 0.0
    sent_max: float = -1e9
    w_sum: float = 0.0
    ws_sum: float = 0.0
    recency_sum: float = 0.0
    latest_articles: List[Dict[str, Any]] = None  # type: ignore

    def __post_init__(self):
        if self.latest_articles is None:
            self.latest_articles = []


def _push_latest(agg: _Agg, art: Dict[str, Any]) -> None:
    url = str(art.get("url") or "").strip()
    if not url:
        return
    for a in agg.latest_articles:
        if str(a.get("url") or "").strip() == url:
            return
    agg.latest_articles.append(
        {
            "url": url,
            "headline": art.get("headline"),
            "source": art.get("source"),
            "published_at": art.get("published_at"),
            "sentiment_score": art.get("sentiment_score"),
            "relevance_score": art.get("relevance_score"),
        }
    )
    if len(agg.latest_articles) > MAX_PER_SYMBOL_LATEST:
        agg.latest_articles = agg.latest_articles[-MAX_PER_SYMBOL_LATEST:]


# ==============================================================
# Core scan helpers
# ==============================================================

def _iter_cached_articles() -> Iterable[Dict[str, Any]]:
    yield from news_cache.iter_articles(limit=None)


def _article_symbols(art: Dict[str, Any]) -> List[str]:
    syms = art.get("symbols")
    if not isinstance(syms, list):
        return []
    out = []
    for s in syms:
        if not s:
            continue
        ss = str(s).upper().strip()
        if ss and not ss.startswith("_"):
            out.append(ss)
    return out


def _article_weights(
    art: Dict[str, Any],
    ref: datetime,
    mode: str,
) -> Tuple[float, float, float, float]:
    sent = _safe_float(art.get("sentiment_score"), 0.0)

    rel = _safe_float(art.get("relevance_score"), 1.0)
    rel = _clamp(rel if rel > 0 else 1.0, 0.0, 10.0)

    pub = _parse_iso(art.get("published_at"))
    if pub is None:
        return sent, rel, 0.0, 0.0

    pub = pub.astimezone(timezone.utc)
    age = max((ref - pub).total_seconds(), 0.0)

    if mode == "intraday":
        rec = _exp_decay(age / 60.0, INTRADAY_DECAY_MIN)
    else:
        rec = _exp_decay(age / 86400.0, ROLLING_DECAY_DAYS)

    w = rec * (0.5 + 0.5 * (rel / 10.0))
    return sent, rel, rec, w


def _finalize(agg: _Agg) -> Dict[str, Any]:
    if agg.count <= 0:
        return {
            "article_count": 0,
            "sentiment_mean": 0.0,
            "sentiment_weighted": 0.0,
            "sentiment_max": 0.0,
            "recency_weight_sum": 0.0,
            "buzz_score": 0.0,
            "latest": [],
        }

    mean = agg.sent_sum / max(1, agg.count)
    smax = 0.0 if agg.sent_max < -1e8 else agg.sent_max
    wmean = agg.ws_sum / agg.w_sum if agg.w_sum > 0 else mean

    buzz = (
        W_COUNT * agg.count
        + W_RECENCY * agg.recency_sum
        + W_SENTIMENT * max(0.0, mean) * agg.count
        + W_RELEVANCE * agg.w_sum
    )
    buzz = float(_clamp(buzz, 0.0, 1e9))

    return {
        "article_count": int(agg.count),
        "sentiment_mean": float(mean),
        "sentiment_weighted": float(wmean),
        "sentiment_max": float(smax),
        "recency_weight_sum": float(agg.recency_sum),
        "buzz_score": float(buzz),
        "latest": agg.latest_articles[-MAX_PER_SYMBOL_LATEST:],
    }


# ==============================================================
# Public builders
# ==============================================================

def build_news_brain(
    rolling_days: int = DEFAULT_ROLLING_DAYS,
    intraday_minutes: int = DEFAULT_INTRADAY_MINUTES,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    ref = as_of or _now_utc()
    ref = ref.astimezone(timezone.utc)

    rolling_start = ref - timedelta(days=max(1, int(rolling_days)))
    intraday_start = ref - timedelta(minutes=max(1, int(intraday_minutes)))

    rolling_aggs: Dict[str, _Agg] = {}
    intraday_aggs: Dict[str, _Agg] = {}

    scanned = used_rolling = used_intraday = 0

    log(f"[news_brain_builder] 🧠 Building news brain (FIXED multi-symbol scan)")

    for art in _iter_cached_articles():
        scanned += 1

        pub = _parse_iso(art.get("published_at"))
        if pub is None:
            continue
        pub = pub.astimezone(timezone.utc)

        for sym in _article_symbols(art):

            if rolling_start <= pub <= ref:
                agg = rolling_aggs.setdefault(sym, _Agg())
                sent, rel, rec, w = _article_weights(art, ref, "rolling")
                agg.count += 1
                agg.sent_sum += sent
                agg.sent_max = max(agg.sent_max, sent)
                agg.recency_sum += rec
                agg.w_sum += w
                agg.ws_sum += sent * w
                _push_latest(agg, art)
                used_rolling += 1

            if intraday_start <= pub <= ref:
                agg = intraday_aggs.setdefault(sym, _Agg())
                sent, rel, rec, w = _article_weights(art, ref, "intraday")
                agg.count += 1
                agg.sent_sum += sent
                agg.sent_max = max(agg.sent_max, sent)
                agg.recency_sum += rec
                agg.w_sum += w
                agg.ws_sum += sent * w
                _push_latest(agg, art)
                used_intraday += 1

    rolling_payload = {
        "meta": {
            "generated_at": ts(),
            "as_of_utc": ref.isoformat(),
            "window_days": rolling_days,
            "symbols": len(rolling_aggs),
            "articles_scanned": scanned,
            "articles_used": used_rolling,
            "schema": "news_brain_v1",
        },
        "symbols": {k: _finalize(v) for k, v in rolling_aggs.items()},
    }

    intraday_payload = {
        "meta": {
            "generated_at": ts(),
            "as_of_utc": ref.isoformat(),
            "window_minutes": intraday_minutes,
            "symbols": len(intraday_aggs),
            "articles_scanned": scanned,
            "articles_used": used_intraday,
            "schema": "news_brain_v1",
        },
        "symbols": {k: _finalize(v) for k, v in intraday_aggs.items()},
    }

    return {"rolling": rolling_payload, "intraday": intraday_payload}


def write_news_brain_snapshots(
    rolling_days: int = DEFAULT_ROLLING_DAYS,
    intraday_minutes: int = DEFAULT_INTRADAY_MINUTES,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    brain = build_news_brain(rolling_days, intraday_minutes, as_of)

    with gzip.open(OUT_ROLLING, "wt", encoding="utf-8") as f:
        json.dump(brain["rolling"], f)
    with gzip.open(OUT_INTRADAY, "wt", encoding="utf-8") as f:
        json.dump(brain["intraday"], f)

    META_FILE.write_text(json.dumps({
        "updated_at": datetime.now(TIMEZONE).isoformat(),
        "schema": "news_brain_v1",
        "rolling_path": str(OUT_ROLLING),
        "intraday_path": str(OUT_INTRADAY),
    }, indent=2))

    log("[news_brain_builder] ✅ News brain snapshots written")
    return {"status": "ok"}


if __name__ == "__main__":
    print(json.dumps(write_news_brain_snapshots(), indent=2))