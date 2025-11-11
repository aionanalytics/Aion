# backend/trading_bot_nightly.py
# Nightly Swing Trading Bots — AION Analytics / StockAnalyzerPro
# --------------------------------------------------------------
# Five bots (momentum, mean-revert, signal-follow, breakout, hybrid)
# trade insights (1w-52w) timeframes.
#   • Full mode (pre-market): rebalance vs top-50 insights
#   • Loop mode (hourly): monitor PnL, stops, and rotate weak positions
# --------------------------------------------------------------

from __future__ import annotations
import os, json, gzip, argparse, random, math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.config import PATHS

# ---- paths & horizons ----
from backend.config import PATHS
from backend.data_pipeline import log, _read_rolling
from backend.ml_helpers import _safe_float

HORIZON = "4w"  # <-- set per file: "1w", "2w", "4w"

INSIGHTS_DIR = PATHS["insights"]                      # uses your centralized paths
ROLLING_PATH = PATHS["stock_cache"] / "master" / "rolling.json.gz"
BOT_DIR      = PATHS["stock_cache"] / "master" / "bot"
LOG_DIR      = PATHS["ml_data"] / "bot_logs" / HORIZON
STATE_DIR    = PATHS["ml_data"] / "bot_state"
STATE_FILE   = STATE_DIR / f"rolling_{HORIZON}.json"

# ensure dirs exist
BOT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)   # <-- FIX: add parentheses
STATE_DIR.mkdir(parents=True, exist_ok=True)

# ==============================================================
# ---------- Market Hours Utilities -----------------------------
# ==============================================================
import pytz
from datetime import datetime, time as dtime

def is_market_open() -> bool:
    """Return True only during U.S. regular trading hours (Mon–Fri 9:30–16:00 ET)."""
    ny = pytz.timezone("America/New_York")
    now = datetime.now(ny)
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    open_t = dtime(9, 30)
    close_t = dtime(16, 0)
    return open_t <= now.time() <= close_t

# ==============================================================
# ---------- Safe Symbol Extractor ------------------------------
# ==============================================================

def _get_symbol(row: dict) -> str:
    """
    Safely extract a symbol field from any insight record.
    Supports keys: 'symbol', 'ticker', or 'name'.
    Returns uppercase ticker or empty string if none found.
    """
    if not isinstance(row, dict):
        return ""
    sym = row.get("symbol") or row.get("ticker") or row.get("name") or ""
    return str(sym).strip().upper()

# ==============================================================
# ---------- CONFIG ---------------------------------------------
# ==============================================================

START_CASH = 1_000.0
STOP_LOSS = -0.05
TAKE_PROFIT = 0.10
POSITION_SIZE = 0.20  # 20% of equity per position
MAX_POSITIONS = 10


# ==============================================================
# ---------- Helper: load/save JSON.GZ with safety --------------
# ==============================================================
def _load_gz(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_gz(path: Path, obj: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as raw:
        gz = gzip.GzipFile(filename=path.name, fileobj=raw, mode="wb")
        gz.write(json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))
        gz.close()
    tmp.replace(path)

# ==============================================================
# ---------- Base Bot ------------------------------------------
# ==============================================================
class BaseBot:
    def __init__(self, name: str):
        self.name = name
        self.path = BOT_DIR / f"rolling_{name}.json.gz"
        self.state = _load_gz(self.path) or {"cash": START_CASH, "positions": {}}

    # --- core account helpers ---
    def equity(self, prices: Dict[str, float]) -> float:
        eq = self.state.get("cash", START_CASH)
        for sym, pos in self.state.get("positions", {}).items():
            px = prices.get(sym)
            if px:
                eq += pos["qty"] * px
        return round(eq, 2)

    def _alloc_qty(self, sym: str, price: float) -> float:
        eq = self.equity({})
        cash = self.state.get("cash", 0)
        alloc = eq * POSITION_SIZE
        size = min(cash, alloc) / max(price, 1e-6)
        return round(size, 3)

    def _enter(self, sym: str, price: float, reason: str):
        # Restrict trading to market hours only
        from backend.trading_bot_nightly_4w import is_market_open
        if not is_market_open():
            log(f"[{self.name}] ⏸ Market closed — skipping buy for {sym}.")
            return

        if len(self.state["positions"]) >= MAX_POSITIONS:
            return
        qty = self._alloc_qty(sym, price)
        cost = qty * price
        if qty <= 0 or cost > self.state["cash"]:
            return
        stop = price * (1 + STOP_LOSS)
        target = price * (1 + TAKE_PROFIT)
        self.state["cash"] -= cost
        self.state["positions"][sym] = {
            "entry": price, "qty": qty, "stop": stop, "target": target,
            "reason": reason, "opened": datetime.utcnow().isoformat()
        }
        self.log_action(sym, "BUY", reason, price, qty)

    def _exit(self, sym: str, price: float, reason: str):
        # Restrict trading to market hours only
        from backend.trading_bot_nightly_4w import is_market_open
        if not is_market_open():
            log(f"[{self.name}] ⏸ Market closed — skipping sell for {sym}.")
            return

        pos = self.state["positions"].get(sym)
        if not pos:
            return
        proceeds = pos["qty"] * price
        pnl = (price - pos["entry"]) * pos["qty"]
        self.state["cash"] += proceeds
        del self.state["positions"][sym]
        self.log_action(sym, "SELL", reason, price, pos["qty"], pnl)

    def risk_checks(self, prices: Dict[str, float]):
        for sym, pos in list(self.state["positions"].items()):
            px = prices.get(sym)
            if not px: continue
            if px <= pos["stop"]:
                self._exit(sym, px, "STOP_LOSS")
            elif px >= pos["target"]:
                self._exit(sym, px, "TAKE_PROFIT")

    # --- logging ---
    def log_action(self, sym, action, reason, price, qty, pnl=None):
        entry = {
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "bot": self.name, "symbol": sym, "action": action,
            "reason": reason, "price": round(price, 3),
            "qty": round(qty, 3)
        }
        if pnl is not None:
            entry["pnl"] = round(pnl, 2)
        self.actions.append(entry)

    # --- main hooks ---
    def evaluate_full(self, insights: dict, prices: dict): pass
    def evaluate_loop(self, insights: dict, prices: dict): pass

    # --- run wrapper ---
    def run(self, mode: str, insights: dict, prices: dict):
        self.actions: List[dict] = []
        if mode == "full":
            self.evaluate_full(insights, prices)
        else:
            self.evaluate_loop(insights, prices)
        self.risk_checks(prices)
        _save_gz(self.path, self.state)
        return self.actions


# ==============================================================
# ---------- Specific Bots -------------------------------------
# ==============================================================

class MomentumBot(BaseBot):
    def __init__(self): super().__init__("momentum")
    def evaluate_full(self, insights, prices):
        picks = insights.get(HORIZON, [])[:50]
        for row in picks:
            sym = _get_symbol(row)
            if not sym:
                continue
            if sym not in self.state["positions"]:
                px = prices.get(sym)
                if px: self._enter(sym, px, "Top50 1w momentum")
    def evaluate_loop(self, insights, prices):
        # Double down on winners
        for sym, pos in list(self.state["positions"].items()):
            px = prices.get(sym)
            if not px: continue
            gain = (px - pos["entry"]) / pos["entry"]
            if gain > 0.05 and random.random() < 0.3:
                self._enter(sym, px, "DoubleDown")

class MeanRevertBot(BaseBot):
    def __init__(self): super().__init__("mean_revert")
    def evaluate_full(self, insights, prices):
        picks = insights.get("2w", [])[:50]
        for row in picks:
            sym = _get_symbol(row)
            if not sym:
                continue
            score = float(row.get("rankingScore", 0))
            if sym not in self.state["positions"] and score < 0.3:
                px = prices.get(sym)
                if px: self._enter(sym, px, "ReboundBuy")
    def evaluate_loop(self, insights, prices):
        # Sell laggards
        for sym, pos in list(self.state["positions"].items()):
            px = prices.get(sym)
            if not px: continue
            if (px - pos["entry"]) / pos["entry"] < -0.03:
                self._exit(sym, px, "LagExit")

class SignalFollowBot(BaseBot):
    def __init__(self): super().__init__("signal_follow")
    def evaluate_full(self, insights, prices):
        picks = insights.get("4w", [])[:50]
        for row in picks:
            sym = _get_symbol(row)
            if not sym:
                continue
            if sym not in self.state["positions"]:
                px = prices.get(sym)
                if px: self._enter(sym, px, "FollowAI")
    def evaluate_loop(self, insights, prices):
        pass  # follow signals only on full runs

class BreakoutBot(BaseBot):
    def __init__(self): super().__init__("breakout")
    def evaluate_full(self, insights, prices):
        picks = insights.get("1w", [])[:50]
        for row in picks:
            sym = _get_symbol(row)
            if not sym:
                continue
            if sym not in self.state["positions"] and random.random() < 0.2:
                px = prices.get(sym)
                if px: self._enter(sym, px, "RangeBreakout")
    def evaluate_loop(self, insights, prices):
        # rotate out of weak breakouts
        for sym, pos in list(self.state["positions"].items()):
            px = prices.get(sym)
            if not px: continue
            if (px - pos["entry"]) / pos["entry"] < -0.02:
                self._exit(sym, px, "BreakFail")

class HybridBot(BaseBot):
    def __init__(self): super().__init__("hybrid")
    def evaluate_full(self, insights, prices):
        picks = insights.get("1w", [])[:30] + insights.get("2w", [])[:20]
        for row in picks:
            sym = _get_symbol(row)
            if not sym:
                continue
            if sym not in self.state["positions"]:
                px = prices.get(sym)
                if px: self._enter(sym, px, "HybridBlend")
    def evaluate_loop(self, insights, prices):
        # periodic re-evaluation
        for sym in list(self.state["positions"].keys()):
            if random.random() < 0.1:
                px = prices.get(sym)
                if px: self._exit(sym, px, "HybridRebalance")


# ==============================================================
# ---------- Insights + Prices Loaders --------------------------
# ==============================================================
def load_insights() -> dict:
    out = {}
    for name in ("1w","2w","4w","52w"):
        f = INSIGHTS_DIR / f"top50_{name}.json"
        if f.exists():
            try:
                out[name] = json.load(open(f))
            except Exception: pass
    return out

def load_prices() -> dict:
    roll = _read_rolling() or {}
    prices = {}
    for sym, node in roll.items():
        px = _safe_float(node.get("close") or node.get("price"))
        if px: prices[sym] = px
    return prices


# ==============================================================
# ---------- Main Runner ----------------------------------------
# ==============================================================
def run_all_bots(mode: str = "full"):
    log(f"[BotRunner] 🤖 Nightly bots starting in {mode.upper()} mode")
    insights = load_insights()
    prices = load_prices()
    bots = [
        MomentumBot(),
        MeanRevertBot(),
        SignalFollowBot(),
        BreakoutBot(),
        HybridBot()
    ]

    successes, failures, log_entries = [], [], []
    pnl_values = {}

    for bot in bots:
        try:
            # record starting equity
            start_eq = bot.equity(prices)
            acts = bot.run(mode, insights, prices)
            end_eq = bot.equity(prices)
            pnl_pct = round(((end_eq - start_eq) / start_eq) * 100.0, 3) if start_eq else 0.0
            pnl_values[bot.name] = pnl_pct
            log_entries.extend(acts)
            successes.append(bot.name)
        except Exception as e:
            log(f"⚠️ [BotRunner] {bot.name} failed: {e}")
            failures.append(bot.name)
            pnl_values[bot.name] = 0.0

    # Write grouped JSON log
    date_tag = datetime.utcnow().strftime("%Y-%m-%d")
    path = LOG_DIR / f"bot_activity_{date_tag}.json"
    grouped: Dict[str, List[dict]] = {}
    for a in log_entries:
        grouped.setdefault(a["bot"], []).append(a)
    _save_gz(path, grouped)

    # ------------------------------------------------------------
    # Reset positions automatically at end of timeframe
    # ------------------------------------------------------------
    if mode == "full":
        # Each bot has its own persistence file, so wipe after reporting
        for bot in bots:
            try:
                bot.state["positions"] = {}
                _save_gz(bot.path, bot.state)
                log(f"[BotRunner] 🔁 Positions reset for {bot.name} (new cycle start).")
            except Exception as e:
                log(f"⚠️ Could not reset positions for {bot.name}: {e}")

    # Final summary
    total = len(bots)
    ok = len(successes)
    fail = len(failures)
    avg_pnl = sum(pnl_values.values()) / max(1, total)
    mode_label = "Full" if mode == "full" else "Intraday"

    if fail == 0:
        msg = f"✅ {mode_label} bots complete ({ok}/{total} successful, avg PnL {avg_pnl:+.2f}%)"
    else:
        failed_str = ", ".join(failures)
        msg = f"⚠️ {mode_label} bots complete ({ok}/{total}, avg PnL {avg_pnl:+.2f}%) — failed: {failed_str}"

    # Log + print summary for scheduler visibility
    log(f"[BotRunner] {msg}")
    print(msg)

    # Append to daily summary text log
    summary_path = LOG_DIR / "daily_summary.log"
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")

    # Detailed per-bot line in summary
    for bname, val in pnl_values.items():
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"    {bname:<15} {val:+.2f}%\n")

    return {
        "status": "ok" if fail == 0 else "partial",
        "mode": mode,
        "success": successes,
        "failed": failures,
        "avg_pnl": avg_pnl,
        "per_bot_pnl": pnl_values,
        "log_path": str(path),
        "summary_log": str(summary_path)
    }

    # ==============================================================
    # Horizon-based reset logic — clears positions after window
    # ==============================================================
    import datetime as dt

    # define horizon duration per timeframe (you can override per file)
    HORIZON = "1w"  # <-- set this per-file: "1w", "2w", or "4w"
    HORIZON_DAYS = {"1w": 5, "2w": 10, "4w": 20}.get(HORIZON, 5)

    def horizon_days_reached(opened_iso: str) -> bool:
        try:
            opened = dt.datetime.fromisoformat(opened_iso.replace("Z", ""))
            return (dt.datetime.utcnow() - opened).days >= HORIZON_DAYS
        except Exception:
            return False

    # perform per-bot reset checks
    for bot in bots:
        for sym, pos in list(bot.state.get("positions", {}).items()):
            opened = pos.get("opened")
            if opened and horizon_days_reached(opened):
                # exit position (simulate sell at entry price)
                bot._exit(sym, pos.get("entry", 0.0), f"RESET_{HORIZON}")
        if not bot.state.get("positions"):
            bot.state["cash"] = START_CASH
            log(f"[{bot.name}] 🔁 Resetting portfolio after {HORIZON} window.")

    # Final summary
    total = len(bots)
    ok = len(successes)
    fail = len(failures)
    avg_pnl = sum(pnl_values.values()) / max(1, total)
    mode_label = "Full" if mode == "full" else "Intraday"

    if fail == 0:
        msg = f"✅ {mode_label} bots complete ({ok}/{total} successful, avg PnL {avg_pnl:+.2f}%)"
    else:
        failed_str = ", ".join(failures)
        msg = f"⚠️ {mode_label} bots complete ({ok}/{total}, avg PnL {avg_pnl:+.2f}%) — failed: {failed_str}"

    # Log + print summary for scheduler visibility
    log(f"[BotRunner] {msg}")
    print(msg)

    # Append to daily summary text log
    summary_path = LOG_DIR / "daily_summary.log"
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")

    # Detailed per-bot line in summary
    for bname, val in pnl_values.items():
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"    {bname:<15} {val:+.2f}%\n")

    return {
        "status": "ok" if fail == 0 else "partial",
        "mode": mode,
        "success": successes,
        "failed": failures,
        "avg_pnl": avg_pnl,
        "per_bot_pnl": pnl_values,
        "log_path": str(path),
        "summary_log": str(summary_path)
    }

# ==============================================================
# ---------- CLI / Scheduler entry ------------------------------
# ==============================================================
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["full","loop"], default="full")
    args = p.parse_args()
    run_all_bots(args.mode)
