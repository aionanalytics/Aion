# backend/services/backfill_history.py
"""
backfill_history.py — v3.5
(Rolling-Native, Normalized Batch StockAnalysis Bundle, Alpaca History Bootstrap)

Purpose:
- Refreshes and repairs ticker data directly inside Rolling cache.
- BATCH fetches metrics from StockAnalysis (parallel /s/d/<metric> requests).
- Uses /s/i only for basic metadata (symbol, name, price, volume, marketCap, peRatio, industry).
- Uses /s/d/<metric> for everything else (incl. open/high/low/close, rsi, growth, etc.).
- Normalizes all fetched field names before saving (camelCase → snake_case, rsi → rsi_14).
- Writes directly into rolling.json.gz using backend.core.data_pipeline helpers.

History bootstrap (replaces YFinance):
- If history is too short (< min_days unique dates), bootstrap daily bars from Alpaca Data API.
- Bootstrap is append-only + per-date dedupe (never wipes existing history).
- Bootstrap is done in BULK batches to minimize API calls.

Universe auto-prune (safe):
- Track symbols that have NO StockAnalysis bundle entry AND also have NO Alpaca history (0 bars).
- Optionally prune those from master_universe.json after the run.
- Safety cap prevents mass deletes:
    AION_PRUNE_MAX_RATIO (default 0.05) limits prune to <= 5% of symbols per run.

Environment:
- Alpaca Data API requires:
    ALPACA_API_KEY_ID
    ALPACA_API_SECRET_KEY
  Optional:
    ALPACA_DATA_BASE_URL (default: https://data.alpaca.markets)
    ALPACA_DATA_FEED (default: iex)
    AION_ALPACA_BATCH_SIZE (default: 200)
    AION_ALPACA_MAX_RETRIES (default: 5)
    AION_ALPACA_BACKOFF_SECONDS (default: 1.5)
    AION_PRUNE_MAX_RATIO (default: 0.05)
    AION_SA_INDEX_LIMIT (default: 20000)
"""

from __future__ import annotations

import gzip
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

from backend.core.config import PATHS
from backend.core.data_pipeline import _read_rolling, log, save_rolling
from backend.services.metrics_fetcher import build_latest_metrics
from utils.progress_bar import progress_bar

UNIVERSE_FILE = PATHS["universe"] / "master_universe.json"

# -------------------------------------------------------------------
# Verbosity controls (tune as you like)
# -------------------------------------------------------------------
VERBOSE_BOOTSTRAP = False          # per-ticker “Bootstrapped history for XYZ…”
VERBOSE_BOOTSTRAP_ERRORS = False   # per-ticker Alpaca failure messages

# -------------------------------------------------------------------
# Env helpers
# -------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()

def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return default

# Safety: refuse to prune a huge chunk in one run
PRUNE_MAX_RATIO = max(0.0, min(1.0, _env_float("AION_PRUNE_MAX_RATIO", 0.05)))

# StockAnalysis /s/i limit (your universe is >10k, so default to 20k)
SA_INDEX_LIMIT = max(1000, _env_int("AION_SA_INDEX_LIMIT", 20000))

# -------------------------------------------------------------------
# Alpaca Data API config
# -------------------------------------------------------------------
ALPACA_API_KEY_ID = _env("ALPACA_API_KEY_ID")
ALPACA_API_SECRET_KEY = _env("ALPACA_API_SECRET_KEY")
ALPACA_DATA_BASE_URL = _env("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/")
ALPACA_DATA_FEED = _env("ALPACA_DATA_FEED", "iex")

ALPACA_BATCH_SIZE = max(1, min(1000, _env_int("AION_ALPACA_BATCH_SIZE", 200)))
ALPACA_MAX_RETRIES = max(0, _env_int("AION_ALPACA_MAX_RETRIES", 5))
ALPACA_BACKOFF_SECONDS = max(0.25, _env_float("AION_ALPACA_BACKOFF_SECONDS", 1.5))

# -------------------------------------------------------------------
# Track "bad" symbols for end-of-run prune (thread-safe)
#   Criteria: missing from StockAnalysis bundle AND Alpaca returns 0 bars.
# -------------------------------------------------------------------
_NO_DATA: Set[str] = set()
_NO_DATA_LOCK = Lock()

# -------------------------------------------------------------------
# StockAnalysis endpoints
# -------------------------------------------------------------------
SA_BASE = "https://stockanalysis.com/api/screener"

SA_INDEX_FIELDS = [
    "symbol", "name", "price", "change", "volume",
    "marketCap", "peRatio", "industry",
]

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
# Universe loader
# -------------------------------------------------------------------
def load_universe() -> list[str]:
    if not UNIVERSE_FILE.exists():
        log(f"⚠️ Universe file not found at {UNIVERSE_FILE}")
        return []
    try:
        with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "symbols" in data:
            return data["symbols"]
        if isinstance(data, list):
            return data
    except Exception as e:
        log(f"⚠️ Failed to load universe: {e}")
    return []

# -------------------------------------------------------------------
# HTTP helpers — StockAnalysis
# -------------------------------------------------------------------
def _sa_post_json(path: str, payload: dict | None = None, timeout: int = 20) -> dict | None:
    url = f"{SA_BASE}/{path.strip('/')}"
    try:
        if payload is not None:
            r = requests.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log(f"⚠️ SA request failed for {url}: {e}")
    return None

# Simple symbol-level fetch (used only in rare incremental mode)
_INDEX_CACHE: Dict[str, Dict[str, Any]] = {}

def _fetch_from_stockanalysis(sym: str) -> Dict[str, Any] | None:
    global _INDEX_CACHE
    sym = sym.upper()

    if not _INDEX_CACHE:
        payload = {
            "fields": SA_INDEX_FIELDS,
            "filter": {"exchange": "all"},
            "order": ["marketCap", "desc"],
            "offset": 0,
            "limit": int(SA_INDEX_LIMIT),
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
    payload = {
        "fields": SA_INDEX_FIELDS,
        "filter": {"exchange": "all"},
        "order": ["marketCap", "desc"],
        "offset": 0,
        "limit": int(SA_INDEX_LIMIT),
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
    result: Dict[str, Dict[str, Any]] = {}
    metrics = list(metrics)

    def _job(m: str):
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

def _merge_index_and_metrics(
    index_map: Dict[str, Dict[str, Any]],
    metrics_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    out = dict(index_map)
    for metric, tbl in (metrics_map or {}).items():
        for sym, val in (tbl or {}).items():
            if sym not in out:
                out[sym] = {"symbol": sym}
            out[sym][metric] = val
    return out

def _normalize_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    changed = 0
    for _, node in bundle.items():
        before = set(node.keys())
        _normalize_node_keys(node)
        after = set(node.keys())
        diff = len(after - before)
        if diff:
            changed += diff
    log(
        f"🔧 Normalization summary — {len(bundle)} tickers, "
        f"~{changed} fields normalized (rsi→rsi_14, sharesOut→shares_outstanding, etc.)."
    )
    return bundle

def _save_sa_bundle_snapshot(bundle: Dict[str, Any]) -> str | None:
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = METRICS_BUNDLE_DIR / f"sa_bundle_{ts}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump({"date": ts, "data": bundle}, f)
        log(f"✅ Saved StockAnalysis bundle → {path}")
        return str(path)
    except Exception as e:
        log(f"⚠️ Failed to save SA bundle: {e}")
        return None

def fetch_sa_bundle_parallel(max_workers: int = 8) -> Dict[str, Dict[str, Any]]:
    base = _fetch_sa_index_batch()
    if not base:
        log("⚠️ /s/i returned no rows.")
        return {}

    metrics_map = _fetch_sa_metrics_bulk(SA_METRICS, max_workers=max_workers)
    bundle = _merge_index_and_metrics(base, metrics_map)
    _normalize_bundle(bundle)
    _save_sa_bundle_snapshot(bundle)
    return bundle

# -------------------------------------------------------------------
# Alpaca Data helpers — daily bars (bulk)
# -------------------------------------------------------------------
def _alpaca_headers() -> Dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY_ID,
        "APCA-API-SECRET-KEY": ALPACA_API_SECRET_KEY,
    }

def _alpaca_get_json(path: str, params: dict, timeout: int = 30) -> Optional[dict]:
    if not ALPACA_API_KEY_ID or not ALPACA_API_SECRET_KEY:
        return None

    url = f"{ALPACA_DATA_BASE_URL.rstrip('/')}{path}"
    backoff = float(ALPACA_BACKOFF_SECONDS)

    for attempt in range(1, int(ALPACA_MAX_RETRIES) + 2):
        try:
            r = requests.get(url, headers=_alpaca_headers(), params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()

            if r.status_code in (429, 500, 502, 503, 504):
                if attempt <= int(ALPACA_MAX_RETRIES):
                    time.sleep(backoff)
                    backoff = min(backoff * 1.8, 30.0)
                    continue
                return None

            return None
        except Exception:
            if attempt <= int(ALPACA_MAX_RETRIES):
                time.sleep(backoff)
                backoff = min(backoff * 1.8, 30.0)
                continue
            return None
    return None

def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _date_from_ts(ts: Any) -> str:
    try:
        s = str(ts)
        if "T" in s:
            return s.split("T", 1)[0]
        return s[:10]
    except Exception:
        return ""

def _bars_to_history(bars: list[dict]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for b in bars or []:
        d = _date_from_ts(b.get("t"))
        if not d:
            continue
        out.append(
            {
                "date": d,
                "open": float(b.get("o") or 0.0),
                "high": float(b.get("h") or 0.0),
                "low": float(b.get("l") or 0.0),
                "close": float(b.get("c") or 0.0),
                "volume": float(b.get("v") or 0.0),
            }
        )
    out.sort(key=lambda x: x.get("date") or "")
    return out[-MAX_HISTORY_DAYS:]

def _fetch_alpaca_bars_bulk(symbols: List[str], start: datetime, end: datetime) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {s.upper(): [] for s in symbols}
    if not symbols or not ALPACA_API_KEY_ID or not ALPACA_API_SECRET_KEY:
        return out

    params = {
        "symbols": ",".join([s.upper() for s in symbols]),
        "timeframe": "1Day",
        "start": _iso_z(start),
        "end": _iso_z(end),
        "adjustment": "raw",
        "feed": ALPACA_DATA_FEED,
        "limit": 10000,
    }

    page_token: Optional[str] = None
    safety_pages = 0

    while True:
        if page_token:
            params["page_token"] = page_token
        else:
            params.pop("page_token", None)

        js = _alpaca_get_json("/v2/stocks/bars", params=params, timeout=30)
        if not js:
            return out

        bars_obj = js.get("bars") or {}
        if isinstance(bars_obj, dict):
            for sym, bars in bars_obj.items():
                sym_u = str(sym).upper()
                if sym_u not in out:
                    out[sym_u] = []
                if isinstance(bars, list):
                    out[sym_u].extend(_bars_to_history(bars))

        page_token = js.get("next_page_token") or None
        safety_pages += 1
        if not page_token or safety_pages >= 5:
            break

    # Deduplicate per date (since we appended per page)
    for sym, hist in out.items():
        by_date: Dict[str, Dict[str, Any]] = {}
        for bar in hist or []:
            d = str(bar.get("date"))
            if not d:
                continue
            by_date[d] = bar
        merged = list(by_date.values())
        merged.sort(key=lambda x: x.get("date") or "")
        out[sym] = merged[-MAX_HISTORY_DAYS:]
    return out

def _unique_history_dates(hist: List[Dict[str, Any]]) -> set[str]:
    return {str(b.get("date")) for b in (hist or []) if b.get("date")}

def _prefetch_alpaca_histories(symbols: List[str], rolling: Dict[str, Any], min_days: int) -> Dict[str, List[Dict[str, Any]]]:
    need: List[str] = []
    for s in symbols:
        sym = str(s).upper()
        node = rolling.get(sym)
        hist = (node or {}).get("history") if isinstance(node, dict) else None
        hist = hist or []
        if len(_unique_history_dates(hist)) < int(min_days):
            need.append(sym)

    if not need:
        return {}

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * 4)

    log(f"📥 Alpaca bootstrap prefetch: {len(need)} symbols need history (batch size={ALPACA_BATCH_SIZE}, feed={ALPACA_DATA_FEED}).")
    out: Dict[str, List[Dict[str, Any]]] = {}

    for i in range(0, len(need), int(ALPACA_BATCH_SIZE)):
        chunk = need[i:i + int(ALPACA_BATCH_SIZE)]
        out.update(_fetch_alpaca_bars_bulk(chunk, start=start, end=end))

    return out

def _ensure_bootstrap_history_if_needed(
    symbol: str,
    hist: List[Dict[str, Any]],
    min_days: int,
    alpaca_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    symbol = symbol.upper()
    existing_dates = _unique_history_dates(hist)
    if len(existing_dates) >= int(min_days):
        return hist

    alp_bars: List[Dict[str, Any]] = []
    if alpaca_cache is not None:
        alp_bars = alpaca_cache.get(symbol) or []

    if not alp_bars:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=365 * 4)
        alp_bars = _fetch_alpaca_bars_bulk([symbol], start=start, end=end).get(symbol) or []

    if not alp_bars:
        return hist

    by_date: Dict[str, Dict[str, Any]] = {}

    for bar in alp_bars:
        d = str(bar.get("date"))
        if not d:
            continue
        by_date[d] = bar

    for bar in hist or []:
        d = str(bar.get("date"))
        if not d:
            continue
        if d not in by_date:
            by_date[d] = bar

    merged = list(by_date.values())
    merged.sort(key=lambda x: x.get("date") or "")
    merged = merged[-MAX_HISTORY_DAYS:]

    if VERBOSE_BOOTSTRAP:
        log(f"🧪 Bootstrapped history for {symbol}: {len(merged)} days.")

    return merged

# -------------------------------------------------------------------
# Local node helper
# -------------------------------------------------------------------
def _ensure_symbol_node(rolling: Dict[str, Any], sym_u: str) -> Dict[str, Any]:
    node = rolling.get(sym_u)
    if not isinstance(node, dict):
        node = {"symbol": sym_u, "history": []}
    else:
        node.setdefault("symbol", sym_u)
        node.setdefault("history", [])
    rolling[sym_u] = node
    return node

# -------------------------------------------------------------------
# Universe pruning helpers
# -------------------------------------------------------------------
def _prune_universe_file(path: Path, bad_syms: set[str]) -> int:
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
    before = [str(s).upper() for s in syms]
    keep = [s for s in before if s not in bad_u]

    removed = int(len(before) - len(keep))
    if removed <= 0:
        return 0

    # backup original
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(path.name + f".bak_{ts}")
        backup_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    except Exception:
        pass

    # write pruned
    try:
        if wrapper == "dict":
            raw["symbols"] = keep
            path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        else:
            path.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"⚠️ Failed to write pruned universe file {path}: {e}")
        return 0

    return removed

# -------------------------------------------------------------------
# Main backfill routine
# -------------------------------------------------------------------
def backfill_symbols(symbols: List[str], min_days: int = 180, max_workers: int = 8) -> int:
    rolling = _read_rolling() or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    mode = "full"
    if not rolling:
        mode = "fallback"
        log("⚠️ Rolling cache missing — forcing full rebuild.")
    log(f"🧩 Backfill mode: {mode.upper()} | Date: {today}")

    if not symbols:
        symbols = [s for s in rolling.keys() if not str(s).startswith("_")]

    total = len(symbols)
    if not total:
        log("⚠️ No symbols to backfill.")
        return 0

    alpaca_cache = _prefetch_alpaca_histories(symbols, rolling=rolling, min_days=min_days)

    updated = 0
    start_ts = time.time()

    if mode in ("full", "fallback"):
        log(f"🔧 Starting full rolling backfill for {total} symbols (batch SA fetch + Alpaca bootstrap)…")
        sa_bundle = fetch_sa_bundle_parallel(max_workers=max_workers)

        if sa_bundle:
            try:
                build_latest_metrics()
            except Exception as e:
                log(f"⚠️ build_latest_metrics during backfill failed: {e}")
        else:
            log("⚠️ Empty SA bundle.")

        def _process(sym: str) -> int:
            sym_u = str(sym).upper()
            node = _ensure_symbol_node(rolling, sym_u)

            hist = node.get("history") or []
            hist = _ensure_bootstrap_history_if_needed(sym_u, hist, min_days=min_days, alpaca_cache=alpaca_cache)

            sa = sa_bundle.get(sym_u) if sa_bundle else None

            if not sa:
                if not hist:
                    try:
                        with _NO_DATA_LOCK:
                            _NO_DATA.add(sym_u)
                    except Exception:
                        pass
                    return 0

                node["history"] = hist
                try:
                    last_bar = hist[-1]
                    node["close"] = last_bar.get("close")
                    node.setdefault("price", last_bar.get("close"))
                except Exception:
                    pass
                rolling[sym_u] = node
                return 1

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
                if not d:
                    continue
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
                updated += int(fut.result() or 0)

    else:
        def _process(sym: str) -> int:
            sym_u = str(sym).upper()
            node = _ensure_symbol_node(rolling, sym_u)

            hist = node.get("history") or []
            hist = _ensure_bootstrap_history_if_needed(sym_u, hist, min_days=min_days, alpaca_cache=alpaca_cache)

            if hist and str(hist[-1].get("date")) == today:
                node["history"] = hist
                rolling[sym_u] = node
                return 0

            sa = _fetch_from_stockanalysis(sym_u)
            if not sa:
                if not hist:
                    try:
                        with _NO_DATA_LOCK:
                            _NO_DATA.add(sym_u)
                    except Exception:
                        pass
                    return 0

                node["history"] = hist
                try:
                    last_bar = hist[-1]
                    node["close"] = last_bar.get("close")
                    node.setdefault("price", last_bar.get("close"))
                except Exception:
                    pass
                rolling[sym_u] = node
                return 1

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
                if not d:
                    continue
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
                updated += int(fut.result() or 0)

    save_rolling(rolling)

    try:
        with _NO_DATA_LOCK:
            bad = set(_NO_DATA)

        if bad:
            max_allowed = int(max(1, round(PRUNE_MAX_RATIO * float(total))))
            if len(bad) > max_allowed:
                log(
                    f"⚠️ Universe auto-prune SKIPPED: {len(bad)} symbols flagged, "
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
                    f"(no StockAnalysis entry AND no Alpaca history)."
                )
    except Exception as e:
        log(f"⚠️ Universe prune step failed: {e}")

    dur = time.time() - start_ts
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
