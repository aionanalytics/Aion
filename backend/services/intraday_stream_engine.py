# backend/services/intraday_stream_engine.py
"""
Intraday Stream Engine

Builds a lightweight snapshot for the intraday dashboard every time
it's called. Designed to be fast and safe for 5s polling.

Data sources:
  • Intraday prediction ranks (top N) from dt_backend
  • Paper positions + cash from broker_api
  • Live prices from StockAnalysis (via live_prices_router.fetch_live_prices)

Output:
  A dict like:
  {
    "as_of": "...",
    "cash": 100000.0,
    "rows": [
       {
         "symbol": "AAPL",
         "price": 187.23,
         "change_pct": 0.0123,
         "volume": 1234567,
         "score": 0.87,
         "prob_buy": 0.82,
         "prob_sell": 0.05,
         "action": "BUY",
         "position_qty": 15.0,
         "position_avg_price": 180.0,
       },
       ...
    ]
  }
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Set

from utils.logger import log, warn
from utils.json_tools import read_json_gz
from utils.time_utils import ts

from backend.config import PATHS

# dt_backend broker paper account
from dt_backend.engines.broker_api import get_positions, get_cash  # type: ignore

# live price / snapshot helper
from backend.routers.live_prices_router import fetch_live_prices  # type: ignore


def _root() -> Path:
    root = PATHS.get("root")
    if not root:
        root = Path(".").resolve()
    return root


def _candidate_rank_files() -> List[Path]:
    """
    Try a few likely locations for the intraday rank file.
    This keeps us robust even if layout evolves slightly.
    """
    root = _root()
    candidates: List[Path] = []

    # Classic layout from earlier logs:
    candidates.append(root / "ml_data_dt" / "signals" / "prediction_rank_fetch.json.gz")

    # dt_backend-aware layout (if DT_PATHS added a signals-like key)
    try:
        from dt_backend.config_dt import DT_PATHS  # type: ignore

        for k in ["signals_rank_fetch", "signals", "dtml_signals", "dtml_data"]:
            p = DT_PATHS.get(k)
            if not p:
                continue
            if p.suffix:  # direct file path
                candidates.append(p)
            else:
                candidates.append(p / "prediction_rank_fetch.json.gz")
    except Exception:
        pass

    # Deduplicate while preserving order
    uniq: List[Path] = []
    seen: Set[Path] = set()
    for c in candidates:
        if c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq


def _load_prediction_ranks(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Load intraday prediction ranks from whatever file we can find.
    We don't error hard if nothing exists; just return [].
    """
    for path in _candidate_rank_files():
        if not path.exists():
            continue
        data = read_json_gz(path)
        if not data:
            continue

        # Expect either list[...] or {"ranks": [...]}
        if isinstance(data, dict):
            rows = data.get("ranks") or data.get("rows") or []
        else:
            rows = data

        if not isinstance(rows, list):
            continue

        # Try to sort by rank or score if available
        def key_fn(r: Dict[str, Any]) -> Any:
            if "rank" in r:
                return r.get("rank")
            if "score" in r:
                return -float(r.get("score") or 0.0)
            return 0

        rows_sorted = sorted(rows, key=key_fn)
        return rows_sorted[:limit]

    warn("[intraday_stream] No prediction rank file found; returning empty ranks.")
    return []


def _universe(top_rows: List[Dict[str, Any]], include_positions: bool = True) -> List[str]:
    syms: Set[str] = set()

    for row in top_rows:
        s = (row.get("symbol") or row.get("sym") or "").upper()
        if s:
            syms.add(s)

    if include_positions:
        try:
            pos = get_positions()  # Dict[str, Position]
            for s in pos.keys():
                syms.add(s.upper())
        except Exception as e:
            warn(f"[intraday_stream] Failed to read positions: {e}")

    return sorted(syms)


def _merge_rows(
    symbols: List[str],
    ranks: List[Dict[str, Any]],
    prices: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Join ranks + prices + positions for each symbol.
    """
    # Re-index ranks by symbol
    rank_by_sym: Dict[str, Dict[str, Any]] = {}
    for r in ranks:
        s = (r.get("symbol") or r.get("sym") or "").upper()
        if not s:
            continue
        rank_by_sym[s] = r

    # Positions / cash
    try:
        pos = get_positions()
    except Exception:
        pos = {}

    rows: List[Dict[str, Any]] = []
    for s in symbols:
        snap = prices.get(s, {}) or {}
        r = rank_by_sym.get(s, {}) or {}
        p_node = pos.get(s)

        price = snap.get("price")
        volume = snap.get("volume") or snap.get("avg_volume")

        # prediction fields (names are flexible)
        prob_buy = r.get("prob_buy") or r.get("p_buy") or r.get("p1")
        prob_sell = r.get("prob_sell") or r.get("p_sell") or r.get("p0")
        action = r.get("action") or r.get("signal") or None
        score = r.get("score") or r.get("rank_score") or None

        rows.append(
            {
                "symbol": s,
                "price": float(price) if price is not None else None,
                "volume": float(volume) if volume is not None else None,
                "score": float(score) if score is not None else None,
                "prob_buy": float(prob_buy) if prob_buy is not None else None,
                "prob_sell": float(prob_sell) if prob_sell is not None else None,
                "action": action,
                "position_qty": float(getattr(p_node, "qty", 0.0)) if p_node else 0.0,
                "position_avg_price": float(
                    getattr(p_node, "avg_price", 0.0)
                )
                if p_node
                else 0.0,
            }
        )

    return rows


def get_intraday_snapshot(limit: int = 100) -> Dict[str, Any]:
    """
    Build a snapshot for the intraday dashboard:
      • top `limit` symbols from prediction ranks
      • plus all currently held positions
      • enriched with live prices

    Designed to be cheap enough for 5s polling.
    """
    ranks = _load_prediction_ranks(limit=limit)
    syms = _universe(ranks, include_positions=True)

    if not syms:
        return {
            "as_of": ts(),
            "cash": 0.0,
            "rows": [],
        }

    prices = {}
    try:
        prices = fetch_live_prices(symbols=syms, limit=len(syms))
        # fetch_live_prices returns dict keyed by symbol, not wrapped
        # If your version wraps in {"prices": {...}}, adjust here.
        if "prices" in prices and isinstance(prices["prices"], dict):
            prices = {p["symbol"]: p for p in prices["prices"]}
    except Exception as e:
        warn(f"[intraday_stream] Live price fetch failed: {e}")
        prices = {}

    rows = _merge_rows(syms, ranks, prices)

    try:
        cash = float(get_cash())
    except Exception:
        cash = 0.0

    return {
        "as_of": ts(),
        "cash": cash,
        "rows": rows,
    }
