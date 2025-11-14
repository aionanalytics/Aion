"""
ticker_fetcher.py
Builds and maintains:
  1) master.json   — { "AAPL": "Apple Inc.", ... }
  2) universe.json — [ "AAPL", "MSFT", "GOOGL", ... ]

Used by:
  - news_fetcher.py  (for name tagging)
  - data_pipeline.py (for offline fetch loops)
  - nightly_job.py   (step 0 prefetch)
"""

import os
import json
import aiohttp
import asyncio
from datetime import datetime
from typing import Dict, List, Any

CACHE_DIR = "stock_cache"
MAP_FILE = os.path.join(CACHE_DIR, "master.json")
UNIVERSE_FILE = os.path.join(CACHE_DIR, "universe.json")
os.makedirs(CACHE_DIR, exist_ok=True)

# =========================================================
# Fetch from StockAnalysis
# =========================================================
async def _fetch_stockanalysis_universe() -> Dict[str, Any]:
    """Fetch the full ticker universe from StockAnalysis."""
    url = "https://stockanalysis.com/api/screener/s/i"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"StockAnalysis API error {resp.status}")
            data = await resp.json()

    rows = (data.get("data") or {}).get("data", [])
    result = {}
    for row in rows:
        sym = row.get("s")
        if not sym:
            continue
        result[sym] = {
            "symbol": sym,
            "name": row.get("n", ""),
            "sector": row.get("industry"),
            "marketCap": row.get("marketCap"),
            "price": row.get("price"),
            "volume": row.get("volume"),
        }
    print(f"✅ Retrieved {len(result)} tickers from StockAnalysis")
    return result

# =========================================================
# Save / Load
# =========================================================
def _atomic_write(path: str, data: Any):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def save_universe(universe: List[str]):
    _atomic_write(UNIVERSE_FILE, universe)
    print(f"💾 Saved {len(universe)} symbols → {UNIVERSE_FILE}")

def save_master(mapping: Dict[str, str]):
    _atomic_write(MAP_FILE, mapping)
    print(f"💾 Saved {len(mapping)} symbol→name pairs → {MAP_FILE}")

def load_master() -> Dict[str, str]:
    if not os.path.exists(MAP_FILE):
        return {}
    return json.load(open(MAP_FILE, "r", encoding="utf-8"))

def load_universe() -> List[str]:
    if not os.path.exists(UNIVERSE_FILE):
        return []
    return json.load(open(UNIVERSE_FILE, "r", encoding="utf-8"))

# =========================================================
# Builder
# =========================================================
async def build_ticker_assets():
    """Fetch universe + map and write both files."""
    data = await _fetch_stockanalysis_universe()
    mapping = {sym: info.get("name", "") for sym, info in data.items()}
    universe = sorted(data.keys())
    save_master(mapping)
    save_universe(universe)
    return {"count": len(universe), "master": MAP_FILE, "universe": UNIVERSE_FILE}

# =========================================================
# Helper (merged from helper.py)
# =========================================================
def get_universe_symbols() -> list[str]:
    """Return the ticker universe list (fallback to master keys)."""
    uni = load_universe()
    if uni:
        return uni
    master = load_master()
    return list(master.keys())

# =========================================================
# CLI
# =========================================================
if __name__ == "__main__":
    print("🔎 Building master.json + universe.json ...")
    out = asyncio.run(build_ticker_assets())
    print(f"✅ Done: {out}")
