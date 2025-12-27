# backend/services/macro_fetcher.py — v1.2
"""
macro_fetcher.py — v1.2 (rate-limit aware + throttle + retries + atomic writes)

Fixes / upgrades:
  ✅ Batch-fetch tickers with one yfinance call (less rate-limit pain)
  ✅ Throttle: skip fetch if last macro_state.json is "fresh" (default 6h)
  ✅ Retry w/ exponential backoff on rate limits (default 3 tries)
  ✅ If fetch returns junk (zeros/empty), DO NOT overwrite existing snapshots
  ✅ Atomic writes (tmp -> replace)
  ✅ No duplicate SPY fetch for breadth
  ✅ Adds alias keys used downstream: vix, spy_pct, breadth (regime_detector-friendly)
  ✅ Returns last-known-good snapshot when throttled or rate-limited (best-effort)
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import numpy as np
import yfinance as yf

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import log, safe_float


# ------------------------------------------------------------
# Config knobs (env)
# ------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        v = str(os.getenv(name, "")).strip()
        if not v:
            return float(default)
        return float(v)
    except Exception:
        return float(default)

def _env_int(name: str, default: int) -> int:
    try:
        v = str(os.getenv(name, "")).strip()
        if not v:
            return int(default)
        return int(float(v))
    except Exception:
        return int(default)

def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v == "":
        return default
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


# Default: don’t refetch macro more than once every 6 hours
MACRO_MIN_REFRESH_HOURS = _env_float("AION_MACRO_MIN_REFRESH_HOURS", 0.0)
MACRO_MAX_RETRIES = _env_int("AION_MACRO_MAX_RETRIES", 3)
MACRO_RETRY_BASE_SEC = _env_float("AION_MACRO_RETRY_BASE_SEC", 15.0)
MACRO_FORCE = _env_bool("AION_MACRO_FORCE", False)


# ------------------------------------------------------------
# Atomic write helper
# ------------------------------------------------------------

def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    try:
        if not path.exists():
            return False
        age_s = max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
        return age_s < float(max_age_hours) * 3600.0
    except Exception:
        return False


# ------------------------------------------------------------
# Batch yfinance helper
# ------------------------------------------------------------

def _extract_close_series(df: Any, symbol: str) -> Any:
    """
    df can be:
      - regular columns: ["Open","High","Low","Close"...] (single ticker)
      - MultiIndex columns: top level = ticker, second level = OHLCV (multi ticker)
    Return a "Close" series-like object or None.
    """
    try:
        if df is None or getattr(df, "empty", True):
            return None

        cols = getattr(df, "columns", None)
        if cols is None:
            return None

        # Multi-ticker: df[symbol]["Close"]
        if hasattr(cols, "levels") and len(cols.levels) >= 2:
            # yfinance sometimes returns tickers uppercased; normalize best-effort
            top = list(df.columns.get_level_values(0))
            if symbol in top:
                sub = df[symbol]
            else:
                # try uppercase match
                sym_u = str(symbol).upper()
                if sym_u in top:
                    sub = df[sym_u]
                else:
                    return None

            if "Close" in sub.columns:
                return sub["Close"]
            return None

        # Single ticker: df["Close"]
        if "Close" in df.columns:
            return df["Close"]
        return None
    except Exception:
        return None


def _last2_close_pct(close_series: Any) -> Tuple[float, float]:
    """
    Return (close, pct) where pct is percent change vs previous close.
    """
    try:
        s = close_series.dropna()
        if len(s) < 2:
            return 0.0, 0.0
        last = float(s.iloc[-1])
        prev = float(s.iloc[-2])
        if prev <= 0:
            return float(last if last > 0 else 0.0), 0.0
        pct = ((last - prev) / prev) * 100.0
        return float(last), float(pct)
    except Exception:
        return 0.0, 0.0


def _yf_download_batch(syms: List[str]) -> Any:
    """
    One yfinance call for many tickers.

    Notes:
      - threads=True can increase burstiness; during rate-limit debugging it can make things worse.
      - We default threads=False for calmer behavior.
    """
    return yf.download(
        tickers=" ".join(syms),
        period="5d",
        interval="1d",
        progress=False,
        group_by="ticker",
        threads=False,          # calmer; reduces burstiness
        auto_adjust=False,
    )


def _yf_last2_batch(symbols: List[str]) -> Dict[str, Dict[str, float]]:
    """
    One yfinance call for many tickers. Returns:
      { "SPY": {"close":..., "pct":...}, ... }

    Retries with exponential backoff on failure (rate-limit friendly).
    """
    out: Dict[str, Dict[str, float]] = {s: {"close": 0.0, "pct": 0.0} for s in symbols}
    syms = [s for s in symbols if str(s).strip()]
    if not syms:
        return out

    last_err: Optional[str] = None

    for attempt in range(1, max(1, MACRO_MAX_RETRIES) + 1):
        try:
            df = _yf_download_batch(syms)
            if df is None or getattr(df, "empty", True):
                last_err = "empty_df"
            else:
                for s in syms:
                    close_series = _extract_close_series(df, s)
                    if close_series is None:
                        continue
                    close, pct = _last2_close_pct(close_series)
                    out[s] = {"close": float(close), "pct": float(pct)}
                return out

        except Exception as e:
            last_err = str(e)

        # Backoff (with jitter) before retrying
        if attempt < MACRO_MAX_RETRIES:
            base = float(MACRO_RETRY_BASE_SEC) * (2 ** (attempt - 1))
            jitter = random.uniform(0.0, 0.25 * base)
            sleep_s = min(300.0, base + jitter)  # cap at 5 minutes
            log(f"[macro_fetcher] ⚠️ yfinance fetch attempt {attempt}/{MACRO_MAX_RETRIES} failed: {last_err} — sleeping {sleep_s:.1f}s")
            try:
                import time
                time.sleep(sleep_s)
            except Exception:
                pass

    log(f"[macro_fetcher] ⚠️ yfinance batch fetch failed after retries: {last_err}")
    return out


# ------------------------------------------------------------
# Sanity gate
# ------------------------------------------------------------

def _macro_looks_sane(m: Dict[str, Any]) -> bool:
    """
    Consider macro sane if we have at least some real signal.
    Most important: SPY should not be zero.
    Prefer VIX nonzero too, but don't brick everything if only VIX fails.
    """
    try:
        spy = abs(safe_float(m.get("spy_close", 0.0)))
        if spy <= 0.0:
            return False

        vix = abs(safe_float(m.get("vix_close", 0.0)))
        spy_dec = abs(safe_float(m.get("spy_pct_decimal", 0.0)))
        breadth = abs(safe_float(m.get("breadth_proxy", 0.0)))

        # If VIX is present and in a plausible range, accept
        if vix >= 8.0:
            return True

        # Otherwise accept if we have *any* non-flat movement
        return (spy_dec > 0.0001) or (breadth > 0.0001)
    except Exception:
        return False


# ------------------------------------------------------------
# Risk-off (kept stable)
# ------------------------------------------------------------

def _risk_off_score(vix_close: float, spy_pct_dec: float, dxy_pct_dec: float) -> float:
    vix_component = min(max((vix_close - 15.0) / 25.0, 0.0), 1.0)      # 15..40 mapped
    spy_component = min(max((-spy_pct_dec) / 0.03, 0.0), 1.0)          # -3% maps to 1
    dxy_component = min(max((dxy_pct_dec) / 0.01, 0.0), 1.0)           # +1% maps to 1
    return float(min(1.0, 0.45 * vix_component + 0.45 * spy_component + 0.10 * dxy_component))


# ------------------------------------------------------------
# Main builder
# ------------------------------------------------------------

def build_macro_features() -> Dict[str, Any]:
    log("🌐 Fetching macro signals (VIX, SPY, QQQ, TNX, DXY, GLD, USO)…")

    # canonical destination
    out_path = PATHS.get("macro_state")
    if not isinstance(out_path, Path):
        out_path = None

    # Throttle: if we have a recent macro_state.json, skip fetch unless forced
    if (not MACRO_FORCE) and out_path and _is_fresh(out_path, MACRO_MIN_REFRESH_HOURS):
        cached = _read_json_if_exists(out_path)
        if cached:
            log(f"[macro_fetcher] ℹ️ macro_state.json is fresh (<{MACRO_MIN_REFRESH_HOURS}h). Skipping fetch.")
            return {"status": "skipped", "reason": "fresh_cache", "macro_state": cached}
        # If unreadable, fall through and attempt fetch.

    tickers = ["^VIX", "SPY", "QQQ", "^TNX", "DX-Y.NYB", "GLD", "USO"]
    data = _yf_last2_batch(tickers)

    vix_close, vix_pct = safe_float(data["^VIX"]["close"]), safe_float(data["^VIX"]["pct"])
    spy_close, spy_pct = safe_float(data["SPY"]["close"]), safe_float(data["SPY"]["pct"])
    qqq_close, qqq_pct = safe_float(data["QQQ"]["close"]), safe_float(data["QQQ"]["pct"])
    tnx_close, tnx_pct = safe_float(data["^TNX"]["close"]), safe_float(data["^TNX"]["pct"])
    dxy_close, dxy_pct = safe_float(data["DX-Y.NYB"]["close"]), safe_float(data["DX-Y.NYB"]["pct"])
    gld_close, gld_pct = safe_float(data["GLD"]["close"]), safe_float(data["GLD"]["pct"])
    uso_close, uso_pct = safe_float(data["USO"]["close"]), safe_float(data["USO"]["pct"])

    spy_pct_dec = spy_pct / 100.0
    dxy_pct_dec = dxy_pct / 100.0

    # breadth_proxy: keep proxy style, but do not refetch SPY
    breadth_proxy = float(spy_pct_dec)

    volatility = float(max(0.0, min(0.10, float(vix_close) / 100.0)))
    risk_off = _risk_off_score(float(vix_close), float(spy_pct_dec), float(dxy_pct_dec))

    now_iso_utc = datetime.now(timezone.utc).isoformat()
    now_iso_local = datetime.now(TIMEZONE).isoformat()

    macro_state: Dict[str, Any] = {
        "vix_close": float(vix_close),
        "vix_daily_pct": float(vix_pct),

        "spy_close": float(spy_close),
        "spy_daily_pct": float(spy_pct),          # percent
        "spy_pct_decimal": float(spy_pct_dec),    # decimal

        "qqq_close": float(qqq_close),
        "qqq_daily_pct": float(qqq_pct),

        "tnx_close": float(tnx_close),
        "tnx_daily_pct": float(tnx_pct),

        "dxy_close": float(dxy_close),
        "dxy_daily_pct": float(dxy_pct),
        "dxy_pct_decimal": float(dxy_pct_dec),

        "gld_close": float(gld_close),
        "gld_daily_pct": float(gld_pct),

        "uso_close": float(uso_close),
        "uso_daily_pct": float(uso_pct),

        "breadth_proxy": float(breadth_proxy),

        # stable downstream keys
        "volatility": float(volatility),
        "risk_off": float(risk_off),

        # timestamps
        "generated_at": now_iso_local,
        "updated_at": now_iso_utc,
    }

    # Add aliases used by regime_detector / context_state debug expectations
    # (These do not replace your canonical keys; they just prevent key-mismatch bugs.)
    macro_state["vix"] = float(macro_state.get("vix_close", 0.0))
    macro_state["spy_pct"] = float(macro_state.get("spy_pct_decimal", 0.0))
    macro_state["breadth"] = float(macro_state.get("breadth_proxy", 0.0))

    # ----------------------------
    # Critical: Do NOT overwrite on failure
    # ----------------------------
    if not _macro_looks_sane(macro_state):
        log("[macro_fetcher] ⚠️ Macro fetch looks invalid (zeros/empty). Keeping last snapshot (no overwrite).")

        # Best-effort: return last known good snapshot if we have it
        if out_path:
            cached = _read_json_if_exists(out_path)
            if cached:
                return {"status": "skipped", "reason": "macro_not_sane_keep_last", "macro_state": cached}

        return {"status": "skipped", "reason": "macro_not_sane_no_cache", "macro_state": macro_state}

    # Save canonical macro_state.json
    if out_path:
        try:
            _atomic_write_json(out_path, macro_state)
            log(f"[macro_fetcher] 📈 macro_state.json updated → {out_path}")
        except Exception as e:
            log(f"[macro_fetcher] ⚠️ Failed to write macro_state.json: {e}")

    # Also write market_state-compatible snapshot for regime_detector fallbacks
    try:
        ml_root = PATHS.get("ml_data", Path("ml_data"))
        market_state_path = Path(ml_root) / "market_state.json"
        payload = dict(macro_state)
        payload.setdefault("regime_hint", "neutral")
        _atomic_write_json(market_state_path, payload)
        log(f"[macro_fetcher] 🧭 market_state.json (macro snapshot) updated → {market_state_path}")
    except Exception as e:
        log(f"[macro_fetcher] ⚠️ Failed to write market_state.json macro snapshot: {e}")

    return {"status": "ok", **macro_state}


if __name__ == "__main__":
    res = build_macro_features()
    print(json.dumps(res, indent=2))
