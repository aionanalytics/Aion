"""
fundamentals_fetcher.py — v2.2 (Rolling Enricher + Key Normalization)
Author: AION Analytics / StockAnalyzerPro

Purpose:
- Integrate financial fundamentals from StockAnalysis endpoints.
- Enrich Rolling cache with stable ratios and balance metrics.
- Maintain lock-safe, incremental updates.
- Normalize all keys to snake_case for Rolling consistency.
"""

import os, json, time, requests
from datetime import datetime
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from .data_pipeline import (
    _read_rolling,
    _atomic_write_json_gz,
    RollingLock,
    ensure_symbol_fields,
    log,
    ROLLING_PATH,
)

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------
SA_BASE = "https://stockanalysis.com/api/screener"
FUND_FIELDS = [
    "peRatio", "pbRatio", "psRatio", "pegRatio",
    "debtEquity", "debtEbitda",
    "roa", "roe", "roic",
    "grossMargin", "operatingMargin", "profitMargin",
    "revenueGrowth", "epsGrowth",
    "dividendYield", "payoutRatio", "marketCap",
]

# ---------------------------------------------------------------------
# Normalization Helpers
# ---------------------------------------------------------------------
NORMALIZE_KEYS = {
    "peRatio": "pe_ratio", "pbRatio": "pb_ratio", "psRatio": "ps_ratio",
    "pegRatio": "peg_ratio", "debtEquity": "debt_equity", "debtEbitda": "debt_ebitda",
    "revenueGrowth": "revenue_growth", "epsGrowth": "eps_growth",
    "profitMargin": "profit_margin", "operatingMargin": "operating_margin",
    "grossMargin": "gross_margin", "dividendYield": "dividend_yield",
    "payoutRatio": "payout_ratio", "marketCap": "marketCap",
    "roa": "roa", "roe": "roe", "roic": "roic",
}

def normalize_keys(node: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize camelCase fundamentals → snake_case for Rolling consistency."""
    if not isinstance(node, dict):
        return node
    for old, new in NORMALIZE_KEYS.items():
        if old in node and new not in node:
            node[new] = node.pop(old)
    return node


# ---------------------------------------------------------------------
# Fetch fundamentals in batch
# ---------------------------------------------------------------------
def _fetch_fundamentals_batch() -> Dict[str, Dict[str, Any]]:
    """Pulls all fundamentals from StockAnalysis screener and normalizes keys."""
    try:
        payload = {"fields": FUND_FIELDS, "limit": 10000, "order": ["marketCap", "desc"]}
        r = requests.post(f"{SA_BASE}/s/i", json=payload, timeout=15)
        js = r.json().get("data", {}).get("data", [])
        out = {}
        for row in js:
            sym = str(row.get("symbol") or row.get("s") or "").upper()
            if not sym:
                continue
            fields = {f: row.get(f) for f in FUND_FIELDS}
            out[sym] = normalize_keys(fields)
        return out
    except Exception as e:
        log(f"⚠️ fundamentals fetch failed: {e}")
        return {}


# ---------------------------------------------------------------------
# Core enrichment
# ---------------------------------------------------------------------
def enrich_fundamentals(max_workers: int = 8) -> Dict[str, Any]:
    start = time.time()
    log("[fundamentals_fetcher] 🚀 Fetching fundamentals from StockAnalysis...")

    rolling = _read_rolling()
    if not rolling:
        log("⚠️ Rolling cache empty — cannot enrich fundamentals.")
        return {}

    fundamentals = _fetch_fundamentals_batch()
    if not fundamentals:
        log("⚠️ No fundamentals fetched from StockAnalysis.")
        return {}

    updated = 0

    # Iterate through fetched fundamentals and merge normalized keys
    for sym, fields in fundamentals.items():
        sym = sym.upper()
        node = rolling.get(sym)
        if not node:
            node = ensure_symbol_fields(sym)
        if not node:
            continue

        node = normalize_keys(node)
        changed = False
        for k, v in fields.items():
            if v is None:
                continue
            if k not in node or node.get(k) in (None, "", 0):
                node[k] = v
                changed = True

        if changed:
            rolling[sym] = node
            updated += 1

    # ✅ Safe atomic save
    if updated:
        with RollingLock():
            _atomic_write_json_gz(ROLLING_PATH, rolling)

    dur = time.time() - start
    log(f"[fundamentals_fetcher] ✅ Enriched {updated} tickers in {dur:.1f}s (keys normalized).")
    return {"updated": updated, "duration": dur}
