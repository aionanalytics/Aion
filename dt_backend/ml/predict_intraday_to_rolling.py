# dt_backend/ml/predict_intraday_to_rolling.py — v1.0
"""
Attach intraday model predictions to rolling in a stable schema.

Writes:
  rolling[sym]["predictions_dt"] = {
      "label": "BUY"|"HOLD"|"SELL",
      "proba": {"BUY": float, "HOLD": float, "SELL": float},
      "confidence": float,   # max(proba)
      "ts": "...Z",
      "meta": {"source": "...", "models": {...}}
  }

This module is intentionally small and "glue-like":
  • reads rolling/features_dt
  • batches to pandas
  • calls score_intraday_batch
  • writes back predictions_dt
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import pandas as pd

from dt_backend.core.data_pipeline_dt import _read_rolling, save_rolling, ensure_symbol_node, log
from dt_backend.ml.ai_model_intraday import score_intraday_batch, load_intraday_models


_ALLOWED = ("BUY", "HOLD", "SELL")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _normalize_proba(row: Dict[str, Any]) -> Dict[str, float]:
    p = {k: _safe_float(row.get(k, 0.0), 0.0) for k in _ALLOWED}
    s = sum(p.values())
    if s > 0:
        p = {k: v / s for k, v in p.items()}
    else:
        p = {"BUY": 0.0, "HOLD": 1.0, "SELL": 0.0}
    return p


def _features_to_row(sym: str, feats: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert features_dt dict into a flat row. Drops non-numeric fields.
    We keep it permissive: if a value can't be float-cast, it becomes 0.0
    (LightGBM-friendly).
    """
    row: Dict[str, Any] = {"symbol": sym}
    for k, v in (feats or {}).items():
        if k in {"ts", "timestamp", "symbol"}:
            continue
        try:
            row[k] = float(v)
        except Exception:
            row[k] = 0.0
    return row


def attach_intraday_predictions(
    max_symbols: int | None = None,
) -> Dict[str, Any]:
    """
    Main entrypoint.

    Returns:
      {status, symbols_seen, predicted, missing_features, ts}
    """
    rolling = _read_rolling()
    if not rolling:
        log("[dt_predict] ⚠️ rolling empty.")
        return {"status": "empty", "symbols_seen": 0, "predicted": 0}

    syms = [s for s in rolling.keys() if not str(s).startswith("_")]
    syms.sort()
    if max_symbols is not None:
        syms = syms[: max(0, int(max_symbols))]

    rows: List[Dict[str, Any]] = []
    used_syms: List[str] = []
    missing_features = 0

    for sym in syms:
        node = ensure_symbol_node(rolling, sym)
        feats = node.get("features_dt") or {}
        if not isinstance(feats, dict) or not feats:
            missing_features += 1
            continue
        rows.append(_features_to_row(sym, feats))
        used_syms.append(sym)

    if not rows:
        log("[dt_predict] ⚠️ no features_dt rows to score.")
        return {
            "status": "no_rows",
            "symbols_seen": len(syms),
            "predicted": 0,
            "missing_features": missing_features,
            "ts": _utc_now_iso(),
        }

    df = pd.DataFrame.from_records(rows).set_index("symbol")

    # Load models once (faster + consistent)
    models = load_intraday_models()

    proba_df, label_series = score_intraday_batch(df, models=models)

    predicted = 0
    ts = _utc_now_iso()

    for sym in used_syms:
        node = ensure_symbol_node(rolling, sym)

        if sym not in proba_df.index:
            continue

        row = proba_df.loc[sym].to_dict()
        proba = _normalize_proba(row)

        # label source-of-truth: derived from proba to avoid order mismatches
        label = max(proba.items(), key=lambda kv: kv[1])[0]
        if label not in _ALLOWED:
            label = "HOLD"

        conf = float(max(proba.values()))

        node["predictions_dt"] = {
            "label": label,
            "proba": proba,
            "confidence": conf,
            "ts": ts,
            "meta": {
                "source": "score_intraday_batch",
                "lgb_active": bool(models.lgb is not None),
                "lstm_active": bool(models.lstm is not None),
                "transf_active": bool(models.transf is not None),
            },
        }
        rolling[sym] = node
        predicted += 1

    save_rolling(rolling)
    log(f"[dt_predict] ✅ wrote predictions_dt for {predicted} symbols.")
    return {
        "status": "ok",
        "symbols_seen": len(syms),
        "predicted": predicted,
        "missing_features": missing_features,
        "ts": ts,
    }


if __name__ == "__main__":
    out = attach_intraday_predictions()
    log(f"[dt_predict] done: {out}")