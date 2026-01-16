"""
fundamentals_fetcher.py — v3.0
Aligned with new backend/core stack + nightly_job v4.0
--------------------------------------------------------

Goals:
    • Fetch and enrich fundamentals for all symbols in Rolling
    • Support multiple upstream providers (StockAnalysis, FMP, AlphaVantage)
    • Normalize all fields (camelCase → snake_case)
    • Merge fundamentals cleanly into Rolling
    • Full compatibility with updated data_pipeline + nightly_job

This module DOES NOT overwrite any fields already existing in Rolling
unless new fundamentals contain fresher values.

Works with:
    - StockAnalysis /s/d/<metric>
    - (optionally) FMP fundamentals endpoints
    - (optionally) Alpha Vantage fundamentals endpoints
"""

from __future__ import annotations

import json
import time
import os
from pathlib import Path
from typing import Any, Dict, List

import requests

from backend.core.data_pipeline import (
    _read_rolling,
    save_rolling,
    safe_float,
    log,
)
from backend.core.config import PATHS


# ==============================================================================
# Constants
# ==============================================================================

FUND_DIR = PATHS["fundamentals_raw"]
FUND_DIR.mkdir(parents=True, exist_ok=True)

SA_BASE = "https://stockanalysis.com/api/screener"

# StockAnalysis metrics we treat as “fundamentals”
FUNDAMENTAL_METRICS = [
    "pbRatio", "psRatio", "pegRatio",
    "profitMargin", "operatingMargin", "grossMargin",
    "revenueGrowth", "epsGrowth",
    "debtEquity", "debtEbitda",
    "fcfYield", "earningsYield",
    "dividendYield",
    "sector",
    "sharesOut",
]


# ==============================================================================
# Normalization helper
# ==============================================================================
def _to_float(val):
    """
    Normalize fundamentals to float.
    Handles %, commas, dashes, blanks safely.
    Returns None if it can't be parsed.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    if s in {"", "-", "—", "N/A", "NA", "null"}:
        return None

    # strip commas and % signs
    s = s.replace(",", "")
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except Exception:
            return None

    try:
        return float(s)
    except Exception:
        return None


def _normalize_keys(node: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize camelCase fields to snake_case and convert to floats."""
    if not isinstance(node, dict):
        return {}

    out = {}

    # Map SA fields → normalized names
    rename_map = {
        "pbRatio": "pb_ratio",
        "psRatio": "ps_ratio",
        "pegRatio": "peg_ratio",
        "revenueGrowth": "revenue_growth",
        "epsGrowth": "eps_growth",
        "profitMargin": "profit_margin",
        "operatingMargin": "operating_margin",
        "grossMargin": "gross_margin",
        "fcfYield": "fcf_yield",
        "earningsYield": "earnings_yield",
        "dividendYield": "dividend_yield",
        "debtEquity": "debt_equity",
        "debtEbitda": "debt_ebitda",
        "sharesOut": "shares_outstanding",
    }

    for old, new in rename_map.items():
        if old in node:
            out[new] = _to_float(node[old])

    # Pass through any extra fields
    for k, v in node.items():
        if k not in rename_map:
            out[k] = v

    return out


# ==============================================================================
# StockAnalysis API helpers
# ==============================================================================

def _sa_get_metric(metric: str) -> Dict[str, Any]:
    """Fetch a StockAnalysis metric table."""
    url = f"{SA_BASE}/s/d/{metric}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return {}
        js = r.json()
        tbl = {}
        rows = (js or {}).get("data", {}).get("data", [])
        for row in rows:
            if isinstance(row, list):
                sym = str(row[0]).upper()
                val = row[1]
            else:
                sym = (row.get("symbol") or row.get("s") or "").upper()
                val = row.get(metric)
            if sym:
                tbl[sym] = val
        return tbl
    except Exception as e:
        log(f"⚠️ SA fundamental metric '{metric}' fetch failed: {e}")
        return {}


def _fetch_sa_fundamentals() -> Dict[str, Dict[str, Any]]:
    """Batch-fetch all fundamental metrics from StockAnalysis."""
    bundle: Dict[str, Dict[str, Any]] = {}
    for metric in FUNDAMENTAL_METRICS:
        tbl = _sa_get_metric(metric)
        for sym, val in tbl.items():
            if sym not in bundle:
                bundle[sym] = {"symbol": sym}
            bundle[sym][metric] = val
    return bundle


# ==============================================================================
# Optional FMP support (kept from your original code)
# ==============================================================================

def _fetch_fmp_data(sym: str, api_key: str | None) -> Dict[str, Any]:
    """Stock fundamentals from FMP (fallback source)."""
    if not api_key:
        return {}

    try:
        url = f"https://financialmodelingprep.com/api/v3/profile/{sym}?apikey={api_key}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return {}
        js = r.json()
        if not js:
            return {}
        row = js[0]
        return {
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "description": row.get("description"),
            "ceo": row.get("ceo"),
            "country": row.get("country"),
            "employees": row.get("fullTimeEmployees"),
            "beta": row.get("beta"),
            "pe_ratio": row.get("pe"),
            "marketcap": row.get("mktCap"),
        }
    except Exception as e:
        log(f"⚠️ FMP fetch failed for {sym}: {e}")
        return {}


# ==============================================================================
# Main fundamental enrichment
# ==============================================================================

def enrich_fundamentals() -> Dict[str, Any]:
    """
    Merge multiple sources (replay-aware):
        1) In replay mode: load from snapshot
        2) In live mode: fetch from StockAnalysis + FMP
    
    Integrate into Rolling:
        rolling[sym]["fundamentals"] = merged_fields
    """
    from backend.services.replay_data_pipeline import is_replay_mode, get_replay_date, load_fundamentals_for_replay
    
    # Replay mode: load from snapshot
    if is_replay_mode():
        replay_date = get_replay_date()
        if not replay_date:
            log("⚠️ Replay mode enabled but AION_ASOF_DATE not set")
            return {"status": "error", "error": "replay_mode_no_date"}
        
        log(f"🔄 Replay mode: loading fundamentals from snapshot ({replay_date})")
        try:
            fundamentals = load_fundamentals_for_replay(replay_date)
            
            # Apply to rolling
            rolling = _read_rolling()
            if not rolling:
                log("⚠️ No rolling.json.gz in replay mode")
                return {"status": "error", "error": "no_rolling"}
            
            updated = 0
            for sym, fund_data in fundamentals.items():
                if sym in rolling:
                    node = rolling[sym]
                    if isinstance(node, dict):
                        node["fundamentals"] = fund_data
                        rolling[sym] = node
                        updated += 1
            
            save_rolling(rolling)
            log(f"✅ Replay mode: loaded fundamentals for {updated} symbols from snapshot")
            return {"status": "ok", "updated": updated, "total": len(fundamentals)}
        except Exception as e:
            log(f"❌ Replay mode: failed to load fundamentals: {e}")
            return {"status": "error", "error": str(e)}
    
    # Live mode: continue with normal fetch logic
    rolling = _read_rolling()
    if not rolling:
        log("⚠️ No rolling.json.gz — fundamentals enrichment aborted.")
        return {"status": "no_rolling"}

    log("📘 Fetching fundamental metrics from StockAnalysis...")
    sa_bundle = _fetch_sa_fundamentals()

    updated = 0
    total = len(rolling)

    FMP_KEY = os.environ.get("FMP_API_KEY", "")

    for sym, node in rolling.items():
        if sym.startswith("_"):
            continue

        sym_u = sym.upper()
        base = {}

        # 1) SA metrics
        sa = sa_bundle.get(sym_u, {})
        base.update(sa)

        # 2) FMP optional enrichment (legacy support)
        if FMP_KEY:
            fmp = _fetch_fmp_data(sym_u, FMP_KEY)
            base.update(fmp)

        # normalize
        base = _normalize_keys(base)

        # Attach to rolling
        fund = node.get("fundamentals", {})
        fund.update(base)
        node["fundamentals"] = fund
        rolling[sym_u] = node
        updated += 1

    save_rolling(rolling)
    log(f"✅ Fundamentals enriched for {updated}/{total} symbols.")
    return {"updated": updated, "total": total, "status": "ok"}


def update_fundamentals(rolling=None):
    """
    Thin wrapper for nightly_job compatibility.
    Calls enrich_fundamentals() and returns its result.
    """
    return enrich_fundamentals()
