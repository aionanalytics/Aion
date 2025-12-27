# backend/services/macro_fetcher.py — v1.4 (FRED + Alpaca(GLD) only for gold)
"""
macro_fetcher.py — v1.4 (FRED-first; gold via Alpaca GLD; resilient + clear fields)

Upgrades / fixes:
  ✅ Clear naming: sp500_close/sp500_daily_pct/sp500_pct_decimal are canonical
  ✅ Back-compat aliases preserved: spy_close/spy_daily_pct/spy_pct_decimal mirror SP500 proxy
  ✅ HTTP debug: log status + body snippet when JSON is empty/bad (no more blindfold)
  ✅ Robust parsing: choose last two *valid numeric* values within a window (default 90d),
     skipping "." and blanks (avoids false "empty")
  ✅ Gold via Alpaca ONLY (GLD daily bars), no FRED gold series dependency
  ✅ Throttle: skip fetch if last macro_state.json is fresh (default 6h)
  ✅ Retry with exponential backoff
  ✅ Atomic writes
  ✅ Writes canonical macro_state.json AND ml_data/market_state.json snapshot
  ✅ Adds downstream aliases: vix, spy_pct, breadth (regime_detector-friendly)

Notes:
  • We use FRED "SP500" index level, not the SPY ETF.
    - Canonical fields are sp500_*
    - Legacy spy_* fields are aliases to keep old code working.
  • Gold is proxied via GLD ETF daily bars from Alpaca Market Data.
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


MACRO_MIN_REFRESH_HOURS = _env_float("AION_MACRO_MIN_REFRESH_HOURS", 6.0)
MACRO_MAX_RETRIES = _env_int("AION_MACRO_MAX_RETRIES", 3)
MACRO_RETRY_BASE_SEC = _env_float("AION_MACRO_RETRY_BASE_SEC", 10.0)
MACRO_FORCE = _env_bool("AION_MACRO_FORCE", False)

# Robust value scanning window for FRED series (days)
FRED_LOOKBACK_DAYS = _env_int("AION_FRED_LOOKBACK_DAYS", 90)

FRED_API_KEY = (os.getenv("FRED_API", "") or "").strip()

# Alpaca keys (gold-only via GLD)
ALPACA_KEY_ID = (os.getenv("ALPACA_API_KEY_ID", "") or "").strip()
ALPACA_SECRET = (os.getenv("ALPACA_API_SECRET_KEY", "") or "").strip()

# Alpaca market data base
ALPACA_DATA_BASE = (os.getenv("ALPACA_DATA_BASE_URL", "") or "").strip() or "https://data.alpaca.markets"


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
# HTTP helpers (stdlib-only) with debug
# ------------------------------------------------------------

def _http_get_text(url: str, timeout_s: float = 20.0) -> Tuple[Optional[int], str, str]:
    """
    Returns (status_code, body_text, content_type).
    On hard failure returns (None, "", "").
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
            status = getattr(resp, "status", None)
            ctype = str(resp.headers.get("Content-Type") or "")
            body = resp.read().decode("utf-8", errors="ignore")
            return (int(status) if status is not None else None, body, ctype)

    except Exception:
        return (None, "", "")


def _json_loads_safe(s: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _snippet(s: str, n: int = 220) -> str:
    ss = (s or "").replace("\n", " ").replace("\r", " ").strip()
    if len(ss) <= n:
        return ss
    return ss[:n] + "…"


# ------------------------------------------------------------
# FRED helpers
# ------------------------------------------------------------

def _fred_obs_url(series_id: str, *, observation_start: str, limit: int) -> str:
    base = "https://api.stlouisfed.org/fred/series/observations"
    # sort desc so newest are first; we then scan for last 2 valid numeric values
    return (
        f"{base}"
        f"?series_id={series_id}"
        f"&api_key={FRED_API_KEY}"
        f"&file_type=json"
        f"&sort_order=desc"
        f"&observation_start={observation_start}"
        f"&limit={max(1, int(limit))}"
    )


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
        return float(x)
    except Exception:
        return None


def _fred_last2_valid(series_id: str) -> Tuple[float, float, Dict[str, Any]]:
    """
    Returns (last_value, pct_change_vs_prev, meta_debug).

    Strategy:
      - Request up to ~200 observations over the last FRED_LOOKBACK_DAYS
      - Scan newest->older and pick the first 2 valid numeric values
      - Compute pct change
    """
    if not FRED_API_KEY:
        return 0.0, 0.0, {"err": "missing_fred_key"}

    start_dt = datetime.now(timezone.utc) - timedelta(days=max(7, int(FRED_LOOKBACK_DAYS)))
    observation_start = start_dt.date().isoformat()

    url = _fred_obs_url(series_id, observation_start=observation_start, limit=200)
    last_err: Optional[str] = None
    last_status: Optional[int] = None
    last_snip: str = ""

    for attempt in range(1, max(1, MACRO_MAX_RETRIES) + 1):
        try:
            status, body, ctype = _http_get_text(url, timeout_s=20.0)
            last_status = status
            last_snip = _snippet(body)

            js = _json_loads_safe(body)
            if not js or "observations" not in js:
                # Log WHY it’s bad: status + snippet
                last_err = "empty_or_bad_json"
                raise RuntimeError(last_err)

            obs = js.get("observations") or []
            if not isinstance(obs, list):
                last_err = "bad_observations_schema"
                raise RuntimeError(last_err)

            vals: List[float] = []
            for o in obs:
                if not isinstance(o, dict):
                    continue
                x = _parse_fred_value(o.get("value"))
                if x is None:
                    continue
                vals.append(float(x))
                if len(vals) >= 2:
                    break

            if len(vals) < 1:
                last_err = "no_numeric_values_in_window"
                raise RuntimeError(last_err)

            last = float(vals[0])
            prev = float(vals[1]) if len(vals) >= 2 else 0.0
            if prev == 0.0:
                return last, 0.0, {"status": status, "series": series_id, "window_days": FRED_LOOKBACK_DAYS}

            pct = ((last - prev) / prev) * 100.0
            return last, float(pct), {"status": status, "series": series_id, "window_days": FRED_LOOKBACK_DAYS}

        except Exception as e:
            last_err = str(e)

        if attempt < MACRO_MAX_RETRIES:
            base_s = float(MACRO_RETRY_BASE_SEC) * (2 ** (attempt - 1))
            jitter = random.uniform(0.0, 0.25 * base_s)
            sleep_s = min(180.0, base_s + jitter)
            log(
                f"[macro_fetcher] ⚠️ FRED fetch {series_id} attempt {attempt}/{MACRO_MAX_RETRIES} failed: "
                f"{last_err} (status={last_status} snippet='{last_snip}') — sleeping {sleep_s:.1f}s"
            )
            try:
                time.sleep(sleep_s)
            except Exception:
                pass

    log(
        f"[macro_fetcher] ⚠️ FRED fetch {series_id} failed after retries: {last_err} "
        f"(status={last_status} snippet='{last_snip}')"
    )
    return 0.0, 0.0, {"err": last_err, "status": last_status, "snippet": last_snip, "series": series_id}


# ------------------------------------------------------------
# Alpaca (gold-only via GLD)
# ------------------------------------------------------------

def _alpaca_last2_daily_close(symbol: str = "GLD") -> Tuple[float, float, Dict[str, Any]]:
    """
    Fetch last two daily bars via Alpaca Market Data and compute pct.

    We keep this *gold-only* by design.
    """
    if not (ALPACA_KEY_ID and ALPACA_SECRET):
        return 0.0, 0.0, {"err": "missing_alpaca_keys"}

    sym = (symbol or "").strip().upper()
    if not sym:
        return 0.0, 0.0, {"err": "bad_symbol"}

    # Request a small window so we can skip any missing days and still find last 2 closes.
    # feed=iex is usable without a subscription (good for nightly macro). :contentReference[oaicite:1]{index=1}
    url = (
        f"{ALPACA_DATA_BASE}/v2/stocks/bars"
        f"?symbols={sym}"
        f"&timeframe=1Day"
        f"&limit=20"
        f"&adjustment=raw"
        f"&feed=iex"
    )

    last_err: Optional[str] = None
    last_status: Optional[int] = None
    last_snip: str = ""

    for attempt in range(1, max(1, MACRO_MAX_RETRIES) + 1):
        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                headers={
                    "APCA-API-KEY-ID": ALPACA_KEY_ID,
                    "APCA-API-SECRET-KEY": ALPACA_SECRET,
                    "Accept": "application/json",
                    "User-Agent": "AION-Analytics/1.0 (macro_fetcher)",
                },
            )

            with urllib.request.urlopen(req, timeout=20.0) as resp:
                last_status = int(getattr(resp, "status", 0) or 0)
                body = resp.read().decode("utf-8", errors="ignore")
                last_snip = _snippet(body)

            js = _json_loads_safe(body)
            if not js:
                last_err = "empty_or_bad_json"
                raise RuntimeError(last_err)

            # Response shapes vary; handle both:
            #   {"bars": {"GLD": [ ... ]}}
            #   {"bars": [ ... ]}
            bars_obj = js.get("bars")
            bars_list: List[Dict[str, Any]] = []

            if isinstance(bars_obj, dict):
                got = bars_obj.get(sym)
                if isinstance(got, list):
                    bars_list = [b for b in got if isinstance(b, dict)]
            elif isinstance(bars_obj, list):
                bars_list = [b for b in bars_obj if isinstance(b, dict)]

            # Need last 2 closes (bars often newest->oldest? not guaranteed). Sort by time if present.
            def _t_key(b: Dict[str, Any]) -> str:
                return str(b.get("t") or b.get("timestamp") or "")

            bars_list = sorted(bars_list, key=_t_key)  # oldest->newest

            closes: List[float] = []
            for b in reversed(bars_list):  # newest->older
                c = b.get("c") if "c" in b else b.get("close")
                try:
                    x = float(c)
                    if x > 0 and x == x:
                        closes.append(x)
                except Exception:
                    pass
                if len(closes) >= 2:
                    break

            if len(closes) < 1:
                last_err = "no_valid_closes"
                raise RuntimeError(last_err)

            last = float(closes[0])
            prev = float(closes[1]) if len(closes) >= 2 else 0.0
            pct = ((last - prev) / prev) * 100.0 if prev > 0 else 0.0

            return last, float(pct), {"status": last_status, "symbol": sym, "source": "alpaca"}

        except Exception as e:
            last_err = str(e)

        if attempt < MACRO_MAX_RETRIES:
            base_s = float(MACRO_RETRY_BASE_SEC) * (2 ** (attempt - 1))
            jitter = random.uniform(0.0, 0.25 * base_s)
            sleep_s = min(120.0, base_s + jitter)
            log(
                f"[macro_fetcher] ⚠️ Alpaca(GLD) attempt {attempt}/{MACRO_MAX_RETRIES} failed: "
                f"{last_err} (status={last_status} snippet='{last_snip}') — sleeping {sleep_s:.1f}s"
            )
            try:
                time.sleep(sleep_s)
            except Exception:
                pass

    log(
        f"[macro_fetcher] ⚠️ Alpaca(GLD) failed after retries: {last_err} "
        f"(status={last_status} snippet='{last_snip}')"
    )
    return 0.0, 0.0, {"err": last_err, "status": last_status, "snippet": last_snip, "source": "alpaca"}


# ------------------------------------------------------------
# Sanity gate
# ------------------------------------------------------------

def _macro_looks_sane(m: Dict[str, Any]) -> bool:
    """
    Consider macro sane if we have at least some real signal.
    Most important: sp500_close should not be zero.
    Prefer VIX too, but don't brick everything if only VIX fails.
    """
    try:
        spx = abs(safe_float(m.get("sp500_close", 0.0)))
        if spx <= 0.0:
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

def _risk_off_score(vix_close: float, spy_pct_dec: float, dxy_pct_dec: float) -> float:
    vix_component = min(max((vix_close - 15.0) / 25.0, 0.0), 1.0)      # 15..40 mapped
    spy_component = min(max((-spy_pct_dec) / 0.03, 0.0), 1.0)          # -3% maps to 1
    dxy_component = min(max((dxy_pct_dec) / 0.01, 0.0), 1.0)           # +1% maps to 1
    return float(min(1.0, 0.45 * vix_component + 0.45 * spy_component + 0.10 * dxy_component))


# ------------------------------------------------------------
# Main builder
# ------------------------------------------------------------

def build_macro_features() -> Dict[str, Any]:
    log("🌐 Fetching macro signals via FRED (+ gold via Alpaca GLD)…")

    # canonical destination
    out_path = PATHS.get("macro_state")
    if not isinstance(out_path, Path):
        out_path = None

    # If no FRED key, best-effort return cached
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
    SERIES = {
        "vix": "VIXCLS",       # CBOE Volatility Index
        "sp500": "SP500",      # S&P 500 index level (NOT SPY)
        "nasdaq": "NASDAQCOM", # NASDAQ Composite
        "tnx": "DGS10",        # 10Y Treasury constant maturity rate (%)
        "dxy": "DTWEXBGS",     # Trade weighted USD index (broad)
        "uso": "DCOILWTICO",   # WTI oil
    }

    vix_close, vix_pct, vix_meta = _fred_last2_valid(SERIES["vix"])
    spx_close, spx_pct, spx_meta = _fred_last2_valid(SERIES["sp500"])
    nas_close, nas_pct, nas_meta = _fred_last2_valid(SERIES["nasdaq"])
    tnx_close, tnx_pct, tnx_meta = _fred_last2_valid(SERIES["tnx"])
    dxy_close, dxy_pct, dxy_meta = _fred_last2_valid(SERIES["dxy"])
    uso_close, uso_pct, uso_meta = _fred_last2_valid(SERIES["uso"])

    # Gold via Alpaca GLD (only)
    gld_close, gld_pct, gld_meta = _alpaca_last2_daily_close("GLD")

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

        # Canonical SP500 fields (clarity)
        "sp500_close": float(safe_float(spx_close)),
        "sp500_daily_pct": float(safe_float(spx_pct)),           # percent
        "sp500_pct_decimal": float(safe_float(spx_pct_dec)),     # decimal

        # Legacy aliases (back-compat): treat as SP500 proxy
        "spy_close": float(safe_float(spx_close)),
        "spy_daily_pct": float(safe_float(spx_pct)),
        "spy_pct_decimal": float(safe_float(spx_pct_dec)),
        "spy_is_proxy": True,
        "spy_proxy_source": "FRED:SP500",

        # NASDAQ surrogate for QQQ-ish tech risk (optional)
        "qqq_close": float(safe_float(nas_close)),
        "qqq_daily_pct": float(safe_float(nas_pct)),

        # 10Y yield (%)
        "tnx_close": float(safe_float(tnx_close)),
        "tnx_daily_pct": float(safe_float(tnx_pct)),

        # DXY-ish index
        "dxy_close": float(safe_float(dxy_close)),
        "dxy_daily_pct": float(safe_float(dxy_pct)),
        "dxy_pct_decimal": float(safe_float(dxy_pct_dec)),

        # Gold & Oil (gold via Alpaca GLD)
        "gld_close": float(safe_float(gld_close)),
        "gld_daily_pct": float(safe_float(gld_pct)),
        "gld_source": "alpaca_gld",
        "gld_symbol": "GLD",

        "uso_close": float(safe_float(uso_close)),
        "uso_daily_pct": float(safe_float(uso_pct)),

        # Breadth proxy
        "breadth_proxy": float(safe_float(breadth_proxy)),

        # Downstream keys
        "volatility": float(safe_float(volatility)),
        "risk_off": float(safe_float(risk_off)),

        # timestamps
        "generated_at": now_iso_local,
        "updated_at": now_iso_utc,

        # provenance
        "source": "fred_plus_alpaca_gold",
        "fred_series": dict(SERIES),
        "fetch_meta": {
            "fred": {
                "vix": vix_meta,
                "sp500": spx_meta,
                "nasdaq": nas_meta,
                "tnx": tnx_meta,
                "dxy": dxy_meta,
                "uso": uso_meta,
            },
            "alpaca_gold": gld_meta,
        },
    }

    # Aliases used by regime_detector / context_state expectations
    macro_state["vix"] = float(macro_state.get("vix_close", 0.0))
    macro_state["spy_pct"] = float(macro_state.get("sp500_pct_decimal", 0.0))  # regime uses this anyway
    macro_state["breadth"] = float(macro_state.get("breadth_proxy", 0.0))

    # ----------------------------
    # Critical: Do NOT overwrite on failure
    # ----------------------------
    if not _macro_looks_sane(macro_state):
        log("[macro_fetcher] ⚠️ Macro fetch looks invalid. Keeping last snapshot (no overwrite).")
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
