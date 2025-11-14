"""
backfill_history.py — v2.3 (Rolling-Native, Normalized Batch SA Bundle)
Author: AION Analytics / StockAnalyzerPro

Purpose:
- Refreshes and repairs ticker data directly inside Rolling cache.
- BATCH fetches metrics from StockAnalysis (parallel /s/d/<metric> requests)
  using modern endpoints.
- Uses /s/i only for basic metadata (symbol, name, price, volume, marketCap, peRatio, industry).
- Auto self-healing via get_symbol_data()/ensure_symbol_fields() if SA misses any.
- Normalizes all fetched field names before saving (camelCase → snake_case, rsi → rsi_14).
- Uses RollingLock for concurrency-safe writes.
"""

from __future__ import annotations
import os, sys, json, gzip, time, requests, traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Iterable
from pathlib import Path
from backend.data_pipeline import (
    _fetch_from_stockanalysis,
    _read_rolling,
    _atomic_write_json_gz,
    ensure_symbol_fields,
    RollingLock,
    log,
    ROLLING_PATH,
    MAX_HISTORY_DAYS,
    load_all_cached_stocks,
    save_rolling,
)
from .metrics_fetcher import build_latest_metrics

# -------------------------------------------------------------------
# StockAnalysis endpoints
# -------------------------------------------------------------------
SA_BASE = "https://stockanalysis.com/api/screener"

# Index "base" fields from /s/i
SA_INDEX_FIELDS = [
    "symbol", "name", "price", "change", "volume",
    "marketCap", "peRatio", "industry"
]

# Metrics from /s/d/<metric>
SA_METRICS = [
    "rsi", "ma50", "ma200",
    "pbRatio", "psRatio", "pegRatio",
    "beta",
    "fcfYield", "earningsYield", "dividendYield",
    "revenueGrowth", "epsGrowth",
    "profitMargin", "operatingMargin", "grossMargin",
    "debtEquity", "debtEbitda",
    "sector", "float", "sharesOutstanding",
    "ch1w", "ch1m", "ch3m", "ch6m", "ch1y", "chYTD",
]

# Directory for audit bundle
METRICS_BUNDLE_DIR = os.path.join("data", "metrics_cache", "bundle")
os.makedirs(METRICS_BUNDLE_DIR, exist_ok=True)

# -------------------------------------------------------------------
# HTTP helpers
# -------------------------------------------------------------------
def _sa_post_json(path: str, payload: dict | None = None, timeout=20) -> dict | None:
    """Generic helper for StockAnalysis API POST/GET requests."""
    url = f"{SA_BASE}/{path.strip('/')}"
    try:
        r = requests.post(url, json=(payload or {}), timeout=timeout)
        if r.status_code == 200:
            return r.json()
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# -------------------------------------------------------------------
# Batch bundle builders
# -------------------------------------------------------------------
def _fetch_sa_index_batch() -> Dict[str, Dict[str, Any]]:
    """Fetch base index snapshot from /s/i (up to 10k rows)."""
    payload = {
        "fields": SA_INDEX_FIELDS,
        "filter": {"exchange": "all"},
        "order": ["marketCap", "desc"],
        "offset": 0,
        "limit": 10000,
    }
    js = _sa_post_json("s/i", payload)
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = (js or {}).get("data", {}).get("data", [])
        for row in rows:
            sym = (row.get("symbol") or row.get("s") or "").upper()
            if not sym:
                continue
            out[sym] = {
                "symbol": sym,
                "name": row.get("name") or row.get("n"),
                "price": row.get("price"),
                "change": row.get("change"),
                "volume": row.get("volume"),
                "marketCap": row.get("marketCap"),
                "pe_ratio": row.get("peRatio"),
                "industry": row.get("industry"),
            }
    except Exception as e:
        log(f"⚠️ Failed to parse /s/i: {e}")
    return out

def _fetch_sa_metric(metric: str, timeout: int = 20) -> Dict[str, Any]:
    """Fetch a single metric table from /s/d/<metric>."""
    js = _sa_post_json(f"s/d/{metric}", timeout=timeout)
    out: Dict[str, Any] = {}
    try:
        rows = (js or {}).get("data", {}).get("data", [])
        for r in rows:
            if isinstance(r, list) and len(r) >= 2:
                out[str(r[0]).upper()] = r[1]
            elif isinstance(r, dict):
                sym = r.get("symbol") or r.get("s")
                val = r.get(metric)
                if sym:
                    out[str(sym).upper()] = val
    except Exception:
        pass
    return out

def _fetch_sa_metrics_bulk(metrics: Iterable[str], max_workers: int = 8) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple /s/d/<metric> endpoints in parallel."""
    result: Dict[str, Dict[str, Any]] = {}
    metrics = list(metrics)

    def _job(m):
        return m, _fetch_sa_metric(m)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_job, m) for m in metrics]
        for fut in as_completed(futs):
            m, tbl = fut.result()
            result[m] = tbl or {}
    return result

# -------------------------------------------------------------------
# Normalization helper
# -------------------------------------------------------------------
def _normalize_node_keys(node: Dict[str, Any]) -> Dict[str, Any]:
    """Convert camelCase → snake_case and ensure RSI normalized."""
    if not isinstance(node, dict):
        return node
    replacements = {
        "peRatio": "pe_ratio", "pbRatio": "pb_ratio", "psRatio": "ps_ratio",
        "pegRatio": "peg_ratio", "debtEquity": "debt_equity", "debtEbitda": "debt_ebitda",
        "revenueGrowth": "revenue_growth", "epsGrowth": "eps_growth",
        "profitMargin": "profit_margin", "operatingMargin": "operating_margin", "grossMargin": "gross_margin",
        "dividendYield": "dividend_yield",
        "fcfYield": "fcf_yield", "earningsYield": "earnings_yield",
        "rsi": "rsi_14"
    }
    for old, new in replacements.items():
        if old in node:
            node[new] = node.pop(old)
    return node

# -------------------------------------------------------------------
# Merge + bundle save
# -------------------------------------------------------------------
def _merge_index_and_metrics(index_map: Dict[str, Dict[str, Any]], metrics_map: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Merge /s/i index snapshot and /s/d metric tables."""
    out = dict(index_map)
    for metric, tbl in (metrics_map or {}).items():
        for sym, val in (tbl or {}).items():
            if sym not in out:
                out[sym] = {"symbol": sym}
            out[sym][metric] = val
    return out

def _normalize_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize all field names in the bundle once at the end."""
    changed = 0
    for sym, node in bundle.items():
        before = set(node.keys())
        bundle[sym] = _normalize_node_keys(node)
        after = set(bundle[sym].keys())
        diff = len(after - before)
        if diff:
            changed += diff
    log(f"🔧 Normalization summary — {len(bundle)} tickers, ~{changed} fields normalized (rsi→rsi_14 included).")
    return bundle

def _save_sa_bundle_snapshot(bundle: Dict[str, Any]) -> str | None:
    """Save full bundle snapshot for audit."""
    try:
        ts = datetime.utcnow().strftime("%Y-%m-%d")
        path = os.path.join(METRICS_BUNDLE_DIR, f"sa_bundle_{ts}.json.gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump({"date": ts, "data": bundle}, f)
        log(f"✅ Saved StockAnalysis bundle → {path}")
        return path
    except Exception as e:
        log(f"⚠️ Failed to save SA bundle: {e}")
        return None

# -------------------------------------------------------------------
# Bundle build
# -------------------------------------------------------------------
def fetch_sa_bundle_parallel(max_workers: int = 8) -> Dict[str, Dict[str, Any]]:
    """Fetch index + all metrics, normalize, and return unified bundle."""
    base = _fetch_sa_index_batch()
    if not base:
        log("⚠️ /s/i returned no rows.")
        return {}
    metrics_map = _fetch_sa_metrics_bulk(SA_METRICS, max_workers=max_workers)
    bundle = _merge_index_and_metrics(base, metrics_map)
    bundle = _normalize_bundle(bundle)
    _save_sa_bundle_snapshot(bundle)
    return bundle

# -------------------------------------------------------------------
# Main backfill routine (same logic)
# -------------------------------------------------------------------
def backfill_symbols(symbols: List[str], min_days: int = 180, max_workers: int = 8):
    """Perform full or incremental Rolling backfill."""
    import calendar

    rolling = _read_rolling() or {}
    today = datetime.utcnow().strftime("%Y-%m-%d")
    weekday = datetime.utcnow().weekday()
    mode = "full"
    if not rolling:
        mode = "fallback"
        log("⚠️ Rolling cache missing — forcing full rebuild.")
    log(f"🧩 Backfill mode: {mode.upper()} | Date: {today}")

    if not symbols:
        symbols = list(rolling.keys())
    total = len(symbols)
    if not total:
        log("⚠️ No symbols to backfill.")
        return 0

    updated = 0
    start = time.time()

    if mode in ("full", "fallback"):
        log(f"🔧 Starting full rolling backfill for {total} symbols (batch SA fetch)...")
        sa_bundle = fetch_sa_bundle_parallel(max_workers=max_workers)
        if sa_bundle:
            build_latest_metrics()
        else:
            log("⚠️ Empty SA bundle.")

        def _process(sym: str) -> int:
            sym = sym.upper()
            node = rolling.get(sym) or {"symbol": sym, "history": []}
            sa = sa_bundle.get(sym)
            if not sa:
                return 0
            hist = node.get("history") or []
            latest_bar = {
                "date": today,
                "open": sa.get("open"),
                "high": sa.get("high"),
                "low": sa.get("low"),
                "close": sa.get("price"),
                "volume": sa.get("volume"),
            }
            hist.append(latest_bar)
            hist = sorted(hist, key=lambda x: x.get("date") or "")[-MAX_HISTORY_DAYS:]
            node["history"] = hist
            node["close"] = latest_bar.get("close")
            node.update(sa)
            rolling[sym] = node
            return 1

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_process, s) for s in symbols]
            for i, fut in enumerate(as_completed(futs), 1):
                updated += fut.result()
                pct = 100 * i / total
                sys.stdout.write(f"\r({i}/{total}) — {pct:.1f}% done | {updated} updated")
                sys.stdout.flush()

    else:
        def _process(sym: str) -> int:
            sym = sym.upper()
            node = rolling.get(sym) or ensure_symbol_fields(sym)
            if not node:
                return 0
            hist = node.get("history") or []
            if hist and str(hist[-1].get("date")) == today:
                return 0
            sa = _fetch_from_stockanalysis(sym)
            if not sa:
                return 0
            latest_bar = {
                "date": today,
                "open": sa.get("open"),
                "high": sa.get("high"),
                "low": sa.get("low"),
                "close": sa.get("close"),
                "volume": sa.get("volume"),
            }
            hist.append(latest_bar)
            hist = hist[-MAX_HISTORY_DAYS:]
            node["history"] = hist
            node["close"] = sa.get("close")
            node["marketCap"] = sa.get("marketCap", node.get("marketCap"))
            rolling[sym] = node
            return 1

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_process, s) for s in symbols]
            for i, fut in enumerate(as_completed(futs), 1):
                updated += fut.result()
                pct = 100 * i / total
                sys.stdout.write(f"\r({i}/{total}) — {pct:.1f}% done | {updated} updated")
                sys.stdout.flush()

    with RollingLock():
        _atomic_write_json_gz(ROLLING_PATH, rolling)

    dur = time.time() - start
    log(f"\n✅ Backfill ({mode}) complete — {updated}/{total} updated in {dur:.1f}s.")
    return updated

# -------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AION Rolling Backfill (Batch SA)")
    parser.add_argument("--workers", type=int, default=8, help="Thread pool size")
    parser.add_argument("--min_days", type=int, default=180, help="Minimum history days")
    args = parser.parse_args()
    rolling = _read_rolling()
    symbols = list(rolling.keys())
    backfill_symbols(symbols, min_days=args.min_days, max_workers=args.workers)
