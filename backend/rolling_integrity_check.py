"""
rolling_integrity_check.py
────────────────────────────────────────────
Purpose:
    Scans rolling.json.gz and verifies that all expected data
    points (price, volume, ratios, metrics, etc.) exist for each
    ticker over its 180-day history window.

Output:
    stock_cache/master/rolling_integrity_report.json

Author: AION Analytics / Diagnostic Utility
"""

import os, json, gzip, time
from collections import defaultdict
from tqdm import tqdm

ROLLING_PATH = os.path.join("stock_cache", "master", "rolling.json.gz")
OUT_PATH = os.path.join("stock_cache", "master", "rolling_integrity_report.json")

# Define every field we care about (include both variants)
EXPECTED_FIELDS = [
    "date","open","high","low","close","volume","history",
    "symbol","name","sector","industry",
    "price","change","marketCap","beta",
    "peRatio","pe_ratio","pbRatio","pb_ratio","psRatio","ps_ratio",
    "pegRatio","peg_ratio","revenueGrowth","revenue_growth",
    "epsGrowth","eps_growth","profitMargin","profit_margin",
    "operatingMargin","operating_margin","grossMargin","gross_margin",
    "debtEquity","debt_equity","debtEbitda","debt_ebitda",
    "dividendYield","dividend_yield","fcfYield","earningsYield",
    "rsi","rsi_14","ma50","ma200","ma50ch","ma200ch",
    "ch1w","ch1m","ch3m","ch6m","ch1y","chYTD",
    "momentum_5d","volatility_10d","ret1","ret5","ret10",
    "trend","confidence","score","rankingScore"
]

def load_rolling():
    if not os.path.exists(ROLLING_PATH):
        raise FileNotFoundError(f"⚠️ rolling.json.gz not found at {ROLLING_PATH}")
    with gzip.open(ROLLING_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)

def analyze_ticker(sym: str, node: dict) -> dict:
    result = {}
    for field in EXPECTED_FIELDS:
        values = []
        # If field is in history array (open/high/low/close/volume)
        if field in ["open","high","low","close","volume","date"]:
            hist = node.get("history", [])
            vals = [h.get(field) for h in hist if isinstance(h, dict)]
            total = len(hist)
            have = sum(1 for v in vals if v not in (None, "", []))
            result[field] = {"data_points": have, "missing": max(0, total - have)}
        else:
            val = node.get(field)
            result[field] = {"data_points": 1 if val not in (None, "", []) else 0,
                             "missing": 0 if val not in (None, "", []) else 1}
    return result

def main():
    print(f"[{time.strftime('%H:%M:%S')}] 🔍 Loading rolling cache...")
    rolling = load_rolling()
    print(f"[{time.strftime('%H:%M:%S')}] 📦 Loaded {len(rolling)} tickers")

    report = {}
    with tqdm(total=len(rolling), ncols=90, desc="Scanning", unit="ticker", ascii=True) as bar:
        for sym, node in rolling.items():
            try:
                report[sym] = analyze_ticker(sym, node)
            except Exception as e:
                report[sym] = {"error": str(e)}
            finally:
                bar.update(1)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n✅ Integrity report saved → {OUT_PATH}")
    print("Example entry:")
    sample_sym = next(iter(report.keys()))
    print(json.dumps({sample_sym: report[sample_sym]}, indent=2)[:800])

if __name__ == "__main__":
    main()
