# dt_backend/engines/broker_api.py — v1.1
"""
Abstract broker API for AION dt_backend.

Paper broker improvements (v1.1):
  ✅ Stable state schema with fills ledger
  ✅ Realized PnL computed on SELL fills
  ✅ Defensive path fallback (won't crash if DT_PATHS key differs)
  ✅ Keeps fractional qty support but executor can round to shares

This is still a paper broker. Real broker adapters can keep the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from dt_backend.core import DT_PATHS, log


def _resolve_paper_state_path() -> Path:
    """
    Prefer a dedicated DT_PATHS key if present, else fall back to dt_backend root.
    """
    # If you later add DT_PATHS["paper_account_dt"], it will be used automatically.
    p = DT_PATHS.get("paper_account_dt")
    if isinstance(p, Path):
        return p
    root = DT_PATHS.get("dt_backend")
    if isinstance(root, Path):
        return root / "paper_account_intraday.json"
    return Path("paper_account_intraday.json")


PAPER_STATE_PATH: Path = _resolve_paper_state_path()


@dataclass
class Position:
    symbol: str
    qty: float
    avg_price: float


@dataclass
class Order:
    symbol: str
    side: str           # "BUY" or "SELL"
    qty: float
    limit_price: float | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_state() -> Dict[str, Any]:
    return {
        "cash": 100_000.0,
        "positions": {},   # { "AAPL": {"qty": 10, "avg_price": 180.0} }
        "fills": [],       # list of fill dicts
        "meta": {
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        },
    }


def _read_state() -> Dict[str, Any]:
    if not PAPER_STATE_PATH.exists():
        return _default_state()
    try:
        import json
        with open(PAPER_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_state()

        # sanitize minimally
        data.setdefault("cash", 100_000.0)
        data.setdefault("positions", {})
        data.setdefault("fills", [])
        meta = data.get("meta")
        if not isinstance(meta, dict):
            data["meta"] = {"created_at": _utc_now_iso(), "updated_at": _utc_now_iso()}
        else:
            meta.setdefault("created_at", _utc_now_iso())
            meta.setdefault("updated_at", _utc_now_iso())
            data["meta"] = meta
        return data
    except Exception as e:
        log(f"[broker_paper] ⚠️ failed to read state: {e}")
        return _default_state()


def _save_state(state: Dict[str, Any]) -> None:
    try:
        import json
        PAPER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = state if isinstance(state, dict) else _default_state()
        state.setdefault("meta", {})
        state["meta"]["updated_at"] = _utc_now_iso()
        tmp = PAPER_STATE_PATH.with_suffix(PAPER_STATE_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(PAPER_STATE_PATH)
    except Exception as e:
        log(f"[broker_paper] ⚠️ failed to save state: {e}")


def get_positions() -> Dict[str, Position]:
    state = _read_state()
    positions_raw = state.get("positions") or {}
    out: Dict[str, Position] = {}
    if not isinstance(positions_raw, dict):
        return out

    for sym, node in positions_raw.items():
        if not isinstance(node, dict):
            continue
        try:
            out[sym] = Position(
                symbol=str(sym),
                qty=float(node.get("qty", 0.0)),
                avg_price=float(node.get("avg_price", 0.0)),
            )
        except Exception:
            continue
    return out


def get_cash() -> float:
    state = _read_state()
    try:
        return float(state.get("cash", 0.0))
    except Exception:
        return 0.0


def submit_order(order: Order, last_price: float | None = None) -> Dict[str, Any]:
    """
    Paper-fill a simple market/limit order.

    Logic:
      • If limit_price is set, require last_price to be favorable.
      • Fills immediately at last_price (or limit if provided).
      • BUY updates avg_price.
      • SELL computes realized PnL vs avg_price.
    """
    state = _read_state()
    positions = state.get("positions") or {}
    if not isinstance(positions, dict):
        positions = {}
    fills = state.get("fills") or []
    if not isinstance(fills, list):
        fills = []

    cash = float(state.get("cash", 0.0))

    sym = str(order.symbol).upper().strip()
    side = str(order.side).upper().strip()
    if side not in {"BUY", "SELL"}:
        return {"status": "rejected", "reason": "bad_side"}

    if last_price is None:
        log(f"[broker_paper] ⚠️ no last_price for {sym}, skipping order.")
        return {"status": "rejected", "reason": "no_price"}

    fill_price = float(last_price)

    # Limit logic
    if order.limit_price is not None:
        lp = float(order.limit_price)
        if side == "BUY" and fill_price > lp:
            return {"status": "rejected", "reason": "limit_not_reached"}
        if side == "SELL" and fill_price < lp:
            return {"status": "rejected", "reason": "limit_not_reached"}
        fill_price = lp  # if favorable, assume fill at limit

    qty_req = float(order.qty)
    if qty_req <= 0:
        return {"status": "rejected", "reason": "bad_qty"}

    pos = positions.get(sym) or {"qty": 0.0, "avg_price": 0.0}
    try:
        pos_qty = float(pos.get("qty", 0.0))
        pos_avg = float(pos.get("avg_price", 0.0))
    except Exception:
        pos_qty, pos_avg = 0.0, 0.0

    realized_pnl = 0.0
    filled_qty = qty_req

    if side == "BUY":
        cost = fill_price * filled_qty
        if cost > cash:
            return {"status": "rejected", "reason": "insufficient_cash"}

        cash -= cost
        new_qty = pos_qty + filled_qty
        if new_qty <= 0:
            positions.pop(sym, None)
        else:
            new_avg = ((pos_avg * pos_qty) + cost) / new_qty if pos_qty > 0 else fill_price
            positions[sym] = {"qty": new_qty, "avg_price": new_avg}

    else:  # SELL
        if pos_qty <= 0:
            return {"status": "rejected", "reason": "no_position"}

        if filled_qty > pos_qty:
            filled_qty = pos_qty  # clamp
        proceeds = fill_price * filled_qty
        cash += proceeds

        # realized pnl vs avg
        realized_pnl = (fill_price - pos_avg) * filled_qty

        new_qty = pos_qty - filled_qty
        if new_qty <= 0:
            positions.pop(sym, None)
        else:
            positions[sym] = {"qty": new_qty, "avg_price": pos_avg}

    fill = {
        "t": _utc_now_iso(),
        "symbol": sym,
        "side": side,
        "qty": float(filled_qty),
        "price": float(fill_price),
        "realized_pnl": float(realized_pnl),
    }
    fills.append(fill)

    state["cash"] = float(cash)
    state["positions"] = positions
    state["fills"] = fills
    _save_state(state)

    log(f"[broker_paper] ✅ filled {side} {filled_qty} {sym} @ {fill_price} pnl={realized_pnl:.4f}")
    return {"status": "filled", **fill}