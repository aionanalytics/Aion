"""Shared pytest fixtures for unit tests."""

import pytest
from typing import Dict, Any
from dt_backend.core.policy_engine_dt import PolicyConfig


@pytest.fixture
def default_policy_config() -> PolicyConfig:
    """Default PolicyConfig for testing."""
    return PolicyConfig()


@pytest.fixture
def strict_policy_config() -> PolicyConfig:
    """Strict PolicyConfig with higher confidence requirements."""
    cfg = PolicyConfig()
    cfg.min_confidence = 0.40
    cfg.buy_threshold = 0.15
    cfg.sell_threshold = -0.15
    cfg.confirmations_to_flip = 3
    return cfg


@pytest.fixture
def sample_node_no_position() -> Dict[str, Any]:
    """Sample symbol node without a position."""
    return {
        "context_dt": {
            "intraday_trend": "bull",
            "vol_bucket": "medium",
            "intraday_return": 0.015,
        },
        "features_dt": {
            "rsi_14": 55.0,
            "atr_14": 2.5,
            "last_price": 150.25,
        },
        "predictions_dt": {
            "p_buy": 0.65,
            "p_sell": 0.20,
            "p_hold": 0.15,
        },
    }


@pytest.fixture
def sample_node_with_position() -> Dict[str, Any]:
    """Sample symbol node with an open position."""
    return {
        "context_dt": {
            "intraday_trend": "bull",
            "vol_bucket": "medium",
        },
        "features_dt": {
            "rsi_14": 55.0,
            "atr_14": 2.5,
            "last_price": 150.25,
        },
        "predictions_dt": {
            "p_buy": 0.65,
            "p_sell": 0.20,
            "p_hold": 0.15,
        },
        "position_dt": {
            "qty": 100.0,
            "avg_price": 145.00,
            "side": "BUY",
        },
    }


@pytest.fixture
def sample_rolling_basic() -> Dict[str, Any]:
    """Basic rolling cache for testing."""
    return {
        "AAPL": {
            "context_dt": {
                "intraday_trend": "bull",
                "vol_bucket": "medium",
            },
            "features_dt": {
                "rsi_14": 55.0,
                "atr_14": 2.5,
                "last_price": 150.25,
            },
            "predictions_dt": {
                "p_buy": 0.65,
                "p_sell": 0.20,
                "p_hold": 0.15,
            },
        },
        "_GLOBAL_DT": {
            "regime_dt": {
                "label": "bull",
                "confidence": 0.75,
            },
        },
    }


@pytest.fixture
def sample_rolling_with_history() -> Dict[str, Any]:
    """Rolling cache with policy history for hysteresis testing."""
    return {
        "AAPL": {
            "context_dt": {
                "intraday_trend": "bull",
                "vol_bucket": "medium",
            },
            "features_dt": {
                "rsi_14": 55.0,
                "atr_14": 2.5,
                "last_price": 150.25,
            },
            "predictions_dt": {
                "p_buy": 0.50,
                "p_sell": 0.40,
                "p_hold": 0.10,
            },
            "policy_dt": {
                "action": "HOLD",
                "intent": "HOLD",
                "confidence": 0.0,
                "trade_gate": False,
                "_state": {
                    "prev_action": "HOLD",
                    "pending_action": "",
                    "pending_count": 0,
                },
            },
        },
        "_GLOBAL_DT": {
            "regime_dt": {
                "label": "bull",
                "confidence": 0.75,
            },
        },
    }


@pytest.fixture
def sample_positions_state() -> Dict[str, Any]:
    """Sample positions state for P&L attribution tests."""
    return {
        "AAPL": {
            "status": "OPEN",
            "side": "BUY",
            "qty": 100.0,
            "entry_price": 145.00,
            "entry_ts": "2024-01-15T09:30:00Z",
            "stop": 142.00,
            "take_profit": 152.00,
            "bot": "ORB",
            "confidence": 0.75,
        },
        "MSFT": {
            "status": "CLOSED",
            "side": "BUY",
            "qty": 50.0,
            "entry_price": 380.00,
            "entry_ts": "2024-01-15T09:35:00Z",
            "last_exit_ts": "2024-01-15T11:00:00Z",
            "last_exit_reason": "take_profit",
            "bot": "VWAP",
            "confidence": 0.65,
        },
    }
