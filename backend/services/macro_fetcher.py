# backend/services/macro_fetcher.py — v1.4
"""
macro_fetcher.py — v1.4 (FRED core + GOLD via Alpaca GLD; resilient; clear naming)

Upgrades / fixes:
  ✅ Canonical fields renamed to sp500_close/sp500_* (clarity)
     - Keeps legacy aliases spy_close/spy_* for downstream compatibility.
  ✅ Better debugging when FRED returns junk:
     - Logs HTTP status + small snippet for empty_or_bad_json cases.
  ✅ Observation parsing is robust:
     - Finds the last TWO valid numeric values within a lookback window (default 60 days),
       skipping "." and blanks (so series with occasional missing values stop failing).
  ✅ Gold is fetched via Alpaca GLD daily bars (only for gold), not FRED, not yfinance.
     - Uses timeframe=1Day over a wider date range so it works outside market hours.
  ✅ Throttle + retries + atomic writes + do-not-overwrite-on-failure remain.

Notes:
  • FRED provides index/yield style series; they are not ETFs. That’s fine for regime math.
  • Alpaca GLD is an ETF proxy for gold price; if your Alpaca data feed can’t serve GLD,
    you’ll see empty bars and we’ll log it clearly.
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import log, safe_float


# ------------------------------------------------------------
# Env helpers
# ------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        v = str(os.getenv(name, "")).strip()
        return float(v) if v else float(default)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        v = str(os.getenv(name, "")).strip()
        return int(float(v)) if v else int(default)
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


# ------------------------------------------------------------
# Config knobs (env)
# ------------------------------------------------------------

MACRO_MIN_REFRESH_HOURS = _env_float("AION_MACRO_MIN_REFRESH_HOURS", 6.0)
MACRO_MAX_RETRIES = _env_int("AION_MACRO_MAX_RETRIES", 3)
MACRO_RETRY_BASE_SEC = _env_float("AION_MACRO_RETRY_BASE_SEC", 10.0)
MACRO_FORCE = _env_bool("AION_MACRO_FORCE", False)

# How far back to look for last-two valid numeric observations
FRED_LOOKBACK_DAYS = _env_int("AION_FRED_LOOKBACK_DAYS", 60)

FRED_API_KEY = (os.getenv("FRED_API", "") or "").strip()

# Alpaca (for GLD only)
ALPACA_KEY = (os.getenv("ALPACA_API_KEY_ID", "") or "").strip()
ALPACA_SECRET = (os.getenv("ALPACA_API_SECRET_KEY", "") or "").strip()
# Data base URL (market data). Default is Alpaca's standard data host.
ALPACA_DATA_BASE_URL = (os.getenv("ALPACA_DATA_BASE_URL", "") or "").strip() or "https://data.alpaca.markets"
# Optional feed selector: "" (omit), "iex", or "sip" depending on your subscription.
ALPACA_DATA_FEED = (os.getenv("AION_ALPACA_DATA_FEED", "") or "").strip().lower()


# ------------------------------------------------------------
# Atomic write + cache helpers
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
    This is the exact logic for "skip if it's < N hours old":
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
# HTTP helper (stdlib, logs status/snippet)
# ------------------------------------------------------------

def _http_get_text(url: str, timeout_s: float = 20.0) -> Tuple[Optional[int], str]:
    """
    Returns (status_code, body_text).
    On some failures, status_code may be None.
    """
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AION-Analytics/1.0 (macro_fetcher)",
                "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            status = getattr(resp, "status", None)
            data = resp.read().decode("utf-8", errors="ignore")
            return (int(status) if status is not None else 200), data
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return int(getattr(e, "code", 0) or 0), body
    except Exception:
        return None, ""


def _http_get_json(url: str, timeout_s: float = 20.0) -> Tuple[Optional[int], Optional[Dict[str, Any]], str]:
    """
    Returns (status_code, parsed_json_dict_or_none, snippet_for_logs)
    """
    status, body = _http_get_text(url, timeout_s=timeout_s)
    snippet = (body or "").strip().replace("\n", " ")[:240]
    if not body:
        return status, None, snippet
    try:
        js = json.loads(body)
        return status, js if isinstance(js, dict) else None, snippet
    except Exception:
        return status, None, snippet


# ------------------------------------------------------------
# FRED helpers
# ------------------------------------------------------------

def _fred_observations(series_id: str, lookback_days: int, limit: int = 200) -> List[Dict[str, Any]]:
    """
    Fetch observations (newest first) within a recent window.
    Retries with exponential backoff and logs status/snippet on bad JSON.
    """
    if not FRED_API_KEY:
        return []

    base = "https://api.stlouisfed.org/fred/series/observations"
    start_date = (datetime.now(timezone.utc) - timedelta(days=max(7, int(lookback_days)))).date().isoformat()

    params = (
        f"?series_id={series_id}"
        f"&api_key={FRED_API_KEY}"
        f"&file_type=json"
        f"&sort_order=desc"
        f"&observation_start={start_date}"
        f"&limit={max(1, int(limit))}"
    )
    url = base + params

    last_err = None
    last_status: Optional[int] = None
    last_snip: str = ""

    for attempt in range(1, max(1, MACRO_MAX_RETRIES) + 1):
        try:
            status, js, snip = _http_get_json(url, timeout_s=20.0)
            last_status, last_snip = status, snip

            if not js or "observations" not in js:
                # If FRED returns an error payload, surface it.
                meta_err = ""
                try:
                    if isinstance(js, dict) and ("error_message" in js or "error_code" in js):
                        meta_err = f" error_code={js.get('error_code')} error_message={js.get('error_message')}"
                except Exception:
                    pass

                raise RuntimeError(f"empty_or_bad_json (status={status}{meta_err} snippet='{snip}')")

            obs = js.get("observations") or []
            if not isinstance(obs, list):
                raise RuntimeError(f"bad_observations_schema (status={status} snippet='{snip}')")

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

    # Final failure log with status/snippet context
    if last_err:
        log(f"[macro_fetcher] ⚠️ FRED fetch {series_id} failed after retries: {last_err}")
    else:
        log(f"[macro_fetcher] ⚠️ FRED fetch {series_id} failed after retries: unknown_error (status={last_status} snippet='{last_snip}')")

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


def _last2_from_fred(series_id: str, lookback_days: int) -> Tuple[float, float]:
    """
    Returns (last_value, pct_change_vs_prev) where pct is percent.
    Robust: scans newest->older within lookback window and picks last two valid numbers.
    """
    obs = _fred_observations(series_id, lookback_days=lookback_days, limit=250)

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
# Alpaca GLD helper (daily bars, wide window)
# ------------------------------------------------------------

def _alpaca_last2_close(symbol: str = "GLD", lookback_days: int = 60) -> Tuple[float, float]:
    """
    Fetch last two daily closes for GLD from Alpaca market data.
    Returns (close, pct_change_percent). On failure returns (0.0, 0.0).

    If your account/feed cannot serve GLD, Alpaca may return {"bars":{}}.
    We log status + snippet so you can tell if it's feed-permission vs param error.
    """
    if not (ALPACA_KEY and ALPACA_SECRET):
        log("[macro_fetcher] ⚠️ Alpaca keys missing; cannot fetch GLD.")
        return 0.0, 0.0

    start = (datetime.now(timezone.utc) - timedelta(days=max(10, int(lookback_days)))).isoformat().replace("+00:00", "Z")

    # Alpaca bars endpoint (v2)
    url = (
        f"{ALPACA_DATA_BASE_URL}/v2/stocks/bars"
        f"?symbols={symbol}"
        f"&timeframe=1Day"
        f"&start={start}"
        f"&limit=1000"
        f"&adjustment=raw"
    )
    if ALPACA_DATA_FEED in {"iex", "sip"}:
        url += f"&feed={ALPACA_DATA_FEED}"

    last_err: Optional[str] = None

    for attempt in range(1, max(1, MACRO_MAX_RETRIES) + 1):
        try:
            import urllib.request
            import urllib.error

            req = urllib.request.Request(
                url,
                headers={
                    "APCA-API-KEY-ID": ALPACA_KEY,
                    "APCA-API-SECRET-KEY": ALPACA_SECRET,
                    "Accept": "application/json",
                    "User-Agent": "AION-Analytics/1.0 (macro_fetcher alpaca)",
                },
            )

            with urllib.request.urlopen(req, timeout=20.0) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read().decode("utf-8", errors="ignore")

            snippet = (body or "").strip().replace("\n", " ")[:240]

            js = json.loads(body) if body else {}
            bars = (js or {}).get("bars") or {}

            if not isinstance(bars, dict) or not bars:
                raise RuntimeError(f"no_valid_closes (status={status} snippet='{snippet}')")

            series = bars.get(symbol) or bars.get(symbol.upper()) or bars.get(symbol.lower()) or []
            if not isinstance(series, list) or not series:
                raise RuntimeError(f"no_valid_closes (status={status} snippet='{snippet}')")

            # Extract closes, sort by time, take last two valid
            closes: List[Tuple[str, float]] = []
            for b in series:
                if not isinstance(b, dict):
                    continue
                t = str(b.get("t") or "")
                c = b.get("c")
                try:
                    cf = float(c)
                    if cf > 0:
                        closes.append((t, cf))
                except Exception:
                    continue

            if len(closes) < 2:
                raise RuntimeError(f"no_valid_closes (status={status} snippet='{snippet}')")

            closes.sort(key=lambda x: x[0])  # oldest -> newest
            prev = float(closes[-2][1])
            last = float(closes[-1][1])
            pct = ((last - prev) / prev) * 100.0 if prev > 0 else 0.0
            return last, float(pct)

        except Exception as e:
            last_err = str(e)

        if attempt < MACRO_MAX_RETRIES:
            base_s = float(MACRO_RETRY_BASE_SEC) * (2 ** (attempt - 1))
            jitter = random.uniform(0.0, 0.25 * base_s)
            sleep_s = min(180.0, base_s + jitter)
            log(f"[macro_fetcher] ⚠️ Alpaca({symbol}) attempt {attempt}/{MACRO_MAX_RETRIES} failed: {last_err} — sleeping {sleep_s:.1f}s")
            try:
                time.sleep(sleep_s)
            except Exception:
                pass

    log(f"[macro_fetcher] ⚠️ Alpaca({symbol}) failed after retries: {last_err}")
    return 0.0, 0.0


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
        sp500 = abs(safe_float(m.get("sp500_close", 0.0)))
        if sp500 <= 0.0:
            return False

        vix = abs(safe_float(m.get("vix_close", 0.0)))
        spx_dec = abs(safe_float(m.get("sp500_pct_decimal", 0.0)))
        breadth = abs(safe_float(m.get("breadth_proxy", 0.0)))

        if vix >= 8.0:
            return True

        return (spx_dec > 0.0001) or (breadth > 0.0001)
    except Exception:
        return False


# ------------------------------------------------------------
# Risk-off (kept stable)
# ------------------------------------------------------------

def _risk_off_score(vix_close: float, sp500_pct_dec: float, dxy_pct_dec: float) -> float:
    vix_component = min(max((vix_close - 15.0) / 25.0, 0.0), 1.0)      # 15..40 mapped
    spx_component = min(max((-sp500_pct_dec) / 0.03, 0.0), 1.0)         # -3% maps to 1
    dxy_component = min(max((dxy_pct_dec) / 0.01, 0.0), 1.0)            # +1% maps to 1
    return float(min(1.0, 0.45 * vix_component + 0.45 * spx_component + 0.10 * dxy_component))


# ------------------------------------------------------------
# Main builder
# ------------------------------------------------------------

def build_macro_features() -> Dict[str, Any]:
    log("🌐 Fetching macro signals via FRED (+ gold via Alpaca GLD)…")

    out_path = PATHS.get("macro_state")
    if not isinstance(out_path, Path):
        out_path = None

    # Missing FRED key => return cache if possible
    if not FRED_API_KEY:
        log("[macro_fetcher] ❌ FRED_API key missing. Set FRED_API in .env.")
        if out_path:
            cached = _read_json_if_exists(out_path)
            if cached:
                return {"status": "skipped", "reason": "missing_fred_key_return_cache", "macro_state": cached}
        return {"status": "error", "error": "missing_fred_api_key"}

    # Throttle: skip fetch if fresh unless forced
    if (not MACRO_FORCE) and out_path and _is_fresh(out_path, MACRO_MIN_REFRESH_HOURS):
        cached = _read_json_if_exists(out_path)
        if cached:
            log(f"[macro_fetcher] ℹ️ macro_state.json is fresh (<{MACRO_MIN_REFRESH_HOURS}h). Skipping fetch.")
            return {"status": "skipped", "reason": "fresh_cache", "macro_state": cached}

    # --- FRED series map ---
    SERIES = {
        "vix": "VIXCLS",         # CBOE Volatility Index
        "sp500": "SP500",        # S&P 500 Index level
        "nasdaq": "NASDAQCOM",   # NASDAQ Composite Index level
        "tnx": "DGS10",          # 10Y Treasury constant maturity rate (%)
        "dxy": "DTWEXBGS",       # Trade Weighted U.S. Dollar Index: Broad
        "uso": "DCOILWTICO",     # WTI crude oil price
    }

    vix_close, vix_pct = _last2_from_fred(SERIES["vix"], lookback_days=FRED_LOOKBACK_DAYS)
    spx_close, spx_pct = _last2_from_fred(SERIES["sp500"], lookback_days=FRED_LOOKBACK_DAYS)
    nas_close, nas_pct = _last2_from_fred(SERIES["nasdaq"], lookback_days=FRED_LOOKBACK_DAYS)
    tnx_close, tnx_pct = _last2_from_fred(SERIES["tnx"], lookback_days=FRED_LOOKBACK_DAYS)
    dxy_close, dxy_pct = _last2_from_fred(SERIES["dxy"], lookback_days=FRED_LOOKBACK_DAYS)
    uso_close, uso_pct = _last2_from_fred(SERIES["uso"], lookback_days=FRED_LOOKBACK_DAYS)

    # Gold via Alpaca GLD daily bars (ETF proxy)
    gld_close, gld_pct = _alpaca_last2_close("GLD", lookback_days=FRED_LOOKBACK_DAYS)

    # Percent->decimal transforms
    spx_pct_dec = float(safe_float(spx_pct) / 100.0)
    dxy_pct_dec = float(safe_float(dxy_pct) / 100.0)

    breadth_proxy = float(spx_pct_dec)
    volatility = float(max(0.0, min(0.10, float(safe_float(vix_close)) / 100.0)))
    risk_off = _risk_off_score(float(safe_float(vix_close)), float(spx_pct_dec), float(dxy_pct_dec))

    now_iso_utc = datetime.now(timezone.utc).isoformat()
    now_iso_local = datetime.now(TIMEZONE).isoformat()

    macro_state: Dict[str, Any] = {
        # VIX
        "vix_close": float(safe_float(vix_close)),
        "vix_daily_pct": float(safe_float(vix_pct)),

        # Canonical: SP500 (index level)
        "sp500_close": float(safe_float(spx_close)),
        "sp500_daily_pct": float(safe_float(spx_pct)),          # percent
        "sp500_pct_decimal": float(safe_float(spx_pct_dec)),    # decimal

        # NASDAQ (index level) as a tech-risk proxy
        "nasdaq_close": float(safe_float(nas_close)),
        "nasdaq_daily_pct": float(safe_float(nas_pct)),

        # Keep legacy qqq_* fields too (many parts of your system expect qqq_* names)
        "qqq_close": float(safe_float(nas_close)),
        "qqq_daily_pct": float(safe_float(nas_pct)),

        # 10Y yield (%)
        "tnx_close": float(safe_float(tnx_close)),
        "tnx_daily_pct": float(safe_float(tnx_pct)),

        # Dollar index
        "dxy_close": float(safe_float(dxy_close)),
        "dxy_daily_pct": float(safe_float(dxy_pct)),
        "dxy_pct_decimal": float(safe_float(dxy_pct_dec)),

        # Oil
        "uso_close": float(safe_float(uso_close)),
        "uso_daily_pct": float(safe_float(uso_pct)),

        # Gold via GLD (Alpaca)
        "gld_close": float(safe_float(gld_close)),
        "gld_daily_pct": float(safe_float(gld_pct)),

        # Breadth proxy (consistent with your previous approach)
        "breadth_proxy": float(safe_float(breadth_proxy)),

        # Downstream keys
        "volatility": float(safe_float(volatility)),
        "risk_off": float(safe_float(risk_off)),

        # timestamps
        "generated_at": now_iso_local,
        "updated_at": now_iso_utc,

        # provenance
        "source": "fred+alpaca_gld",
        "fred_series": dict(SERIES),
        "alpaca_gold_symbol": "GLD",
        "alpaca_data_feed": ALPACA_DATA_FEED or "omitted",
    }

    # ------------------------------------------------------------
    # Legacy aliases (to avoid breaking downstream code)
    # ------------------------------------------------------------
    # Your regime code historically reads spy_close/spy_pct_decimal/breadth_proxy.
    # We keep them, but they are explicitly SP500-derived.
    macro_state["spy_close"] = float(macro_state["sp500_close"])
    macro_state["spy_daily_pct"] = float(macro_state["sp500_daily_pct"])
    macro_state["spy_pct_decimal"] = float(macro_state["sp500_pct_decimal"])

    # Keys regime_detector/context_state often look for
    macro_state["vix"] = float(macro_state.get("vix_close", 0.0))
    macro_state["spy_pct"] = float(macro_state.get("spy_pct_decimal", 0.0))
    macro_state["breadth"] = float(macro_state.get("breadth_proxy", 0.0))

    # ------------------------------------------------------------
    # Do NOT overwrite on failure
    # ------------------------------------------------------------
    if not _macro_looks_sane(macro_state):
        log("[macro_fetcher] ⚠️ Macro fetch looks invalid. Keeping last snapshot (no overwrite).")
        if out_path:
            cached = _read_json_if_exists(out_path)
            if cached:
                return {"status": "skipped", "reason": "macro_not_sane_keep_last", "macro_state": cached}
        return {"status": "skipped", "reason": "macro_not_sane_no_cache", "macro_state": macro_state}

    # Write canonical macro_state.json
    if out_path:
        try:
            _atomic_write_json(out_path, macro_state)
            log(f"[macro_fetcher] 📈 macro_state.json updated → {out_path}")
        except Exception as e:
            log(f"[macro_fetcher] ⚠️ Failed to write macro_state.json: {e}")

    # Also write market_state.json snapshot (fallback for regime/context)
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
