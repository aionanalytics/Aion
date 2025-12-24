# backend/services/insights_builder.py — v1.2 (Regression Edition, signal-safe + tie-safe boards)
"""
AION Insights Builder — Regression Edition

Boards use:
    • predicted_return (primary)
    • confidence (secondary)
    • sentiment (news + social)

Fixes:
    • Ignore no-signal predicted_return using realistic thresholds (prevents fake targets & alphabetical boards)
    • Deterministic tie-safe sorting:
         score → abs(pred_return) → confidence → symbol
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import _read_rolling, log, safe_float

INSIGHTS_DIR = PATHS.get("insights", Path("insights"))
INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)

_HORIZONS = ("1d", "3d", "1w", "2w", "4w", "13w", "26w", "52w")

# Match prediction_logger thresholds (local copy to avoid circular imports)
NO_SIGNAL_THR: Dict[str, float] = {
    "1d": 0.0010,
    "3d": 0.0015,
    "1w": 0.0030,
    "2w": 0.0045,
    "4w": 0.0075,
    "13w": 0.0150,
    "26w": 0.0200,
    "52w": 0.0300,
}
EPS_RET = 1e-12


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
def _price_from_node(node: Dict[str, Any]) -> float:
    price = safe_float(node.get("close") or 0.0)
    if price == 0.0:
        price = safe_float(node.get("price") or 0.0)

    if price == 0.0:
        hist = node.get("history") or []
        if isinstance(hist, list) and hist:
            last = hist[-1]
            if isinstance(last, dict):
                price = safe_float(last.get("close") or 0.0)

    return float(price)


def _is_signal(h: str, ret: float) -> bool:
    thr = float(NO_SIGNAL_THR.get(h, 0.0) or 0.0)
    return abs(float(ret)) >= max(thr, EPS_RET)


def _extract_pred(node: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    preds = node.get("predictions") or {}
    if not isinstance(preds, dict):
        return out

    for h in _HORIZONS:
        block = preds.get(h)
        if not isinstance(block, dict):
            continue

        ret = safe_float(block.get("predicted_return", 0.0))
        if not _is_signal(h, ret):
            continue  # drop no-signal for boards

        tgt = block.get("target_price")
        try:
            tgt = float(tgt) if tgt is not None else None
        except Exception:
            tgt = None

        out[h] = {
            "ret": float(ret),
            "conf": safe_float(block.get("confidence", 0.5)),
            "score": safe_float(block.get("score", 0.0)),
            "tgt": tgt,
        }
    return out


def _extract_sent(node: Dict[str, Any]) -> Dict[str, float]:
    news = node.get("news") or {}
    social = node.get("social") or {}

    return {
        "news_sent": safe_float(news.get("sentiment", 0.0)) if isinstance(news, dict) else 0.0,
        "news_imp": safe_float(news.get("impact_score", 0.0)) if isinstance(news, dict) else 0.0,
        "social_sent": safe_float(social.get("sentiment", 0.0)) if isinstance(social, dict) else 0.0,
        "social_heat": safe_float(social.get("heat_score", 0.0)) if isinstance(social, dict) else 0.0,
    }


def _get_horizon(preds, h, fallback_ret, fallback_conf):
    block = preds.get(h)
    if not block:
        return fallback_ret, fallback_conf, None
    return block["ret"], block["conf"], block["tgt"]


def _target_price(price, predicted_return, model_target=None):
    if not price:
        return None

    if model_target is not None:
        try:
            return float(model_target)
        except Exception:
            pass

    return float(price * (1.0 + float(predicted_return)))


# ---------------------------------------------------------------------
# SCORING
# ---------------------------------------------------------------------
def _score_1w(pred, conf, sent):
    return pred * 0.75 + conf * 0.20 + (sent["news_sent"] + sent["social_sent"]) * 0.05

def _score_2w(pred, conf, sent):
    return pred * 0.70 + conf * 0.20 + sent["news_imp"] * 0.10

def _score_4w(pred, conf, sent):
    return pred * 0.80 + conf * 0.15 + sent["news_sent"] * 0.05

def _score_52w(pred, conf, sent):
    return pred * 0.60 + conf * 0.35 + (sent["news_sent"] + sent["social_sent"]) * 0.05


# ---------------------------------------------------------------------
# WRITE BOARD (tie-safe)
# ---------------------------------------------------------------------
def _write_board(rows, limit, file, meta):
    """
    Deterministic sorting prevents alphabetical tie cascades.
    """
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            safe_float(r.get("score", 0.0)),
            abs(safe_float(r.get("pred_val", 0.0))),
            safe_float(r.get("conf_val", 0.0)),
            str(r.get("symbol", "")),
        ),
        reverse=True,
    )[:limit]

    payload = {
        "generated_at": datetime.now(TIMEZONE).isoformat(),
        "items": rows_sorted,
        "limit": limit,
        "count": len(rows_sorted),
        "meta": meta,
    }

    path = INSIGHTS_DIR / file
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"[insights_builder] wrote {file} ({len(rows_sorted)} items)")
    return {"file": str(path), "count": len(rows_sorted)}


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def build_daily_insights(limit: int = 50) -> Dict[str, Any]:
    rolling = _read_rolling()
    if not rolling:
        return {"status": "no_rolling"}

    rows_1w, rows_2w, rows_4w, rows_52w = [], [], [], []

    for sym, node in rolling.items():
        if str(sym).startswith("_") or not isinstance(node, dict):
            continue

        price = _price_from_node(node)
        if price <= 0:
            continue

        preds = _extract_pred(node)
        if not preds:
            continue

        sent = _extract_sent(node)

        base = preds.get("1w") or preds.get("4w")
        if not base:
            continue

        p1w, c1w, t1w = _get_horizon(preds, "1w", base["ret"], base["conf"])
        p4w, c4w, t4w = _get_horizon(preds, "4w", base["ret"], base["conf"])
        p52w, c52w, t52w = _get_horizon(preds, "52w", base["ret"], base["conf"])

        p2w = 0.5 * (p1w + p4w)
        c2w = 0.5 * (c1w + c4w)

        sym_u = str(sym).upper()

        rows_1w.append({
            "symbol": sym_u,
            "price": float(price),
            "pred_ret_1w": float(p1w),
            "target_price_1w": _target_price(price, p1w, t1w),
            "score": float(_score_1w(p1w, c1w, sent)),
            "pred_val": float(p1w),
            "conf_val": float(c1w),
            **sent
        })

        rows_2w.append({
            "symbol": sym_u,
            "price": float(price),
            "pred_ret_2w": float(p2w),
            "target_price_2w": _target_price(price, p2w),
            "score": float(_score_2w(p2w, c2w, sent)),
            "pred_val": float(p2w),
            "conf_val": float(c2w),
            **sent
        })

        rows_4w.append({
            "symbol": sym_u,
            "price": float(price),
            "pred_ret_4w": float(p4w),
            "target_price_4w": _target_price(price, p4w, t4w),
            "score": float(_score_4w(p4w, c4w, sent)),
            "pred_val": float(p4w),
            "conf_val": float(c4w),
            **sent
        })

        rows_52w.append({
            "symbol": sym_u,
            "price": float(price),
            "pred_ret_52w": float(p52w),
            "target_price_52w": _target_price(price, p52w, t52w),
            "score": float(_score_52w(p52w, c52w, sent)),
            "pred_val": float(p52w),
            "conf_val": float(c52w),
            **sent
        })

    return {
        "status": "ok",
        "outputs": {
            "1w": _write_board(rows_1w, limit, "top50_1w.json", {"horizon": "1w"}),
            "2w": _write_board(rows_2w, limit, "top50_2w.json", {"horizon": "2w"}),
            "4w": _write_board(rows_4w, limit, "top50_4w.json", {"horizon": "4w"}),
            "52w": _write_board(rows_52w, limit, "top50_52w.json", {"horizon": "52w"}),
        },
    }
