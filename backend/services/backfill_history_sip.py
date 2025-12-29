# backend/services/backfill_history.py
"""
backfill_history.py — v3.6
(Rolling-Native, Normalized Batch StockAnalysis Bundle, Alpaca History Bootstrap)

Purpose:
- Refreshes and repairs ticker data directly inside Rolling cache.
- BATCH fetches metrics from StockAnalysis (parallel /s/d/<metric> requests)
  using modern endpoints.
- Uses /s/i only for basic metadata (symbol, name, price, volume, marketCap, peRatio, industry).
- Uses /s/d/<metric> for everything else (incl. open/high/low/close, rsi, growth, etc.).
- Normalizes all fetched field names before saving (camelCase → snake_case, rsi → rsi_14).
- Writes directly into rolling.json.gz using the new backend.core.data_pipeline helpers.

Bootstrap History:
- Uses Alpaca Market Data API (daily bars) to fetch up to ~3 years (~750 trading days)
  when history is too short (< min_days).
- History is strictly append-only with per-date dedupe (never wipes existing history).

Universe Prune (safe):
- Track symbols where StockAnalysis bundle contains no data for that symbol
  (neither /s/i nor any /s/d metric row).
- Optionally prune them from master_universe.json (and swing_universe.json if present)
  after the run.
- Safety cap prevents mass-deletes if StockAnalysis is degraded/outage:
    AION_PRUNE_MAX_RATIO (default 0.05) limits prune to <= 5% of symbols per run.
- Pruning is disabled automatically if SA bundle is empty.

Notes:
- Alpaca "feed=iex" is the only feed available on free tiers and is incomplete vs SIP.
  If you have SIP entitlement, set AION_ALPACA_FEED=sip for fuller coverage.

"""

from __future__ import annotations

import os
import json
import gzip
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Iterable, Optional, Set, Tuple
from pathlib import Path
from threading import Lock

import requests

from backend.core.config import PATHS
from backend.core.data_pipeline import (
    _read_rolling,
    save_rolling,
    log,
)
from backend.services.metrics_fetcher import build_latest_metrics
from utils.progress_bar import progress_bar

UNIVERSE_FILE = PATHS["universe"] / "master_universe.json"

# -------------------------------------------------------------------
# Verbosity controls (tune as you like)
# -------------------------------------------------------------------
VERBOSE_BOOTSTRAP = False          # per-ticker “Bootstrapped history for XYZ…”
VERBOSE_BOOTSTRAP_ERRORS = False   # per-ticker bootstrap failure messages

# -------------------------------------------------------------------
# Env helpers
# -------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or default).strip()

def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "") or default).strip())
    except Exception:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "") or default).strip())
    except Exception:
        return default

def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v == "":
        return default
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default

# -------------------------------------------------------------------
# Alpaca (Market Data) config
# -------------------------------------------------------------------
ALPACA_API_KEY_ID = _env("ALPACA_API_KEY_ID")
ALPACA_API_SECRET_KEY = _env("ALPACA_API_SECRET_KEY")

# IMPORTANT: Market data uses data.alpaca.markets (not paper-api)
ALPACA_DATA_BASE_URL = _env("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/")

# feed: "iex" (free) or "sip" (paid)
ALPACA_FEED = _env("AION_ALPACA_FEED", _env("ALPACA_DATA_FEED", "iex")).lower() or "iex"

# Alpaca bulk fetch knobs
ALPACA_BARS_BATCH = max(1, _env_int("AION_ALPACA_BARS_BATCH", 200))  # max symbols per request (200 is common-safe)
ALPACA_BARS_TIMEFRAME = _env("AION_ALPACA_BARS_TIMEFRAME", "1Day")   # "1Day" for daily bars
ALPACA_LOOKBACK_DAYS = max(30, _env_int("AION_ALPACA_LOOKBACK_DAYS", 365 * 4))  # request window (4y gives buffer)

# Rate-limit / retry behavior (data API limits vary by plan; keep it gentle)
ALPACA_MAX_RETRIES = max(0, _env_int("AION_ALPACA_MAX_RETRIES", 6))
ALPACA_BACKOFF_SECONDS = max(0.25, _env_float("AION_ALPACA_BACKOFF_SECONDS", 1.5))
ALPACA_MIN_SPACING_SECONDS = max(0.0, _env_float("AION_ALPACA_MIN_SPACING_SECONDS", 0.12))

# -------------------------------------------------------------------
# Prune safety (StockAnalysis-based)
# -------------------------------------------------------------------
PRUNE_ENABLED = _env_bool("AION_PRUNE_ENABLED", default=True)
PRUNE_MAX_RATIO = max(0.0, min(1.0, _env_float("AION_PRUNE_MAX_RATIO", 0.05)))

# Track SA-missing cases for end-of-run prune (thread-safe)
_SA_NO_DATA: Set[str] = set()
_SA_NO_DATA_LOCK = Lock()

# Track Alpaca no-bars cases (for diagnostics; NOT used for pruning)
_ALPACA_NO_BARS: Set[str] = set()
_ALPACA_NO_BARS_LOCK = Lock()

# -------------------------------------------------------------------
# Universe helpers
# -------------------------------------------------------------------
def load_universe() -> list[str]:
    if not UNIVERSE_FILE.exists():
        log(f"⚠️ Universe file not found at {UNIVERSE_FILE}")
        return []
    try:
        with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "symbols" in data:
            return [str(x) for x in (data.get("symbols") or [])]
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception as e:
        log(f"⚠️ Failed to load universe: {e}")
    return []

# -------------------------------------------------------------------
# StockAnalysis endpoints
# -------------------------------------------------------------------
SA_BASE = "https://stockanalysis.com/api/screener"

# Index "base" fields from /s/i
SA_INDEX_FIELDS = [
    "symbol", "name", "price", "change", "volume",
    "marketCap", "peRatio", "industry",
]

# Metrics from /s/d/<metric> (aligned with SA docs)
# NOTE:
#   - rsi normalized → rsi_14
#   - sharesOut used for shares outstanding
#   - open/high/low/close fetched from /s/d/* for fuller bars
SA_METRICS = [
    "rsi", "ma50", "ma200",
    "pbRatio", "psRatio", "pegRatio",
    "beta",
    "fcfYield", "earningsYield", "dividendYield",
    "revenueGrowth", "epsGrowth",
    "profitMargin", "operatingMargin", "grossMargin",
    "debtEquity", "debtEbitda",
    "sector", "float", "sharesOut",
    "ch1w", "ch1m", "ch3m", "ch6m", "ch1y", "chYTD",
    "open", "high", "low", "close",
]

# How many days of history to keep in rolling (~3 trading years)
MAX_HISTORY_DAYS = 750

# Directory for audit bundle
METRICS_BUNDLE_DIR = Path("data") / "metrics_cache" / "bundle"
METRICS_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------
# HTTP helpers (StockAnalysis)
# -------------------------------------------------------------------
def _sa_post_json(path: str, payload: dict | None = None, timeout: int = 20) -> dict | None:
    """Generic helper for StockAnalysis API POST/GET requests."""
    url = f"{SA_BASE}/{path.strip('/')}"
    try:
        if payload is not None:
            r = requests.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        # Fallback GET
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log(f"⚠️ SA request failed for {url}: {e}")
    return None

# Simple symbol-level fetch (used only in *rare* incremental mode)
_INDEX_CACHE: Dict[str, Dict[str, Any]] = {}

def _fetch_from_stockanalysis(sym: str) -> Dict[str, Any] | None:
    """
    Lightweight helper to fetch a single symbol snapshot.
    For now, we reuse /s/i batch and cache once per run, then read from it.
    This is only used in the incremental branch, which is rarely hit.
    """
    global _INDEX_CACHE
    sym = sym.upper()

    if not _INDEX_CACHE:
        payload = {
            "fields": SA_INDEX_FIELDS,
            "filter": {"exchange": "all"},
            "order": ["marketCap", "desc"],
            "offset": 0,
            "limit": 10000,
        }
        js = _sa_post_json("s/i", payload)
        rows = (js or {}).get("data", {}).get("data", [])
        for row in rows:
            rsym = (row.get("symbol") or row.get("s") or "").upper()
            if not rsym:
                continue
            _INDEX_CACHE[rsym] = {
                "symbol": rsym,
                "name": row.get("name") or row.get("n"),
                "price": row.get("price"),
                "change": row.get("change"),
                "volume": row.get("volume"),
                "marketCap": row.get("marketCap"),
                "pe_ratio": row.get("peRatio"),
                "industry": row.get("industry"),
            }

    return _INDEX_CACHE.get(sym)

# -------------------------------------------------------------------
# Batch SA bundle builders
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

    def _job(m: str) -> Tuple[str, Dict[str, Any]]:
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
    """Convert camelCase → snake_case and ensure RSI normalized to rsi_14."""
    if not isinstance(node, dict):
        return node
    replacements = {
        "peRatio": "pe_ratio",
        "pbRatio": "pb_ratio",
        "psRatio": "ps_ratio",
        "pegRatio": "peg_ratio",
        "debtEquity": "debt_equity",
        "debtEbitda": "debt_ebitda",
        "revenueGrowth": "revenue_growth",
        "epsGrowth": "eps_growth",
        "profitMargin": "profit_margin",
        "operatingMargin": "operating_margin",
        "grossMargin": "gross_margin",
        "dividendYield": "dividend_yield",
        "fcfYield": "fcf_yield",
        "earningsYield": "earnings_yield",
        "rsi": "rsi_14",
        "sharesOut": "shares_outstanding",
    }
    for old, new in replacements.items():
        if old in node:
            node[new] = node.pop(old)
    return node

# -------------------------------------------------------------------
# Merge + bundle save
# -------------------------------------------------------------------
def _merge_index_and_metrics(
    index_map: Dict[str, Dict[str, Any]],
    metrics_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Merge /s/i index snapshot and /s/d metric tables into a per-symbol bundle."""
    out = dict(index_map)
    for metric, tbl in (metrics_map or {}).items():
        for sym, val in (tbl or {}).items():
            sym_u = str(sym).upper()
            if sym_u not in out:
                out[sym_u] = {"symbol": sym_u}
            out[sym_u][metric] = val
    return out

def _normalize_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize all field names in the bundle once at the end."""
    changed = 0
    for sym, node in bundle.items():
        if not isinstance(node, dict):
            continue
        before = set(node.keys())
        bundle[sym] = _normalize_node_keys(node)
        after = set(bundle[sym].keys())
        diff = len(after - before)
        if diff:
            changed += diff
    log(
        f"🔧 Normalization summary — {len(bundle)} tickers, "
        f"~{changed} fields normalized (rsi→rsi_14, sharesOut→shares_outstanding, etc.)."
    )
    return bundle

def _save_sa_bundle_snapshot(bundle: Dict[str, Any]) -> str | None:
    """Save full bundle snapshot for audit."""
    try:
        ts = datetime.utcnow().strftime("%Y-%m-%d")
        path = METRICS_BUNDLE_DIR / f"sa_bundle_{ts}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump({"date": ts, "data": bundle}, f)
        log(f"✅ Saved StockAnalysis bundle → {path}")
        return str(path)
    except Exception as e:
        log(f"⚠️ Failed to save SA bundle: {e}")
        return None

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
# Alpaca history bootstrap helpers
# -------------------------------------------------------------------
_ALPACA_TS_LOCK = Lock()
_ALPACA_LAST_CALL_TS = 0.0

def _alpaca_headers() -> Dict[str, str]:
    # Accept both common header styles
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY_ID,
        "APCA-API-SECRET-KEY": ALPACA_API_SECRET_KEY,
        "accept": "application/json",
        "user-agent": "AION-backfill/3.6",
    }

def _alpaca_sleep_spacing() -> None:
    """Enforce a minimum spacing between Alpaca requests (very light throttling)."""
    global _ALPACA_LAST_CALL_TS
    if ALPACA_MIN_SPACING_SECONDS <= 0:
        return
    try:
        with _ALPACA_TS_LOCK:
            now = time.time()
            wait = float(ALPACA_MIN_SPACING_SECONDS) - (now - float(_ALPACA_LAST_CALL_TS))
            if wait > 0:
                time.sleep(wait)
            _ALPACA_LAST_CALL_TS = time.time()
    except Exception:
        pass

def _alpaca_get_json(url: str, params: dict, timeout: int = 30) -> dict | None:
    """
    GET with retries + backoff for Alpaca data API.

    Returns decoded JSON dict or None.
    """
    if not ALPACA_API_KEY_ID or not ALPACA_API_SECRET_KEY:
        # Don't spam logs per symbol; log once when first needed.
        return None

    tries = 0
    backoff = float(ALPACA_BACKOFF_SECONDS)

    while True:
        tries += 1
        try:
            _alpaca_sleep_spacing()
            r = requests.get(url, headers=_alpaca_headers(), params=params, timeout=timeout)

            # Retry on transient issues
            if r.status_code in (429, 500, 502, 503, 504):
                if tries <= int(ALPACA_MAX_RETRIES):
                    if VERBOSE_BOOTSTRAP_ERRORS:
                        log(f"⚠️ Alpaca {r.status_code} on {url} (try {tries}/{ALPACA_MAX_RETRIES})")
                    time.sleep(backoff)
                    backoff = min(backoff * 1.8, 60.0)
                    continue
                return None

            if r.status_code != 200:
                if VERBOSE_BOOTSTRAP_ERRORS:
                    log(f"⚠️ Alpaca request failed {r.status_code}: {r.text[:200]}")
                return None

            return r.json()

        except KeyboardInterrupt:
            raise
        except Exception as e:
            if tries <= int(ALPACA_MAX_RETRIES):
                if VERBOSE_BOOTSTRAP_ERRORS:
                    log(f"⚠️ Alpaca request error (try {tries}/{ALPACA_MAX_RETRIES}): {e}")
                time.sleep(backoff)
                backoff = min(backoff * 1.8, 60.0)
                continue
            if VERBOSE_BOOTSTRAP_ERRORS:
                log(f"⚠️ Alpaca request error giving up: {e}")
            return None

def _parse_alpaca_bars_payload(js: dict) -> Dict[str, List[Dict[str, Any]]]:
    """
    Alpaca stock bars payload uses keys:
      - "bars": { "AAPL": [ {t,o,h,l,c,v,...}, ... ], ... }  (for multi-symbol)
        OR "bars": [ {t,o,h,l,c,v,...}, ... ]               (for single symbol)
    We normalize to: {SYM: [{"date": "YYYY-MM-DD", "open":..., ...}, ...], ...}
    """
    out: Dict[str, List[Dict[str, Any]]] = {}

    if not isinstance(js, dict):
        return out

    bars = js.get("bars")
    if bars is None:
        return out

    def _norm_bar(b: dict) -> Optional[Dict[str, Any]]:
        try:
            t = b.get("t") or b.get("timestamp") or b.get("time")
            if not t:
                return None
            # Alpaca timestamps are ISO8601; daily bars can be midnight UTC.
            d = str(t)[:10]
            return {
                "date": d,
                "open": float(b.get("o") or 0.0),
                "high": float(b.get("h") or 0.0),
                "low": float(b.get("l") or 0.0),
                "close": float(b.get("c") or 0.0),
                "volume": float(b.get("v") or 0.0),
            }
        except Exception:
            return None

    if isinstance(bars, dict):
        for sym, seq in bars.items():
            sym_u = str(sym).upper()
            out[sym_u] = []
            if isinstance(seq, list):
                for b in seq:
                    if isinstance(b, dict):
                        nb = _norm_bar(b)
                        if nb:
                            out[sym_u].append(nb)

    elif isinstance(bars, list):
        # Single-symbol format: infer symbol via "symbol" field if present; else caller should wrap.
        sym = None
        if bars and isinstance(bars[0], dict):
            sym = bars[0].get("S") or bars[0].get("symbol")
        sym_u = str(sym or "").upper() or "_"
        out[sym_u] = []
        for b in bars:
            if isinstance(b, dict):
                nb = _norm_bar(b)
                if nb:
                    out[sym_u].append(nb)

    # sort and dedupe per symbol
    for sym_u, seq in list(out.items()):
        by_date: Dict[str, Dict[str, Any]] = {}
        for bar in seq:
            d = str(bar.get("date"))
            if d:
                by_date[d] = bar
        merged = list(by_date.values())
        merged.sort(key=lambda x: x.get("date") or "")
        out[sym_u] = merged

    return out

def _fetch_alpaca_bars_bulk(symbols: List[str], start_iso: str, end_iso: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch bars for multiple symbols using Alpaca's multi-symbol endpoint.

    IMPORTANT: This endpoint paginates by (symbol, time). If you request many symbols
    with a big time window, you MUST follow next_page_token until exhausted,
    otherwise you'll mostly get bars for only the first few symbols alphabetically.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not symbols:
        return out

    # Normalize symbols list
    syms = sorted({str(s).upper() for s in symbols if str(s).strip()})
    url = f"{ALPACA_DATA_BASE_URL}/v2/stocks/bars"

    page_token: Optional[str] = None
    pages = 0
    max_pages = 500  # hard guard

    while True:
        pages += 1
        if pages > max_pages:
            if VERBOSE_BOOTSTRAP_ERRORS:
                log(f"⚠️ Alpaca bars pagination hit max_pages={max_pages}; stopping early.")
            break

        params = {
            "symbols": ",".join(syms),
            "timeframe": ALPACA_BARS_TIMEFRAME,
            "start": start_iso,
            "end": end_iso,
            "feed": ALPACA_FEED,
            "adjustment": "raw",
            "limit": 10000,  # Alpaca enforces max; requesting large reduces page churn
        }
        if page_token:
            params["page_token"] = page_token

        js = _alpaca_get_json(url, params=params, timeout=60)
        if not js:
            break

        parsed = _parse_alpaca_bars_payload(js)
        # Merge into out
        for sym_u, bars in parsed.items():
            if sym_u == "_":
                continue
            if sym_u not in out:
                out[sym_u] = []
            out[sym_u].extend(bars)

        nxt = js.get("next_page_token") or js.get("nextPageToken")
        page_token = str(nxt).strip() if nxt else ""

        if not page_token:
            break

    # Deduplicate and sort per symbol, cap
    for sym_u, seq in list(out.items()):
        by_date: Dict[str, Dict[str, Any]] = {}
        for bar in seq:
            d = str(bar.get("date"))
            if d:
                by_date[d] = bar
        merged = list(by_date.values())
        merged.sort(key=lambda x: x.get("date") or "")
        out[sym_u] = merged[-MAX_HISTORY_DAYS:]

    return out

def _unique_history_dates(hist: List[Dict[str, Any]]) -> set[str]:
    return {str(b.get("date")) for b in (hist or []) if b.get("date")}

def _merge_histories(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Append-only merge with per-date dedupe and MAX_HISTORY_DAYS cap."""
    by_date: Dict[str, Dict[str, Any]] = {}
    for bar in incoming or []:
        d = str(bar.get("date"))
        if d:
            by_date[d] = bar
    for bar in existing or []:
        d = str(bar.get("date"))
        if d and d not in by_date:
            by_date[d] = bar
    merged = list(by_date.values())
    merged.sort(key=lambda x: x.get("date") or "")
    return merged[-MAX_HISTORY_DAYS:]

def _ensure_bootstrap_history_if_needed_alpaca(
    symbol: str,
    hist: List[Dict[str, Any]],
    min_days: int,
    prefetched: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    If history is too short (< min_days unique dates), use Alpaca daily bars.
    Prefetched bars (if present) are used first; otherwise returns existing hist.
    """
    sym_u = symbol.upper()
    existing_dates = _unique_history_dates(hist)
    if len(existing_dates) >= min_days:
        return hist

    bars = prefetched or []
    if not bars:
        try:
            with _ALPACA_NO_BARS_LOCK:
                _ALPACA_NO_BARS.add(sym_u)
        except Exception:
            pass
        return hist

    merged = _merge_histories(hist, bars)

    if VERBOSE_BOOTSTRAP:
        log(f"🧪 Bootstrapped history for {sym_u}: {len(merged)} days (Alpaca {ALPACA_FEED}).")

    return merged

def _prefetch_alpaca_histories(
    symbols: List[str],
    rolling: Dict[str, Any],
    min_days: int,
    max_workers: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Prefetch Alpaca histories for symbols that need bootstrap (history < min_days).
    Uses multi-symbol endpoint in batches and paginates properly.
    Returns {SYM: [bars...]}.
    """
    if not symbols:
        return {}

    if not ALPACA_API_KEY_ID or not ALPACA_API_SECRET_KEY:
        log("⚠️ Alpaca keys missing — skipping Alpaca history bootstrap.")
        return {}

    need: List[str] = []
    for sym in symbols:
        sym_u = str(sym).upper()
        node = rolling.get(sym_u)
        hist = node.get("history") if isinstance(node, dict) else None
        hist = hist if isinstance(hist, list) else []
        if len(_unique_history_dates(hist)) < int(min_days):
            need.append(sym_u)

    need = sorted(set(need))
    if not need:
        return {}

    # Restrict to symbols that StockAnalysis knows about (optional safety).
    # This prevents spending tons of data requests on junk symbols.
    # Disable by setting AION_ALPACA_PREFETCH_REQUIRE_SA=0.
    require_sa = _env_bool("AION_ALPACA_PREFETCH_REQUIRE_SA", default=True)
    if require_sa:
        # We'll filter later once SA bundle is available. Here we keep all.
        pass

    batch = int(ALPACA_BARS_BATCH)
    feed = ALPACA_FEED

    start_dt = datetime.utcnow() - timedelta(days=int(ALPACA_LOOKBACK_DAYS))
    end_dt = datetime.utcnow() + timedelta(days=1)
    start_iso = start_dt.replace(microsecond=0).isoformat() + "Z"
    end_iso = end_dt.replace(microsecond=0).isoformat() + "Z"

    log(f"📥 Alpaca bootstrap prefetch: {len(need)} symbols need history (batch size={batch}, feed={feed}).")

    # Use a tiny threadpool: each task may paginate and is network heavy.
    # Default max_workers=1 is safest; you can raise if your plan supports it.
    max_workers = max(1, int(_env_int("AION_ALPACA_PREFETCH_WORKERS", max_workers)))

    # Split into chunks
    chunks: List[List[str]] = [need[i:i+batch] for i in range(0, len(need), batch)]
    out: Dict[str, List[Dict[str, Any]]] = {}
    out_lock = Lock()

    def _job(chunk_syms: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        return _fetch_alpaca_bars_bulk(chunk_syms, start_iso=start_iso, end_iso=end_iso)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_job, c): c for c in chunks}
        for fut in progress_bar(as_completed(futs), desc="Alpaca Prefetch", unit="batch", total=len(chunks)):
            try:
                got = fut.result() or {}
            except Exception as e:
                if VERBOSE_BOOTSTRAP_ERRORS:
                    log(f"⚠️ Alpaca prefetch batch failed: {e}")
                got = {}
            if got:
                with out_lock:
                    for sym_u, bars in got.items():
                        if sym_u not in out:
                            out[sym_u] = bars
                        else:
                            # merge (keep newest, dedupe)
                            out[sym_u] = _merge_histories(out[sym_u], bars)

    return out

# -------------------------------------------------------------------
# Universe pruning helpers (StockAnalysis-based)
# -------------------------------------------------------------------
def _prune_universe_file(path: Path, bad_syms: set[str]) -> int:
    """
    Remove symbols from a universe JSON file.

    Supports:
      - {"symbols": [...]} dict format
      - [...] list format

    Writes a timestamped backup next to the file before overwriting.
    Returns number removed.
    """
    if not path.exists():
        return 0

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"⚠️ Failed to read universe file for pruning {path}: {e}")
        return 0

    wrapper = None
    syms: list[str] = []

    if isinstance(raw, dict) and isinstance(raw.get("symbols"), list):
        wrapper = "dict"
        syms = [str(x) for x in (raw.get("symbols") or [])]
    elif isinstance(raw, list):
        wrapper = "list"
        syms = [str(x) for x in raw]
    else:
        return 0

    bad_u = {str(s).upper() for s in (bad_syms or set())}
    before_u = [str(s).upper() for s in syms]
    keep_u = [s for s in before_u if s not in bad_u]

    removed = int(len(before_u) - len(keep_u))
    if removed <= 0:
        return 0

    # backup original
    try:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(path.name + f".bak_{ts}")
        backup_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    except Exception:
        pass

    # write pruned (preserve wrapper format)
    try:
        if wrapper == "dict":
            raw["symbols"] = keep_u
            path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        else:
            path.write_text(json.dumps(keep_u, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"⚠️ Failed to write pruned universe file {path}: {e}")
        return 0

    return removed

# -------------------------------------------------------------------
# Local node helper (replaces ensure_symbol_fields)
# -------------------------------------------------------------------
def _ensure_symbol_node(rolling: Dict[str, Any], sym_u: str) -> Dict[str, Any]:
    """
    Minimal per-symbol scaffolding:
      - guarantees 'symbol' and 'history' keys exist.
    All other normalization (predictions/context/news/social/policy, sector
    normalization, etc.) is handled centrally in backend.core.data_pipeline.save_rolling().
    """
    node = rolling.get(sym_u)
    if not isinstance(node, dict):
        node = {"symbol": sym_u, "history": []}
    else:
        node.setdefault("symbol", sym_u)
        node.setdefault("history", [])
    rolling[sym_u] = node
    return node

# -------------------------------------------------------------------
# Main backfill routine (same external API)
# -------------------------------------------------------------------
def backfill_symbols(symbols: List[str], min_days: int = 180, max_workers: int = 8) -> int:
    """
    Perform full or incremental Rolling backfill.

    Called from:
        backend/jobs/nightly_job.py
        or CLI: python -m backend.services.backfill_history --min_days 180

    Mode:
        - If rolling is empty → 'fallback' full rebuild using SA bundle (+ Alpaca bootstrap)
        - Otherwise → 'full' bundle-based refresh (plus per-symbol Alpaca bootstrap if history < min_days)
        - Incremental branch kept for compatibility, but rarely used.
    """
    rolling = _read_rolling() or {}
    today = datetime.utcnow().strftime("%Y-%m-%d")
    mode = "full"
    if not rolling:
        mode = "fallback"
        log("⚠️ Rolling cache missing — forcing full rebuild.")
    log(f"🧩 Backfill mode: {mode.upper()} | Date: {today}")

    # If caller didn't specify symbols, derive from existing rolling keys (skip meta)
    if not symbols:
        symbols = [s for s in rolling.keys() if not str(s).startswith("_")]

    symbols = [str(s).upper() for s in symbols if str(s).strip()]
    total = len(symbols)
    if not total:
        log("⚠️ No symbols to backfill.")
        return 0

    start = time.time()

    # Prefetch histories (optional, but strongly recommended for performance)
    # We prefetch BEFORE SA fetch; later we'll optionally filter to SA-known symbols.
    alpaca_prefetch: Dict[str, List[Dict[str, Any]]] = {}
    try:
        alpaca_prefetch = _prefetch_alpaca_histories(symbols, rolling, min_days=min_days, max_workers=1)
    except Exception as e:
        log(f"⚠️ Alpaca prefetch failed: {e}")
        alpaca_prefetch = {}

    # ----------------------------------------------------------
    # FULL / FALLBACK MODE — bundle-based refresh + bootstrap
    # ----------------------------------------------------------
    updated = 0
    if mode in ("full", "fallback"):
        log(f"🔧 Starting full rolling backfill for {total} symbols (batch SA fetch + Alpaca bootstrap)…")
        sa_bundle = fetch_sa_bundle_parallel(max_workers=max_workers)

        sa_is_healthy = bool(sa_bundle)
        if sa_bundle:
            # Optionally rebuild metrics cache for other services.
            try:
                build_latest_metrics()
            except Exception as e:
                log(f"⚠️ build_latest_metrics during backfill failed: {e}")
        else:
            log("⚠️ Empty StockAnalysis bundle (skipping SA-prune logic).")

        # Optionally filter alpaca_prefetch to SA-known symbols (saves a lot of work on junk)
        if _env_bool("AION_ALPACA_PREFETCH_REQUIRE_SA", default=True) and sa_bundle:
            alpaca_prefetch = {k: v for k, v in alpaca_prefetch.items() if k in sa_bundle}

        def _process(sym: str) -> int:
            sym_u = sym.upper()
            node = _ensure_symbol_node(rolling, sym_u)

            hist = node.get("history") or []
            if not isinstance(hist, list):
                hist = []

            # Ensure multi-day history via Alpaca if too short
            pref = alpaca_prefetch.get(sym_u)
            hist = _ensure_bootstrap_history_if_needed_alpaca(sym_u, hist, min_days=min_days, prefetched=pref)

            # Pull SA node (metrics snapshot)
            sa = sa_bundle.get(sym_u) if sa_bundle else None
            if not sa or not isinstance(sa, dict) or (set(sa.keys()) <= {"symbol"}):
                # Track as SA-missing for prune consideration (only in full/fallback)
                if sa_is_healthy:
                    try:
                        with _SA_NO_DATA_LOCK:
                            _SA_NO_DATA.add(sym_u)
                    except Exception:
                        pass

                # Still persist bootstrapped history if we have it
                if hist:
                    node["history"] = hist
                    try:
                        last_bar = hist[-1]
                        node["close"] = last_bar.get("close")
                        node.setdefault("price", last_bar.get("close"))
                    except Exception:
                        pass
                    rolling[sym_u] = node
                    return 1
                rolling[sym_u] = node
                return 0

            latest_bar = {
                "date": today,
                "open": sa.get("open"),
                "high": sa.get("high"),
                "low": sa.get("low"),
                "close": sa.get("price") or sa.get("close"),
                "volume": sa.get("volume"),
            }

            # Append-only dedupe (history)
            by_date: Dict[str, Dict[str, Any]] = {}
            for bar in hist or []:
                d = str(bar.get("date"))
                if d:
                    by_date[d] = bar
            by_date[today] = latest_bar
            hist_new = list(by_date.values())
            hist_new.sort(key=lambda x: x.get("date") or "")
            hist_new = hist_new[-MAX_HISTORY_DAYS:]

            node["history"] = hist_new
            node["close"] = latest_bar.get("close")
            node.update(sa)
            rolling[sym_u] = node
            return 1

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_process, s): s for s in symbols}
            for fut in progress_bar(as_completed(futs), desc="Backfill (SA+Alpaca)", unit="sym", total=total):
                try:
                    updated += int(fut.result() or 0)
                except Exception:
                    pass

    # ----------------------------------------------------------
    # INCREMENTAL MODE — per-symbol repair (kept for compatibility)
    # ----------------------------------------------------------
    else:
        def _process(sym: str) -> int:
            sym_u = sym.upper()
            node = _ensure_symbol_node(rolling, sym_u)

            hist = node.get("history") or []
            if not isinstance(hist, list):
                hist = []

            pref = alpaca_prefetch.get(sym_u)
            hist = _ensure_bootstrap_history_if_needed_alpaca(sym_u, hist, min_days=min_days, prefetched=pref)

            # If already have today's bar, skip
            if hist and str(hist[-1].get("date")) == today:
                node["history"] = hist
                rolling[sym_u] = node
                return 0

            sa = _fetch_from_stockanalysis(sym_u)
            if not sa:
                # Still persist bootstrapped history if we have it
                if hist:
                    node["history"] = hist
                    try:
                        last_bar = hist[-1]
                        node["close"] = last_bar.get("close")
                        node.setdefault("price", last_bar.get("close"))
                    except Exception:
                        pass
                    rolling[sym_u] = node
                    return 1
                rolling[sym_u] = node
                return 0

            latest_bar = {
                "date": today,
                "open": sa.get("open"),
                "high": sa.get("high"),
                "low": sa.get("low"),
                "close": sa.get("price") or sa.get("close"),
                "volume": sa.get("volume"),
            }

            by_date: Dict[str, Dict[str, Any]] = {}
            for bar in hist or []:
                d = str(bar.get("date"))
                if d:
                    by_date[d] = bar
            by_date[today] = latest_bar
            hist_new = list(by_date.values())
            hist_new.sort(key=lambda x: x.get("date") or "")
            hist_new = hist_new[-MAX_HISTORY_DAYS:]

            node["history"] = hist_new
            node["close"] = latest_bar.get("close") or sa.get("close")
            node["marketCap"] = sa.get("marketCap", node.get("marketCap"))
            node.update(sa)
            rolling[sym_u] = node
            return 1

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_process, s): s for s in symbols}
            for fut in progress_bar(as_completed(futs), desc="Backfill (incremental)", unit="sym", total=total):
                try:
                    updated += int(fut.result() or 0)
                except Exception:
                    pass

    # ----------------------------------------------------------
    # Save rolling via new core helper (atomic + backups)
    # ----------------------------------------------------------
    save_rolling(rolling)

    # ----------------------------------------------------------
    # Universe auto-prune (StockAnalysis-based, safe)
    # ----------------------------------------------------------
    if PRUNE_ENABLED and mode in ("full", "fallback"):
        try:
            # Only prune if SA bundle was healthy (non-empty)
            with _SA_NO_DATA_LOCK:
                bad = set(_SA_NO_DATA)

            if bad:
                max_allowed = int(max(1, round(PRUNE_MAX_RATIO * float(total))))
                if len(bad) > max_allowed:
                    log(
                        f"⚠️ Universe auto-prune SKIPPED: {len(bad)} symbols flagged (no StockAnalysis data), "
                        f"cap={max_allowed} (AION_PRUNE_MAX_RATIO={PRUNE_MAX_RATIO})."
                    )
                else:
                    removed = 0
                    removed += _prune_universe_file(UNIVERSE_FILE, bad)

                    swing_file = PATHS["universe"] / "swing_universe.json"
                    if swing_file.exists():
                        removed += _prune_universe_file(swing_file, bad)

                    log(
                        f"🧹 Universe auto-prune: removed {removed} symbols "
                        f"(no StockAnalysis bundle data)."
                    )
        except Exception as e:
            log(f"⚠️ Universe prune step failed: {e}")

    # Diagnostics
    try:
        with _ALPACA_NO_BARS_LOCK:
            n_no_bars = len(_ALPACA_NO_BARS)
        if n_no_bars:
            log(f"ℹ️ Alpaca bootstrap: {n_no_bars} symbols still have no bars (feed={ALPACA_FEED}).")
    except Exception:
        pass

    dur = time.time() - start
    log(f"✅ Backfill ({mode}) complete — {updated}/{total} updated in {dur:.1f}s.")
    return updated

# -------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AION Rolling Backfill (Batch SA + Alpaca Bootstrap, New Core)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min_days", type=int, default=180)
    args = parser.parse_args()

    symbols = load_universe()

    if not symbols:
        log("⚠️ Universe empty — cannot backfill.")
    else:
        backfill_symbols(symbols, min_days=args.min_days, max_workers=args.workers)
