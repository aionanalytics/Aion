# backend/services/news_cache.py
"""
News Cache v1.0 — Deduplicated, Timestamped, Brain-Ready Storage

Purpose:
  • Receive normalized articles from news_fetcher
  • Deduplicate aggressively (URL + stable article_id)
  • Persist to disk as compressed JSONL (append-only, safe)
  • Maintain a lightweight index for fast dedupe checks
  • Prepare clean handoff for future:
        news_n_buzz_brain (rolling intelligence)

Design rules:
  - NO API calls here
  - NO sentiment math here
  - NO per-ticker loops
  - This is memory, not analysis
"""

from __future__ import annotations

import json
import gzip
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Iterable
from datetime import datetime

from backend.core.config import PATHS, TIMEZONE
from utils.logger import log, warn, error
from utils.time_utils import ts

# ==============================================================
# Paths
# ==============================================================

ROOT = Path(PATHS.get("root") or Path("."))

BRAINS_ROOT = ROOT / "da_brains"
BRAINS_ROOT.mkdir(parents=True, exist_ok=True)

NEWS_BRAIN_DIR = BRAINS_ROOT / "news_n_buzz_brain"
NEWS_BRAIN_DIR.mkdir(parents=True, exist_ok=True)

RAW_STORE = NEWS_BRAIN_DIR / "raw_articles.jsonl.gz"
INDEX_FILE = NEWS_BRAIN_DIR / "article_index.json.gz"
META_FILE = NEWS_BRAIN_DIR / "meta.json"

# ==============================================================
# In-memory index (lazy loaded)
# ==============================================================

# article_id -> minimal metadata
_ARTICLE_INDEX: Dict[str, Dict[str, Any]] = {}
_INDEX_LOADED = False

# ==============================================================
# Helpers
# ==============================================================

def _load_index() -> None:
    global _ARTICLE_INDEX, _INDEX_LOADED
    if _INDEX_LOADED:
        return

    if INDEX_FILE.exists():
        try:
            with gzip.open(INDEX_FILE, "rt", encoding="utf-8") as f:
                _ARTICLE_INDEX = json.load(f)
                log(f"[news_cache] Loaded article index ({len(_ARTICLE_INDEX)} entries).")
        except Exception as e:
            error("[news_cache] Failed loading article index; rebuilding empty.", e)
            _ARTICLE_INDEX = {}
    else:
        _ARTICLE_INDEX = {}

    _INDEX_LOADED = True


def _persist_index() -> None:
    try:
        with gzip.open(INDEX_FILE, "wt", encoding="utf-8") as f:
            json.dump(_ARTICLE_INDEX, f)
    except Exception as e:
        error("[news_cache] Failed writing article index", e)


def _stable_fingerprint(article: Dict[str, Any]) -> str:
    """
    Secondary fingerprint safety net (in case article_id changes upstream).
    """
    url = str(article.get("url") or "").lower().strip()
    headline = str(article.get("headline") or "").lower().strip()
    source = str(article.get("source") or "").lower().strip()
    published = str(article.get("published_at") or "").lower().strip()

    blob = f"{url}|{headline}|{source}|{published}"
    return hashlib.sha1(blob.encode("utf-8", errors="ignore")).hexdigest()


def _now_iso() -> str:
    return datetime.now(TIMEZONE).isoformat()


# ==============================================================
# Core ingest logic
# ==============================================================

def ingest_articles(
    articles: Iterable[Dict[str, Any]],
    source_tag: str = "unknown",
) -> Dict[str, int]:
    """
    Ingest normalized articles into the news cache.

    Deduplication layers:
      1) article_id (primary)
      2) secondary fingerprint (URL/title/source/time)

    Returns:
      {
        "received": int,
        "inserted": int,
        "duplicates": int,
      }
    """
    _load_index()

    received = 0
    inserted = 0
    duplicates = 0

    RAW_STORE.parent.mkdir(parents=True, exist_ok=True)

    try:
        with gzip.open(RAW_STORE, "at", encoding="utf-8") as f:
            for art in articles:
                if not isinstance(art, dict):
                    continue

                received += 1

                article_id = str(art.get("article_id") or "").strip()
                if not article_id:
                    continue

                if article_id in _ARTICLE_INDEX:
                    duplicates += 1
                    continue

                # Secondary fingerprint protection
                fp = _stable_fingerprint(art)
                if fp in _ARTICLE_INDEX:
                    duplicates += 1
                    continue

                record = dict(art)
                record["_cached_at"] = ts()
                record["_source_tag"] = source_tag

                f.write(json.dumps(record, ensure_ascii=False) + "\n")

                _ARTICLE_INDEX[article_id] = {
                    "fingerprint": fp,
                    "published_at": art.get("published_at"),
                    "cached_at": record["_cached_at"],
                    "source_tag": source_tag,
                }

                inserted += 1

    except Exception as e:
        error("[news_cache] Failed ingesting articles", e)

    if inserted > 0:
        _persist_index()

    _update_meta(received, inserted, duplicates)

    log(
        f"[news_cache] Ingested articles: received={received}, "
        f"inserted={inserted}, duplicates={duplicates}"
    )

    return {
        "received": received,
        "inserted": inserted,
        "duplicates": duplicates,
    }


# ==============================================================
# Meta tracking
# ==============================================================

def _update_meta(received: int, inserted: int, duplicates: int) -> None:
    meta = {
        "updated_at": _now_iso(),
        "received": received,
        "inserted": inserted,
        "duplicates": duplicates,
        "total_articles": len(_ARTICLE_INDEX),
    }

    try:
        if META_FILE.exists():
            old = json.loads(META_FILE.read_text(encoding="utf-8"))
        else:
            old = {}
    except Exception:
        old = {}

    merged = {
        **old,
        **meta,
    }

    try:
        META_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    except Exception as e:
        error("[news_cache] Failed writing meta.json", e)


# ==============================================================
# Read helpers (for future brains)
# ==============================================================

def iter_articles(limit: int | None = None):
    """
    Stream articles from disk (generator).
    Safe for large datasets.
    """
    if not RAW_STORE.exists():
        return

    count = 0
    with gzip.open(RAW_STORE, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
                count += 1
                if limit and count >= limit:
                    return
            except Exception:
                continue


def load_all_articles(limit: int | None = None) -> List[Dict[str, Any]]:
    """
    Convenience loader (NOT recommended for very large stores).
    """
    return list(iter_articles(limit=limit))


# ==============================================================
# CLI sanity check
# ==============================================================

if __name__ == "__main__":
    print(
        json.dumps(
            {
                "articles_cached": len(_ARTICLE_INDEX),
                "store": str(RAW_STORE),
                "index": str(INDEX_FILE),
            },
            indent=2,
        )
    )