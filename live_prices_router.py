"""
live_prices_router.py — FastAPI router serving /live-prices using StockAnalysis live snapshot fetch.
Supports both batch (top N) and on-demand symbol queries.
"""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Query
from typing import Optional, List
from backend.data_pipeline import _fetch_from_stockanalysis, _read_rolling, log
import requests

router = APIRouter()

@router.get("/live-prices")
async def get_live_prices(
    symbols: Optional[str] = Query(
        None,
        description="Comma-separated list of symbols, e.g. AAPL,MSFT,TSLA. "
                    "If omitted, the first 50 tickers in Rolling are used."
    ),
    limit: int = Query(50, description="Number of tickers to fetch if no symbols provided."),
):
    """
    Returns live price snapshots for selected or default symbols.
    Pulls directly from StockAnalysis API via data_pipeline._fetch_from_stockanalysis.
    """
    rolling = _read_rolling() or {}

    # --- Determine symbols to fetch ---
    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbol_list = list(rolling.keys())[:limit]

    results = []
    for sym in symbol_list:
        try:
            snap = _fetch_from_stockanalysis(sym)
            if snap:
                results.append({
                    "symbol": sym,
                    "name": snap.get("name"),
                    "price": snap.get("close") or snap.get("price"),
                    "volume": snap.get("volume"),
                    "marketCap": snap.get("marketCap"),
                    "pe_ratio": snap.get("pe_ratio"),
                    "pb_ratio": snap.get("pb_ratio"),
                    "ps_ratio": snap.get("ps_ratio"),
                    "sector": snap.get("sector"),
                })
        except Exception as e:
            log(f"⚠️ live price fetch failed for {sym}: {e}")

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "count": len(results),
        "symbols_requested": symbol_list,
        "prices": results,
    }


# ------------------------------------------------------------
# Batch helper — fixed for ?m=marketCap&order=desc
# ------------------------------------------------------------
def _fetch_batch_from_stockanalysis(symbols: list[str]) -> dict:
    """
    Batch fetch live snapshot data for multiple tickers using StockAnalysis screener API (/s/i/).
    Fully aligned with backfill_history.py and tested JSON structure.
    """
    try:
        url = "https://stockanalysis.com/api/screener/s/i/"
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            log(f"⚠️ StockAnalysis batch fetch returned {r.status_code}")
            return {}

        j = r.json()
        # ✅ Correct JSON path: j["data"]["data"]
        data = (j.get("data") or {}).get("data", [])
        if not data:
            log("⚠️ StockAnalysis returned no data (empty dataset).")
            return {}

        out = {}
        for item in data:
            sym = str(item.get("s", "")).upper()
            if not sym:
                continue
            out[sym] = {
                "symbol": sym,
                "name": item.get("n"),
                "price": item.get("price"),
                "change": item.get("change"),
                "industry": item.get("industry"),
                "volume": item.get("volume"),
                "marketCap": item.get("marketCap"),
                "pe_ratio": item.get("peRatio"),
            }

        log(f"💹 Successfully parsed {len(out)} tickers from StockAnalysis /s/i/")
        return out

    except Exception as e:
        log(f"⚠️ Batch fetch failed: {e}")
        return {}

# ------------------------------------------------------------
# Synchronous helper for backend + DT jobs
# ------------------------------------------------------------
def fetch_live_prices(symbols: Optional[list[str]] = None, limit: int = 50) -> dict:
    """
    Sync batch fetch for backend + DT jobs using StockAnalysis screener /s/i/.
    Returns a dict keyed by symbol: { "AAPL": {"price": ..., "volume": ...}, ... }
    """
    rolling = _read_rolling() or {}

    # --- Determine symbols to fetch ---
    if symbols:
        symbol_list = [s.strip().upper() for s in symbols if s.strip()]
    else:
        symbol_list = list(rolling.keys())[:limit]

    if not symbol_list:
        log("⚠️ No symbols to fetch.")
        return {}

    # ✅ Batch call (one request)
    results = _fetch_batch_from_stockanalysis(symbol_list)
    log(f"💹 Batch fetched {len(results)} tickers from StockAnalysis /s/i/")
    return results
