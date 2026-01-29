"""
prediction_logger.py — v1.6.0 (Regression Edition + Ranked Arrays + Ledger + No-Signal Tagging + Integrity Gates)

AION Analytics — Nightly Prediction & Policy Logger

Regression schema:
    predicted_return
    confidence
    score
    target_price
    rating / rating_score (preserved)
    label (preserved)

Writes UI-ready feed:
    nightly_predictions/latest_predictions.json

Upgrades:
    ✅ Deterministic ranked arrays per horizon (backend owns ordering)
    ✅ Entry close + entry date for accuracy evaluation
    ✅ Durable prediction ledger: nightly_predictions/predictions_ledger.jsonl

Fixes:
    ✅ Do NOT drop near-zero predictions globally (we tag no_signal instead)
    ✅ No-signal tagging per horizon threshold
    ✅ Tie-safe ranking:
         score → abs(expected_return) → confidence → symbol
    ✅ Synthetic 2w includes score

Step 3 (v1.6.0):
    ✅ Horizon validity map:
         - counts, signal_frac, valid flag, reason
    ✅ If a horizon is invalid (no signal / no targets):
         - ranked arrays are []
         - top_moves are empty arrays
    ✅ Ledger hygiene:
         - invalid horizons DO NOT write rows (prevents polluted calibration)
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import (
    _read_rolling,
    save_rolling,
    log,
    safe_float,
)
from backend.core.ai_model.core_training import predict_all
from backend.core.ai_model.target_builder import HORIZONS
from backend.core.policy_engine import apply_policy

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
LOG_DIR: Path = PATHS["nightly_predictions"]
LOG_DIR.mkdir(parents=True, exist_ok=True)

LEDGER_PATH: Path = LOG_DIR / "predictions_ledger.jsonl"

# ---------------------------------------------------------------------
# "No-signal" thresholds (per horizon)
# ---------------------------------------------------------------------
# These are NOT used to drop rows. They are used to tag rows so:
#  - UI can hide no_signal if desired
#  - ranking remains stable and meaningful
NO_SIGNAL_THR: Dict[str, float] = {
    "1d": 0.0010,   # 0.10%
    "3d": 0.0015,   # 0.15%
    "1w": 0.0030,   # 0.30%
    "2w": 0.0045,   # 0.45%
    "4w": 0.0075,   # 0.75%
    "13w": 0.0150,  # 1.50%
    "26w": 0.0200,  # 2.00%
    "52w": 0.0300,  # 3.00%
}

# extremely tiny numerical noise guard (never “signal gating”)
EPS_RET = 1e-12


# ---------------------------------------------------------------------
# Disk space helper
# ---------------------------------------------------------------------
def check_disk_space(path: Path, required_mb: int = 100) -> bool:
    """
    Check if sufficient disk space is available before writing.
    
    Args:
        path: Path to check (uses parent directory for disk stats)
        required_mb: Minimum required free space in MB
    
    Returns:
        True if sufficient space available, False otherwise
    """
    try:
        stat = shutil.disk_usage(path.parent)
        available_mb = stat.free / (1024 * 1024)
        return available_mb >= required_mb
    except Exception as e:
        log(f"[prediction_logger] ⚠️ Failed to check disk space: {e}")
        # On error, allow write to proceed (fail-open for safety)
        return True


def _file_timestamp() -> str:
    return datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now(TIMEZONE).isoformat()


def _today_ymd() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


def _no_signal(h: str, expected_return: float) -> bool:
    thr = float(NO_SIGNAL_THR.get(h, 0.0) or 0.0)
    return abs(float(expected_return)) < max(thr, EPS_RET)


def _universe_has_signal(payload_symbols: Dict[str, Any], horizon: str) -> bool:
    thr = float(NO_SIGNAL_THR.get(horizon, 0.0) or 0.0)
    for _sym, block in payload_symbols.items():
        if not isinstance(block, dict):
            continue
        targets = block.get("targets") or {}
        t = targets.get(horizon)
        if not isinstance(t, dict):
            continue
        exp = safe_float(t.get("expected_return", 0.0))
        if abs(float(exp)) >= max(thr, EPS_RET):
            return True
    return False


# ---------------------------------------------------------------------
# Extract regression-clean block
# ---------------------------------------------------------------------
def _extract_clean(preds: Dict[str, Any], pol: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert ai_model.predict_all() → compact block preserving regression fields.

    IMPORTANT:
      - We do NOT drop near-zero predictions.
      - We keep them and tag no_signal later at target construction time.
    """
    out: Dict[str, Any] = {}

    if isinstance(preds, dict):
        for h, block in preds.items():
            if h == "policy":
                continue
            if not isinstance(block, dict):
                continue

            valid = bool(block.get('valid', True))
            coverage = block.get('coverage')
            status = block.get('status')
            reason = block.get('reason') or block.get('invalid_reason')
            if not valid:
                out[h] = {
                    'valid': False,
                    'status': str(status or 'invalid'),
                    'reason': str(reason or 'insufficient_variance'),
                    'coverage': coverage,
                    'score': 0.0,
                    'confidence': 0.5,
                    'label': 0,
                    'predicted_return': 0.0,
                    'target_price': None,
                    'rating': None,
                    'rating_score': None,
                }
                continue

            pred_ret = safe_float(block.get('predicted_return', 0.0))
            out[h] = {
                'valid': True,
                'coverage': coverage,
                'score': safe_float(block.get('score', 0.0)),
                'confidence': safe_float(block.get('confidence', 0.5)),
                'label': int(block.get('label', 0) or 0),
                'predicted_return': float(pred_ret),
                'target_price': block.get('target_price'),
                'rating': block.get('rating'),
                'rating_score': block.get('rating_score'),
            }

    if isinstance(pol, dict):
        out["policy"] = {
            "intent": pol.get("intent"),
            "score": safe_float(pol.get("score", 0.0)),
            "confidence": safe_float(pol.get("confidence", 0.5)),
            "exposure_scale": safe_float(pol.get("exposure_scale", 1.0)),
            "risk": pol.get("risk"),
        }
    else:
        out["policy"] = None

    return out


# ---------------------------------------------------------------------
# Cross-horizon disagreement (regression version)
# ---------------------------------------------------------------------
_SHORT_H = ("1d", "3d", "1w")
_LONG_H = ("13w", "26w", "52w")

# UI grouping (do not mix short/long in the same list)
SHORT_TERM_HORIZONS = ("1d", "3d", "1w", "2w", "4w")
LONG_TERM_HORIZONS = ("13w", "26w", "52w")


def _compute_xh_disagreement(clean_block: Dict[str, Any]) -> Dict[str, Any]:
    short_scores = []
    long_scores = []

    for h in _SHORT_H:
        blk = clean_block.get(h)
        if isinstance(blk, dict):
            short_scores.append(safe_float(blk.get("score", 0.0)))

    for h in _LONG_H:
        blk = clean_block.get(h)
        if isinstance(blk, dict):
            long_scores.append(safe_float(blk.get("score", 0.0)))

    if not short_scores or not long_scores:
        return {"status": "insufficient", "pattern": None}

    s_avg = sum(short_scores) / len(short_scores)
    l_avg = sum(long_scores) / len(long_scores)

    def sign(x: float, tol: float = 1e-6) -> int:
        if x > tol:
            return 1
        if x < -tol:
            return -1
        return 0

    s_label = sign(s_avg)
    l_label = sign(l_avg)

    status = "agree"
    pattern = None

    if s_label == 0 or l_label == 0:
        status = "weak"
    elif s_label != l_label:
        status = "disagree"
        if s_label < 0 and l_label > 0:
            pattern = "short_bearish_long_bullish"
        elif s_label > 0 and l_label < 0:
            pattern = "short_bullish_long_bearish"

    return {
        "status": status,
        "pattern": pattern,
        "short_avg_score": float(s_avg),
        "long_avg_score": float(l_avg),
    }


# ---------------------------------------------------------------------
# Target helper
# ---------------------------------------------------------------------
def _make_target(horizon: str, price: float, blk: Dict[str, Any]) -> Dict[str, Any]:
    pred_ret = safe_float(blk.get("predicted_return", 0.0))
    conf = safe_float(blk.get("confidence", 0.5))
    score = safe_float(blk.get("score", 0.0))

    expected = float(pred_ret)
    no_sig = _no_signal(horizon, expected)

    target_price = blk.get("target_price")
    if target_price is None and price:
        target_price = price * (1.0 + expected)

    try:
        target_price = float(target_price) if target_price is not None else None
    except Exception:
        target_price = None

    if expected > 0:
        direction = "up"
    elif expected < 0:
        direction = "down"
    else:
        direction = "flat"

    return {
        "expected_return": float(expected),
        # Expected value proxy (used for ranking): EV ≈ P(hit) × return
        # (volatility/risk adjustments can be layered on later)
        "ev": float(expected) * float(conf),
        "abs_expected_return": float(abs(expected)),
        "target_price": target_price,
        "confidence": float(conf),
        "direction": direction,
        "score": float(score),
        "no_signal": bool(no_sig),
        "no_signal_threshold": float(NO_SIGNAL_THR.get(horizon, 0.0) or 0.0),
    }


# ---------------------------------------------------------------------
# Horizon validity (Step 3)
# ---------------------------------------------------------------------
def _compute_horizon_validity(payload_symbols: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns per-horizon validity + stats.
    Valid means:
      - horizon exists in targets for at least 1 symbol
      - AND at least one symbol exceeds NO_SIGNAL_THR (universe_has_signal)
    """
    out: Dict[str, Any] = {}

    symbols_total = len(payload_symbols)
    for h in ("1d", "3d", "1w", "2w", "4w", "13w", "26w", "52w"):
        targets_seen = 0
        signal_seen = 0
        thr = float(NO_SIGNAL_THR.get(h, 0.0) or 0.0)

        for _sym, block in payload_symbols.items():
            if not isinstance(block, dict):
                continue
            t = (block.get("targets") or {}).get(h)
            if not isinstance(t, dict):
                continue
            targets_seen += 1
            exp = safe_float(t.get("expected_return", 0.0))
            if abs(float(exp)) >= max(thr, EPS_RET):
                signal_seen += 1

        has_targets = targets_seen > 0
        has_signal = signal_seen > 0

        valid = bool(has_targets and has_signal)
        reason = "ok"
        if not has_targets:
            reason = "no_targets"
        elif not has_signal:
            reason = "no_signal_universe"

        out[h] = {
            "valid": bool(valid),
            "reason": str(reason),
            "symbols_total": int(symbols_total),
            "symbols_with_targets": int(targets_seen),
            "symbols_with_signal": int(signal_seen),
            "signal_frac": float(signal_seen / targets_seen) if targets_seen else 0.0,
            "no_signal_threshold": float(thr),
        }

    return out


# ---------------------------------------------------------------------
# Top movers for dashboard (signal-safe)
# ---------------------------------------------------------------------
def _top_moves(summary: Dict[str, Any], horizon: str, top_n: int = 20) -> Dict[str, Any]:
    """
    If no meaningful signal exists, return empty lists.
    """
    rows_signal: List[Tuple[str, float]] = []
    thr = float(NO_SIGNAL_THR.get(horizon, 0.0) or 0.0)

    for sym, data in summary.items():
        targets = data.get("targets", {}) or {}
        t = targets.get(horizon)
        if not isinstance(t, dict):
            continue

        exp = safe_float(t.get("expected_return", 0.0))
        if abs(float(exp)) >= max(thr, EPS_RET):
            rows_signal.append((sym, exp))

    if not rows_signal:
        return {"top_bullish": [], "top_bearish": []}

    rows_signal.sort(key=lambda x: x[1], reverse=True)
    return {
        "top_bullish": rows_signal[:top_n],
        "top_bearish": rows_signal[-top_n:][::-1],
    }



# ---------------------------------------------------------------------
# Grouped (SHORT vs LONG) leaderboards
# ---------------------------------------------------------------------
def _merge_group_best(
    payload_symbols: Dict[str, Any],
    horizons: Tuple[str, ...],
    *,
    bearish: bool,
    limit: int = 50,
    horizon_validity: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    For each symbol, pick its single best row across the provided horizons,
    then rank those best-per-symbol rows.

    This prevents humans from having to mentally de-duplicate the same symbol
    across multiple horizons, and keeps SHORT vs LONG cleanly separated.
    """
    best_rows: List[Dict[str, Any]] = []

    for sym, block in payload_symbols.items():
        if not isinstance(block, dict):
            continue
        targets = block.get("targets") or {}
        if not isinstance(targets, dict):
            continue

        chosen: Optional[Dict[str, Any]] = None
        for h in horizons:
            # horizon validity gate
            if isinstance(horizon_validity, dict):
                hv = horizon_validity.get(h)
                if isinstance(hv, dict) and not bool(hv.get("valid", False)):
                    continue

            t = targets.get(h)
            if not isinstance(t, dict):
                continue

            exp = safe_float(t.get("expected_return", 0.0))
            score = safe_float(t.get("score", 0.0))
            conf = safe_float(t.get("confidence", 0.5))
            no_sig = bool(t.get("no_signal", False))

            row = {
                "symbol": sym,
                "name": block.get("name"),
                "sector": block.get("sector"),
                "price": safe_float(block.get("price", 0.0)),
                "entry_close": safe_float(block.get("entry_close", safe_float(block.get("price", 0.0)))),
                "entry_date": block.get("entry_date"),
                "horizon": h,
                "expected_return": float(exp),
                "target_price": t.get("target_price"),
                "confidence": float(conf),
                "score": float(score),
                "direction": t.get("direction"),
                "no_signal": bool(no_sig),
            }

            if chosen is None:
                chosen = row
            else:
                # pick "better" based on score direction
                if bearish:
                    if float(row["score"]) < float(chosen["score"]):
                        chosen = row
                else:
                    if float(row["score"]) > float(chosen["score"]):
                        chosen = row

        if chosen is not None:
            best_rows.append(chosen)

    # Stable ranking: symbol tiebreak
    best_rows.sort(key=lambda r: str(r.get("symbol", "")))
    best_rows.sort(key=lambda r: safe_float(r.get("confidence", 0.0)), reverse=True)
    best_rows.sort(key=lambda r: abs(safe_float(r.get("expected_return", 0.0))), reverse=True)
    best_rows.sort(key=lambda r: safe_float(r.get("score", 0.0)), reverse=(not bearish))

    out = best_rows[:limit]
    for i, r in enumerate(out, start=1):
        r["rank"] = i
    return out


# ---------------------------------------------------------------------
# Ranked outputs (robust tiebreaks + Step 3 validity)
# ---------------------------------------------------------------------
def _build_ranked(
    payload_symbols: Dict[str, Any],
    horizon: str,
    limit: int = 200,
    *,
    bearish: bool = False,
    horizon_validity: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Deterministic backend-owned ranking.
    Returns [] if horizon is invalid (Step 3).
    """
    if isinstance(horizon_validity, dict):
        hv = horizon_validity.get(horizon)
        if isinstance(hv, dict) and not bool(hv.get("valid", False)):
            return []

    # Extra safety: if the *entire universe* is no-signal for this horizon, return [].
    if not _universe_has_signal(payload_symbols, horizon):
        return []

    rows: List[Dict[str, Any]] = []

    # UI sanity: never mix LONG and SHORT in the same ranked list.
    # Bullish list contains ONLY meaningful positive expected_return.
    # Bearish list contains ONLY meaningful negative expected_return.
    thr = float(NO_SIGNAL_THR.get(horizon, 0.0) or 0.0)

    for sym, block in payload_symbols.items():
        if not isinstance(block, dict):
            continue
        targets = block.get("targets") or {}
        t = targets.get(horizon)
        if not isinstance(t, dict):
            continue

        score = safe_float(t.get("score", 0.0))
        conf = safe_float(t.get("confidence", 0.5))
        exp = safe_float(t.get("expected_return", 0.0))
        no_sig = bool(t.get("no_signal", False))

        # Direction split (and ignore no-signal rows for ranked lists)
        if no_sig:
            continue
        if bearish:
            if exp >= -max(thr, EPS_RET):
                continue
        else:
            if exp <= max(thr, EPS_RET):
                continue

        ev = float(exp) * float(conf)

        rows.append({
            "symbol": sym,
            "name": block.get("name"),
            "sector": block.get("sector"),
            "price": safe_float(block.get("price", 0.0)),
            "entry_close": safe_float(block.get("entry_close", safe_float(block.get("price", 0.0)))),
            "entry_date": block.get("entry_date"),
            "expected_return": float(exp),
            "ev": float(ev),
            "target_price": t.get("target_price"),
            "confidence": float(conf),
            "score": float(score),
            "direction": t.get("direction"),
            "no_signal": bool(no_sig),
        })

    # Stable multi-key sort (python sort is stable)
    # Primary:
    #   - bullish: EV desc (largest positive conf*return)
    #   - bearish: EV asc (most negative conf*return)
    # Secondary:
    #   - score (kept for backward comparability)
    #   - abs(expected_return)
    #   - confidence
    #   - symbol
    rows.sort(key=lambda r: str(r.get("symbol", "")))
    rows.sort(key=lambda r: safe_float(r.get("confidence", 0.0)), reverse=True)
    rows.sort(key=lambda r: abs(safe_float(r.get("expected_return", 0.0))), reverse=True)
    rows.sort(key=lambda r: safe_float(r.get("score", 0.0)), reverse=(not bearish))
    rows.sort(key=lambda r: safe_float(r.get("ev", 0.0)), reverse=(not bearish))

    out = rows[:limit]
    for i, r in enumerate(out, start=1):
        r["rank"] = i
    return out


def _append_ledger(
    run_ts: str,
    symbols_payload: Dict[str, Any],
    *,
    horizon_validity: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Step 3: do NOT append invalid horizons to ledger.
    This prevents polluted calibration / accuracy metrics.
    Includes retry logic for transient file system failures.
    """
    lines: List[str] = []

    for sym, block in symbols_payload.items():
        if not isinstance(block, dict):
            continue

        entry_close = safe_float(block.get("entry_close", safe_float(block.get("price", 0.0))))
        entry_date = block.get("entry_date")

        targets = block.get("targets") or {}
        if not isinstance(targets, dict):
            continue

        for h, t in targets.items():
            if not isinstance(t, dict):
                continue

            # validity gate
            if isinstance(horizon_validity, dict):
                hv = horizon_validity.get(h)
                if isinstance(hv, dict) and not bool(hv.get("valid", False)):
                    continue

            pred_ret = safe_float(t.get("expected_return", 0.0))

            line_obj = {
                "run_ts": run_ts,
                "symbol": sym,
                "horizon": h,
                "entry_date": entry_date,
                "entry_close": float(entry_close),
                "predicted_return": float(pred_ret),
                "ev": float(pred_ret) * safe_float(t.get("confidence", 0.5)),
                "confidence": safe_float(t.get("confidence", 0.5)),
                "score": safe_float(t.get("score", 0.0)) if "score" in t else None,
                "direction": t.get("direction"),
                "target_price": t.get("target_price"),
                "no_signal": bool(t.get("no_signal", False)),
                "no_signal_threshold": float(t.get("no_signal_threshold", 0.0) or 0.0),
            }
            lines.append(json.dumps(line_obj))

    if not lines:
        return 0

    # Check disk space before writing
    if not check_disk_space(LEDGER_PATH):
        log("[prediction_logger] ❌ Insufficient disk space")
        raise IOError("Disk full: cannot write prediction ledger")

    # Retry logic for transient failures
    max_attempts = 3
    sleep_secs = 1.0
    last_error = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            with LEDGER_PATH.open("a", encoding="utf-8") as f:
                for ln in lines:
                    f.write(ln + "\n")
            
            if attempt > 1:
                log(f"[prediction_logger] ✓ Ledger write succeeded on attempt {attempt}/{max_attempts}")
            return len(lines)
            
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                log(f"[prediction_logger] ⚠️ Ledger write failed (attempt {attempt}/{max_attempts}), retrying: {e}")
                import time
                time.sleep(sleep_secs)
            else:
                log(f"[prediction_logger] ❌ Failed writing ledger after {max_attempts} attempts: {e}")
    
    # If all retries failed, raise the last exception
    if last_error:
        raise last_error
    
    return 0


# ---------------------------------------------------------------------
# MAIN ENTRY
# ---------------------------------------------------------------------
def log_predictions(
    as_of_date: Optional[str] = None,
    save_to_file: bool = True,
    *,
    append_ledger: bool = True,
    apply_policy_first: bool = True,
    write_timestamped: bool = True,
    run_ts_override: Optional[str] = None,
    entry_date_override: Optional[str] = None,
) -> Dict[str, Any]:
    log("[prediction_logger] 🚀 Regression prediction logging starting…")

    run_ts = str(run_ts_override) if run_ts_override else _now_iso()
    entry_date = str(entry_date_override) if entry_date_override else _today_ymd()

    rolling = _read_rolling() or {}
    if not rolling:
        log("[prediction_logger] ⚠️ No rolling available.")
        return {"status": "no_rolling"}

    # Ensure predictions exist
    missing_preds = 0
    for sym, node in rolling.items():
        if str(sym).startswith("_"):
            continue
        if not isinstance(node, dict):
            continue
        preds = node.get("predictions")
        if not isinstance(preds, dict) or not preds:
            missing_preds += 1

    backfilled = False
    if missing_preds > 0:
        log(f"[prediction_logger] ℹ️ {missing_preds} symbols missing predictions — running predict_all() to backfill.")
        preds_by_symbol = predict_all(rolling, write_diagnostics=False) or {}
        if preds_by_symbol:
            for sym, node in rolling.items():
                if str(sym).startswith("_"):
                    continue
                if not isinstance(node, dict):
                    continue
                su = str(sym).upper()
                if su in preds_by_symbol:
                    node["predictions"] = preds_by_symbol[su]
                    rolling[sym] = node
            try:
                save_rolling(rolling)
                backfilled = True
            except Exception as e:
                log(f"[prediction_logger] ⚠️ save_rolling failed (continuing): {e}")
        else:
            log("[prediction_logger] ⚠️ predict_all() returned empty — continuing with whatever exists.")

    # Apply policy (optional)
    if apply_policy_first:
        try:
            apply_policy()
        except Exception as e:
            log(f"[prediction_logger] ⚠️ apply_policy failed (continuing): {e}")

    rolling = _read_rolling() or rolling

    summary_per_symbol: Dict[str, Any] = {}

    for sym, node in rolling.items():
        if str(sym).startswith("_"):
            continue
        if not isinstance(node, dict):
            continue

        sym_u = str(sym).upper()

        pred = node.get("predictions", {}) or {}
        pol = node.get("policy", {}) or {}

        clean = _extract_clean(pred if isinstance(pred, dict) else {}, pol if isinstance(pol, dict) else {})

        price = safe_float(node.get("close", None))
        if price == 0.0:
            price = safe_float(node.get("price", None))

        if price == 0.0:
            hist = node.get("history") or []
            if isinstance(hist, list) and hist:
                last = hist[-1]
                if isinstance(last, dict):
                    price = safe_float(last.get("close", 0.0))

        name = node.get("name")
        sector = node.get("sector") or (node.get("fundamentals") or {}).get("sector")

        targets: Dict[str, Any] = {}

        for h in HORIZONS:
            blk = clean.get(h)
            if isinstance(blk, dict) and bool(blk.get("valid", True)):
                targets[h] = _make_target(h, price, blk)

        # Synthetic 2w: avg of 1w + 4w (includes score)
        if "1w" in targets and "4w" in targets:
            w1 = targets["1w"]
            w4 = targets["4w"]

            exp2 = 0.5 * (safe_float(w1.get("expected_return")) + safe_float(w4.get("expected_return")))
            conf2 = 0.5 * (safe_float(w1.get("confidence")) + safe_float(w4.get("confidence")))
            score2 = 0.5 * (safe_float(w1.get("score")) + safe_float(w4.get("score")))

            t_price = price * (1.0 + exp2) if price else None
            direction = "up" if exp2 > 0 else "down" if exp2 < 0 else "flat"

            targets["2w"] = {
                "expected_return": float(exp2),
                "target_price": float(t_price) if t_price is not None else None,
                "confidence": float(conf2),
                "direction": direction,
                "score": float(score2),
                "no_signal": bool(_no_signal("2w", exp2)),
                "no_signal_threshold": float(NO_SIGNAL_THR.get("2w", 0.0) or 0.0),
            }

        xh = _compute_xh_disagreement(clean)
        disagreement_ui = {
            "max_disagreement": abs(
                safe_float(xh.get("short_avg_score", 0.0)) - safe_float(xh.get("long_avg_score", 0.0))
            ),
            "label": xh.get("status"),
            "pattern": xh.get("pattern"),
        }

        news = node.get("news") or {}
        social = node.get("social") or {}
        sentiment = {
            "news_sentiment": (news.get("sentiment") if isinstance(news, dict) else None),
            "news_impact": (news.get("impact_score") if isinstance(news, dict) else None),
            "social_sentiment": (social.get("sentiment") if isinstance(social, dict) else None),
            "social_heat": (social.get("heat_score") if isinstance(social, dict) else None),
        }

        summary_per_symbol[sym_u] = {
            "symbol": sym_u,
            "name": name,
            "sector": sector,
            "price": float(price),
            "entry_close": float(price),
            "entry_date": entry_date,
            "targets": targets,
            "disagreement": disagreement_ui,
            "sentiment": sentiment,
            "policy": clean.get("policy"),
        }

    # Step 3: compute horizon validity before ranked/top_moves/ledger
    horizon_validity = _compute_horizon_validity(summary_per_symbol)
    valid_horizons = [h for h, v in horizon_validity.items() if isinstance(v, dict) and v.get("valid")]

    top: Dict[str, Any] = {}
    for h in ("1d", "3d", "1w", "2w", "4w", "13w", "26w", "52w"):
        hv = horizon_validity.get(h, {})
        if isinstance(hv, dict) and not bool(hv.get("valid", False)):
            top[h] = {"top_bullish": [], "top_bearish": []}
        else:
            top[h] = _top_moves(summary_per_symbol, h, 20)

    ranked: Dict[str, Any] = {}
    ranked_bearish: Dict[str, Any] = {}
    for h in ("1d", "3d", "1w", "2w", "4w", "13w", "26w", "52w"):
        ranked[h] = _build_ranked(summary_per_symbol, h, limit=250, bearish=False, horizon_validity=horizon_validity)
        ranked_bearish[h] = _build_ranked(summary_per_symbol, h, limit=250, bearish=True, horizon_validity=horizon_validity)

    # Grouped leaderboards (SHORT vs LONG). Never mix longs and shorts in the same list.
    ranked_short = _merge_group_best(summary_per_symbol, SHORT_TERM_HORIZONS, bearish=False, limit=50, horizon_validity=horizon_validity)
    ranked_short_bearish = _merge_group_best(summary_per_symbol, SHORT_TERM_HORIZONS, bearish=True, limit=50, horizon_validity=horizon_validity)
    ranked_long = _merge_group_best(summary_per_symbol, LONG_TERM_HORIZONS, bearish=False, limit=50, horizon_validity=horizon_validity)
    ranked_long_bearish = _merge_group_best(summary_per_symbol, LONG_TERM_HORIZONS, bearish=True, limit=50, horizon_validity=horizon_validity)

    payload = {
        "timestamp": run_ts,
        "symbols": summary_per_symbol,
        "ranked": ranked,
        "ranked_bearish": ranked_bearish,
        "ranked_short": ranked_short,
        "ranked_short_bearish": ranked_short_bearish,
        "ranked_long": ranked_long,
        "ranked_long_bearish": ranked_long_bearish,
        "top_moves": top,
        "meta": {
            "symbols_total": int(len(summary_per_symbol)),
            "backfilled_predictions": bool(backfilled),
            "ledger_path": str(LEDGER_PATH),
            "no_signal_thresholds": NO_SIGNAL_THR,
            "append_ledger": bool(append_ledger),
            "apply_policy_first": bool(apply_policy_first),
            # Step 3 integrity metadata:
            "horizon_validity": horizon_validity,
            "valid_horizons": valid_horizons,
            "valid_horizons_count": int(len(valid_horizons)),
            "short_term_horizons": list(SHORT_TERM_HORIZONS),
            "long_term_horizons": list(LONG_TERM_HORIZONS),
        },
    }

    if append_ledger:
        ledger_rows = _append_ledger(run_ts, summary_per_symbol, horizon_validity=horizon_validity)
        payload["meta"]["ledger_rows_appended"] = int(ledger_rows)
    else:
        payload["meta"]["ledger_rows_appended"] = 0

    if save_to_file:
        latest_path = LOG_DIR / "latest_predictions.json"
        ts_path = LOG_DIR / f"predictions_{_file_timestamp()}.json"

        # Check disk space before writing
        if not check_disk_space(latest_path):
            log("[prediction_logger] ❌ Insufficient disk space")
            raise IOError("Disk full: cannot write prediction files")

        # Retry logic for transient failures
        max_attempts = 3
        sleep_secs = 1.0
        last_error = None
        
        for attempt in range(1, max_attempts + 1):
            try:
                txt = json.dumps(payload, indent=2)
                latest_path.write_text(txt, encoding="utf-8")
                log(f"[prediction_logger] 💾 Updated → {latest_path}")

                if write_timestamped:
                    ts_path.write_text(txt, encoding="utf-8")
                    log(f"[prediction_logger] 💾 Saved → {ts_path}")
                
                if attempt > 1:
                    log(f"[prediction_logger] ✓ File write succeeded on attempt {attempt}/{max_attempts}")
                break  # Success, exit retry loop
                
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    log(f"[prediction_logger] ⚠️ File write failed (attempt {attempt}/{max_attempts}), retrying: {e}")
                    import time
                    time.sleep(sleep_secs)
                else:
                    log(f"[prediction_logger] ❌ Failed writing logs after {max_attempts} attempts: {e}")
        
        # If all retries failed and we have an error, log it but continue (non-critical)
        if last_error and attempt == max_attempts:
            log(f"[prediction_logger] ⚠️ Continuing despite write failure: {last_error}")

    return payload


if __name__ == "__main__":
    out = log_predictions()
    print(json.dumps(out, indent=2))