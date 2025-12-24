"""
social_sentiment_fetcher.py — v3.0
AION Analytics — Social Market Intelligence Layer
"""

from __future__ import annotations

import os
import re
import json
import math
import statistics
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import requests

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import (
    _read_rolling,
    save_rolling,
    safe_float,
    log,
)

# =====================================================================
# PATHS
# =====================================================================

CACHE_FILE = PATHS["social_intel"]
TICKER_REGEX = re.compile(r"\b[A-Z]{2,6}\b")

# === Missing variable FIX — ensure Twitter bearer key exists ===
TWITTER_BEARER = os.getenv("TWITTER_BEARER", "AAAAAAAAAAAAAAAAAAAAAJfg5gEAAAAAf%2BqWNGwm9RoEDk9HIe3efSlT0rY%3DgrLD6CxmXuUHhyl3GMDO3MbyQwkbxd1xISlX5LgE7etp1v9lzJ")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "NnA0xlByVyMj6MO8y6Ytsg")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "QSYxwtG47e1AYfRJvWtEmCjV66_THQ")
REDDIT_SECRET = os.getenv("REDDIT_SECRET", "AionAnalytics/1.0 (by u/Interesting_Maize331)")



# =====================================================================
# Heuristics for sentiment
# =====================================================================

POS_WORDS = [
    "moon", "bull", "bullish", "rocket", "soaring", "profit",
    "gain", "pump", "call", "calls", "green", "run",
]

NEG_WORDS = [
    "bagholder", "dump", "bearish", "red", "crash", "puts",
    "collapse", "panic", "down", "loss", "bleeding",
]

def score_sentiment(text: str) -> float:
    if not text:
        return 0.0
    t = text.lower()
    pos = sum(1 for w in POS_WORDS if w in t)
    neg = sum(1 for w in NEG_WORDS if w in t)
    if pos == 0 and neg == 0:
        return 0.0
    raw = (pos - neg) / (pos + neg)
    return max(-1.0, min(1.0, raw))


def extract_tickers(text: str) -> List[str]:
    if not text:
        return []
    tickers = [m.group(0) for m in TICKER_REGEX.finditer(text)]
    blacklist = {"YOLO", "DD", "CEO", "GDP", "USA", "FED", "IMO", "OTM"}
    return [t for t in set(tickers) if t not in blacklist]


# =====================================================================
# Reddit Fetcher
# =====================================================================

def _fetch_reddit() -> List[Dict[str, Any]]:
    try:
        url = "https://api.pushshift.io/reddit/search/comment/"
        params = {
            "subreddit": "stocks,investing,wallstreetbets",
            "size": 500,
            "sort": "desc",
        }
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return []
        js = r.json()
        data = js.get("data", [])
    except Exception as e:
        log(f"[social] ⚠️ Reddit fetch failed: {e}")
        data = []

    out = []
    for itm in data:
        body = itm.get("body") or ""
        tickers = extract_tickers(body)
        sent = score_sentiment(body)

        out.append({
            "source": "reddit",
            "text": body,
            "tickers": tickers,
            "sentiment": sent,
            "timestamp": itm.get("created_utc"),
            "buzz": 1,
        })
    return out


# =====================================================================
# Twitter/X Fetcher
# =====================================================================

def _fetch_twitter() -> List[Dict[str, Any]]:
    if not TWITTER_BEARER:
        return []
    try:
        url = "https://api.twitter.com/2/tweets/search/recent"
        headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}
        params = {
            "query": "(stocks OR investing OR trading) lang:en -is:retweet",
            "tweet.fields": "created_at,text,public_metrics",
            "max_results": 50,
        }
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            return []

        js = r.json()
        data = js.get("data", [])
    except Exception as e:
        log(f"[social] ⚠️ Twitter fetch failed: {e}")
        return []

    out = []
    for itm in data:
        text = itm.get("text") or ""
        tickers = extract_tickers(text)
        sent = score_sentiment(text)
        pm = itm.get("public_metrics", {})
        likes = pm.get("like_count", 0)
        retweets = pm.get("retweet_count", 0)

        out.append({
            "source": "twitter",
            "text": text,
            "tickers": tickers,
            "sentiment": sent,
            "buzz": 1 + (likes + retweets) / 10,
            "timestamp": itm.get("created_at"),
        })
    return out


# =====================================================================
# Fallback FinViz
# =====================================================================

def _fallback_sources() -> List[Dict[str, Any]]:
    try:
        url = "https://finviz.com/api/news.ashx"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return []
        js = r.json()
    except Exception:
        return []

    out = []
    for itm in js:
        text = itm.get("title") or ""
        tickers = extract_tickers(text)
        sent = score_sentiment(text)

        out.append({
            "source": "finviz",
            "text": text,
            "tickers": tickers,
            "sentiment": sent,
            "buzz": 1,
            "timestamp": itm.get("date"),
        })
    return out


# =====================================================================
# SOCIAL INTEL CORE
# =====================================================================

def build_social_sentiment() -> Dict[str, Any]:
    log("💬 Fetching social sentiment (Reddit / X / fallback)…")

    posts = []
    sources_used = []

    r = _fetch_reddit()
    if r:
        posts.extend(r)
        sources_used.append("reddit")

    t = _fetch_twitter()
    if t:
        posts.extend(t)
        sources_used.append("twitter")

    f = _fallback_sources()
    if f:
        posts.extend(f)
        sources_used.append("finviz")

    if not posts:
        log("[social] ⚠️ No social posts found.")
        return {"status": "empty"}

    # =====================================================================
    # Novelty (FIXED timezone issue)
    # =====================================================================

    def novelty(ts: Any) -> float:
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts).replace(tzinfo=TIMEZONE)
            else:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TIMEZONE)
        except Exception:
            return 0.0

        age_hours = (datetime.now(TIMEZONE) - dt).total_seconds() / 3600
        return max(0.0, min(1.0, math.exp(-age_hours / 12)))

    # =====================================================================
    # Aggregate per-symbol
    # =====================================================================

    clusters: Dict[str, List[Dict[str, Any]]] = {}
    for p in posts:
        tickers = p.get("tickers") or []
        for sym in tickers:
            sym_u = sym.upper()
            clusters.setdefault(sym_u, []).append(p)

    rolling = _read_rolling()
    if not rolling:
        log("[social] ⚠️ No rolling.json.gz — cannot store symbol intel.")
        return {"status": "no_rolling"}

    updated = 0

    for sym, node in rolling.items():
        if sym.startswith("_"):
            continue

        sym_u = sym.upper()
        plist = clusters.get(sym_u) or []

        if not plist:
            node["social"] = {
                "sentiment": 0.0,
                "buzz": 0,
                "novelty": 0.0,
                "heat_score": 0.0,
                "last_updated": datetime.now(TIMEZONE).isoformat(),
            }
            rolling[sym] = node
            updated += 1
            continue

        sentiments = [p["sentiment"] for p in plist]
        buzzes = [safe_float(p.get("buzz", 1)) for p in plist]
        novs = [novelty(p.get("timestamp")) for p in plist]

        avg_sent = statistics.mean(sentiments) if sentiments else 0.0
        total_buzz = sum(buzzes)
        avg_nov = statistics.mean(novs) if novs else 0.0
        heat = avg_sent * math.log1p(total_buzz) * (1 + avg_nov)

        node["social"] = {
            "sentiment": float(avg_sent),
            "buzz": int(total_buzz),
            "novelty": float(avg_nov),
            "heat_score": float(round(heat, 4)),
            "last_updated": datetime.now(TIMEZONE).isoformat(),
        }
        rolling[sym] = node
        updated += 1

    save_rolling(rolling)
    log(f"[social] Updated social sentiment for {updated} symbols.")

    # =====================================================================
    # Global social_intel.json
    # =====================================================================

    try:
        market_sent = statistics.mean([p["sentiment"] for p in posts]) if posts else 0.0
        buzz_index = sum(p.get("buzz", 1) for p in posts)

        trending = sorted(
            clusters.items(),
            key=lambda kv: sum(p.get("buzz", 1) for p in kv[1]),
            reverse=True
        )[:20]

        intel = {
            "timestamp": datetime.now(TIMEZONE).isoformat(),
            "market_social_sentiment": market_sent,
            "buzz_index": buzz_index,
            "sources_used": sources_used,
            "top_trending_tickers": [
                {"symbol": sym, "buzz": sum(p.get("buzz", 1) for p in plist)}
                for sym, plist in trending
            ],
        }

        CACHE_FILE.write_text(json.dumps(intel, indent=2), encoding="utf-8")
        log("[social] 🧠 Updated social_intel.json")

    except Exception as e:
        log(f"[social] ⚠️ Failed writing social_intel.json: {e}")

    return {"status": "ok", "updated": updated, "sources": sources_used}
