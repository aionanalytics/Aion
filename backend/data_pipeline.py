from __future__ import annotations
import os, re, json, gzip, shutil, time, threading, sys, glob
from pathlib import Path  # ✅ fix: import Path
from datetime import datetime, timedelta
from backend.config import get_path
from typing import Dict, Any, List, Optional

if os.name == "nt":
    import msvcrt
else:
    import fcntl


STOCK_CACHE_DIR = os.getenv("SAP_STOCK_CACHE_DIR", "data/stock_cache")
MASTER_DIR = os.path.join(STOCK_CACHE_DIR, "master")
MACRO_CACHE_DIR = os.path.join("data", "macro_cache")
NEWS_CACHE_DIR = os.path.join("data", "news_cache")
METRICS_CACHE_DIR = os.path.join("data", "metrics_cache")

os.makedirs(MASTER_DIR, exist_ok=True)
os.makedirs(MACRO_CACHE_DIR, exist_ok=True)
os.makedirs(NEWS_CACHE_DIR, exist_ok=True)
os.makedirs(METRICS_CACHE_DIR, exist_ok=True)

ROLLING_PATH = os.path.join(MASTER_DIR, "rolling.json.gz")
BRAIN_PATH = os.path.join(MASTER_DIR, "rolling_brain.json.gz")
ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "data" / "stock_cache" / "master" / "rolling.lock"
BACKUP_DIR = os.path.join(MASTER_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(LOCK_PATH.parent, exist_ok=True)
import sys
if sys.platform.startswith("win"):
    sys.stdout.reconfigure(encoding="utf-8")

MAX_HISTORY_DAYS = int(os.getenv("SAP_MAX_HISTORY_DAYS", "180"))
MAX_BACKUPS = int(os.getenv("SAP_MAX_ROLLING_BACKUPS", "8"))

NORMALIZE_KEYS = {
    "peRatio": "pe_ratio", "pbRatio": "pb_ratio", "psRatio": "ps_ratio",
    "pegRatio": "peg_ratio", "debtEquity": "debt_equity", "debtEbitda": "debt_ebitda",
    "revenueGrowth": "revenue_growth", "epsGrowth": "eps_growth",
    "profitMargin": "profit_margin", "operatingMargin": "operating_margin",
    "grossMargin": "gross_margin", "marketCap": "marketCap",
}

# =====================================================================
# LOGGING UTILITIES
# =====================================================================

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str) -> None:
    print(f"[{_now()}] {msg}", flush=True)

# =====================================================================
# LOCKING SYSTEM
# =====================================================================

import random, time
from .config import PATHS  # ✅ unified path import

class RollingLock:
    """Cross-platform file lock to protect rolling.json.gz from concurrent writes.
    Adds a small random backoff delay to prevent race conditions when multiple
    threads or processes attempt to lock simultaneously.
    """
    def __enter__(self):
        # Add small random delay before acquiring lock to avoid contention
        time.sleep(random.uniform(0.05, 0.15))

        self.lock_file = open(LOCK_PATH, "w")  # ✅ replaced LOCK_PATH
        if os.name == "nt":
            # Windows file lock
            msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            # POSIX file lock
            fcntl.flock(self.lock_file, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if os.name == "nt":
            try:
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            try:
                fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            except Exception:
                pass
        self.lock_file.close()

# =====================================================================
# CORE I/O HELPERS
# =====================================================================

def _read_rolling() -> Dict[str, Any]:
    """Read current rolling cache from disk."""
    rolling_path = PATHS["rolling"]  # ✅ replaced ROLLING_PATH
    if not os.path.exists(rolling_path):
        return {}
    try:
        with gzip.open(rolling_path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ Failed to read rolling cache: {e}")
        return {}

def _atomic_write_json_gz(path: Any, obj: Any) -> None:
    """Safely write JSON to gzipped file with backup rotation (Windows/Linux safe)."""
    # ✅ Convert Path → str before appending ".tmp"
    path_str = str(path)
    tmp_path = path_str + ".tmp"

    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)

    # ✅ Handle backup rotation safely
    if os.path.exists(path_str):
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = os.path.join("data", "stock_cache", "master", "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"rolling_{ts}.json.gz")
        shutil.copy2(path_str, backup_path)
        _prune_backups()

    os.replace(tmp_path, path_str)

def _prune_backups() -> None:
    backup_dir = PATHS["rolling_backups"]  # ✅ replaced BACKUP_DIR
    files = sorted(
        [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith(".json.gz")]
    )
    if len(files) > MAX_BACKUPS:
        for f in files[:-MAX_BACKUPS]:
            try: os.remove(f)
            except Exception: pass

def _load_json_first_existing(paths: List[str]) -> Optional[dict]:
    """Try a list of json file paths and return the first that loads."""
    for p in paths:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
    return None

def _latest_dated_json_in_dir(dir_path: str, prefix: str) -> Optional[str]:
    """Find latest YYYY-MM-DD json file matching prefix in a directory."""
    if not os.path.isdir(dir_path):
        return None
    cand = []
    for name in os.listdir(dir_path):
        if name.startswith(prefix) and name.endswith(".json"):
            cand.append(os.path.join(dir_path, name))
    if not cand:
        return None
    cand.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cand[0]

def _safe_float(x, default=None):
    try:
        if x is None: 
            return default
        return float(x)
    except Exception:
        return default

def _read_brain() -> dict:
    brain_path = PATHS["brain"]  # ✅ replaced BRAIN_PATH
    if not os.path.exists(brain_path):
        return {}
    try:
        with gzip.open(brain_path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ Failed to read rolling_brain: {e}")
        return {}

def save_brain(data: dict) -> None:
    brain_path = Path("data/stock_cache/master/rolling_brain.json.gz")  # ✅ direct path, no PATHS dependency
    try:
        with RollingLock():
            tmp_path = str(brain_path) + ".tmp"  # ✅ fix: convert Path → str before appending
            with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, brain_path)
    except Exception as e:
        log(f"⚠️ Failed to save rolling_brain: {e}")

def normalize_keys(node: dict) -> dict:
    if not isinstance(node, dict):
        return node
    for old, new in NORMALIZE_KEYS.items():
        if old in node and new not in node:
            node[new] = node.pop(old)
    return node

# =====================================================================
# SELF-HEALING FIELD FETCHER
# =====================================================================

def _fetch_from_stockanalysis(symbol: str) -> Optional[dict]:
    """Pulls latest fundamentals/metrics for a symbol from StockAnalysis backend."""
    try:
        import requests
        base = "https://stockanalysis.com/api/screener"
        payload = {
            "fields": ["symbol","name","price","change","volume","high","low","marketCap",
                       "peRatio","pbRatio","psRatio","beta","sector","industry"],
            "filter": {"symbol": symbol},
            "limit": 1
        }
        r = requests.post(f"{base}/s/i", json=payload, timeout=10)
        if r.status_code == 200:
            js = r.json().get("data", {}).get("data", [])
            if js:
                d = js[0]
                return {
                    "symbol": symbol.upper(),
                    "name": d.get("name"),
                    "sector": d.get("sector"),
                    "industry": d.get("industry"),
                    "open": d.get("open"),
                    "high": d.get("high"),
                    "low": d.get("low"),
                    "close": d.get("price"),
                    "volume": d.get("volume"),
                    "pe_ratio": d.get("peRatio"),
                    "pb_ratio": d.get("pbRatio"),
                    "ps_ratio": d.get("psRatio"),
                    "beta": d.get("beta"),
                    "marketCap": d.get("marketCap"),
                }
    except Exception as e:
        log(f"⚠️ StockAnalysis fetch failed for {symbol}: {e}")
    return None

def ensure_symbol_fields(symbol: str) -> Optional[dict]:
    """
    Guarantee that a symbol node inside Rolling contains essential fields.
    If missing, fetch directly from StockAnalysis; fallback to yfinance for sector.
    """
    rolling = _read_rolling()
    node = rolling.get(symbol, {})
    required = ["symbol", "history", "sector", "pe_ratio", "marketCap", "close"]

    # --- Sector fallback (yfinance) ---
    if node.get("sector") in (None, "", []):
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).info
            if info and info.get("sector"):
                node["sector"] = info.get("sector")
                log(f"🌐 Fallback sector for {symbol}: {node['sector']}")
        except Exception:
            pass

    # Check for remaining missing fields
    missing = [f for f in required if f not in node or node.get(f) in (None, [], "", 0)]
    if not missing:
        return node

    log(f"🩹 Missing {missing} for {symbol}, fetching from StockAnalysis...")
    fetched = _fetch_from_stockanalysis(symbol)
    if not fetched:
        return node

    # Merge fields safely
    node.update({k: v for k, v in fetched.items() if v is not None})
    rolling[symbol] = node

    # Persist updated rolling node
    with RollingLock():
        _atomic_write_json_gz(PATHS["rolling"], rolling)  # ✅ replaced ROLLING_PATH

    return node

# =====================================================================
# HIGH-LEVEL API
# =====================================================================

def load_all_cached_stocks(force_reload: bool = False) -> Dict[str, Any]:
    """
    Public helper to return the unified Rolling cache.
    If `force_reload=True`, re-reads from disk even if an in-memory cache exists (future-safe).
    """
    # In future: add caching logic here.
    return _read_rolling()


def save_rolling(data: dict) -> None:
    """Safely write the unified rolling cache with automatic locking and atomic I/O."""
    try:
        with RollingLock():
            _atomic_write_json_gz(PATHS["rolling"], data)  # ✅ replaced ROLLING_PATH
    except Exception as e:
        log(f"⚠️ Failed to save rolling cache: {e}")


def get_symbol_data(symbol: str) -> Optional[dict]:
    """
    Return latest complete view of a single symbol.
    If not found or incomplete, auto-repair via ensure_symbol_fields().
    """
    rolling = _read_rolling() or {}
    node = rolling.get(symbol)

    # Self-heal missing or incomplete nodes
    if not node or "history" not in node:
        try:
            node = ensure_symbol_fields(symbol)
            rolling[symbol] = node
            save_rolling(rolling)
            log(f"🩹 Healed missing symbol in rolling: {symbol}")
        except Exception as e:
            log(f"⚠️ Could not repair symbol {symbol}: {e}")
            return None

    return node

# =====================================================================
# FUTURE HOOKS
# =====================================================================

def _utc_today_tag() -> str:
    """Helper for dated filenames."""
    import datetime
    return datetime.datetime.utcnow().strftime("%Y%m%d")


def apply_context_enrichment(rolling: Optional[dict] = None, save: bool = True) -> dict:
    """
    Phase 2 Step 1 — Context Enrichment Hook
    Merges macro, sentiment/news buzz, sector momentum, and basic trend flags into Rolling.
    - Non-destructive: only fills fields when missing OR writes additive fields.
    - Zero external calls: reads from local caches if present.
    - Safe defaults so it never breaks nightly_job.

    Writes/updates per symbol (if available):
      • sentiment_score (float)    • buzz (int)
      • sector_momentum_1m (float) • macro_breadth (float 0..1, same for all rows)
      • trend ("bullish"/"bearish"/"neutral")
      • event_short_impulse / mid / long (floats)
      • next_event_date / next_event_kind (optional)
    """
    try:
        # ---------- Load the working rolling ----------
        if rolling is None:
            rolling = _read_rolling() or {}
        if not rolling:
            log("ℹ️ apply_context_enrichment: rolling empty, nothing to enrich.")
            return {}

        # ---------- Load macro context ----------
        macro_file_candidates = [
            os.path.join(PATHS["macro"], "macro_features.json"),  # ✅
            _latest_dated_json_in_dir(PATHS["macro"], "macro_features_"),  # ✅
        ]
        macro_js = _load_json_first_existing([p for p in macro_file_candidates if p])
        macro_breadth = 0.5  # neutral default
        if isinstance(macro_js, dict):
            try:
                if "breadth_pos" in macro_js and isinstance(macro_js["breadth_pos"], list) and len(macro_js["breadth_pos"]) > 0:
                    last_bp = _safe_float(macro_js["breadth_pos"][-1], None)
                    if last_bp is not None:
                        macro_breadth = max(0.0, min(1.0, 0.5 + 0.25 * (last_bp / max(1.0, 100.0))))
                elif isinstance(macro_js, list) and macro_js:
                    row = macro_js[-1]
                    if isinstance(row, dict) and "breadth_pos" in row:
                        last_bp = _safe_float(row.get("breadth_pos"), None)
                        if last_bp is not None:
                            macro_breadth = max(0.0, min(1.0, 0.5 + 0.25 * (last_bp / max(1.0, 100.0))))
            except Exception:
                pass

        # ---------- Load latest metrics (optional) ----------
        latest_metrics_path = os.path.join(PATHS["metrics"], "latest_metrics.json")  # ✅
        latest_metrics = _load_json_first_existing([latest_metrics_path]) or {}

        # ---------- Load sentiment/news (optional) ----------
        dated_news = _latest_dated_json_in_dir(PATHS["news"], "news_sentiment_")  # ✅
        news_js = _load_json_first_existing([
            p for p in [
                dated_news,
                os.path.join(PATHS["news"], "news_sentiment.json"),  # ✅
                os.path.join(PATHS["news"], "daily_sentiment.json"),  # ✅
            ] if p
        ]) or {}

        # Build easy lookups: sentiment average & buzz per ticker
        senti_map: Dict[str, float] = {}
        buzz_map: Dict[str, int] = {}
        try:
            if isinstance(news_js, dict) and "rows" in news_js and isinstance(news_js["rows"], list):
                rows = news_js["rows"]
            elif isinstance(news_js, list):
                rows = news_js
            elif isinstance(news_js, dict):
                for k, v in news_js.items():
                    if isinstance(v, dict):
                        s = _safe_float(v.get("sentiment"), None)
                        b = int(v.get("buzz", 0)) if v.get("buzz") is not None else 0
                        if s is not None:
                            senti_map[k.upper()] = s
                        buzz_map[k.upper()] = b
                rows = []
            else:
                rows = []

            if rows:
                tmp_sum: Dict[str, float] = {}
                tmp_cnt: Dict[str, int] = {}
                tmp_buzz: Dict[str, int] = {}
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    sym = (r.get("symbol") or r.get("ticker") or "").upper()
                    if not sym:
                        continue
                    s = _safe_float(r.get("sentiment"), None)
                    if s is not None:
                        tmp_sum[sym] = tmp_sum.get(sym, 0.0) + s
                        tmp_cnt[sym] = tmp_cnt.get(sym, 0) + 1
                    b = r.get("buzz") or r.get("mentions") or r.get("count")
                    if b is not None:
                        try:
                            tmp_buzz[sym] = tmp_buzz.get(sym, 0) + int(b)
                        except Exception:
                            pass
                for sym, ssum in tmp_sum.items():
                    senti_map[sym] = ssum / max(1, tmp_cnt.get(sym, 1))
                for sym, bsum in tmp_buzz.items():
                    buzz_map[sym] = bsum
        except Exception:
            pass

        # ---------- Sector momentum (1m) ----------
        sector_ret: Dict[str, list] = {}
        for sym, node in rolling.items():
            if not isinstance(node, dict):
                continue
            sector = (node.get("sector") or "").strip()
            if not sector:
                continue
            mrow = latest_metrics.get(sym) if isinstance(latest_metrics, dict) else None
            ch1m = _safe_float((mrow or {}).get("ch1m"), None)
            if ch1m is None:
                hist = node.get("history") or []
                if len(hist) >= 22 and hist[-1].get("close") and hist[-22].get("close"):
                    try:
                        c0 = float(hist[-22]["close"])
                        c1 = float(hist[-1]["close"])
                        ch1m = ((c1 - c0) / c0) * 100.0
                    except Exception:
                        ch1m = None
            if ch1m is not None:
                sector_ret.setdefault(sector, []).append(ch1m)

        sector_mom: Dict[str, float] = {}
        for s, vals in sector_ret.items():
            if vals:
                try:
                    sector_mom[s] = float(sum(vals) / len(vals))
                except Exception:
                    pass

        # ---------- Compute/update fields per symbol ----------
        updated = 0
        for sym, node in rolling.items():
            if not isinstance(node, dict):
                continue

            # sentiment / buzz
            s = senti_map.get(sym)
            b = buzz_map.get(sym)
            if node.get("sentiment_score") is None and s is not None:
                node["sentiment_score"] = float(s)
            if node.get("buzz") is None and b is not None:
                node["buzz"] = int(b)

            # sector momentum
            sec = node.get("sector")
            if sec:
                sm = sector_mom.get(sec)
                if sm is not None:
                    node["sector_momentum_1m"] = float(sm)

            # macro breadth
            node["macro_breadth"] = float(macro_breadth)

            # simple trend flag
            trend = node.get("trend")
            if not trend:
                try:
                    ma50  = _safe_float(node.get("ma50"), None)
                    ma200 = _safe_float(node.get("ma200"), None)
                    price = _safe_float(node.get("close") or node.get("price"), None)
                    if ma50 is not None and ma200 is not None and price is not None:
                        if ma50 > ma200 and price >= ma50:
                            node["trend"] = "bullish"
                        elif ma50 < ma200 and price <= ma50:
                            node["trend"] = "bearish"
                        else:
                            node["trend"] = "neutral"
                    else:
                        hist = node.get("history") or []
                        if len(hist) >= 2 and hist[-1].get("close") and hist[-2].get("close"):
                            c1 = _safe_float(hist[-1]["close"], None)
                            c0 = _safe_float(hist[-2]["close"], None)
                            if c1 is not None and c0 is not None:
                                node["trend"] = "bullish" if c1 > c0 else "bearish" if c1 < c0 else "neutral"
                except Exception:
                    pass

            rolling[sym] = node
            updated += 1

        # ---------- New: Enrich with News Intelligence fields ----------
        try:
            sent_map_files = sorted(
                glob.glob(os.path.join(PATHS["news"], "sentiment_map_*.json")),  # ✅
                key=os.path.getmtime,
                reverse=True,
            )
            if sent_map_files:
                latest_map = sent_map_files[0]
                with open(latest_map, "r", encoding="utf-8") as f:
                    enhanced_sent = json.load(f)
                updated_count = 0
                for sym, node in (rolling or {}).items():
                    k = sym.upper()
                    emap = enhanced_sent.get(k) or {}
                    if not emap:
                        continue
                    node["sentiment_score"] = float(
                        emap.get("sentiment", node.get("sentiment_score") or 0.0)
                    )
                    node["buzz"] = int(emap.get("buzz") or node.get("buzz") or 0)
                    node["event_short_impulse"] = float(emap.get("event_short_impulse") or 0.0)
                    node["event_mid_impulse"] = float(emap.get("event_mid_impulse") or 0.0)
                    node["event_long_impulse"] = float(emap.get("event_long_impulse") or 0.0)
                    if emap.get("next_event_date"):
                        node["next_event_date"] = emap.get("next_event_date")
                        node["next_event_kind"] = emap.get("next_event_kind")
                    rolling[sym] = node
                    updated_count += 1

                os.makedirs(PATHS["ml_data"], exist_ok=True)  # ✅
                snapshot_path = os.path.join(PATHS["ml_data"], f"context_enriched_sentiment_{_utc_today_tag()}.json")  # ✅
                with open(snapshot_path, "w", encoding="utf-8") as f:
                    json.dump(enhanced_sent, f, indent=2, ensure_ascii=False)

                log(f"[context] ✅ Enriched {updated_count} symbols with News Intelligence fields.")
        except Exception as e:
            log(f"⚠️ News Intelligence enrichment skipped: {e}")

        # ---------- Persist if requested ----------
        if save and updated:
            save_rolling(rolling)
            log(f"✅ Context enrichment applied — {updated} symbols updated (macro/sentiment/sector/trend).")

        return rolling

    except Exception as e:
        log(f"⚠️ apply_context_enrichment failed: {e}")
        return rolling or {}

# =====================================================================
# PERIODIC COMPRESSION FROM ROLLING (Weekly + Monthly)
# =====================================================================

def _aggregate_from_rolling(freq: str = "W-FRI") -> Optional[str]:
    """
    Internal helper for compress_to_weekly_from_rolling() and compress_to_monthly_from_rolling().
    freq:
        "W-FRI" → Weekly aggregation (Friday close)
        "M"     → Month-end aggregation
    Returns the output Parquet path.
    """
    import pandas as pd
    from collections import defaultdict
    from backend.config import get_path

    start_time = time.time()
    rolling = _read_rolling()
    if not rolling:
        log(f"⚠️ No rolling cache found — skipping {freq} aggregation.")
        return None

    tag = "weekly" if freq.startswith("W") else "monthly"
    out_path = get_path(f"training_data_{tag}")
    os.makedirs(out_path.parent, exist_ok=True)

    frames = []
    for sym, node in rolling.items():
        hist = node.get("history", [])
        if not hist or len(hist) < 5:
            continue

        df = pd.DataFrame(hist)
        if "date" not in df.columns or df.empty:
            continue

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        if df.empty:
            continue

        # Aggregate OHLCV by chosen period
        df = df.set_index("date").sort_index()
        agg = df.resample(freq).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna()

        if agg.empty:
            continue

        agg["ticker"] = sym
        agg["sector"] = node.get("sector")
        agg["marketCap"] = node.get("marketCap")
        agg["pe_ratio"] = node.get("pe_ratio")
        agg["beta"] = node.get("beta")
        agg["ps_ratio"] = node.get("ps_ratio")
        agg["pb_ratio"] = node.get("pb_ratio")
        frames.append(agg.reset_index())

    if not frames:
        log(f"ℹ️ No valid {tag} data to aggregate.")
        return None

    full_df = pd.concat(frames, ignore_index=True)
    full_df.to_parquet(out_path, index=False)

    dur = time.time() - start_time
    log(f"✅ {tag.capitalize()} compression complete — {len(full_df)} rows → {out_path} ({dur:.1f}s)")
    return out_path


def compress_to_weekly_from_rolling() -> str:
    """Public wrapper for weekly OHLCV aggregation from rolling cache."""
    path = _aggregate_from_rolling("W-FRI")
    return path or "skipped"


def compress_to_monthly_from_rolling() -> str:
    """Public wrapper for monthly OHLCV aggregation from rolling cache."""
    path = _aggregate_from_rolling("M")
    return path or "skipped"

# =====================================================================
# MAIN (manual test)
# =====================================================================

if __name__ == "__main__":
    log("🔍 Testing rolling read/write + self-heal...")
    rolling = _read_rolling()
    if not rolling:
        log("⚠️ No rolling cache found.")
    else:
        sym = next(iter(rolling.keys()))
        log(f"Sample symbol: {sym}")
        ensure_symbol_fields(sym)
    log("✅ data_pipeline.py integrity check complete.")
