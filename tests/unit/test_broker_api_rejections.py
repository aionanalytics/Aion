"""Tests for dt_backend/engines/broker_api.py rejection handling.

Tests that order rejections properly capture and log Alpaca rejection reasons.
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from dt_backend.engines import broker_api


@pytest.fixture
def temp_ledger_dir(tmp_path, monkeypatch):
    """Set up temporary ledger directory."""
    ledger_dir = tmp_path / "intraday" / "brokers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    
    # Override the ledger path
    monkeypatch.setenv("DT_TRUTH_DIR", str(tmp_path))
    monkeypatch.setenv("DT_BOT_ID", "test_bot")
    
    # Disable Alpaca for most tests
    monkeypatch.setenv("ALPACA_API_KEY_ID", "")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "")
    
    yield ledger_dir


class TestRejectionReasons:
    """Test that rejection reasons from Alpaca are properly captured."""
    
    def test_poll_order_timeout_increased_to_6_seconds(self):
        """Test that _poll_order default timeout is 6 seconds, not 4."""
        # Check the function signature
        import inspect
        sig = inspect.signature(broker_api._poll_order)
        default_timeout = sig.parameters['max_wait_s'].default
        
        assert default_timeout == 6.0, f"Expected timeout of 6.0, got {default_timeout}"
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api._alpaca_post')
    @patch('dt_backend.engines.broker_api._poll_order')
    @patch('dt_backend.engines.broker_api._cancel_order')
    def test_rejection_captures_alpaca_reason_and_message(
        self, mock_cancel, mock_poll, mock_post, mock_enabled, temp_ledger_dir
    ):
        """Test that rejections capture reason and message from Alpaca response."""
        # Enable Alpaca for this test
        mock_enabled.return_value = True
        
        # Mock order creation response
        mock_post.return_value = {"id": "test_order_123"}
        
        # Mock poll response with rejection details
        mock_poll.return_value = {
            "id": "test_order_123",
            "status": "rejected",
            "filled_qty": 0,
            "reason": "insufficient_buying_power",
            "message": "Insufficient buying power to place order",
        }
        
        # Create order
        order = broker_api.Order(
            symbol="AAPL",
            side="BUY",
            qty=100.0,
            limit_price=None,
        )
        
        result = broker_api.submit_order(order, last_price=150.0)
        
        # Verify poll was called with 6 second timeout
        mock_poll.assert_called_once()
        call_args = mock_poll.call_args
        assert call_args[1]['max_wait_s'] == 6.0
        
        # Verify rejection result includes Alpaca details
        assert result["status"] == "rejected"
        assert result["reason"] == "alpaca_not_filled_fast"
        assert result["alpaca_status"] == "rejected"
        assert result["alpaca_reason"] == "insufficient_buying_power"
        assert result["alpaca_message"] == "Insufficient buying power to place order"
        assert "alpaca_response" in result
        
        # Verify cancel was called
        mock_cancel.assert_called_once_with("test_order_123")
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api._alpaca_post')
    @patch('dt_backend.engines.broker_api._poll_order')
    @patch('dt_backend.engines.broker_api._cancel_order')
    def test_rejection_handles_empty_reason_fields(
        self, mock_cancel, mock_poll, mock_post, mock_enabled, temp_ledger_dir
    ):
        """Test that rejections handle missing reason/message gracefully."""
        mock_enabled.return_value = True
        mock_post.return_value = {"id": "test_order_456"}
        
        # Mock poll response without reason/message
        mock_poll.return_value = {
            "id": "test_order_456",
            "status": "canceled",
            "filled_qty": 0,
        }
        
        order = broker_api.Order(
            symbol="MSFT",
            side="BUY",
            qty=50.0,
            limit_price=None,
        )
        
        result = broker_api.submit_order(order, last_price=300.0)
        
        # Should still have the fields, just empty
        assert result["status"] == "rejected"
        assert result["alpaca_reason"] == ""
        assert result["alpaca_message"] == ""
        assert result["alpaca_status"] == "canceled"
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api._alpaca_post')
    def test_exception_captures_alpaca_error_json(
        self, mock_post, mock_enabled, temp_ledger_dir
    ):
        """Test that exceptions extract Alpaca error JSON from RuntimeError."""
        mock_enabled.return_value = True
        
        # Mock an exception with JSON error in message
        error_json = {
            "code": 40310000,
            "message": "insufficient buying power",
            "reason": "INSUFFICIENT_BUYING_POWER"
        }
        mock_post.side_effect = RuntimeError(
            f'alpaca POST /orders 403: {json.dumps(error_json)}'
        )
        
        order = broker_api.Order(
            symbol="TSLA",
            side="BUY",
            qty=10.0,
            limit_price=None,
        )
        
        result = broker_api.submit_order(order, last_price=200.0)
        
        # Verify error details were extracted
        assert result["status"] == "rejected"
        assert result["reason"] == "alpaca_error"
        assert "alpaca_reason" in result
        assert result["alpaca_reason"] == "INSUFFICIENT_BUYING_POWER"
        assert "alpaca_message" in result
        assert result["alpaca_message"] == "insufficient buying power"
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api._alpaca_post')
    def test_exception_handles_non_json_errors(
        self, mock_post, mock_enabled, temp_ledger_dir
    ):
        """Test that non-JSON exceptions are handled gracefully."""
        mock_enabled.return_value = True
        
        # Mock an exception without JSON
        mock_post.side_effect = RuntimeError("Network timeout")
        
        order = broker_api.Order(
            symbol="NVDA",
            side="BUY",
            qty=5.0,
            limit_price=None,
        )
        
        result = broker_api.submit_order(order, last_price=500.0)
        
        # Should still work, just without extracted reason/message
        assert result["status"] == "rejected"
        assert result["reason"] == "alpaca_error"
        assert "Network timeout" in result["detail"]
        # alpaca_reason and alpaca_message should not be present if parsing failed
        # (they're only added if they have values)


class TestPollingTimeout:
    """Test that polling timeout was increased from 4 to 6 seconds."""
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api._alpaca_post')
    @patch('dt_backend.engines.broker_api._alpaca_get')
    @patch('dt_backend.engines.broker_api._cancel_order')
    @patch('time.time')
    @patch('time.sleep')
    def test_poll_order_uses_6_second_timeout(
        self, mock_sleep, mock_time, mock_cancel, mock_get, mock_post, mock_enabled
    ):
        """Test that _poll_order polls for up to 6 seconds."""
        mock_enabled.return_value = True
        
        # Simulate time progression
        start_time = 1000.0
        mock_time.side_effect = [
            start_time,        # Initial time.time() call
            start_time + 1.0,  # First iteration check
            start_time + 2.0,  # Second iteration check
            start_time + 3.0,  # Third iteration check
            start_time + 4.0,  # Fourth iteration check
            start_time + 5.0,  # Fifth iteration check
            start_time + 6.5,  # Sixth iteration check - past deadline
        ]
        
        # Mock GET to return pending status
        mock_get.return_value = {
            "id": "order_789",
            "status": "pending_new",
            "filled_qty": 0,
        }
        
        result = broker_api._poll_order("order_789", max_wait_s=6.0)
        
        # Should have made multiple GET calls before timing out
        assert mock_get.call_count >= 5
        # Result should be the last response
        assert result["status"] == "pending_new"
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api._alpaca_post')
    @patch('dt_backend.engines.broker_api._poll_order')
    def test_submit_order_calls_poll_with_6_seconds(
        self, mock_poll, mock_post, mock_enabled
    ):
        """Test that submit_order calls _poll_order with 6 second timeout."""
        mock_enabled.return_value = True
        mock_post.return_value = {"id": "test_order"}
        
        # Mock poll to return a filled order
        mock_poll.return_value = {
            "id": "test_order",
            "status": "filled",
            "filled_qty": 10,
            "filled_avg_price": 150.0,
        }
        
        order = broker_api.Order(
            symbol="AAPL",
            side="BUY",
            qty=10.0,
            limit_price=None,
        )
        
        # Submit order (needs to initialize ledger)
        with patch('dt_backend.engines.broker_api._read_ledger') as mock_read:
            mock_read.return_value = {
                "cash": 10000.0,
                "positions": {"ACTIVE": {}, "CARRY": {}},
                "fills": [],
            }
            with patch('dt_backend.engines.broker_api._save_ledger'):
                broker_api.submit_order(order, last_price=150.0)
        
        # Verify poll was called with 6.0 seconds
        mock_poll.assert_called_once()
        assert mock_poll.call_args[1]['max_wait_s'] == 6.0


class TestLogging:
    """Test that rejection details are properly logged."""
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api._alpaca_post')
    @patch('dt_backend.engines.broker_api._poll_order')
    @patch('dt_backend.engines.broker_api._cancel_order')
    @patch('dt_backend.engines.broker_api.log')
    def test_rejection_logs_detailed_info(
        self, mock_log, mock_cancel, mock_poll, mock_post, mock_enabled, temp_ledger_dir
    ):
        """Test that rejections log detailed reason and message."""
        mock_enabled.return_value = True
        mock_post.return_value = {"id": "order_999"}
        
        mock_poll.return_value = {
            "id": "order_999",
            "status": "rejected",
            "filled_qty": 0,
            "reason": "symbol_not_tradable",
            "message": "Symbol AAPL is not tradable at this time",
        }
        
        order = broker_api.Order(
            symbol="AAPL",
            side="BUY",
            qty=100.0,
            limit_price=None,
        )
        
        broker_api.submit_order(order, last_price=150.0)
        
        # Find the rejection log call
        rejection_logs = [
            call for call in mock_log.call_args_list
            if "Order rejected" in str(call)
        ]
        
        assert len(rejection_logs) > 0, "Should have logged rejection"
        log_msg = str(rejection_logs[0])
        assert "symbol_not_tradable" in log_msg
        assert "Symbol AAPL is not tradable" in log_msg
