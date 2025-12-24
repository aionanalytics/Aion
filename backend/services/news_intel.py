# backend/services/news_intel.py
"""
News Intelligence v4.0 — Brain-Backed (NO API CALLS)

This module aligns with the new architecture:

    Marketaux collector (news_fetcher.py)  ->  dedup cache (news_cache.py)
                                          ->  brain snapshots (news_brain_builder.py)
                                          ->  intel outputs (THIS FILE)

What this file DOES:
  ✅ Reads news brain snapshots from ROOT/da_brains/news_n_buzz_brain/
        - news_brain_rolling.json.gz
        - news_brain_intraday.json.gz
  ✅ Writes ML-ready snapshots:
        - nightly  -> ml_data/news_features/news_features_YYYY-MM-DD.json
        - intraday -> ml_data_dt/news_intraday/news_intraday_YYYY-MM-DD_HHMMSS.json
  ✅ Filters output to requested universe (keeps files tight)
  ✅ Provides a stable schema used by ml_data_builder and policy layers

What this file DOES NOT do:
  ❌ No Marketaux calls
  ❌ No per-ticker fetch loops
  ❌ No raw cache writes
  ❌ No "collection" responsibilities

Entry points:
    build_nightly_news_intel(universe)
    build_intraday_news_intel(universe)
"""

from __future__ import annotations

import json
import gzip
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from backend.core.config import PATHS, TIMEZONE
from utils.logger import log, warn, error
from utils.time_utils import ts, today_str


# ==============================================================
# Paths
# ==============================================================

def _root() -> Path:
    return Path(PATHS.get("root") or Path("."))


def _brains_root() -> Path:
    # You said: move brains to ROOT/da_brains/
    p = _root() / "da_brains"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _news_brain_dir() -> Path:
    p = _brains_root() / "news_n_buzz_brain"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _brain_rolling_path() -> Path:
    return _news_brain_dir() / "news_brain_rolling.json.gz"


def _brain_intraday_path() -> Path:
    return _news_brain_dir() / "news_brain_intraday.json.gz"


def _ml_data_root() -> Path:
    p = PATHS.get("ml_data") or (_root() / "ml_data")
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ml_data_dt_root() -> Path:
    p = PATHS.get("ml_data_dt") or (_root() / "ml_data_dt")
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _nightly_dir() -> Path:
    d = _ml_data_root() / "news_features"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _intraday_dir() -> Path:
    d = _ml_data_dt_root() / "news_intraday"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ==============================================================
# IO helpers
# ==============================================================

def _read_gz_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return obj
        return {}
    except Exception as e:
        warn(f"[news_intel] Failed reading {path.name}: {e}")
        return {}


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        error(f"[news_intel] Failed to write {path}", e)


def _normalize_universe(universe: List[str]) -> List[str]:
    out: List[str] = []
    for s in universe or []:
        if not s:
            continue
        sym = str(s).upper().strip()
        if sym and not sym.startswith("_"):
            out.append(sym)
    # stable order
    return sorted(set(out))


# ==============================================================
# Schema helpers
# ==============================================================

def _empty_symbol_block() -> Dict[str, Any]:
    return {
        "long_horizon": {
            "sentiment_mean": 0.0,
            "sentiment_weighted": 0.0,
            "sentiment_max": 0.0,
            "article_count": 0,
            "recency_weight_sum": 0.0,
        },
        "buzz": {
            "buzz_count": 0,
            "buzz_score": 0.0,
        },
        "latest": [],
        # shock is for intraday; nightly can leave it empty
        "shock": {},
    }


def _coerce_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _symbol_from_brain_payload(payload: Dict[str, Any], sym: str) -> Dict[str, Any]:
    """
    Your brain snapshots are shaped like:
      payload["symbols"][SYM] = {
        "article_count", "sentiment_mean", "sentiment_weighted", "sentiment_max",
        "recency_weight_sum", "buzz_score", "latest"
      }

    We map this into the stable intel schema that ml_data_builder expects:
      long_horizon + buzz + latest (+ shock for intraday).
    """
    node = (payload.get("symbols") or {}).get(sym) or {}
    if not isinstance(node, dict):
        node = {}

    article_count = int(node.get("article_count") or 0)
    sentiment_mean = _coerce_float(node.get("sentiment_mean"), 0.0)
    sentiment_weighted = _coerce_float(node.get("sentiment_weighted"), 0.0)
    sentiment_max = _coerce_float(node.get("sentiment_max"), 0.0)
    recency_weight_sum = _coerce_float(node.get("recency_weight_sum"), 0.0)
    buzz_score = _coerce_float(node.get("buzz_score"), 0.0)

    latest = node.get("latest") or []
    if not isinstance(latest, list):
        latest = []

    return {
        "long_horizon": {
            "sentiment_mean": float(sentiment_mean),
            "sentiment_weighted": float(sentiment_weighted),
            "sentiment_max": float(sentiment_max),
            "article_count": int(article_count),
            "recency_weight_sum": float(recency_weight_sum),
        },
        "buzz": {
            "buzz_count": int(article_count),
            "buzz_score": float(buzz_score),
        },
        "latest": latest,
    }


def _compute_intraday_shock(rolling_node: Dict[str, Any], intraday_node: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight “shock” estimate from intraday vs rolling baseline.
    This is intentionally simple and stable (no fancy math or model dependency).

    shock_score ~= (intraday_sent_mean - rolling_sent_mean) * sqrt(intraday_article_count + 1)
    """
    try:
        r_mean = _coerce_float((rolling_node.get("long_horizon") or {}).get("sentiment_mean"), 0.0)
        i_mean = _coerce_float((intraday_node.get("long_horizon") or {}).get("sentiment_mean"), 0.0)
        i_cnt = int((intraday_node.get("long_horizon") or {}).get("article_count") or 0)

        # sqrt weighting prevents 1 article from overreacting, but still boosts clusters
        import math
        score = (i_mean - r_mean) * math.sqrt(i_cnt + 1)

        # direction for UI/policy convenience
        direction = "neutral"
        if score > 0.15:
            direction = "positive"
        elif score < -0.15:
            direction = "negative"

        return {
            "score": float(score),
            "direction": direction,
            "intraday_count": int(i_cnt),
            "baseline_mean": float(r_mean),
            "intraday_mean": float(i_mean),
        }
    except Exception:
        return {"score": 0.0, "direction": "neutral"}


# ==============================================================
# Public API
# ==============================================================

def build_nightly_news_intel(
    universe: List[str],
    as_of: Optional[datetime] = None,
) -> Path:
    """
    Nightly intel:
      - NO FETCHING
      - Reads rolling brain snapshot
      - Writes ml_data/news_features/news_features_YYYY-MM-DD.json
    """
    as_of = as_of or datetime.now(TIMEZONE)

    universe_u = _normalize_universe(universe)
    log(f"[news_intel] Nightly brain-backed build: universe={len(universe_u)}")

    rolling_brain = _read_gz_json(_brain_rolling_path())
    if not rolling_brain:
        warn("[news_intel] Rolling brain snapshot missing/empty — writing empty features snapshot.")

    out: Dict[str, Any] = {
        "meta": {
            "as_of": ts(),
            "mode": "nightly",
            "source": "news_brain_rolling",
            "brain_path": str(_brain_rolling_path()),
            "universe_size": int(len(universe_u)),
        },
        "symbols": {},
    }

    symbols_block: Dict[str, Any] = {}

    for sym in universe_u:
        sym_node = _symbol_from_brain_payload(rolling_brain, sym) if rolling_brain else _empty_symbol_block()
        # nightly doesn't need shock, but keep key stable
        sym_node["shock"] = {}
        symbols_block[sym] = sym_node

    out["symbols"] = symbols_block

    fname = f"news_features_{today_str()}.json"
    path = _nightly_dir() / fname
    _write_json_atomic(path, out)

    log(f"[news_intel] ✅ Nightly news intel written → {path}")
    return path


def build_intraday_news_intel(
    universe: List[str],
    as_of: Optional[datetime] = None,
) -> Path:
    """
    Intraday intel (dt_backend use):
      - NO FETCHING
      - Reads intraday + rolling brain snapshots
      - Writes ml_data_dt/news_intraday/news_intraday_YYYY-MM-DD_HHMMSS.json
    """
    as_of = as_of or datetime.now(TIMEZONE)

    universe_u = _normalize_universe(universe)
    log(f"[news_intel] Intraday brain-backed build: universe={len(universe_u)}")

    intraday_brain = _read_gz_json(_brain_intraday_path())
    rolling_brain = _read_gz_json(_brain_rolling_path())

    if not intraday_brain:
        warn("[news_intel] Intraday brain snapshot missing/empty — writing empty intraday intel.")
    if not rolling_brain:
        warn("[news_intel] Rolling brain snapshot missing/empty — shock baseline will be weak/zero.")

    out: Dict[str, Any] = {
        "meta": {
            "as_of": ts(),
            "mode": "intraday",
            "source": "news_brain_intraday",
            "brain_intraday_path": str(_brain_intraday_path()),
            "brain_rolling_path": str(_brain_rolling_path()),
            "universe_size": int(len(universe_u)),
        },
        "symbols": {},
    }

    symbols_block: Dict[str, Any] = {}

    for sym in universe_u:
        intraday_node = _symbol_from_brain_payload(intraday_brain, sym) if intraday_brain else _empty_symbol_block()
        rolling_node = _symbol_from_brain_payload(rolling_brain, sym) if rolling_brain else _empty_symbol_block()

        shock = _compute_intraday_shock(rolling_node, intraday_node)
        intraday_node["shock"] = shock

        # also include intraday buzz explicitly (already in "buzz"), and keep latest
        symbols_block[sym] = intraday_node

    out["symbols"] = symbols_block

    fname = f"news_intraday_{as_of.strftime('%Y-%m-%d_%H%M%S')}.json"
    path = _intraday_dir() / fname
    _write_json_atomic(path, out)

    log(f"[news_intel] ✅ Intraday news intel written → {path}")
    return path


# ==============================================================
# CLI smoke test
# ==============================================================

if __name__ == "__main__":
    # Tiny sanity test: build from whatever brain files exist.
    test_universe = ["AAPL", "MSFT", "TSLA"]
    p1 = build_nightly_news_intel(test_universe)
    p2 = build_intraday_news_intel(test_universe)
    print(json.dumps({"nightly": str(p1), "intraday": str(p2)}, indent=2))