# dt_backend/engines/trade_executor.py — v3.0 (EXECUTION_DT-DRIVEN)
"""
Intraday trade execution layer for AION dt_backend (paper by default).

This module executes from:
    rolling[sym]["execution_dt"]

Primary contract (execution_dt)
------------------------------
rolling[sym]["execution_dt"] = {
    "side": "BUY" | "SELL" | "FLAT",
    "size": 0.0–1.0,          # fraction of max capital per symbol
    "confidence_adj": 0.0–1.0,
    "cooldown": bool,
    "valid_until": <ISO8601 UTC>,
    "ts": <ISO8601 UTC>
}

Safety / behavior
-----------------
✅ Executes ONLY when execution_dt is valid and not expired
✅ Respects execution_dt.cooldown (hard stop)
✅ Optional per-symbol post-fill cooldown (extra anti-chop)
✅ Position-aware:
    - BUY blocked if already long (unless allow_add=True)
    - SELL blocked if no position
✅ Integer share sizing option (more broker-realistic)
✅ Writes exec_dt audit metadata back into rolling for observability
✅ Optional gating via policy_dt:
    - trade_gate: bool (default True)
    - action/intent == "STAND_DOWN" (halts)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dt_backend.core.data_pipeline_dt import _read_rolling, save_rolling, log
from dt_backend.engines.broker_api import Order, submit_order, get_cash, get_positions


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

@dataclass
class ExecutionConfig:
    # Hard caps / throughput
    max_trades_per_cycle: int = 20

    # Sizing
    integer_shares: bool = True
    min_qty: float = 1.0

    # Anti-chop
    cooldown_minutes: int = 5  # additional per-symbol cooldown AFTER any fill
    allow_reentry_same_cycle: bool = False

    # Position logic
    allow_add: bool = False            # allow BUY when already long
    sell_full_position: bool = True    # if True, SELL exits the whole position

    # Guards
    min_confidence_adj: float = 0.25   # ignore execution_dt with conf below this
    max_alloc_fraction: float = 0.20   # clamp size to avoid accidental huge size


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = str(ts).strip()
        if not s:
            return None
        # accept both "...Z" and "+00:00"
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _last_price_from_node(node: Dict[str, Any]) -> Optional[float]:
    bars = node.get("bars_intraday") or []
    if not isinstance(bars, list) or not bars:
        return None
    last = bars[-1] if isinstance(bars[-1], dict) else None
    if not last:
        return None
    price = last.get("c") or last.get("close") or last.get("price")
    try:
        p = float(price)
        if p <= 0 or p != p:  # NaN guard
            return None
        return p
    except Exception:
        return None


def _policy_trade_gate(node: Dict[str, Any]) -> bool:
    """
    Optional gate coming from policy_dt:
      - policy_dt.trade_gate (bool) default True
      - policy_dt.action/intent == STAND_DOWN -> False
    """
    pol = (node or {}).get("policy_dt") or {}
    if not isinstance(pol, dict):
        return True

    a = pol.get("action")
    if a is None:
        a = pol.get("intent")
    a = str(a or "").upper().strip()
    if a == "STAND_DOWN":
        return False

    tg = pol.get("trade_gate")
    if tg is None:
        return True
    return bool(tg)


def _get_execution_dt(node: Dict[str, Any]) -> Dict[str, Any]:
    ed = (node or {}).get("execution_dt") or {}
    return ed if isinstance(ed, dict) else {}


def _exec_side(ed: Dict[str, Any]) -> str:
    side = str(ed.get("side") or "FLAT").upper().strip()
    if side not in {"BUY", "SELL", "FLAT"}:
        return "FLAT"
    return side


def _exec_size(ed: Dict[str, Any], cfg: ExecutionConfig) -> float:
    try:
        s = float(ed.get("size", 0.0) or 0.0)
    except Exception:
        s = 0.0
    if s < 0:
        s = 0.0
    if s > cfg.max_alloc_fraction:
        s = cfg.max_alloc_fraction
    return float(s)


def _exec_conf(ed: Dict[str, Any]) -> float:
    try:
        c = float(ed.get("confidence_adj", 0.0) or 0.0)
    except Exception:
        c = 0.0
    if c < 0:
        c = 0.0
    if c > 1.0:
        c = 1.0
    return float(c)


def _exec_valid(ed: Dict[str, Any], now: datetime) -> bool:
    # execution_dt.cooldown is a hard stop
    if bool(ed.get("cooldown", False)):
        return False

    vu = _parse_iso(ed.get("valid_until"))
    if vu is None:
        # safe default: if it doesn't say it's valid, don't trade it
        return False
    return now <= vu


def _cooldown_ok(node: Dict[str, Any], cooldown_min: int, now: datetime) -> bool:
    """
    Extra per-symbol cooldown based on rolling[sym]["exec_dt"]["last_fill_utc"].
    """
    if cooldown_min <= 0:
        return True

    exec_audit = (node or {}).get("exec_dt") or {}
    if not isinstance(exec_audit, dict):
        return True

    last_fill = _parse_iso(exec_audit.get("last_fill_utc"))
    if last_fill is None:
        return True

    return (now - last_fill) >= timedelta(minutes=cooldown_min)


def _qty_from_alloc(alloc: float, price: float, integer_shares: bool) -> float:
    if alloc <= 0 or price <= 0:
        return 0.0
    qty = alloc / price
    if integer_shares:
        qty = float(int(qty))  # floor
    return float(qty)


def _score_for_ranking(node: Dict[str, Any]) -> float:
    """
    If you still have a numeric "strength" somewhere, use it.
    Prefer execution_dt.confidence_adj, else policy_dt.confidence, else 0.
    """
    ed = _get_execution_dt(node)
    c = _exec_conf(ed)
    if c > 0:
        return c

    pol = (node or {}).get("policy_dt") or {}
    if isinstance(pol, dict):
        try:
            return float(pol.get("confidence", 0.0) or 0.0)
        except Exception:
            return 0.0
    return 0.0


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def execute_from_execution_dt(cfg: ExecutionConfig | None = None) -> Dict[str, Any]:
    """
    Execute orders based on rolling[sym]["execution_dt"].

    Returns:
        {
          status,
          orders_sent,
          orders_filled,
          selected,
          fills,
          notes
        }
    """
    cfg = cfg or ExecutionConfig()

    rolling = _read_rolling()
    if not rolling:
        log("[dt_exec] ⚠️ rolling empty, nothing to execute.")
        return {"status": "empty", "orders_sent": 0, "orders_filled": 0, "selected": []}

    cash = get_cash()
    if cash <= 0:
        log("[dt_exec] ⚠️ no cash in paper account, skipping.")
        return {"status": "no_cash", "orders_sent": 0, "orders_filled": 0, "selected": []}

    positions = get_positions()  # symbol -> Position
    now = _utc_now()

    # -----------------------------
    # Candidate selection
    # -----------------------------
    candidates: List[str] = []
    for sym, node in rolling.items():
        if str(sym).startswith("_"):
            continue
        if not isinstance(node, dict):
            continue

        # Optional policy gate (trade_gate / stand_down)
        if not _policy_trade_gate(node):
            continue

        ed = _get_execution_dt(node)
        if not ed:
            continue

        side = _exec_side(ed)
        if side == "FLAT":
            continue

        conf_adj = _exec_conf(ed)
        if conf_adj < cfg.min_confidence_adj:
            continue

        if not _exec_valid(ed, now):
            continue

        if not _cooldown_ok(node, cfg.cooldown_minutes, now):
            continue

        sym_u = str(sym).upper()
        has_pos = (sym_u in positions) and (positions[sym_u].qty > 0)

        # Position-aware guards
        if side == "BUY" and has_pos and not cfg.allow_add:
            continue
        if side == "SELL" and not has_pos:
            continue

        candidates.append(sym_u)

    # Strongest first
    def _rank_key(sym_u: str) -> float:
        node = rolling.get(sym_u) or rolling.get(sym_u.upper()) or rolling.get(sym_u.lower())
        if not isinstance(node, dict):
            return 0.0
        return float(_score_for_ranking(node))

    candidates.sort(key=lambda s: abs(_rank_key(s)), reverse=True)
    selected = candidates[: max(0, int(cfg.max_trades_per_cycle))]

    # -----------------------------
    # Execute
    # -----------------------------
    orders_sent = 0
    filled = 0
    fills_detail: List[Dict[str, Any]] = []
    acted_syms: set[str] = set()

    for sym_u in selected:
        # Find node in rolling (keys may be mixed-case; normalize best-effort)
        node = rolling.get(sym_u)
        if not isinstance(node, dict):
            # try fallback search
            node = rolling.get(sym_u.upper()) or rolling.get(sym_u.lower()) or {}
        if not isinstance(node, dict):
            continue

        # Prevent churn within same run
        if not cfg.allow_reentry_same_cycle and sym_u in acted_syms:
            continue

        ed = _get_execution_dt(node)
        side = _exec_side(ed)
        if side == "FLAT":
            continue

        if not _exec_valid(ed, now):
            continue

        size = _exec_size(ed, cfg)
        conf_adj = _exec_conf(ed)

        price = _last_price_from_node(node)
        if price is None:
            continue

        # SELL requires a position
        pos = positions.get(sym_u)
        has_pos = bool(pos and pos.qty > 0)

        if side == "BUY":
            # Allocation uses size fraction of current cash (simple & safe)
            alloc = cash * size
            qty = _qty_from_alloc(alloc, price, cfg.integer_shares)
            if qty < cfg.min_qty:
                continue
            if has_pos and not cfg.allow_add:
                continue

        elif side == "SELL":
            if not has_pos:
                continue
            if cfg.sell_full_position:
                qty = float(pos.qty)
            else:
                # sell proportional to size of max fraction (safe default)
                qty = float(pos.qty) * max(0.0, min(1.0, size))
                if cfg.integer_shares:
                    qty = float(int(qty))
                if qty < cfg.min_qty:
                    continue

        else:
            continue

        orders_sent += 1
        order = Order(symbol=sym_u, side=side, qty=float(qty))
        res = submit_order(order, last_price=price)
        status = (res.get("status") if isinstance(res, dict) else None)

        # -----------------------------
        # Write execution audit metadata into rolling[sym]["exec_dt"]
        # -----------------------------
        exec_dt = node.get("exec_dt") or {}
        if not isinstance(exec_dt, dict):
            exec_dt = {}

        exec_dt["last_attempt_utc"] = now.isoformat().replace("+00:00", "Z")
        exec_dt["last_side"] = side
        exec_dt["last_size"] = float(size)
        exec_dt["last_confidence_adj"] = float(conf_adj)
        exec_dt["last_price"] = float(price)
        exec_dt["last_result"] = dict(res) if isinstance(res, dict) else {"status": "unknown"}

        # Carry-through for debugging: snapshot the execution_dt used
        exec_dt["last_execution_dt"] = dict(ed) if isinstance(ed, dict) else {}

        if status == "filled":
            filled += 1
            exec_dt["last_fill_utc"] = now.isoformat().replace("+00:00", "Z")
            fills_detail.append(exec_dt["last_result"])
            acted_syms.add(sym_u)

            # refresh live snapshots (paper account changes immediately)
            cash = get_cash()
            positions = get_positions()

        node["exec_dt"] = exec_dt
        rolling[sym_u] = node  # keep normalized key

    save_rolling(rolling)

    return {
        "status": "ok",
        "orders_sent": int(orders_sent),
        "orders_filled": int(filled),
        "selected": list(selected),
        "fills": fills_detail,
        "notes": {
            "driver": "execution_dt",
            "integer_shares": bool(cfg.integer_shares),
            "cooldown_minutes": int(cfg.cooldown_minutes),
            "max_alloc_fraction": float(cfg.max_alloc_fraction),
            "sell_full_position": bool(cfg.sell_full_position),
            "allow_add": bool(cfg.allow_add),
        },
    }


# Backward-compatible alias (if older schedulers call execute_from_policy)
def execute_from_policy(cfg: ExecutionConfig | None = None) -> Dict[str, Any]:
    return execute_from_execution_dt(cfg=cfg)


if __name__ == "__main__":
    out = execute_from_execution_dt()
    log(f"[dt_exec] done: {out}")