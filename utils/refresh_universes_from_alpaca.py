# backend/services/refresh_universes_from_alpaca.py
"""
refresh_universes_from_alpaca.py

What this script does (NOW):
- Builds your symbol universes from StockAnalysis so you're not gated by Alpaca SIP/IEX coverage.
- Writes:
    - master_universe.json   (NASDAQ + NYSE union)
    - swing_universe.json    (same as master; swing backend uses SA + other sources)
    - dt_universe.json       (same as master by default)

FUTURE FETCH (kept in-file on purpose):
- The original Alpaca-based universe builder is still here (assets + snapshots),
  but it's opt-in via --source alpaca. That way you can flip it back on when you
  have SIP (or if you decide you prefer Alpaca's asset list again).

Why StockAnalysis as default:
- It's fast, stable for symbol lists, and doesn't rate-limit like yfinance.
- It also naturally avoids a bunch of weird/non-listed tickers when you constrain
  exchange=nasdaq/nyse.

CLI examples:
  python -m backend.services.refresh_universes_from_alpaca --source stockanalysis
  python -m backend.services.refresh_universes_from_alpaca --source alpaca

Notes:
- File format stays compatible with your loaders: either a list or a dict with "symbols".
- We keep a "source" field for sanity/debugging, but nothing relies on it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests

from backend.core.config import (
    PATHS,
    ALPACA_API_KEY_ID,
    ALPACA_API_SECRET_KEY,
    ALPACA_PAPER_BASE_URL,
)
from backend.core.data_pipeline import log

# -----------------------------
# Paths
# -----------------------------
UNIVERSE_DIR = PATHS["universe"]
MASTER_UNIVERSE_FILE = UNIVERSE_DIR / "master_universe.json"
SWING_UNIVERSE_FILE = UNIVERSE_DIR / "swing_universe.json"
DT_UNIVERSE_FILE = UNIVERSE_DIR / "dt_universe.json"

# -----------------------------
# StockAnalysis config
# -----------------------------
SA_BASE = "https://stockanalysis.com/api/screener"
SA_INDEX_FIELDS = ["symbol"]

# -----------------------------
# Alpaca config (FUTURE FETCH)
# -----------------------------
DEFAULT_TRADING_BASE = (ALPACA_PAPER_BASE_URL or "https://paper-api.alpaca.markets").rstrip("/")
DEFAULT_DATA_BASE = "https://data.alpaca.markets"


# -----------------------------
# Data model
# -----------------------------
@dataclass
class UniverseResult:
    total_assets: int
    base_symbols: List[str]
    swing_symbols: List[str]
    dt_symbols: List[str]
    wrote: List[str]
    source: str = "stockanalysis"


# -------------------------------------------------------------------
# Generic helpers
# -------------------------------------------------------------------
def _now_utc_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_universe(path: Path, symbols: Sequence[str], source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": _now_utc_iso(),
        "source": source,
        "count": int(len(symbols)),
        "symbols": list(symbols),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_universe_files(res: UniverseResult) -> List[str]:
    wrote: List[str] = []
    _write_universe(MASTER_UNIVERSE_FILE, res.base_symbols, res.source)
    wrote.append(str(MASTER_UNIVERSE_FILE))
    _write_universe(SWING_UNIVERSE_FILE, res.swing_symbols, res.source)
    wrote.append(str(SWING_UNIVERSE_FILE))
    _write_universe(DT_UNIVERSE_FILE, res.dt_symbols, res.source)
    wrote.append(str(DT_UNIVERSE_FILE))
    return wrote


# -------------------------------------------------------------------
# StockAnalysis universe builder (DEFAULT)
# -------------------------------------------------------------------
def _sa_post_json(path: str, payload: Optional[dict] = None, timeout: int = 25) -> Optional[dict]:
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
        log(f"⚠️ [universe] StockAnalysis request failed for {url}: {e}")
    return None


def _sa_extract_symbols(js: Optional[dict]) -> List[str]:
    rows = (js or {}).get("data", {}).get("data", []) or []
    out: List[str] = []
    for row in rows:
        # SA sometimes uses short keys in some endpoints, be tolerant.
        if isinstance(row, dict):
            sym = row.get("symbol") or row.get("s")
        elif isinstance(row, list) and row:
            sym = row[0]
        else:
            sym = None

        sym = (str(sym).strip().upper() if sym else "")
        if not sym:
            continue
        # Basic sanitation (avoid obvious non-symbols)
        if " " in sym:
            continue
        out.append(sym)
    return out


def fetch_sa_symbols_for_exchange(exchange: str, *, page_limit: int = 10000) -> List[str]:
    """Fetch all symbols for a single exchange via /s/i with pagination."""
    exchange = (exchange or "").strip().lower()
    if not exchange:
        return []

    # SA filter strings appear to accept lower-case exchanges, but we try a couple
    # fallbacks if the first call returns nothing.
    exchange_variants = [exchange]
    if exchange == "nasdaq":
        exchange_variants += ["NASDAQ", "Nasdaq"]
    if exchange == "nyse":
        exchange_variants += ["NYSE", "Nyse"]

    for exch in exchange_variants:
        all_syms: List[str] = []
        offset = 0
        while True:
            payload = {
                "fields": SA_INDEX_FIELDS,
                "filter": {"exchange": exch},
                "order": ["marketCap", "desc"],
                "offset": int(offset),
                "limit": int(page_limit),
            }
            js = _sa_post_json("s/i", payload)
            page = _sa_extract_symbols(js)

            if page:
                all_syms.extend(page)

            # Break when we clearly hit the end.
            if not page or len(page) < page_limit:
                break

            offset += page_limit

            # Tiny politeness delay; SA is fast but no reason to be a jerk.
            time.sleep(0.05)

        if all_syms:
            # Dedup while preserving order
            seen: Set[str] = set()
            uniq: List[str] = []
            for s in all_syms:
                if s not in seen:
                    seen.add(s)
                    uniq.append(s)
            return uniq

    return []


def build_universes_from_stockanalysis(
    *,
    exchanges: Sequence[str] = ("nasdaq", "nyse"),
) -> UniverseResult:
    log(f"[universe] Refreshing universes from StockAnalysis (exchanges={list(exchanges)})…")

    per_exch: Dict[str, List[str]] = {}
    for exch in exchanges:
        syms = fetch_sa_symbols_for_exchange(exch)
        per_exch[str(exch).lower()] = syms
        log(f"[universe] SA exchange={exch}: {len(syms)} symbols")

    base: List[str] = []
    seen: Set[str] = set()
    for exch in exchanges:
        for s in per_exch.get(str(exch).lower(), []):
            if s not in seen:
                seen.add(s)
                base.append(s)

    # For now, swing and DT both use the same constrained universe.
    # (DT can be split later if you want a tradable-only subset.)
    swing = list(base)
    dt = list(base)

    return UniverseResult(
        total_assets=len(base),
        base_symbols=base,
        swing_symbols=swing,
        dt_symbols=dt,
        wrote=[],
        source="stockanalysis",
    )


# -------------------------------------------------------------------
# FUTURE FETCH: Alpaca-based universe builder (kept for later)
# -------------------------------------------------------------------
def _alpaca_headers() -> Dict[str, str]:
    key = (ALPACA_API_KEY_ID or "").strip()
    secret = (ALPACA_API_SECRET_KEY or "").strip()
    if not key or not secret:
        raise RuntimeError("Missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY in environment.")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _get_json(url: str, *, headers: Dict[str, str], timeout: int = 25) -> Any:
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_tradable_assets(*, trading_base_url: str) -> List[dict]:
    """Fetch Alpaca assets list (FUTURE FETCH)."""
    headers = _alpaca_headers()
    url = f"{trading_base_url.rstrip('/')}/v2/assets?status=active&asset_class=us_equity"
    js = _get_json(url, headers=headers)
    if not isinstance(js, list):
        return []
    return js


def _is_common_stock_asset(a: dict) -> bool:
    # Alpaca returns a lot of asset types; keep it conservative.
    # This is FUTURE logic, not used by default.
    if not isinstance(a, dict):
        return False
    if not a.get("tradable", False):
        return False
    if a.get("exchange") not in ("NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"):
        return False
    sym = (a.get("symbol") or "").upper()
    if not sym or " " in sym:
        return False
    return True


def fetch_latest_snapshots(
    symbols: Sequence[str],
    *,
    data_base_url: str,
    batch: int = 1000,
) -> Dict[str, dict]:
    """Fetch Alpaca latest snapshots in batches (FUTURE FETCH)."""
    headers = _alpaca_headers()
    out: Dict[str, dict] = {}
    symbols = [s.upper() for s in symbols if s]
    for i in range(0, len(symbols), batch):
        chunk = symbols[i : i + batch]
        qs = ",".join(chunk)
        url = f"{data_base_url.rstrip('/')}/v2/stocks/snapshots?symbols={qs}"
        try:
            js = _get_json(url, headers=headers)
            if isinstance(js, dict):
                out.update(js)
        except Exception as e:
            log(f"⚠️ [universe] Alpaca snapshots batch failed ({i}-{i+batch}): {e}")
            continue
        time.sleep(0.15)
    return out


def filter_dt_symbols_from_snapshots(snapshots: Dict[str, dict]) -> List[str]:
    """DT symbols = symbols where Alpaca snapshot contains a latest quote/trade (FUTURE FETCH)."""
    out: List[str] = []
    for sym, snap in (snapshots or {}).items():
        if not isinstance(snap, dict):
            continue
        # Try a few shapes used by Alpaca
        has_trade = bool(snap.get("latestTrade") or snap.get("latest_trade") or snap.get("trade"))
        has_quote = bool(snap.get("latestQuote") or snap.get("latest_quote") or snap.get("quote"))
        if has_trade or has_quote:
            out.append(str(sym).upper())
    out.sort()
    return out


def build_universes_from_alpaca(
    assets: Sequence[dict],
    *,
    trading_base_url: str,
    data_base_url: str,
) -> UniverseResult:
    """Original Alpaca-driven builder (FUTURE FETCH)."""
    # Base universe: tradable + reasonably "normal" exchange list
    base = sorted({(a.get("symbol") or "").upper() for a in assets if _is_common_stock_asset(a)})

    # Swing universe: same as base in this script (you can split later)
    swing = list(base)

    # DT: subset with snapshots
    snaps = fetch_latest_snapshots(base, data_base_url=data_base_url, batch=1000)
    dt = filter_dt_symbols_from_snapshots(snaps)

    return UniverseResult(
        total_assets=len(assets),
        base_symbols=base,
        swing_symbols=swing,
        dt_symbols=dt,
        wrote=[],
        source="alpaca",
    )


# -------------------------------------------------------------------
# Main entry
# -------------------------------------------------------------------
def refresh_universes(
    *,
    source: str = "stockanalysis",
    trading_base_url: str = DEFAULT_TRADING_BASE,
    data_base_url: str = DEFAULT_DATA_BASE,
    write_files: bool = True,
) -> UniverseResult:
    source = (source or "").strip().lower()

    if source in ("sa", "stockanalysis", "stock-analysis"):
        res = build_universes_from_stockanalysis()
    elif source in ("alpaca", "future", "alpacafuture", "alpaca_future"):
        log("[universe] FUTURE FETCH enabled: building universes from Alpaca…")
        log(f"[universe] trading_base_url={trading_base_url}")
        log(f"[universe] data_base_url={data_base_url}")
        assets = fetch_tradable_assets(trading_base_url=trading_base_url)
        res = build_universes_from_alpaca(
            assets,
            trading_base_url=trading_base_url,
            data_base_url=data_base_url,
        )
    else:
        raise ValueError(f"Unknown source: {source}. Use --source stockanalysis or --source alpaca")

    if write_files:
        res.wrote = write_universe_files(res)
        log(f"[universe] Wrote {len(res.wrote)} universe files.")

    return res


# -------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Refresh universes (StockAnalysis default; Alpaca optional)")
    parser.add_argument("--source", type=str, default="stockanalysis", help="stockanalysis (default) or alpaca")
    parser.add_argument("--trading_base", type=str, default=DEFAULT_TRADING_BASE, help="(FUTURE) Alpaca trading base URL")
    parser.add_argument("--data_base", type=str, default=DEFAULT_DATA_BASE, help="(FUTURE) Alpaca data base URL")
    parser.add_argument("--no_write", action="store_true", help="Do not write JSON files")
    args = parser.parse_args()

    result = refresh_universes(
        source=str(args.source).strip(),
        trading_base_url=str(args.trading_base).strip(),
        data_base_url=str(args.data_base).strip(),
        write_files=(not args.no_write),
    )

    print(
        json.dumps(
            {
                "source": result.source,
                "total_assets": result.total_assets,
                "base_symbols": len(result.base_symbols),
                "swing_symbols": len(result.swing_symbols),
                "dt_symbols": len(result.dt_symbols),
                "wrote": result.wrote,
            },
            indent=2,
        )
    )
