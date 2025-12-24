# dt_backend/ml/signals_rank_builder.py — v3.0 (EXECUTION-WIRED + SAFE)
"""
Build intraday signals + ranks from `execution_dt` in rolling.

This is the bridge between the policy/execution layer and external
consumers (front-end, rank fetcher, bot runners). It reads:

    rolling[sym]["execution_dt"]

and produces compact JSON artifacts under DT_PATHS["signals_intraday_*"].

Why v3.0
--------
Your dt_backend schema evolved:

    features_dt → predictions_dt → policy_dt → execution_dt

So ranking must be driven by execution_dt, not policy_dt. This file:
  ✅ ranks by execution conviction (size * confidence_adj)
  ✅ preserves safe defaults
  ✅ never crashes on missing/malformed nodes
  ✅ writes stable artifacts (json + gz rank payload)

Artifacts
---------
• intraday_predictions.json            (UI-friendly rows)
• prediction_rank_fetch.json.gz        (compact rank list for schedulers/bots)

Notes
-----
- "score" is a signed score:
    BUY  => + (size * confidence_adj)
    SELL => - (size * confidence_adj)
  Rank order uses abs(score) desc.
- FLAT signals are omitted by default.
"""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


# Prefer explicit imports; keep fallbacks for resilience
try:
    from dt_backend.core.config_dt import DT_PATHS  # type: ignore
except Exception:  # pragma: no cover
    DT_PATHS: Dict[str, Any] = {}

try:
    from dt_backend.core.data_pipeline_dt import _read_rolling, log  # type: ignore
except Exception:  # pragma: no cover
    def log(msg: str) -> None:
        print(msg, flush=True)

    def _read_rolling() -> Dict[str, Any]:
        return {}


def _signals_dir() -> Path:
    p = DT_PATHS.get("signals_intraday_predictions_dir")
    if p:
        return Path(p)
    return Path("ml_data_dt") / "signals" / "predictions"


def _ranks_dir() -> Path:
    p = DT_PATHS.get("signals_intraday_ranks_dir")
    if p:
        return Path(p)
    return Path("ml_data_dt") / "signals" / "ranks"


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


def _safe_bool(x: Any) -> bool:
    try:
        return bool(x)
    except Exception:
        return False


def _safe_str(x: Any, default: str = "") -> str:
    try:
        s = str(x)
        return s
    except Exception:
        return default


def _execution_to_row(sym: str, node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert rolling node into a ranked row from execution_dt.
    Returns None if not eligible (missing execution_dt or FLAT/invalid).
    """
    exec_dt = (node or {}).get("execution_dt") or {}
    if not isinstance(exec_dt, dict) or not exec_dt:
        return None

    side = _safe_str(exec_dt.get("side"), "FLAT").upper()
    if side not in {"BUY", "SELL", "FLAT"}:
        side = "FLAT"

    size = _safe_float(exec_dt.get("size"), 0.0)
    conf_adj = _safe_float(exec_dt.get("confidence_adj"), 0.0)

    # Clamp to sane ranges (defensive; upstream should already do this)
    size = max(0.0, min(1.0, size))
    conf_adj = max(0.0, min(1.0, conf_adj))

    cooldown = _safe_bool(exec_dt.get("cooldown"))
    valid_until = _safe_str(exec_dt.get("valid_until"), "")
    ts = _safe_str(exec_dt.get("ts"), "")

    # Only active signals should flow into the rank list
    if side == "FLAT" or size <= 0.0 or conf_adj <= 0.0 or cooldown:
        return None

    # Signed score (direction preserved), magnitude used for ranking
    mag = size * conf_adj
    score = mag if side == "BUY" else -mag

    # Pull a bit of optional context for UI/debug (safe if missing)
    pol = (node or {}).get("policy_dt") or {}
    if not isinstance(pol, dict):
        pol = {}

    ctx = (node or {}).get("context_dt") or {}
    if not isinstance(ctx, dict):
        ctx = {}

    row: Dict[str, Any] = {
        "symbol": sym,
        "side": side,
        "score": float(score),
        "magnitude": float(mag),
        "size": float(size),
        "confidence_adj": float(conf_adj),
        "cooldown": bool(cooldown),
        "valid_until": valid_until,
        "ts": ts,

        # Optional UI/debug fields (do not affect ranking)
        "policy_intent": _safe_str(pol.get("intent"), "").upper(),
        "policy_confidence": _safe_float(pol.get("confidence"), 0.0),
        "vol_bucket": _safe_str(ctx.get("vol_bucket"), ""),
        "intraday_trend": _safe_str(ctx.get("intraday_trend"), ""),
        "regime": _safe_str(((node or {}).get("_GLOBAL_DT") or {}).get("regime", ""), ""),
    }
    return row


def build_intraday_signals(top_n: int = 200) -> Dict[str, Any]:
    """
    Aggregate execution_dt into ranked signals and write JSON artifacts.

    Ranking:
      • primary = abs(score) desc (score is signed by side)
      • tie-break = magnitude desc
      • tie-break = symbol asc

    Returns:
      {status, total, top_n, predictions_path, ranks_path}
    """
    rolling = _read_rolling()
    if not rolling:
        log("[dt_signals] ⚠️ rolling empty, nothing to build.")
        return {"status": "empty", "total": 0, "top_n": 0}

    rows: List[Dict[str, Any]] = []

    for sym, node in rolling.items():
        try:
            if str(sym).startswith("_"):
                continue
            if not isinstance(node, dict):
                continue
            row = _execution_to_row(str(sym), node)
            if row is None:
                continue
            rows.append(row)
        except Exception:
            # Never let one bad symbol kill the build
            continue

    if not rows:
        log("[dt_signals] ⚠️ no eligible execution_dt rows found (all FLAT/cooldown/missing).")
        # Still write empty artifacts for downstream robustness
        sig_dir = _signals_dir()
        ranks_dir = _ranks_dir()
        sig_dir.mkdir(parents=True, exist_ok=True)
        ranks_dir.mkdir(parents=True, exist_ok=True)

        predictions_path = sig_dir / "intraday_predictions.json"
        ranks_path = ranks_dir / "prediction_rank_fetch.json.gz"

        try:
            with open(predictions_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"[dt_signals] ⚠️ failed to write empty predictions: {e}")

        try:
            with gzip.open(ranks_path, "wt", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False)
        except Exception as e:
            log(f"[dt_signals] ⚠️ failed to write empty ranks: {e}")

        return {
            "status": "no_rows",
            "total": 0,
            "top_n": 0,
            "predictions_path": str(predictions_path),
            "ranks_path": str(ranks_path),
        }

    # Rank by absolute conviction, keep direction in signed score
    rows.sort(
        key=lambda r: (
            abs(_safe_float(r.get("score"), 0.0)),
            _safe_float(r.get("magnitude"), 0.0),
            str(r.get("symbol", "")),
        ),
        reverse=True,
    )

    top = rows[: max(0, int(top_n))]

    sig_dir = _signals_dir()
    ranks_dir = _ranks_dir()
    sig_dir.mkdir(parents=True, exist_ok=True)
    ranks_dir.mkdir(parents=True, exist_ok=True)

    predictions_path = sig_dir / "intraday_predictions.json"
    ranks_path = ranks_dir / "prediction_rank_fetch.json.gz"

    # Write UI-friendly predictions artifact
    try:
        with open(predictions_path, "w", encoding="utf-8") as f:
            json.dump(top, f, ensure_ascii=False, indent=2)
        log(f"[dt_signals] ✅ wrote top-{len(top)} execution signals → {predictions_path}")
    except Exception as e:
        log(f"[dt_signals] ⚠️ failed to write predictions: {e}")

    # Write compact rank payload (bot/scheduler friendly)
    try:
        rank_payload = [
            {
                "symbol": row["symbol"],
                "side": row["side"],
                "score": float(row["score"]),
                "size": float(row["size"]),
                "confidence_adj": float(row["confidence_adj"]),
                "valid_until": row.get("valid_until"),
            }
            for row in top
        ]
        with gzip.open(ranks_path, "wt", encoding="utf-8") as f:
            json.dump(rank_payload, f, ensure_ascii=False)
        log(f"[dt_signals] ✅ wrote rank fetch payload → {ranks_path}")
    except Exception as e:
        log(f"[dt_signals] ⚠️ failed to write ranks: {e}")

    return {
        "status": "ok",
        "total": len(rows),
        "top_n": len(top),
        "predictions_path": str(predictions_path),
        "ranks_path": str(ranks_path),
    }


def main() -> None:
    out = build_intraday_signals()
    log(f"[dt_signals] done: {out}")


if __name__ == "__main__":
    main()