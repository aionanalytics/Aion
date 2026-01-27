"""backend.services.swing_outcome_logger — Trade Outcome Logger

Records every swing trade execution with detailed metrics for autonomous tuning.

Artifacts:
  • swing_outcomes.jsonl — append-only trade outcomes
  
Storage uses file-based pattern consistent with swing_truth_store.py

Trade Outcome Record:
{
  "bot_key": "swing_1w",
  "trade_id": "unique_id",
  "symbol": "AAPL",
  "side": "BUY|SELL",
  "entry_price": 150.00,
  "exit_price": 155.50,
  "qty": 100.0,
  "entry_confidence": 0.75,
  "expected_return": 0.052,
  "actual_return": 0.037,
  "hold_hours": 48.5,
  "exit_reason": "TAKE_PROFIT|STOP_LOSS|TARGET_REBALANCE|AI_CONFIRM|TIME_STOP",
  "regime_entry": "bull|bear|chop|stress",
  "regime_exit": "bull|bear|chop|stress",
  "pnl": 550.0,
  "pnl_pct": 3.67,
  "entry_ts": "2026-01-27T09:30:00Z",
  "exit_ts": "2026-01-29T14:15:00Z"
}
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from config import PATHS  # type: ignore
except Exception:  # pragma: no cover
    PATHS = {}  # type: ignore

try:
    from backend.core.data_pipeline import log  # type: ignore
except Exception:  # pragma: no cover
    def log(msg: str) -> None:  # type: ignore
        print(msg)


def _utc_iso() -> str:
    """Return current UTC time in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _swing_outcomes_dir() -> Path:
    """Return directory for swing outcome logs."""
    override = (os.getenv("SWING_OUTCOMES_DIR", "") or "").strip()
    if override:
        base = Path(override)
        base.mkdir(parents=True, exist_ok=True)
        return base
    
    # Default: da_brains/swing/outcomes
    da = PATHS.get("da_brains") if isinstance(PATHS, dict) else None
    base = Path(da) if da else Path("da_brains")
    base = base / "swing" / "outcomes"
    base.mkdir(parents=True, exist_ok=True)
    return base


def outcomes_path() -> Path:
    """Return path to swing outcomes log."""
    return _swing_outcomes_dir() / "swing_outcomes.jsonl"


@dataclass
class TradeOutcome:
    """Trade outcome record for tuning analysis."""
    bot_key: str
    trade_id: str
    symbol: str
    side: str  # BUY or SELL
    entry_price: float
    exit_price: float
    qty: float
    entry_confidence: float
    expected_return: float
    actual_return: float
    hold_hours: float
    exit_reason: str
    regime_entry: str
    regime_exit: str
    pnl: float
    pnl_pct: float
    entry_ts: str
    exit_ts: str
    
    # Optional fields
    phit: Optional[float] = None
    position_size_pct: Optional[float] = None
    stop_loss_used: Optional[float] = None
    take_profit_used: Optional[float] = None


def append_swing_outcome(
    *,
    bot_key: str,
    trade_id: Optional[str] = None,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    entry_confidence: float,
    expected_return: float,
    hold_hours: float,
    exit_reason: str,
    regime_entry: str = "unknown",
    regime_exit: str = "unknown",
    entry_ts: Optional[str] = None,
    exit_ts: Optional[str] = None,
    phit: Optional[float] = None,
    position_size_pct: Optional[float] = None,
    stop_loss_used: Optional[float] = None,
    take_profit_used: Optional[float] = None,
    **extra_fields: Any
) -> bool:
    """
    Append a trade outcome to the outcomes log.
    
    Args:
        bot_key: Bot identifier (swing_1w, swing_2w, swing_4w)
        trade_id: Unique trade identifier (generated if None)
        symbol: Stock symbol
        side: BUY or SELL
        entry_price: Entry price
        exit_price: Exit price
        qty: Quantity traded
        entry_confidence: Confidence level at entry (0-1)
        expected_return: Expected return from model (fraction)
        hold_hours: Hours position was held
        exit_reason: Reason for exit
        regime_entry: Market regime at entry
        regime_exit: Market regime at exit
        entry_ts: Entry timestamp (ISO8601)
        exit_ts: Exit timestamp (ISO8601)
        phit: Probability of hit (if using calibration)
        position_size_pct: Position size as % of portfolio
        stop_loss_used: Stop loss % used for this trade
        take_profit_used: Take profit % used for this trade
        **extra_fields: Additional fields to log
    
    Returns:
        True if successfully logged, False otherwise
    """
    try:
        # Generate trade_id if not provided
        if not trade_id:
            trade_id = f"{bot_key}_{symbol}_{uuid.uuid4().hex[:8]}"
        
        # Use current time if timestamps not provided
        if not entry_ts:
            entry_ts = _utc_iso()
        if not exit_ts:
            exit_ts = _utc_iso()
        
        # Calculate P&L
        if side.upper() == "BUY":
            actual_return = (exit_price - entry_price) / entry_price
            pnl = (exit_price - entry_price) * qty
        else:  # SELL
            actual_return = (entry_price - exit_price) / entry_price
            pnl = (entry_price - exit_price) * qty
        
        pnl_pct = actual_return * 100.0
        
        # Create outcome record
        outcome = TradeOutcome(
            bot_key=bot_key,
            trade_id=trade_id,
            symbol=symbol,
            side=side.upper(),
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            qty=float(qty),
            entry_confidence=float(entry_confidence),
            expected_return=float(expected_return),
            actual_return=float(actual_return),
            hold_hours=float(hold_hours),
            exit_reason=exit_reason,
            regime_entry=regime_entry,
            regime_exit=regime_exit,
            pnl=float(pnl),
            pnl_pct=float(pnl_pct),
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            phit=float(phit) if phit is not None else None,
            position_size_pct=float(position_size_pct) if position_size_pct is not None else None,
            stop_loss_used=float(stop_loss_used) if stop_loss_used is not None else None,
            take_profit_used=float(take_profit_used) if take_profit_used is not None else None,
        )
        
        # Convert to dict and add extra fields
        outcome_dict = asdict(outcome)
        outcome_dict.update(extra_fields)
        
        # Append to log file
        path = outcomes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(outcome_dict, ensure_ascii=False) + "\n")
        
        log(f"[outcome_logger] Logged trade {trade_id}: {symbol} {side} pnl={pnl:.2f}")
        return True
        
    except Exception as e:
        log(f"[outcome_logger] Failed to log trade outcome: {e}")
        return False


def load_recent_outcomes(
    *,
    bot_key: Optional[str] = None,
    days: int = 30,
    min_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Load recent trade outcomes from log.
    
    Args:
        bot_key: Filter by bot key (None = all)
        days: Number of days to look back
        min_date: Minimum date (ISO8601) to include
    
    Returns:
        List of outcome dictionaries
    """
    try:
        path = outcomes_path()
        if not path.exists():
            return []
        
        outcomes = []
        cutoff_date = None
        
        if min_date:
            cutoff_date = datetime.fromisoformat(min_date.replace("Z", "+00:00"))
        elif days > 0:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    outcome = json.loads(line)
                    
                    # Filter by bot_key
                    if bot_key and outcome.get("bot_key") != bot_key:
                        continue
                    
                    # Filter by date
                    if cutoff_date:
                        exit_ts = outcome.get("exit_ts", "")
                        if exit_ts:
                            exit_dt = datetime.fromisoformat(exit_ts.replace("Z", "+00:00"))
                            if exit_dt < cutoff_date:
                                continue
                    
                    outcomes.append(outcome)
                except Exception:
                    continue
        
        return outcomes
        
    except Exception as e:
        log(f"[outcome_logger] Failed to load outcomes: {e}")
        return []


# Needed for date filtering
from datetime import timedelta


def get_outcome_statistics(
    *,
    bot_key: Optional[str] = None,
    regime: Optional[str] = None,
    days: int = 30
) -> Dict[str, Any]:
    """
    Calculate statistics from recent outcomes.
    
    Args:
        bot_key: Filter by bot key
        regime: Filter by regime
        days: Number of days to analyze
    
    Returns:
        Dictionary with statistics:
        - total_trades: Total number of trades
        - win_rate: Percentage of profitable trades
        - avg_return: Average return
        - avg_hold_hours: Average hold time
        - sharpe_ratio: Estimated Sharpe ratio
        - exit_reasons: Distribution of exit reasons
    """
    try:
        outcomes = load_recent_outcomes(bot_key=bot_key, days=days)
        
        if regime:
            outcomes = [o for o in outcomes if o.get("regime_entry") == regime]
        
        if not outcomes:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_return": 0.0,
                "avg_hold_hours": 0.0,
                "sharpe_ratio": 0.0,
                "exit_reasons": {}
            }
        
        # Calculate statistics
        returns = [o.get("actual_return", 0.0) for o in outcomes]
        wins = [r for r in returns if r > 0]
        
        win_rate = len(wins) / len(returns) if returns else 0.0
        avg_return = sum(returns) / len(returns) if returns else 0.0
        
        # Sharpe ratio (annualized)
        if len(returns) > 1:
            import math
            std_return = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns))
            if std_return > 0:
                sharpe_ratio = (avg_return / std_return) * math.sqrt(252)  # Annualized
            else:
                sharpe_ratio = 0.0
        else:
            sharpe_ratio = 0.0
        
        # Hold time
        hold_hours = [o.get("hold_hours", 0.0) for o in outcomes]
        avg_hold_hours = sum(hold_hours) / len(hold_hours) if hold_hours else 0.0
        
        # Exit reasons
        exit_reasons = {}
        for o in outcomes:
            reason = o.get("exit_reason", "UNKNOWN")
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        
        return {
            "total_trades": len(outcomes),
            "win_rate": win_rate,
            "avg_return": avg_return,
            "avg_hold_hours": avg_hold_hours,
            "sharpe_ratio": sharpe_ratio,
            "exit_reasons": exit_reasons
        }
        
    except Exception as e:
        log(f"[outcome_logger] Failed to calculate statistics: {e}")
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "avg_hold_hours": 0.0,
            "sharpe_ratio": 0.0,
            "exit_reasons": {}
        }
