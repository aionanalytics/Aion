# backend/routers/bots_page_router.py

"""
Unified Bots Page API — AION Analytics

This router makes the frontend dead simple:
  - one call to fetch everything the Bots page needs
  - best-effort: sub-errors do NOT kill the whole response
  - also exposes a "tape" (signals + fills) by reading artifacts

Mount it twice:
  /api/bots/page
  /api/backend/bots/page
"""

from __future__ import annotations

import gzip
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter

try:
    from backend.core.config import PATHS
except Exception:
    # tolerate older builds
    from backend.config import PATHS  # type: ignore

try:
    from backend.core.config import TIMEZONE
except Exception:
    from settings import TIMEZONE  # type: ignore


router = APIRouter(tags=["bots-page"])


def _err(e: Exception) -> Dict[str, Any]:
    return {
        "error": f"{type(e).__name__}: {e}",
        "trace": traceback.format_exc()[-2000:],
    }


def _read_json(path: Path) -> Optional[Any]:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_json_gz(path: Path) -> Optional[Any]:
    try:
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _first_existing(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _walk_collect_trade_like(obj: Any, out: List[Dict[str, Any]]) -> None:
    """
    Crawl unknown JSON shapes and collect dicts that smell like fills/trades.
    We keep it permissive on purpose.
    """
    if isinstance(obj, dict):
        # "trade-ish" heuristic
        keys = set(obj.keys())
        if ("symbol" in keys) and (("side" in keys) or ("action" in keys)) and (("price" in keys) or ("fill_price" in keys)):
            out.append(obj)  # keep raw; UI will pick fields best-effort

        for v in obj.values():
            _walk_collect_trade_like(v, out)

    elif isinstance(obj, list):
        for v in obj:
            _walk_collect_trade_like(v, out)


def _normalize_fills(raw: List[Dict[str, Any]], limit: int = 50) -> List[Dict[str, Any]]:
    """
    Normalize some common key variants into a shallow fill object.
    Keep extra fields too.
    """
    normed: List[Dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol") or r.get("ticker")
        if not sym:
            continue

        side = (r.get("side") or r.get("action") or r.get("type") or "").upper() or None
        ts = r.get("ts") or r.get("time") or r.get("timestamp") or r.get("filled_at") or r.get("created_at")

        qty = r.get("qty") or r.get("quantity") or r.get("shares")
        price = r.get("price") or r.get("fill_price") or r.get("avg_price")

        item = {
            "ts": ts,
            "symbol": str(sym),
            "side": side,
            "qty": qty,
            "price": price,
            "pnl": r.get("pnl") or r.get("realized_pnl"),
            **r,  # keep raw too
        }
        normed.append(item)

    # best-effort sort (strings sort OK for ISO times)
    normed.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    return normed[:limit]


def _load_intraday_signals() -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Try to load the most recent intraday signals from known artifact locations.
    """
    data_dt = Path(PATHS.get("data_dt", "data_dt"))
    ml_data_dt = Path(PATHS.get("ml_data_dt", "ml_data_dt"))

    candidates = [
        data_dt / "signals" / "intraday" / "predictions" / "intraday_predictions.json",
        ml_data_dt / "signals" / "intraday" / "predictions" / "intraday_predictions.json",
        ml_data_dt / "signals" / "intraday" / "ranks" / "prediction_rank_fetch.json.gz",
    ]

    p = _first_existing(candidates)
    if not p:
        return None, []

    obj = _read_json_gz(p) if p.suffix.endswith(".gz") else _read_json(p)
    if obj is None:
        return None, []

    # We accept a few shapes:
    #  - {"signals": [...]} / {"top": [...]} / {"items":[...]}
    #  - {"results":[...]} / {"buy":[...], "sell":[...]}
    #  - a raw list
    if isinstance(obj, list):
        arr = obj
    elif isinstance(obj, dict):
        arr = (
            _as_list(obj.get("signals"))
            or _as_list(obj.get("items"))
            or _as_list(obj.get("top"))
            or _as_list(obj.get("results"))
        )
        # if it's split buy/sell, merge
        if not arr:
            buy = _as_list(obj.get("buy"))
            sell = _as_list(obj.get("sell"))
            arr = buy + sell
    else:
        arr = []

    # best-effort updated time from file mtime
    try:
        updated_at = datetime.fromtimestamp(p.stat().st_mtime, tz=TIMEZONE).isoformat()
    except Exception:
        updated_at = None

    # normalize lightly to match your UI expectations
    out: List[Dict[str, Any]] = []
    for r in arr:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol") or r.get("ticker")
        if not sym:
            continue
        act = (r.get("action") or r.get("side") or r.get("signal") or "").upper() or None
        ts = r.get("ts") or r.get("time") or r.get("timestamp") or r.get("updated_at")
        conf = r.get("confidence") or r.get("conf") or r.get("p_hit") or r.get("prob")
        out.append({"ts": ts, "symbol": str(sym), "action": act, "confidence": conf, **r})

    return updated_at, out[:200]


async def _load_intraday_fills_best_effort() -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Best-effort fills extraction:
      1) Try sim_logs last-day logs (already used in intraday_logs_router)
      2) Crawl the JSON and collect trade-like dicts
    """
    raw: List[Dict[str, Any]] = []

    try:
        import backend.routers.intraday_logs_router as dt_logs  # module import (NOT from backend.routers import ...)

        # if this exists, it returns {"date":..., "bots":{...}}
        last_day_logs = await dt_logs.get_last_day_logs()
        if isinstance(last_day_logs, dict):
            _walk_collect_trade_like(last_day_logs, raw)
    except Exception:
        pass

    fills = _normalize_fills(raw, limit=100)

    # timestamp: best effort from the newest sim_logs file
    ml_data_dt = Path(PATHS.get("ml_data_dt", "ml_data_dt"))
    sim_log_dir = ml_data_dt / "sim_logs"
    newest = None
    newest_m = 0.0
    try:
        if sim_log_dir.exists():
            for p in sim_log_dir.glob("*.json"):
                mt = p.stat().st_mtime
                if mt > newest_m:
                    newest_m = mt
                    newest = p
    except Exception:
        newest = None

    updated_at = None
    if newest_m > 0:
        try:
            updated_at = datetime.fromtimestamp(newest_m, tz=TIMEZONE).isoformat()
        except Exception:
            updated_at = None

    return updated_at, fills


@router.get("/page")
async def bots_page_bundle() -> Dict[str, Any]:
    """
    One payload for the Bots page.
    Mount with prefixes so the frontend can hit either:
      /api/bots/page
      /api/backend/bots/page
    """
    out: Dict[str, Any] = {
        "as_of": datetime.now(TIMEZONE).isoformat(),
        "swing": {},
        "intraday": {},
    }

    # ---- Swing (EOD) ----
    try:
        import backend.routers.eod_bots_router as eod  # module import

        try:
            out["swing"]["status"] = await eod.eod_status()
        except Exception as e:
            out["swing"]["status"] = _err(e)

        try:
            out["swing"]["configs"] = await eod.list_eod_bot_configs()
        except Exception as e:
            out["swing"]["configs"] = _err(e)

        try:
            out["swing"]["log_days"] = await eod.eod_log_days()
        except Exception as e:
            out["swing"]["log_days"] = _err(e)

    except Exception as e:
        out["swing"] = _err(e)

    # ---- Intraday ----
    try:
        import backend.routers.intraday_logs_router as dt  # module import

        try:
            out["intraday"]["status"] = await dt.intraday_status()
        except Exception as e:
            out["intraday"]["status"] = _err(e)

        try:
            out["intraday"]["configs"] = await dt.intraday_configs()
        except Exception as e:
            out["intraday"]["configs"] = _err(e)

        try:
            out["intraday"]["log_days"] = await dt.list_log_days()
        except Exception as e:
            out["intraday"]["log_days"] = _err(e)

        try:
            out["intraday"]["pnl_last_day"] = await dt.get_last_day_pnl_summary()
        except Exception as e:
            out["intraday"]["pnl_last_day"] = _err(e)

        # tape: signals + fills (artifact based)
        sig_updated, sigs = _load_intraday_signals()
        fill_updated, fills = await _load_intraday_fills_best_effort()

        out["intraday"]["tape"] = {
            "updated_at": sig_updated or fill_updated,
            "signals": sigs,
            "fills": fills,
        }

    except Exception as e:
        out["intraday"] = _err(e)

    return out


@router.get("/tape")
async def bots_tape() -> Dict[str, Any]:
    """
    Just the tape, if you want to poll it separately:
      /api/bots/tape
      /api/backend/bots/tape
    """
    sig_updated, sigs = _load_intraday_signals()
    fill_updated, fills = await _load_intraday_fills_best_effort()
    return {
        "as_of": datetime.now(TIMEZONE).isoformat(),
        "updated_at": sig_updated or fill_updated,
        "signals": sigs,
        "fills": fills,
    }
