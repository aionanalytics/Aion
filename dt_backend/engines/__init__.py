"""
dt_backend.engines package

Contains intraday "engine" components:

  • indicators
  • feature_engineering
  • backtesting_engine
  • broker_api
  • trade_executor
"""

from .indicators import sma, ema, rsi, pct_change, realized_vol
from .feature_engineering import build_intraday_features
from .backtesting_engine import run_intraday_backtest, BacktestConfig, BacktestResult

# Broker API exports:
# - v2.2 uses a per-bot allowance ledger; PAPER_STATE_PATH is kept as a backward-compat alias.
from .broker_api import (
    LEDGER_PATH,
    PAPER_STATE_PATH,
    Position,
    Order,
    get_positions,
    get_cash,
    submit_order,
)

from .trade_executor import ExecutionConfig, execute_from_policy

__all__ = [
    "sma",
    "ema",
    "rsi",
    "pct_change",
    "realized_vol",
    "build_intraday_features",
    "run_intraday_backtest",
    "BacktestConfig",
    "BacktestResult",
    "LEDGER_PATH",
    "PAPER_STATE_PATH",
    "Position",
    "Order",
    "get_positions",
    "get_cash",
    "submit_order",
    "ExecutionConfig",
    "execute_from_policy",
]
