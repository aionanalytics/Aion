# backend/services/performance_aggregator.py — v1.2
"""
Performance Aggregator — AION Analytics

Purpose:
    Aggregate REAL execution outcomes into a stable system performance snapshot.

Reads:
    • ml_data/bot_logs/<horizon>/bot_activity_YYYY-MM-DD.json
      (written by base_swing_bot.append_trades_to_daily_log)

Writes:
    • ml_data/performance/system_perf.json

This file does NOT touch models/policy. Observational only.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from backend.core.config import PATHS, TIMEZONE
from backend.core.data_pipeline import safe_float, log

ML_DATA = Path(PATHS["ml_data"])
BOT_LOGS = ML_DATA / "bot_logs"
OUT_DIR = ML_DATA / "performance"
OUT_FILE = OUT_DIR / "system_perf.json"

LOOKBACK_DAYS = 14

__all__ = ["aggregate_system_performance", "run_performance_aggregation"]


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = str(ts)
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _iter_trade_files() -> List[Path]:
    if not BOT_LOGS.exists():
        return []
    return sorted(p for p in BOT_LOGS.rglob("bot_activity_*.json") if p.is_file())


def _load_trades_lookback(lookback_days: int) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows: List[Dict[str, Any]] = []

    for f in _iter_trade_files():
        try:
            js = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not isinstance(js, dict):
            continue

        for bot_key, trades in js.items():
            if not isinstance(trades, list):
                continue
            for t in trades:
                if not isinstance(t, dict):
                    continue
                dt = _parse_iso(t.get("t"))
                if not dt:
                    continue
                dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    row = dict(t)
                    row["_bot_key"] = str(bot_key)
                    row["_source_file"] = str(f)
                    rows.append(row)

    return rows


def _summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls: List[float] = []
    wins = losses = buys = sells = 0

    for t in trades:
        side = str(t.get("side") or "").upper()
        if side == "BUY":
            buys += 1
        elif side == "SELL":
            sells += 1

        pnl = safe_float(t.get("pnl", 0.0))
        if side == "SELL":
            pnls.append(pnl)
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    closed = len(pnls)
    return {
        "trades_total_events": len(trades),
        "buys": buys,
        "sells": sells,
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / closed) if closed else 0.0, 4),
        "avg_pnl": round((sum(pnls) / closed) if closed else 0.0, 6),
        "drawdown_14d": round(min(pnls) if pnls else 0.0, 6),
        "pnl_sum": round(sum(pnls), 6),
    }


def aggregate_system_performance(lookback_days: int = LOOKBACK_DAYS) -> Dict[str, Any]:
    log(f"[performance_aggregator] 📊 Aggregating execution performance ({lookback_days}d)…")

    trades = _load_trades_lookback(lookback_days)
    metrics = _summarize_trades(trades)

    payload = {
        "generated_at": datetime.now(TIMEZONE).isoformat(),
        "lookback_days": int(lookback_days),
        "metrics": metrics,
    }

    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log(f"[performance_aggregator] ✅ Wrote → {OUT_FILE}")
    except Exception as e:
        log(f"[performance_aggregator] ❌ Failed writing performance: {e}")

    return payload


# Backward compatibility (older imports)
def run_performance_aggregation(lookback_days: int = LOOKBACK_DAYS) -> Dict[str, Any]:
    return aggregate_system_performance(lookback_days)


if __name__ == "__main__":
    print(json.dumps(aggregate_system_performance(), indent=2))