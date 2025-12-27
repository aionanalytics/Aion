# backend/services/macro_fetcher.py — v1.3 (FRED-only + throttle + retries + atomic writes)
"""
macro_fetcher.py — v1.3 (FRED-only; no yfinance; rate-limit resilient)

Upgrades:
  ✅ Replace yfinance with FRED series fetches (no ETF/daily-bars provider needed)
  ✅ Throttle: skip fetch if last macro_state.json is "fresh" (default 6h)
  ✅ Retry w/ exponential backoff on HTTP errors / rate limits (default 3 tries)
  ✅ If fetch returns junk (zeros/empty), DO NOT overwrite existing snapshots
  ✅ Atomic writes (tmp -> replace)
  ✅ Writes canonical macro_state.json AND market_state.json snapshot (fallback)
  ✅ Adds alias keys used downstream: vix, spy_pct, breadth (regime_detector-friendly)

Notes:
  • "spy_close" is sourced from FRED SP500 (S&P 500 index level), not SPY ETF.
  • "tnx_close" uses a Treasury yield series (percent), not ^TNX directly.
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
MACRO_MIN_REFRESH_HOURS = _env_float("AION_MACRO_MIN_REFRESH_HOURS", 6.0)
MACRO_MAX_RETRIES = _env_int("AION_MACRO_MAX_RETRIES", 3)
MACRO_RETRY_BASE_SEC = _env_float("AION_MACRO_RETRY_BASE_SEC", 10.0)
MACRO_FORCE = _env_bool("AION_MACRO_FORCE", False)

FRED_API_KEY = (os.getenv("FRED_API", "") or "").strip()


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
    """
    This is the line of logic that controls "skip if it's < N hours old":
      return age_s < max_age_hours * 3600
    """
    try:
        if not path.exists():
            return False
        age_s = max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
        return age_s < float(max_age_hours) * 3600.0
    except Exception:
        return False


# ------------------------------------------------------------
# FRED fetch helpers
# ------------------------------------------------------------

def _http_get_json(url: str, timeout_s: float = 20.0) -> Optional[Dict[str, Any]]:
    """
    Avoids adding dependencies. Uses urllib from stdlib.
    """
    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AION-Analytics/1.0 (macro_fetcher)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else None

    except Exception:
        return None


def _fred_series_observations(series_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Returns most recent observations (date/value) list.
    We request sort_order=desc so newest comes first.
    """
    if not FRED_API_KEY:
        return []

    base = "https://api.stlouisfed.org/fred/series/observations"
    params = (
        f"?series_id={series_id}"
        f"&api_key={FRED_API_KEY}"
        f"&file_type=json"
        f"&sort_order=desc"
        f"&limit={max(1, int(limit))}"
    )
    url = base + params

    last_err: Optional[str] = None

    for attempt in range(1, max(1, MACRO_MAX_RETRIES) + 1):
        try:
            js = _http_get_json(url, timeout_s=20.0)
            if not js or "observations" not in js:
                last_err = "empty_or_bad_json"
                raise RuntimeError(last_err)

            obs = js.get("observations") or []
            if not isinstance(obs, list):
                last_err = "bad_observations_schema"
                raise RuntimeError(last_err)

            return [o for o in obs if isinstance(o, dict)]

        except Exception as e:
            last_err = str(e)

        if attempt < MACRO_MAX_RETRIES:
            base_s = float(MACRO_RETRY_BASE_SEC) * (2 ** (attempt - 1))
            jitter = random.uniform(0.0, 0.25 * base_s)
            sleep_s = min(180.0, base_s + jitter)
            log(f"[macro_fetcher] ⚠️ FRED fetch {series_id} attempt {attempt}/{MACRO_MAX_RETRIES} failed: {last_err} — sleeping {sleep_s:.1f}s")
            try:
                time.sleep(sleep_s)
            except Exception:
                pass

    log(f"[macro_fetcher] ⚠️ FRED fetch {series_id} failed after retries: {last_err}")
    return []


def _parse_fred_value(v: Any) -> Optional[float]:
    """
    FRED uses "." for missing.
    """
    try:
        s = str(v).strip()
        if not s or s == ".":
            return None
        x = float(s)
        if x != x:
            return None
        return x
    except Exception:
        return None


def _last2_from_fred(series_id: str) -> Tuple[float, float]:
    """
    Returns (last_value, pct_change_vs_prev) where pct is percent.
    If missing, returns (0.0, 0.0).
    """
    obs = _fred_series_observations(series_id, limit=10)
    vals: List[float] = []
    for o in obs:
        x = _parse_fred_value(o.get("value"))
        if x is None:
            continue
        vals.append(float(x))
        if len(vals) >= 2:
            break

    if len(vals) < 1:
        return 0.0, 0.0
    if len(vals) < 2:
        return float(vals[0]), 0.0

    last = float(vals[0])
    prev = float(vals[1])
    if prev == 0:
        return last, 0.0
    pct = ((last - prev) / prev) * 100.0
    return last, float(pct)


# ------------------------------------------------------------
# Sanity gate
# ------------------------------------------------------------

def _macro_looks_sane(m: Dict[str, Any]) -> bool:
    """
    Consider macro sane if we have at least some real signal.
    Most important: SP500 should not be zero.
    Prefer VIX too, but don't brick everything if only VIX fails.
    """
    try:
        spy = abs(safe_float(m.get("spy_close", 0.0)))
        if spy <= 0.0:
            return False

        vix = abs(safe_float(m.get("vix_close", 0.0)))
        spy_dec = abs(safe_float(m.get("spy_pct_decimal", 0.0)))
        breadth = abs(safe_float(m.get("breadth_proxy", 0.0)))

        if vix >= 8.0:
            return True

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
    log("🌐 Fetching macro signals via FRED (VIX, SP500, NASDAQ, 10Y, DXY, Gold, Oil)…")

    # canonical destination
    out_path = PATHS.get("macro_state")
    if not isinstance(out_path, Path):
        out_path = None

    # If no key, best-effort return cached
    if not FRED_API_KEY:
        log("[macro_fetcher] ❌ FRED_API key missing. Set FRED_API in .env.")
        if out_path:
            cached = _read_json_if_exists(out_path)
            if cached:
                return {"status": "skipped", "reason": "missing_fred_key_return_cache", "macro_state": cached}
        return {"status": "error", "error": "missing_fred_api_key"}

    # Throttle: if we have a recent macro_state.json, skip fetch unless forced
    if (not MACRO_FORCE) and out_path and _is_fresh(out_path, MACRO_MIN_REFRESH_HOURS):
        cached = _read_json_if_exists(out_path)
        if cached:
            log(f"[macro_fetcher] ℹ️ macro_state.json is fresh (<{MACRO_MIN_REFRESH_HOURS}h). Skipping fetch.")
            return {"status": "skipped", "reason": "fresh_cache", "macro_state": cached}

    # --- FRED series map ---
    # Core:
    #   VIXCLS: CBOE Volatility Index
    #   SP500: S&P 500 Index
    # Optional enrichers (all on FRED):
    #   NASDAQCOM: NASDAQ Composite Index
    #   DGS10: 10-Year Treasury Constant Maturity Rate (percent)
    #   DTWEXBGS: Trade Weighted U.S. Dollar Index: Broad (index)
    #   GOLDAMGBD228NLBM: Gold Fixing Price 10:30 A.M. (London time)
    #   DCOILWTICO: Crude Oil Prices: WTI
    SERIES = {
        "vix": "VIXCLS",
        "sp500": "SP500",
        "nasdaq": "NASDAQCOM",
        "tnx": "DGS10",
        "dxy": "DTWEXBGS",
        "gld": "GOLDAMGBD228NLBM",
        "uso": "DCOILWTICO",
    }

    vix_close, vix_pct = _last2_from_fred(SERIES["vix"])
    spx_close, spx_pct = _last2_from_fred(SERIES["sp500"])
    nas_close, nas_pct = _last2_from_fred(SERIES["nasdaq"])
    tnx_close, tnx_pct = _last2_from_fred(SERIES["tnx"])
    dxy_close, dxy_pct = _last2_from_fred(SERIES["dxy"])
    gld_close, gld_pct = _last2_from_fred(SERIES["gld"])
    uso_close, uso_pct = _last2_from_fred(SERIES["uso"])

    # "spy_*" fields now represent SP500 (index) equivalents for regime logic
    spy_close = float(safe_float(spx_close))
    spy_pct = float(safe_float(spx_pct))
    spy_pct_dec = float(spy_pct / 100.0)

    dxy_pct_dec = float(safe_float(dxy_pct) / 100.0)
    breadth_proxy = float(spy_pct_dec)

    volatility = float(max(0.0, min(0.10, float(safe_float(vix_close)) / 100.0)))
    risk_off = _risk_off_score(float(safe_float(vix_close)), float(spy_pct_dec), float(dxy_pct_dec))

    now_iso_utc = datetime.now(timezone.utc).isoformat()
    now_iso_local = datetime.now(TIMEZONE).isoformat()

    macro_state: Dict[str, Any] = {
        # VIX
        "vix_close": float(safe_float(vix_close)),
        "vix_daily_pct": float(safe_float(vix_pct)),

        # SP500-as-SPY surrogate (for regime)
        "spy_close": float(safe_float(spy_close)),
        "spy_daily_pct": float(safe_float(spy_pct)),          # percent
        "spy_pct_decimal": float(safe_float(spy_pct_dec)),    # decimal

        # NASDAQ surrogate for QQQ-ish tech risk (optional)
        "qqq_close": float(safe_float(nas_close)),
        "qqq_daily_pct": float(safe_float(nas_pct)),

        # TNX surrogate: 10Y yield (percent). Not the same as ^TNX, but good enough for macro tilt.
        "tnx_close": float(safe_float(tnx_close)),
        "tnx_daily_pct": float(safe_float(tnx_pct)),

        # DXY surrogate: trade-weighted USD index
        "dxy_close": float(safe_float(dxy_close)),
        "dxy_daily_pct": float(safe_float(dxy_pct)),
        "dxy_pct_decimal": float(safe_float(dxy_pct_dec)),

        # Gold & Oil
        "gld_close": float(safe_float(gld_close)),
        "gld_daily_pct": float(safe_float(gld_pct)),
        "uso_close": float(safe_float(uso_close)),
        "uso_daily_pct": float(safe_float(uso_pct)),

        # Breadth proxy (kept consistent with your previous approach)
        "breadth_proxy": float(safe_float(breadth_proxy)),

        # Downstream keys
        "volatility": float(safe_float(volatility)),
        "risk_off": float(safe_float(risk_off)),

        # timestamps
        "generated_at": now_iso_local,
        "updated_at": now_iso_utc,

        # provenance
        "source": "fred",
        "fred_series": dict(SERIES),
    }

    # Alias keys used by regime_detector / context_state debug expectations
    macro_state["vix"] = float(macro_state.get("vix_close", 0.0))
    macro_state["spy_pct"] = float(macro_state.get("spy_pct_decimal", 0.0))
    macro_state["breadth"] = float(macro_state.get("breadth_proxy", 0.0))

    # ----------------------------
    # Critical: Do NOT overwrite on failure
    # ----------------------------
    if not _macro_looks_sane(macro_state):
        log("[macro_fetcher] ⚠️ Macro fetch looks invalid (zeros/empty). Keeping last snapshot (no overwrite).")
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
