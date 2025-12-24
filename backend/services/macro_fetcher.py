# backend/services/macro_fetcher.py — v1.0
"""
macro_fetcher.py — v1.0
Aligned with backend/core + nightly_job (Hybrid Mode)

Fixes (v3.1):
  ✅ Writes PATHS["macro_state"] (canonical)
  ✅ Also writes PATHS["ml_data"]/market_state.json-compatible snapshot (so regime/context/supervisor stop disagreeing)
  ✅ Uses generated_at + updated_at (both)
  ✅ spy_daily_pct stored as percent AND spy_pct_decimal stored as decimal (no unit ambiguity)
  ✅ Adds volatility + risk_off (best-effort, simple + stable)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, Any

import numpy as np
import yfinance as yf

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import log, safe_float

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)


def _yf_last2(symbol: str) -> Dict[str, float]:
    try:
        df = yf.download(symbol, period="5d", interval="1d", progress=False)
        df = df.dropna()
        if len(df) < 2:
            return {"close": 0.0, "pct": 0.0}

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = safe_float(last["Close"])
        prev_close = safe_float(prev["Close"])

        pct = ((close - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
        return {"close": close, "pct": pct}

    except Exception as e:
        log(f"⚠️ yfinance fetch failed for {symbol}: {e}")
        return {"close": 0.0, "pct": 0.0}


def _estimate_breadth() -> float:
    spy = _yf_last2("SPY")
    # breadth_proxy in decimal space
    return safe_float(spy["pct"]) / 100.0


def _risk_off_score(vix_close: float, spy_pct_dec: float, dxy_pct_dec: float) -> float:
    """
    Simple, stable risk-off score in [0..1-ish].
    Not “smart”, just consistent:
      - higher VIX => more risk-off
      - SPY down => more risk-off
      - DXY up  => more risk-off (often flight to USD)
    """
    vix_component = min(max((vix_close - 15.0) / 25.0, 0.0), 1.0)      # 15..40 mapped
    spy_component = min(max((-spy_pct_dec) / 0.03, 0.0), 1.0)          # -3% maps to 1
    dxy_component = min(max((dxy_pct_dec) / 0.01, 0.0), 1.0)           # +1% maps to 1
    return float(min(1.0, 0.45 * vix_component + 0.45 * spy_component + 0.10 * dxy_component))


def build_macro_features() -> Dict[str, Any]:
    log("🌐 Fetching macro signals (VIX, SPY, QQQ, TNX, DXY, GLD, USO)…")

    vix = _yf_last2("^VIX")
    spy = _yf_last2("SPY")
    qqq = _yf_last2("QQQ")
    tnx = _yf_last2("^TNX")
    dxy = _yf_last2("DX-Y.NYB")
    gld = _yf_last2("GLD")
    uso = _yf_last2("USO")

    vix_close, vix_pct = safe_float(vix["close"]), safe_float(vix["pct"])
    spy_close, spy_pct = safe_float(spy["close"]), safe_float(spy["pct"])
    qqq_close, qqq_pct = safe_float(qqq["close"]), safe_float(qqq["pct"])
    tnx_close, tnx_pct = safe_float(tnx["close"]), safe_float(tnx["pct"])
    dxy_close, dxy_pct = safe_float(dxy["close"]), safe_float(dxy["pct"])
    gld_close, gld_pct = safe_float(gld["close"]), safe_float(gld["pct"])
    uso_close, uso_pct = safe_float(uso["close"]), safe_float(uso["pct"])

    spy_pct_dec = spy_pct / 100.0
    dxy_pct_dec = dxy_pct / 100.0

    breadth_proxy = _estimate_breadth()

    # “volatility” proxy: prefer vix/100, but keep it bounded
    volatility = float(max(0.0, min(0.10, vix_close / 100.0)))

    risk_off = _risk_off_score(vix_close, spy_pct_dec, dxy_pct_dec)

    now_iso_utc = datetime.utcnow().isoformat()
    now_iso_local = datetime.now(TIMEZONE).isoformat()

    macro_state = {
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

        # new stable keys used downstream
        "volatility": float(volatility),
        "risk_off": float(risk_off),

        # timestamps (both)
        "generated_at": now_iso_local,
        "updated_at": now_iso_utc,
    }

    # Save canonical macro_state.json
    out_path = PATHS.get("macro_state")
    if out_path:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(macro_state, indent=2), encoding="utf-8")
            log(f"[macro_fetcher] 📈 macro_state.json updated → {out_path}")
        except Exception as e:
            log(f"[macro_fetcher] ⚠️ Failed to write macro_state.json: {e}")

    # Also write a market_state-compatible snapshot for regime_detector fallbacks
    try:
        ml_root = PATHS.get("ml_data", Path("ml_data"))
        market_state_path = ml_root / "market_state.json"
        payload = dict(macro_state)
        # keep a minimal “regime hint” slot for future use
        payload.setdefault("regime_hint", "neutral")
        market_state_path.parent.mkdir(parents=True, exist_ok=True)
        market_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log(f"[macro_fetcher] 🧭 market_state.json (macro snapshot) updated → {market_state_path}")
    except Exception as e:
        log(f"[macro_fetcher] ⚠️ Failed to write market_state.json macro snapshot: {e}")

    return macro_state


if __name__ == "__main__":
    res = build_macro_features()
    print(json.dumps(res, indent=2))
