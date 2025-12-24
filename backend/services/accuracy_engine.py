"""
accuracy_engine.py — v1.2.1 (Trading-Day Step Alignment + Sorted History + Calibration Map + HIT Buckets)

Reads:
  - nightly_predictions/predictions_ledger.jsonl  (durable prediction log)
  - rolling history from _read_rolling()

Computes per horizon, rolling windows:
  - directional_accuracy
  - hit_rate (direction correct AND magnitude not tiny)
  - MAE / RMSE on returns
  - confidence bucket hit-rates  ✅ now uses HIT definition (2.5 fix)

Writes:
  - metrics/accuracy/accuracy_<horizon>_<window>.json
  - metrics/accuracy/accuracy_latest.json (summary)
  - metrics/accuracy/confidence_calibration.json
"""

from __future__ import annotations

import json
import math
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import _read_rolling, log, safe_float

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
LOG_DIR: Path = PATHS["nightly_predictions"]
LOG_DIR.mkdir(parents=True, exist_ok=True)

LEDGER_PATH: Path = LOG_DIR / "predictions_ledger.jsonl"

ACCURACY_DIR: Path = PATHS.get("accuracy", Path("ml_data") / "metrics" / "accuracy")
ACCURACY_DIR.mkdir(parents=True, exist_ok=True)

CALIBRATION_PATH: Path = ACCURACY_DIR / "confidence_calibration.json"

SECTOR_ACCURACY_PATH: Path = PATHS.get('ml_data', Path('ml_data')) / 'metrics' / 'accuracy_by_sector.json'

def _build_symbol_to_sector_map() -> dict[str, str]:
    """Infer sector label per symbol from latest features snapshot (sector_* one-hot)."""
    try:
        from backend.core.ai_model.core_training import _load_feature_list
        from backend.core.ai_model.core_training import _read_latest_snapshot_any
        feat = _load_feature_list()
        feature_cols = feat.get('feature_columns', [])
        df = _read_latest_snapshot_any()
        if df is None or df.empty:
            return {}
        if 'symbol' in df.columns:
            df = df.set_index('symbol')
        df.index = df.index.astype(str).str.upper()
        sector_cols = [c for c in df.columns if isinstance(c, str) and c.startswith('sector_')]
        if not sector_cols:
            return {}
        mat = df[sector_cols].to_numpy(dtype=float, copy=False)
        idx = mat.argmax(axis=1)
        labels = [sector_cols[int(i)][len('sector_'):] for i in idx]
        return {sym: str(lbl).upper().strip() if lbl else 'UNKNOWN' for sym, lbl in zip(df.index.tolist(), labels)}
    except Exception:
        return {}

# Trading-day step mapping (must match ML dataset target construction)
HORIZON_STEPS: Dict[str, int] = {
    "1d": 1,
    "3d": 3,
    "1w": 5,
    "2w": 10,
    "4w": 20,
    "13w": 65,
    "26w": 130,
    "52w": 260,
}

WINDOWS_DAYS = [7, 30, 90]
CAL_WINDOW_DAYS = 30  # calibration map uses 30d by default

# Magnitude-aware hit definition:
# A "hit" happens when direction is correct AND price moved enough in that direction.
# With only close-to-close returns (no intrahorizon OHLC path), we approximate
# "reaches target" by requiring realized magnitude >= HIT_MAG_FRACTION * |predicted|.
HIT_MAG_FRACTION = 0.50

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _parse_ymd(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _today() -> date:
    return datetime.now(TIMEZONE).date()


def _sign(x: float, tol: float = 1e-12) -> int:
    if x > tol:
        return 1
    if x < -tol:
        return -1
    return 0


def _bucket_label(lo: float, hi: float) -> str:
    return f"{lo:.2f}-{hi:.2f}"


def _confidence_buckets() -> List[Tuple[float, float]]:
    buckets: List[Tuple[float, float]] = []
    lo = 0.50
    while lo < 0.95:
        hi = lo + 0.05
        buckets.append((lo, hi))
        lo = hi
    buckets.append((0.95, 1.01))
    return buckets


def _extract_history_series(node: Dict[str, Any]) -> Tuple[List[str], List[float]]:
    """
    Extract a time-ordered list of dates and closes from rolling node history.
    Accepts common keys:
      - 'date', 'asof_date', 'timestamp'
      - 'close' (preferred), fallback 'price'
    Ensures sort by date ascending.
    """
    hist = node.get("history") or []
    if not isinstance(hist, list) or not hist:
        return [], []

    tmp: List[Tuple[str, float]] = []
    for row in hist:
        if not isinstance(row, dict):
            continue

        d = row.get("date") or row.get("asof_date") or row.get("timestamp")
        if d is None:
            continue

        c = row.get("close")
        if c is None:
            c = row.get("price")
        c = safe_float(c)
        if c == 0.0:
            continue

        d10 = str(d)[:10]
        if _parse_ymd(d10) is None:
            continue

        tmp.append((d10, float(c)))

    if not tmp:
        return [], []

    # Sort by date, then de-dupe keeping last occurrence per date
    tmp.sort(key=lambda x: x[0])
    dedup: Dict[str, float] = {}
    for d10, c in tmp:
        dedup[d10] = c

    dates = sorted(dedup.keys())
    closes = [float(dedup[d]) for d in dates]
    return dates, closes


def _find_entry_index(dates: List[str], entry_ymd: str) -> Optional[int]:
    """
    Find exact match first; if not found, find closest date <= entry_ymd.
    """
    if not dates:
        return None

    try:
        return dates.index(entry_ymd)
    except Exception:
        pass

    for i in range(len(dates) - 1, -1, -1):
        if dates[i] <= entry_ymd:
            return i
    return None


def _realized_return_from_history(
    node: Dict[str, Any],
    entry_date: str,
    entry_close: float,
    horizon: str,
) -> Optional[Tuple[float, float]]:
    """
    Uses history index stepping (trading-day approximation).
    Returns (exit_close, realized_return) or None if insufficient future data.
    """
    steps = HORIZON_STEPS.get(horizon)
    if steps is None:
        return None

    dates, closes = _extract_history_series(node)
    if not dates or len(closes) != len(dates):
        return None

    entry_idx = _find_entry_index(dates, entry_date)
    if entry_idx is None:
        return None

    exit_idx = entry_idx + steps
    if exit_idx >= len(closes):
        return None

    exit_close = closes[exit_idx]
    if not entry_close or entry_close == 0.0:
        return None

    realized = (exit_close / float(entry_close)) - 1.0
    return float(exit_close), float(realized)


def _read_ledger(max_lines: int = 2_000_000) -> List[Dict[str, Any]]:
    if not LEDGER_PATH.exists():
        return []

    out: List[Dict[str, Any]] = []
    try:
        with LEDGER_PATH.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:
                    continue
    except Exception as e:
        log(f"[accuracy_engine] ❌ Failed reading ledger: {e}")
        return []

    return out


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"[accuracy_engine] ❌ Failed writing {path}: {e}")


# ---------------------------------------------------------------------
# Main compute
# ---------------------------------------------------------------------
def compute_accuracy() -> Dict[str, Any]:
    log("[accuracy_engine] 🧪 Computing accuracy + calibration… (v1.2.1)")

    ledger = _read_ledger()
    if not ledger:
        log("[accuracy_engine] ⚠️ No ledger rows. Run prediction_logger first.")
        return {"status": "no_ledger"}

    rolling = _read_rolling() or {}
    if not rolling:
        log("[accuracy_engine] ⚠️ No rolling. Cannot compute realized returns.")
        return {"status": "no_rolling"}

    # Sector labels for per-sector accuracy (best-effort)
    symbol_to_sector = _build_symbol_to_sector_map()
    accuracy_by_sector: Dict[str, Dict[str, Any]] = {}

    rolling_map: Dict[str, Dict[str, Any]] = {}
    for sym, node in rolling.items():
        if str(sym).startswith("_"):
            continue
        if isinstance(node, dict):
            rolling_map[str(sym).upper()] = node

    today = _today()
    buckets = _confidence_buckets()

    by_h: Dict[str, List[Dict[str, Any]]] = {}
    for row in ledger:
        h = str(row.get("horizon") or "")
        if h not in HORIZON_STEPS:
            continue
        by_h.setdefault(h, []).append(row)

    outputs: Dict[str, Any] = {
        "status": "ok",
        "updated_at": datetime.now(TIMEZONE).isoformat(),
        "horizons": {},
    }

    calibration_payload: Dict[str, Any] = {
        "updated_at": datetime.now(TIMEZONE).isoformat(),
        "window_days": int(CAL_WINDOW_DAYS),
        "horizons": {},
    }

    for h, rows in by_h.items():
        realized_rows: List[Dict[str, Any]] = []

        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            node = rolling_map.get(sym)
            if node is None:
                continue

            entry_date = str(r.get("entry_date") or "")[:10]
            entry_dt = _parse_ymd(entry_date)
            if entry_dt is None:
                continue

            entry_close = safe_float(r.get("entry_close", 0.0))
            if entry_close == 0.0:
                continue

            realized = _realized_return_from_history(node, entry_date, entry_close, h)
            if realized is None:
                continue

            exit_close, real_ret = realized

            pred_ret = safe_float(r.get("predicted_return", 0.0))
            conf = safe_float(r.get("confidence", 0.0))
            score = r.get("score", None)

            realized_rows.append(
                {
                    "symbol": sym,
                    "entry_date": entry_date,
                    "entry_close": float(entry_close),
                    "exit_close": float(exit_close),
                    "predicted_return": float(pred_ret),
                    "realized_return": float(real_ret),
                    "confidence": float(conf),
                    "score": float(score) if score is not None else None,
                }
            )

        h_out: Dict[str, Any] = {
            "rows_total": len(rows),
            "rows_scored": len(realized_rows),
            "windows": {},
        }

        for w in WINDOWS_DAYS:
            cutoff = today - timedelta(days=int(w))
            window_rows = [
                rr
                for rr in realized_rows
                if (_parse_ymd(rr["entry_date"]) or date(1970, 1, 1)) >= cutoff
            ]

            if not window_rows:
                h_out["windows"][str(w)] = {"status": "no_data", "n": 0}
                continue

            dir_hits = 0
            abs_errs: List[float] = []
            sq_errs: List[float] = []

            # Hit-rate threshold: avoid counting tiny moves as meaningful
            if h in ("1d", "3d"):
                thr = 0.003
            elif h in ("1w", "2w"):
                thr = 0.008
            elif h == "4w":
                thr = 0.015
            else:
                thr = 0.025

            hit_rate_hits = 0
            hit_rate_total = 0

            sec_hits: Dict[str, int] = {}
            sec_total: Dict[str, int] = {}

            # Magnitude-aware calibration quality (Brier score)
            # Computed over the same set of "meaningful" predictions.
            brier_sum = 0.0
            brier_n = 0

            for rr in window_rows:
                pr = float(rr["predicted_return"])
                ar = float(rr["realized_return"])

                if _sign(pr) == _sign(ar) and _sign(pr) != 0:
                    dir_hits += 1

                err = ar - pr
                abs_errs.append(abs(err))
                sq_errs.append(err * err)

                # "Hit" definition (magnitude-aware):
                #  - meaningful predicted move (abs(pr) >= thr)
                #  - correct direction
                #  - realized magnitude reaches at least a fraction of predicted magnitude
                if abs(pr) >= thr:
                    hit_rate_total += 1
                    # sector-level hit tracking
                    sec = symbol_to_sector.get(str(rr.get('symbol','')).upper(), 'UNKNOWN')
                    sec_total[sec] = sec_total.get(sec, 0) + 1
                    ok_dir = (_sign(pr) == _sign(ar) and _sign(pr) != 0)
                    ok_mag = (abs(ar) >= (HIT_MAG_FRACTION * abs(pr)))
                    is_hit = bool(ok_dir and ok_mag)
                    if is_hit:
                        hit_rate_hits += 1
                        sec = symbol_to_sector.get(str(rr.get('symbol','')).upper(), 'UNKNOWN')
                        sec_hits[sec] = sec_hits.get(sec, 0) + 1

                    # Brier score uses probability forecast vs binary outcome
                    c = float(rr.get("confidence", 0.5))
                    brier_sum += (c - (1.0 if is_hit else 0.0)) ** 2
                    brier_n += 1

            n = len(window_rows)
            directional_accuracy = dir_hits / n if n else 0.0
            mae = sum(abs_errs) / n if n else 0.0
            rmse = math.sqrt(sum(sq_errs) / n) if n else 0.0
            hit_rate = (hit_rate_hits / hit_rate_total) if hit_rate_total else None
            brier = (brier_sum / brier_n) if brier_n else None

            # Persist sector-level hit-rate for confidence calibration (calibration window only)
            if int(w) == int(CAL_WINDOW_DAYS):
                for sec, tot in sec_total.items():
                    if tot <= 0:
                        continue
                    hits = int(sec_hits.get(sec, 0) or 0)
                    accuracy_by_sector.setdefault(str(sec).upper(), {})[h] = {
                        'hit_rate': float(hits / tot),
                        'n': int(tot),
                    }

            # ✅ 2.5 FIX: bucket hit-rates must match the SAME "hit" definition
            bucket_rows: List[Dict[str, Any]] = []
            for lo, hi in buckets:
                b = [rr for rr in window_rows if lo <= float(rr["confidence"]) < hi]
                if not b:
                    continue

                b_hits = 0
                b_total = 0
                for rr in b:
                    pr = float(rr["predicted_return"])
                    ar = float(rr["realized_return"])

                    if abs(pr) >= thr:
                        b_total += 1
                        if _sign(pr) == _sign(ar) and _sign(pr) != 0:
                            if abs(ar) >= (HIT_MAG_FRACTION * abs(pr)):
                                b_hits += 1

                # If bucket has no meaningful predicted moves, skip it (keeps map sane)
                if b_total <= 0:
                    continue

                bucket_rows.append(
                    {
                        "range": _bucket_label(lo, min(hi, 1.0)),
                        "hit_rate": float(b_hits / b_total) if b_total else 0.0,
                        "n": int(b_total),
                        "hit_threshold": float(thr),
                        "hit_mag_fraction": float(HIT_MAG_FRACTION),
                    }
                )

            out_payload = {
                "horizon": h,
                "window_days": int(w),
                "updated_at": datetime.now(TIMEZONE).isoformat(),
                "metrics": {
                    "directional_accuracy": float(directional_accuracy),
                    "hit_rate": float(hit_rate) if hit_rate is not None else None,
                    "brier": float(brier) if brier is not None else None,
                    "mae": float(mae),
                    "rmse": float(rmse),
                    "n": int(n),
                    "hit_threshold": float(thr),
                    "hit_mag_fraction": float(HIT_MAG_FRACTION),
                    "horizon_steps": int(HORIZON_STEPS.get(h, 0) or 0),
                },
                "confidence_buckets": bucket_rows,
            }

            file_path = ACCURACY_DIR / f"accuracy_{h}_{w}d.json"
            _write_json(file_path, out_payload)

            h_out["windows"][str(w)] = {
                "status": "ok",
                "file": str(file_path),
                "n": int(n),
                "directional_accuracy": float(directional_accuracy),
                "hit_rate": float(hit_rate) if hit_rate is not None else None,
                "mae": float(mae),
                "rmse": float(rmse),
                "confidence_buckets": bucket_rows,
            }

        outputs["horizons"][h] = h_out

        # calibration map from CAL_WINDOW_DAYS window
        wblk = h_out["windows"].get(str(int(CAL_WINDOW_DAYS)))
        if isinstance(wblk, dict) and wblk.get("status") == "ok":
            cb = wblk.get("confidence_buckets", [])
            if isinstance(cb, list) and cb:
                calibration_payload["horizons"][h] = {
                    "buckets": cb,
                    "source_window_days": int(CAL_WINDOW_DAYS),
                    "n": int(wblk.get("n", 0) or 0),
                    "horizon_steps": int(HORIZON_STEPS.get(h, 0) or 0),
                }

    latest_path = ACCURACY_DIR / "accuracy_latest.json"
    _write_json(latest_path, outputs)
    outputs["latest_file"] = str(latest_path)

    _write_json(CALIBRATION_PATH, calibration_payload)
    outputs["calibration_file"] = str(CALIBRATION_PATH)

    
    try:
        SECTOR_ACCURACY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECTOR_ACCURACY_PATH.write_text(json.dumps(accuracy_by_sector, indent=2), encoding='utf-8')
        log(f"[accuracy_engine] ✅ Wrote sector accuracy → {SECTOR_ACCURACY_PATH}")
    except Exception as e:
        log(f"[accuracy_engine] ⚠️ Failed writing sector accuracy: {e}")

    log(f"[accuracy_engine] ✅ Wrote accuracy artifacts → {ACCURACY_DIR}")
    return outputs


if __name__ == "__main__":
    out = compute_accuracy()
    print(json.dumps(out, indent=2))
