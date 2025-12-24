# backend/core/context_state.py — v1.2 (Aligned to News Intel v4.0 brain-backed + AION brain meta)
"""
Context State — AION Analytics

Adds:
  - volatility (alias for macro_vol)
  - trend derived from pred_score
Also writes ml_data/market_state.json (global snapshot).

UPDATED (v1.1):
  ✅ Reads news intel from ml_data/news_features/news_features_YYYY-MM-DD.json
     (brain-backed; no API calls)
  ✅ Maps new schema:
        payload["symbols"][SYM]["long_horizon"/"buzz"/"shock"/"latest"]

UPDATED (v1.2):
  ✅ Reads AION brain meta (global behavioral knobs) and includes it in:
       - global market_state.json
       - per-symbol context["aion"] block (traceable, non-breaking)

Safe:
  • Runs with missing files; uses neutral defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from .config import PATHS, TIMEZONE
from .data_pipeline import (
    _read_rolling,
    save_rolling,
    safe_float,
    _read_aion_brain,   # ✅ NEW (v1.2)
)
from utils.logger import log

ML_DATA_ROOT: Path = PATHS.get("ml_data", Path("ml_data"))
GLBL_PATH: Path = ML_DATA_ROOT / "market_state.json"

MACRO_STATE_FILE: Path = PATHS.get("macro_state", ML_DATA_ROOT / "macro_state.json")

# ✅ News now lives under ml_data/news_features/ (news_intel output)
NEWS_DIR: Path = (Path(PATHS.get("ml_data") or ML_DATA_ROOT) / "news_features")

# Social unchanged
SOCIAL_DIR: Path = PATHS.get("social", ML_DATA_ROOT / "social")

HORIZONS = ["1d", "3d", "1w", "2w", "4w", "13w", "26w", "52w"]

HORIZON_WEIGHTS: Dict[str, float] = {
    "1d": 0.35,
    "3d": 0.25,
    "1w": 0.20,
    "2w": 0.10,
    "4w": 0.06,
    "13w": 0.02,
    "26w": 0.01,
    "52w": 0.01,
}


def _load_json(path: Optional[Path]) -> Any:
    if not path:
        return None
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_with_prefix(folder: Path, prefix: str) -> Optional[Path]:
    if not folder.exists():
        return None
    candidates = sorted(
        [p for p in folder.glob(f"{prefix}*.json")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _news_map(news_js: Any) -> Dict[str, Dict[str, Any]]:
    """
    news_intel v4.0 schema (brain-backed):
      {
        "meta": {...},
        "symbols": {
          "AAPL": {
            "long_horizon": {...},
            "buzz": {...},
            "latest": [...],
            "shock": {...}   # intraday only; nightly usually {}
          }
        }
      }

    We map to a compact per-symbol dict for context fusion.
    """
    if not isinstance(news_js, dict):
        return {}

    symbols = news_js.get("symbols") or {}
    if not isinstance(symbols, dict):
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    for sym, block in symbols.items():
        if not isinstance(block, dict):
            continue

        lh = block.get("long_horizon") or {}
        buzz = block.get("buzz") or {}
        shock = block.get("shock") or {}

        # Use long_horizon sentiment as the base sentiment
        sent = safe_float(lh.get("sentiment_mean", 0.0))

        # buzz_count is often equal to article_count (by design in news_intel)
        buzz_count = int(buzz.get("buzz_count", 0))
        buzz_score = safe_float(buzz.get("buzz_score", 0.0))

        shock_score = safe_float(shock.get("score", 0.0))
        shock_dir = str(shock.get("direction", "neutral")).lower() if isinstance(shock, dict) else "neutral"

        out[str(sym).upper()] = {
            "sentiment": sent,
            "buzz": buzz_count,
            "impact_score": buzz_score,
            "shock_score": shock_score,
            "shock_direction": shock_dir,
        }

    return out


def _social_map(js: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(js, dict):
        return {}
    data = js.get("data") or js
    out: Dict[str, Dict[str, Any]] = {}
    for sym, block in data.items():
        if not isinstance(block, dict):
            continue
        sym_u = str(sym).upper()
        out[sym_u] = {
            "sentiment_social": safe_float(block.get("avg_sentiment", block.get("sentiment", 0.0))),
            "buzz_social": int(block.get("buzz", 0)),
        }
    return out


def _macro_state(raw: Any) -> Dict[str, Any]:
    """
    Normalize macro_state.json into a dict that is useful to *all* downstream modules.

    IMPORTANT:
      - Preserve the full canonical macro_state fields (vix_close, spy_daily_pct, etc.)
      - Also provide backward-compatible aliases used by regime/context/policy.
    """
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, Any] = dict(raw)

    # Aliases / derived fields expected by other modules
    vix_close = safe_float(out.get("vix_close", out.get("vix", out.get("vix_level", 0.0))))
    spy_pct_percent = safe_float(out.get("spy_daily_pct", out.get("spy_pct", 0.0)))  # percent if provided by macro_fetcher
    spy_pct_dec = spy_pct_percent / 100.0

    out["vix_close"] = float(vix_close)
    out["vix"] = float(vix_close)  # common alias

    out["spy_daily_pct"] = float(spy_pct_percent)
    out["spy_pct"] = float(spy_pct_dec)  # decimal alias (e.g., 0.0123 = +1.23%)

    # Fallback breadth/volatility proxies (keep if already present)
    if "breadth" not in out:
        out["breadth"] = safe_float(out.get("breadth_proxy", 0.5))
    if "volatility" not in out:
        out["volatility"] = float(max(0.0, min(0.10, vix_close / 100.0)))

    # risk_off: allow bool or float
    risk_off = out.get("risk_off", False)
    out["risk_off"] = bool(risk_off) if isinstance(risk_off, bool) else bool(safe_float(risk_off) > 0.5)

    # regime hint
    out["regime_hint"] = str(out.get("regime_hint", out.get("regime", out.get("label", "neutral"))))

    # Timestamp aliases
    ts = out.get("generated_at") or out.get("ts") or datetime.now(TIMEZONE).isoformat()
    out["generated_at"] = ts
    out["ts"] = ts
    return out


def _load_macro_snapshot() -> Dict[str, Any]:
    raw = _load_json(MACRO_STATE_FILE)
    return _macro_state(raw)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _trend_from_pred_score(pred_score: float) -> str:
    if pred_score >= 0.06:
        return "bullish"
    if pred_score <= -0.06:
        return "bearish"
    return "neutral"


def _extract_multi_horizon_scores(preds: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    if not isinstance(preds, dict):
        return out

    for h in HORIZONS:
        blk = preds.get(h)
        if not isinstance(blk, dict):
            continue
        score = safe_float(blk.get("score", 0.0))
        conf = safe_float(blk.get("confidence", 0.0))
        out[h] = {"score": score, "confidence": conf}
    return out


def _multi_horizon_summary(ph: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    def _avg(keys):
        vals = [safe_float(ph[k]["score"]) for k in keys if k in ph]
        return sum(vals) / len(vals) if vals else 0.0

    def _avg_conf(keys):
        vals = [safe_float(ph[k]["confidence"]) for k in keys if k in ph]
        return sum(vals) / len(vals) if vals else 0.0

    short_score = _avg(["1d", "3d"])
    mid_score = _avg(["1w", "2w"])
    long_score = _avg(["4w", "13w", "26w", "52w"])

    short_conf = _avg_conf(["1d", "3d"])
    mid_conf = _avg_conf(["1w", "2w"])
    long_conf = _avg_conf(["4w", "13w", "26w", "52w"])

    pred_score = short_score
    if pred_score == 0.0 and mid_score != 0.0:
        pred_score = mid_score
    if pred_score == 0.0 and long_score != 0.0:
        pred_score = long_score

    pred_score = _clamp(pred_score, -0.4, 0.4)
    short_score = _clamp(short_score, -0.4, 0.4)
    mid_score = _clamp(mid_score, -0.4, 0.4)
    long_score = _clamp(long_score, -0.4, 0.4)

    short_conf = _clamp(short_conf, 0.0, 1.0)
    mid_conf = _clamp(mid_conf, 0.0, 1.0)
    long_conf = _clamp(long_conf, 0.0, 1.0)

    return {
        "pred_score": pred_score,
        "short_term_score": short_score,
        "mid_term_score": mid_score,
        "long_term_score": long_score,
        "conf_short": short_conf,
        "conf_mid": mid_conf,
        "conf_long": long_conf,
    }


def _aion_meta_snapshot() -> Dict[str, Any]:
    """
    AION brain meta is global behavioral memory. We only expose the core knobs here
    so other modules/UI can understand current posture without loading the full file.
    """
    try:
        ab = _read_aion_brain() or {}
        meta = ab.get("_meta", {}) if isinstance(ab, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        return {
            "updated_at": meta.get("updated_at"),
            "confidence_bias": safe_float(meta.get("confidence_bias", 1.0)) or 1.0,
            "risk_bias": safe_float(meta.get("risk_bias", 1.0)) or 1.0,
            "aggressiveness": safe_float(meta.get("aggressiveness", 1.0)) or 1.0,
        }
    except Exception:
        return {
            "updated_at": None,
            "confidence_bias": 1.0,
            "risk_bias": 1.0,
            "aggressiveness": 1.0,
        }


def build_context() -> Dict[str, Any]:
    log("[context_state] 🧠 Building context (v1.2, brain-backed news + AION brain meta)…")

    rolling = _read_rolling() or {}
    if not rolling:
        log("[context_state] ⚠ rolling.json.gz missing or empty.")
        return {"symbols": 0, "global": {}}

    # ✅ Load latest news_features_*.json (nightly news_intel output)
    news_file = _latest_with_prefix(NEWS_DIR, "news_features_")
    news_js = _load_json(news_file)
    news_map = _news_map(news_js)

    if not news_file or not news_map:
        log("[context_state] ⚠ No news_features_* found (or empty). News features will be neutral.")

    social_file = _latest_with_prefix(SOCIAL_DIR, "social_sentiment_")
    social_js = _load_json(social_file)
    social_map = _social_map(social_js)

    macro = _load_macro_snapshot()

    # ✅ AION brain meta snapshot (global posture)
    aion_meta = _aion_meta_snapshot()

    now_iso = datetime.now(TIMEZONE).isoformat()
    global_state = {
        "ts": now_iso,
        "generated_at": now_iso,   # alias for other modules
        "macro": macro,
        "has_news": bool(news_map),
        "has_social": bool(social_map),
        "aion_brain": aion_meta,   # ✅ NEW (v1.2)
    }

    symbols_updated = 0

    for sym, node in rolling.items():
        if sym.startswith("_"):
            continue

        if not isinstance(node, dict):
            continue

        sym_u = str(sym).upper()

        news = news_map.get(sym_u, {})
        soc = social_map.get(sym_u, {})

        sector = (node.get("sector") or (node.get("fundamentals") or {}).get("sector") or "")
        if not isinstance(sector, str):
            sector = ""
        sector = sector.upper().strip()

        preds = node.get("predictions") or {}
        ph = _extract_multi_horizon_scores(preds)
        mh = _multi_horizon_summary(ph)

        # --- Fuse sentiment ---
        news_sent = safe_float(news.get("sentiment", 0.0))
        soc_sent = safe_float(soc.get("sentiment_social", 0.0))

        sentiment = 0.6 * news_sent + 0.4 * soc_sent
        sentiment = _clamp(sentiment, -3.0, 3.0)

        # --- Buzz + impact ---
        buzz = int(news.get("buzz", 0)) + int(soc.get("buzz_social", 0))
        impact_score = safe_float(news.get("impact_score", 0.0))

        # Keep "novelty" for compatibility; approximate via shock_score (brain-backed)
        novelty = safe_float(news.get("shock_score", 0.0))
        novelty = _clamp(novelty, -3.0, 3.0)

        macro_vol = safe_float(macro.get("volatility", 0.0))
        trend = _trend_from_pred_score(float(mh["pred_score"]))

        # --- Attach per-symbol context ---
        ctx = {
            "sentiment": sentiment,
            "buzz": int(buzz),
            "novelty": float(novelty),

            "macro_vol": float(macro_vol),
            "macro_breadth": safe_float(macro.get("breadth", 0.5)),
            "risk_off": bool(macro.get("risk_off", False)),
            "regime_hint": macro.get("regime_hint", "neutral"),

            "short_term_score": mh["short_term_score"],
            "mid_term_score": mh["mid_term_score"],
            "long_term_score": mh["long_term_score"],
            "pred_score": mh["pred_score"],
            "conf_short": mh["conf_short"],
            "conf_mid": mh["conf_mid"],
            "conf_long": mh["conf_long"],

            "sector": sector,

            # policy-friendly aliases
            "volatility": float(macro_vol),
            "trend": trend,

            # ✅ Provide explicit structured blocks so policy_engine can read ctx["news"]
            "news": {
                "sentiment": float(news_sent),
                "buzz": int(news.get("buzz", 0)),
                "impact_score": float(impact_score),
                "shock_score": float(news.get("shock_score", 0.0)),
                "shock_direction": str(news.get("shock_direction", "neutral")),
            },
            "social": {
                "sentiment": float(soc_sent),
                "heat_score": float(int(soc.get("buzz_social", 0))),
            },

            # ✅ NEW (v1.2): AION brain posture (global knobs)
            "aion": {
                "confidence_bias": float(aion_meta.get("confidence_bias", 1.0)),
                "risk_bias": float(aion_meta.get("risk_bias", 1.0)),
                "aggressiveness": float(aion_meta.get("aggressiveness", 1.0)),
                "updated_at": aion_meta.get("updated_at"),
            },
        }

        node["context"] = ctx
        node["sector"] = sector
        rolling[sym] = node
        symbols_updated += 1

    try:
        GLBL_PATH.parent.mkdir(parents=True, exist_ok=True)
        GLBL_PATH.write_text(json.dumps(global_state, indent=2), encoding="utf-8")
        log(f"[context_state] 💾 Global market_state.json updated → {GLBL_PATH}")
    except Exception as e:
        log(f"[context_state] ⚠ Failed writing global context: {e}")

    save_rolling(rolling)
    log(f"[context_state] ✅ Context updated for {symbols_updated} symbols.")

    return {"symbols": symbols_updated, "global": global_state}


if __name__ == "__main__":
    out = build_context()
    print(json.dumps(out, indent=2))
