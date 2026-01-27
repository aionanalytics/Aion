"""dt_backend.engines.broker_api — v2.4.1 (ownership-safe, circular-import resistant)

Key points
----------
- Local per-bot ledger is the DT strategy truth for cash/positions/fills.
- Optional routing to Alpaca paper if API keys exist.
- Strategy-level ownership safety via dt_backend.core.position_registry:
    * SELL qty is clamped to this strategy's reserved qty.
    * Fills update the shared registry.
- Broker account snapshot cache for risk rails.

Why this rewrite?
-----------------
AION's backend imports dt_backend.engines very early (during router import).
If broker_api imports heavier dt_backend.core modules at import-time, you can
get circular imports that manifest as:

    ImportError: cannot import name 'get_positions' from broker_api

So this module is intentionally dependency-light at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import json
import os
import re
import time
import uuid
import urllib.error
import urllib.request


# =========================
# Tiny utils (no dt_backend imports)
# =========================

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _utc_iso() -> str:
    """UTC now, replay-safe via DT_NOW_UTC override."""
    override = _env("DT_NOW_UTC", "")
    if override:
        # If user provides a string, treat it as already ISO-ish.
        return override
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(msg: str) -> None:
    """Best-effort logger that never crashes."""
    try:
        print(msg, flush=True)
    except Exception:
        pass


# =========================
# Models
# =========================


@dataclass
class Position:
    symbol: str
    qty: float
    avg_price: float


@dataclass
class Order:
    symbol: str
    side: str  # "BUY" or "SELL"
    qty: float
    limit_price: float | None = None


# =========================
# Bot identity + ledger path
# =========================


def _bot_id() -> str:
    bid = _env("DT_BOT_ID", "default")
    bid = "".join(c for c in bid if c.isalnum() or c in ("_", "-", "."))
    return bid or "default"


def _strategy_owner() -> str:
    """Strategy ownership tag written into the shared registry.

    DT should use 'DT'. Swing should use 'SWING' (or 'SW'), etc.
    """
    tag = _env("AION_STRATEGY_TAG", "") or _env("DT_STRATEGY_TAG", "") or "DT"
    tag = "".join(c for c in tag.upper() if c.isalnum() or c in {"_", "-"})
    return tag or "DT"


def _resolve_ledger_path() -> Path:
    override = _env("DT_BOT_LEDGER_PATH", "")
    if override:
        return Path(override)

    # Prefer DT_TRUTH_DIR layout when available.
    dt_truth = _env("DT_TRUTH_DIR", "")
    if dt_truth:
        return Path(dt_truth) / "intraday" / "brokers" / f"bot_{_bot_id()}.json"

    # Fall back to stable local path.
    return Path("da_brains") / "intraday" / "brokers" / f"bot_{_bot_id()}.json"


LEDGER_PATH: Path = _resolve_ledger_path()
PAPER_STATE_PATH: Path = LEDGER_PATH  # backward compat alias


# =========================
# Alpaca keys (env-only)
# =========================

try:
    from admin_keys import (
        ALPACA_API_KEY_ID,
        ALPACA_API_SECRET_KEY,
        ALPACA_PAPER_BASE_URL,
        # legacy aliases
        ALPACA_KEY,
        ALPACA_SECRET,
    )
except Exception:
    ALPACA_API_KEY_ID = ""
    ALPACA_API_SECRET_KEY = ""
    ALPACA_PAPER_BASE_URL = ""
    ALPACA_KEY = ""
    ALPACA_SECRET = ""


def _alpaca_keys() -> tuple[str, str]:
    key = (ALPACA_API_KEY_ID or ALPACA_KEY or "").strip()
    secret = (ALPACA_API_SECRET_KEY or ALPACA_SECRET or "").strip()
    return key, secret


def _alpaca_enabled() -> bool:
    k, s = _alpaca_keys()
    return bool(k and s)


def _alpaca_base_v2() -> str:
    base = (ALPACA_PAPER_BASE_URL or "").strip() or "https://paper-api.alpaca.markets"
    base = base.rstrip("/")
    if not base.endswith("/v2"):
        base = base + "/v2"
    return base


def _alpaca_headers() -> Dict[str, str]:
    k, s = _alpaca_keys()
    return {
        "APCA-API-KEY-ID": k,
        "APCA-API-SECRET-KEY": s,
        "Content-Type": "application/json",
    }


def _http_json(method: str, path: str, payload: Dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    url = _alpaca_base_v2() + path
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method.upper())
    for hk, hv in _alpaca_headers().items():
        req.add_header(hk, hv)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return None
            try:
                return json.loads(raw)
            except Exception:
                return raw
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"alpaca {method} {path} {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"alpaca {method} {path} urlerror: {e}")


def _alpaca_post(path: str, payload: Dict[str, Any]) -> Any:
    return _http_json("POST", path, payload=payload, timeout=20.0)


def _alpaca_get(path: str) -> Any:
    return _http_json("GET", path, payload=None, timeout=15.0)


def _alpaca_delete(path: str) -> Any:
    return _http_json("DELETE", path, payload=None, timeout=15.0)


def _alpaca_account() -> Dict[str, Any]:
    if not _alpaca_enabled():
        return {}
    try:
        acct = _alpaca_get("/account")
        return acct if isinstance(acct, dict) else {}
    except Exception as e:
        log(f"[alpaca] ⚠️ account fetch failed: {e}")
        return {}


def _alpaca_account_cash() -> float:
    acct = _alpaca_account()
    return _safe_float(acct.get("cash"), 0.0) if isinstance(acct, dict) else 0.0


def _alpaca_account_equity() -> float:
    acct = _alpaca_account()
    if not isinstance(acct, dict):
        return 0.0
    for k in ("equity", "portfolio_value", "last_equity"):
        v = _safe_float(acct.get(k), 0.0)
        if v > 0:
            return v
    return 0.0


def _alpaca_positions() -> Dict[str, Position]:
    out: Dict[str, Position] = {}
    if not _alpaca_enabled():
        return out
    try:
        items = _alpaca_get("/positions")
        if not isinstance(items, list):
            return out
        for it in items:
            if not isinstance(it, dict):
                continue
            sym = str(it.get("symbol") or "").upper().strip()
            if not sym:
                continue
            qty = _safe_float(it.get("qty"), 0.0)
            avg = _safe_float(it.get("avg_entry_price"), _safe_float(it.get("avg_price"), 0.0))
            if qty != 0:
                out[sym] = Position(symbol=sym, qty=qty, avg_price=avg)
    except Exception as e:
        log(f"[alpaca] ⚠️ positions fetch failed: {e}")
    return out


# =========================
# Local ledger
# =========================


def _starting_cash_cap() -> float:
    cap = _env("DT_BOT_CASH_CAP", "")
    if cap:
        return max(0.0, _safe_float(cap, 0.0))
    return 100_000.0


def _default_ledger() -> Dict[str, Any]:
    cap = _starting_cash_cap()
    now = _utc_iso()
    return {
        "bot_id": _bot_id(),
        "cash": float(cap),
        "positions": {"ACTIVE": {}, "CARRY": {}},
        "fills": [],
        "meta": {
            "created_at": now,
            "updated_at": now,
            "cash_cap": float(cap),
            "venue": "alpaca_paper+local" if _alpaca_enabled() else "local_only",
        },
    }


def _ensure_positions_schema(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = state.get("positions")
    if not isinstance(raw, dict):
        raw = {}

    if "ACTIVE" in raw or "CARRY" in raw:
        active = raw.get("ACTIVE")
        carry = raw.get("CARRY")
        if not isinstance(active, dict):
            active = {}
        if not isinstance(carry, dict):
            carry = {}
        state["positions"] = {"ACTIVE": active, "CARRY": carry}
        return state["positions"]

    legacy = raw if isinstance(raw, dict) else {}
    state["positions"] = {"ACTIVE": legacy, "CARRY": {}}
    return state["positions"]


def _positions_view(state: Dict[str, Any], scope: str) -> Dict[str, Any]:
    buckets = _ensure_positions_schema(state)
    scope = (scope or "ACTIVE").strip().upper()
    if scope == "ALL":
        out: Dict[str, Any] = {}
        for b in ("ACTIVE", "CARRY"):
            part = buckets.get(b, {})
            if isinstance(part, dict):
                out.update(part)
        return out
    if scope == "CARRY":
        return buckets.get("CARRY", {}) if isinstance(buckets.get("CARRY"), dict) else {}
    return buckets.get("ACTIVE", {}) if isinstance(buckets.get("ACTIVE"), dict) else {}


def _read_ledger() -> Dict[str, Any]:
    if not LEDGER_PATH.exists():
        return _default_ledger()
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_ledger()

        data.setdefault("bot_id", _bot_id())
        data.setdefault("cash", _starting_cash_cap())
        data.setdefault("positions", {})
        data.setdefault("fills", [])

        meta = data.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("created_at", _utc_iso())
        meta.setdefault("updated_at", _utc_iso())
        meta.setdefault("cash_cap", _starting_cash_cap())
        meta.setdefault("venue", "alpaca_paper+local" if _alpaca_enabled() else "local_only")
        data["meta"] = meta

        if not isinstance(data.get("positions"), dict):
            data["positions"] = {}
        _ensure_positions_schema(data)
        if not isinstance(data.get("fills"), list):
            data["fills"] = []

        return data
    except Exception as e:
        log(f"[broker_ledger] ⚠️ failed to read ledger: {e}")
        return _default_ledger()


def _save_ledger(state: Dict[str, Any]) -> None:
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = state if isinstance(state, dict) else _default_ledger()
        state.setdefault("meta", {})
        state["meta"]["updated_at"] = _utc_iso()
        tmp = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(LEDGER_PATH)
    except Exception as e:
        log(f"[broker_ledger] ⚠️ failed to save ledger: {e}")


# =========================
# Public: local allowance ledger API (read)
# =========================


def get_positions() -> Dict[str, Position]:
    state = _read_ledger()
    scope = _env("DT_LEDGER_READ_SCOPE", "ACTIVE").upper()  # ACTIVE|CARRY|ALL
    positions_raw = _positions_view(state, scope)
    out: Dict[str, Position] = {}
    if not isinstance(positions_raw, dict):
        return out
    for sym, node in positions_raw.items():
        if not isinstance(node, dict):
            continue
        qty = _safe_float(node.get("qty", 0.0), 0.0)
        avg = _safe_float(node.get("avg_price", 0.0), 0.0)
        sym2 = str(sym).upper().strip()
        if sym2 and qty != 0:
            out[sym2] = Position(symbol=sym2, qty=qty, avg_price=avg)
    return out


def get_positions_scoped(scope: str) -> Dict[str, Position]:
    state = _read_ledger()
    positions_raw = _positions_view(state, scope)
    out: Dict[str, Position] = {}
    if not isinstance(positions_raw, dict):
        return out
    for sym, node in positions_raw.items():
        if not isinstance(node, dict):
            continue
        qty = _safe_float(node.get("qty", 0.0), 0.0)
        avg = _safe_float(node.get("avg_price", 0.0), 0.0)
        sym2 = str(sym).upper().strip()
        if sym2 and qty != 0:
            out[sym2] = Position(symbol=sym2, qty=qty, avg_price=avg)
    return out


def get_cash() -> float:
    state = _read_ledger()
    return _safe_float(state.get("cash", 0.0), 0.0)


def get_ledger_state() -> Dict[str, Any]:
    return _read_ledger()


# =========================
# Execution helpers
# =========================


def _fmt_qty(q: float) -> str:
    if q <= 0:
        return "0"
    s = f"{q:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _client_order_id() -> str:
    bid = _bot_id()[:14]
    suffix = uuid.uuid4().hex[:16]
    return f"DT{bid}-{suffix}"[:48]


def _poll_order(order_id: str, max_wait_s: float = 6.0) -> Dict[str, Any]:
    deadline = time.time() + max_wait_s
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        o = _alpaca_get(f"/orders/{order_id}")
        if isinstance(o, dict):
            last = o
            status = str(o.get("status", "")).lower()
            filled_qty = _safe_float(o.get("filled_qty") or 0.0, 0.0)
            if status in {"filled", "canceled", "rejected"}:
                return o
            if filled_qty > 0 and status in {"partially_filled", "accepted", "new"}:
                return o
        time.sleep(0.35)
    return last or {"status": "unknown"}


def _cancel_order(order_id: str) -> None:
    try:
        _alpaca_delete(f"/orders/{order_id}")
    except Exception as e:
        log(f"[broker_alpaca] ⚠️ cancel failed for {order_id}: {e}")


def _ledger_apply_fill(state: Dict[str, Any], sym: str, side: str, filled_qty: float, fill_price: float) -> Dict[str, Any]:
    buckets = _ensure_positions_schema(state)
    positions = buckets.get("ACTIVE", {})
    if not isinstance(positions, dict):
        positions = {}
        buckets["ACTIVE"] = positions

    fills = state.get("fills") or []
    if not isinstance(fills, list):
        fills = []

    cash = _safe_float(state.get("cash", 0.0), 0.0)

    pos = positions.get(sym) or {"qty": 0.0, "avg_price": 0.0}
    pos_qty = _safe_float(pos.get("qty", 0.0), 0.0)
    pos_avg = _safe_float(pos.get("avg_price", 0.0), 0.0)

    realized_pnl = 0.0

    if side == "BUY":
        cost = fill_price * filled_qty
        cash -= cost
        new_qty = pos_qty + filled_qty
        if new_qty <= 0:
            positions.pop(sym, None)
        else:
            new_avg = ((pos_avg * pos_qty) + cost) / new_qty if pos_qty > 0 else fill_price
            positions[sym] = {"qty": new_qty, "avg_price": new_avg}

    else:  # SELL
        proceeds = fill_price * filled_qty
        cash += proceeds
        realized_pnl = (fill_price - pos_avg) * filled_qty
        new_qty = pos_qty - filled_qty
        if new_qty <= 0:
            positions.pop(sym, None)
        else:
            positions[sym] = {"qty": new_qty, "avg_price": pos_avg}

    fills.append(
        {
            "t": _utc_iso(),
            "bot_id": _bot_id(),
            "symbol": sym,
            "side": side,
            "qty": float(filled_qty),
            "price": float(fill_price),
            "realized_pnl": float(realized_pnl),
            "venue": "alpaca_paper" if _alpaca_enabled() else "local",
        }
    )

    state["cash"] = float(cash)
    state["positions"] = buckets
    state["fills"] = fills
    return state


# =========================
# Ownership registry helpers (import lazily)
# =========================


def _ownership_can_sell(sym: str, qty_req: float) -> float:
    """Return clamped SELL qty based on this strategy's reservation."""
    try:
        from dt_backend.core.position_registry import load_registry, can_sell_qty

        reg = load_registry()
        owner = _strategy_owner()
        allowed = float(can_sell_qty(reg, sym, owner))
        return max(0.0, min(float(qty_req), allowed))
    except Exception:
        # If registry is unavailable, be conservative: allow 0 to avoid cross-strategy damage.
        return 0.0


def _ownership_on_fill(sym: str, side: str, filled_qty: float) -> None:
    try:
        from dt_backend.core.position_registry import load_registry, save_registry, reserve_on_fill

        reg = load_registry()
        reserve_on_fill(reg, sym, side, float(filled_qty), _strategy_owner())
        save_registry(reg)
    except Exception:
        pass


# =========================
# Order submission
# =========================


def submit_order(order: Order, last_price: float | None = None) -> Dict[str, Any]:
    sym = str(order.symbol).upper().strip()
    side = str(order.side).upper().strip()
    qty_req = _safe_float(order.qty, 0.0)

    if not sym:
        return {"status": "rejected", "reason": "bad_symbol"}
    if side not in {"BUY", "SELL"}:
        return {"status": "rejected", "reason": "bad_side"}
    if qty_req <= 0:
        return {"status": "rejected", "reason": "bad_qty"}

    state = _read_ledger()
    cash = _safe_float(state.get("cash", 0.0), 0.0)
    buckets = _ensure_positions_schema(state)
    positions = buckets.get("ACTIVE", {})
    if not isinstance(positions, dict):
        positions = {}
        buckets["ACTIVE"] = positions

    pos = positions.get(sym) or {"qty": 0.0, "avg_price": 0.0}
    pos_qty = _safe_float(pos.get("qty", 0.0), 0.0)
    pos_avg = _safe_float(pos.get("avg_price", 0.0), 0.0)

    # Strategy-level ownership guard (prevents DT selling Swing, etc.)
    if side == "SELL":
        qty_allowed = _ownership_can_sell(sym, qty_req)
        if qty_allowed <= 0.0:
            return {
                "status": "rejected",
                "reason": "not_owned_by_strategy",
                "symbol": sym,
                "strategy": _strategy_owner(),
            }
        qty_req = qty_allowed

    # For local validation, we need a reference price to check affordability on BUY.
    ref_price = None
    if order.limit_price is not None:
        ref_price = _safe_float(order.limit_price, None)  # type: ignore[arg-type]
    elif last_price is not None:
        ref_price = _safe_float(last_price, None)  # type: ignore[arg-type]

    if side == "BUY":
        if ref_price is None:
            return {"status": "rejected", "reason": "no_price_for_buy_check"}
        est_cost = ref_price * qty_req
        if est_cost > cash:
            return {"status": "rejected", "reason": "insufficient_cash_allowance"}
    else:
        if pos_qty <= 0:
            return {"status": "rejected", "reason": "no_position_allowance"}
        if qty_req > pos_qty:
            qty_req = pos_qty

    # ===== Execute on Alpaca if enabled =====
    if _alpaca_enabled():
        if order.limit_price is not None and last_price is None:
            return {"status": "rejected", "reason": "no_price_for_limit"}

        alpaca_side = "buy" if side == "BUY" else "sell"
        payload: Dict[str, Any] = {
            "symbol": sym,
            "side": alpaca_side,
            "time_in_force": "day",
            "client_order_id": _client_order_id(),
        }

        if order.limit_price is not None:
            payload["type"] = "limit"
            payload["limit_price"] = str(_safe_float(order.limit_price, 0.0))
            payload["qty"] = _fmt_qty(qty_req)
        else:
            payload["type"] = "market"
            payload["qty"] = _fmt_qty(qty_req)

        try:
            created = _alpaca_post("/orders", payload)
            if not isinstance(created, dict) or not created.get("id"):
                return {"status": "rejected", "reason": "alpaca_no_order_id", "raw": created}

            oid = str(created["id"])
            final = _poll_order(oid, max_wait_s=6.0)

            status = str(final.get("status", "")).lower()
            filled_qty = _safe_float(final.get("filled_qty") or 0.0, 0.0)
            fill_price = _safe_float(final.get("filled_avg_price"), 0.0)

            if filled_qty <= 0:
                _cancel_order(oid)
                # Capture Alpaca rejection details
                alpaca_reason = str(final.get("reason") or "")
                alpaca_message = str(final.get("message") or "")
                rejection_info = {
                    "status": "rejected",
                    "reason": "alpaca_not_filled_fast",
                    "id": oid,
                    "alpaca_status": status,
                    "alpaca_reason": alpaca_reason,
                    "alpaca_message": alpaca_message,
                    "alpaca_response": final,
                }
                # Log detailed rejection information
                log(f"[broker_alpaca] ❌ Order rejected: {sym} {side} {qty_req} - status={status}, reason={alpaca_reason}, message={alpaca_message}")
                return rejection_info

            if status != "filled":
                _cancel_order(oid)

            state2 = _ledger_apply_fill(state, sym, side, filled_qty, fill_price)
            _save_ledger(state2)

            # Update shared strategy ownership registry (filled qty only).
            _ownership_on_fill(sym, side, filled_qty)

            realized_pnl = 0.0
            if side == "SELL":
                realized_pnl = (fill_price - pos_avg) * filled_qty

            out = {
                "status": "filled",
                "id": oid,
                "t": str(final.get("filled_at") or final.get("submitted_at") or _utc_iso()),
                "symbol": sym,
                "side": side,
                "qty": float(filled_qty),
                "price": float(fill_price),
                "realized_pnl": float(realized_pnl),
                "venue": "alpaca_paper",
                "bot_id": _bot_id(),
                "client_order_id": payload.get("client_order_id"),
            }
            log(f"[broker_alpaca] ✅ filled {side} {filled_qty} {sym} @ {fill_price} (bot={_bot_id()})")
            return out

        except Exception as e:
            error_detail = str(e)[:500]
            # Try to extract Alpaca error details from exception message
            alpaca_reason = ""
            alpaca_message = ""
            try:
                # The RuntimeError from _http_json includes the response body
                # Try to parse it as JSON to extract reason/message
                # Look for JSON pattern after the HTTP status code (e.g., "403: {...}")
                error_str = str(e)
                # Match JSON after status code pattern like "403: {...}"
                json_match = re.search(r'\d{3}:\s*(\{.*\})\s*$', error_str)
                if json_match:
                    json_str = json_match.group(1)
                    error_json = json.loads(json_str)
                    if isinstance(error_json, dict):
                        # Alpaca uses 'reason' field primarily, 'code' is numeric error code
                        alpaca_reason = str(error_json.get("reason") or "")
                        alpaca_message = str(error_json.get("message") or "")
            except Exception:
                pass
            
            rejection_dict = {
                "status": "rejected",
                "reason": "alpaca_error",
                "detail": error_detail,
                "bot_id": _bot_id(),
            }
            if alpaca_reason:
                rejection_dict["alpaca_reason"] = alpaca_reason
            if alpaca_message:
                rejection_dict["alpaca_message"] = alpaca_message
            
            log(f"[broker_alpaca] ❌ submit failed for {sym}: {error_detail}" + 
                (f" (reason={alpaca_reason}, message={alpaca_message})" if alpaca_reason or alpaca_message else ""))
            return rejection_dict

    # ===== Local simulation fallback =====
    if last_price is None:
        return {"status": "rejected", "reason": "no_price_local"}

    fill_price = _safe_float(last_price, 0.0)
    if order.limit_price is not None:
        lp = _safe_float(order.limit_price, 0.0)
        if side == "BUY" and fill_price > lp:
            return {"status": "rejected", "reason": "limit_not_reached"}
        if side == "SELL" and fill_price < lp:
            return {"status": "rejected", "reason": "limit_not_reached"}
        fill_price = lp

    filled_qty = qty_req

    state2 = _ledger_apply_fill(state, sym, side, filled_qty, fill_price)
    _save_ledger(state2)

    _ownership_on_fill(sym, side, filled_qty)

    realized_pnl = 0.0
    if side == "SELL":
        realized_pnl = (fill_price - pos_avg) * filled_qty

    log(f"[broker_local] ✅ filled {side} {filled_qty} {sym} @ {fill_price} (bot={_bot_id()})")
    return {
        "status": "filled",
        "t": _utc_iso(),
        "symbol": sym,
        "side": side,
        "qty": float(filled_qty),
        "price": float(fill_price),
        "realized_pnl": float(realized_pnl),
        "venue": "local",
        "bot_id": _bot_id(),
    }


# =========================
# Reconcile ledger from broker
# =========================


def reconcile_ledger_from_broker(*, mode: str = "IMPORT", sync_cash: bool = False, allow_when_disabled: bool = False) -> Dict[str, Any]:
    mode = (mode or "IMPORT").strip().upper()
    if not _alpaca_enabled() and not allow_when_disabled:
        return {"status": "skipped", "reason": "alpaca_disabled"}

    state = _read_ledger()
    buckets = _ensure_positions_schema(state)

    broker_pos = _alpaca_positions() if _alpaca_enabled() else {}
    broker_syms = sorted(broker_pos.keys())

    if mode == "CLEAR_IF_FLAT" and len(broker_syms) == 0:
        buckets["ACTIVE"] = {}
        state["positions"] = buckets
        _save_ledger(state)
        return {"status": "ok", "mode": mode, "active": 0, "carry": len(_positions_view(state, "CARRY"))}

    active_new: Dict[str, Any] = {}
    for sym, p in broker_pos.items():
        active_new[sym] = {"qty": float(p.qty), "avg_price": float(p.avg_price)}
    buckets["ACTIVE"] = active_new

    if mode == "STRICT":
        buckets["CARRY"] = {}

    state["positions"] = buckets

    if sync_cash:
        c = _alpaca_account_cash()
        if c > 0:
            state["cash"] = float(c)

    _save_ledger(state)
    return {
        "status": "ok",
        "mode": mode,
        "broker_positions": len(broker_syms),
        "active": len(active_new),
        "carry": len(_positions_view(state, "CARRY")),
        "synced_cash": bool(sync_cash),
    }


# =========================
# Broker snapshot cache (v2.3)
# =========================


_ACCOUNT_CACHE: Dict[str, Any] = {}
_ACCOUNT_CACHE_TS: float = 0.0


def get_account_cached(*, ttl_sec: int = 180, force: bool = False) -> Dict[str, Any]:
    global _ACCOUNT_CACHE, _ACCOUNT_CACHE_TS

    if not _alpaca_enabled():
        return {}

    now = time.time()
    ttl = max(1, int(ttl_sec))
    if (not force) and _ACCOUNT_CACHE and (now - _ACCOUNT_CACHE_TS) < ttl:
        return _ACCOUNT_CACHE

    acct = _alpaca_account()
    if isinstance(acct, dict) and acct:
        _ACCOUNT_CACHE = acct
        _ACCOUNT_CACHE_TS = now
        return acct

    return _ACCOUNT_CACHE if isinstance(_ACCOUNT_CACHE, dict) else {}


def get_equity_cached(*, ttl_sec: int = 180, force: bool = False) -> float:
    acct = get_account_cached(ttl_sec=ttl_sec, force=force)
    if not isinstance(acct, dict) or not acct:
        return 0.0
    for k in ("equity", "portfolio_value", "last_equity"):
        v = _safe_float(acct.get(k), 0.0)
        if v > 0:
            return v
    return 0.0


# =========================
# Strategy ownership registry reconcile (v2.4)
# =========================


_OWNERSHIP_RECONCILE_TS: float = 0.0
_OWNERSHIP_RECONCILE_LAST: dict = {}


def reconcile_ownership_cached(*, ttl_sec: int = 180, force: bool = False) -> dict:
    global _OWNERSHIP_RECONCILE_TS, _OWNERSHIP_RECONCILE_LAST

    if not _alpaca_enabled():
        return {}

    now = time.time()
    ttl = max(1, int(ttl_sec))
    if (not force) and _OWNERSHIP_RECONCILE_LAST and (now - _OWNERSHIP_RECONCILE_TS) < ttl:
        return _OWNERSHIP_RECONCILE_LAST

    try:
        broker_pos = _alpaca_positions()
        broker_simple: Dict[str, float] = {sym: float(p.qty) for sym, p in broker_pos.items()}

        from dt_backend.core.position_registry import load_registry, save_registry, reconcile_with_alpaca_positions

        reg = load_registry()
        summary = reconcile_with_alpaca_positions(reg, broker_simple)
        save_registry(reg)

        _OWNERSHIP_RECONCILE_LAST = summary
        _OWNERSHIP_RECONCILE_TS = now

        if isinstance(summary, dict) and summary.get("mismatch_symbols"):
            log(f"[broker_ownership] 🧯 mismatch detected: {summary.get('mismatch_symbols')}")

        return summary
    except Exception as e:
        log(f"[broker_ownership] ⚠️ reconcile failed: {e}")
        return _OWNERSHIP_RECONCILE_LAST if isinstance(_OWNERSHIP_RECONCILE_LAST, dict) else {}


# =========================
# Wrapper class (compat)
# =========================


_RECONCILE_DONE: bool = False


class BrokerAPI:
    """Thin wrapper around the module-level broker functions + optional reconciliation."""

    def __init__(self) -> None:
        global _RECONCILE_DONE
        if _RECONCILE_DONE:
            return

        if _env("DT_LEDGER_RECONCILE_ON_STARTUP", "0").lower() in {"1", "true", "yes", "y", "on"}:
            mode = _env("DT_LEDGER_RECONCILE_MODE", "IMPORT")
            sync_cash = _env("DT_LEDGER_RECONCILE_SYNC_CASH", "0").lower() in {"1", "true", "yes", "y", "on"}
            try:
                res = reconcile_ledger_from_broker(mode=mode, sync_cash=sync_cash)
                log(f"[broker_ledger] 🔄 reconcile_on_startup: {res}")
            except Exception as e:
                log(f"[broker_ledger] ⚠️ reconcile_on_startup failed: {e}")

        _RECONCILE_DONE = True

    def get_cash(self) -> float:
        return get_cash()

    def get_positions(self) -> Dict[str, Position]:
        return get_positions()

    def submit_order(self, order: Order, last_price: float | None = None) -> Dict[str, Any]:
        return submit_order(order, last_price=last_price)

    def get_account_cached(self, *, ttl_sec: int = 180, force: bool = False) -> Dict[str, Any]:
        return get_account_cached(ttl_sec=ttl_sec, force=force)

    def get_equity_cached(self, *, ttl_sec: int = 180, force: bool = False) -> float:
        return get_equity_cached(ttl_sec=ttl_sec, force=force)
