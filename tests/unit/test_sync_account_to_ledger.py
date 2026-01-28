"""Tests for sync_account_to_ledger() function in dt_backend/engines/broker_api.py.

Tests the new cash synchronization feature that syncs Alpaca account cash
to the local ledger to prevent insufficient_cash_allowance errors.
"""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from dt_backend.engines import broker_api


@pytest.fixture
def mock_ledger():
    """Mock ledger with depleted cash."""
    ledger_data = {
        "bot_id": "test_bot",
        "cash": 7333.55,  # Depleted local ledger
        "positions": {"ACTIVE": {}, "CARRY": {}},
        "fills": [],
        "meta": {
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "cash_cap": 100000.0,
            "venue": "alpaca_paper+local",
        },
    }
    
    saved_ledger = {"data": ledger_data.copy()}
    
    def mock_read():
        return saved_ledger["data"].copy()
    
    def mock_save(state):
        saved_ledger["data"] = state.copy()
    
    with patch('dt_backend.engines.broker_api._read_ledger', side_effect=mock_read):
        with patch('dt_backend.engines.broker_api._save_ledger', side_effect=mock_save):
            yield saved_ledger


class TestSyncAccountToLedger:
    """Test sync_account_to_ledger() function."""
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    def test_sync_skipped_when_alpaca_disabled(self, mock_enabled, mock_ledger):
        """Test that sync is skipped when Alpaca is disabled."""
        mock_enabled.return_value = False
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        assert result["status"] == "skipped"
        assert result["reason"] == "alpaca_disabled"
        assert result["synced"] is False
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    def test_sync_skipped_when_account_empty(self, mock_account, mock_enabled, mock_ledger):
        """Test that sync is skipped when Alpaca returns empty account."""
        mock_enabled.return_value = True
        mock_account.return_value = {}  # Empty account response
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        assert result["status"] == "skipped"
        assert result["reason"] == "empty_account_response"
        assert result["synced"] is False
        assert result["cash_before"] == 7333.55
        assert result["cash_after"] == 7333.55  # Unchanged
        
        # Verify ledger was not modified
        assert mock_ledger["data"]["cash"] == 7333.55
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    def test_sync_skipped_when_broker_cash_zero(self, mock_account, mock_enabled, mock_ledger):
        """Test that sync is skipped when broker cash is 0 or negative."""
        mock_enabled.return_value = True
        mock_account.return_value = {"cash": 0.0, "equity": 0.0}
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        assert result["status"] == "skipped"
        assert result["reason"] == "invalid_broker_cash"
        assert result["synced"] is False
        assert result["broker_cash"] == 0.0
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    def test_sync_succeeds_with_valid_broker_cash(self, mock_account, mock_enabled, mock_ledger):
        """Test that sync successfully updates ledger with broker cash."""
        mock_enabled.return_value = True
        mock_account.return_value = {
            "cash": 100000.0,  # Full Alpaca balance
            "equity": 100000.0,
            "portfolio_value": 100000.0,
        }
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        assert result["status"] == "ok"
        assert result["reason"] == "cash_synced"
        assert result["synced"] is True
        assert result["cash_before"] == 7333.55
        assert result["cash_after"] == 100000.0
        assert result["broker_cash"] == 100000.0
        
        # Verify ledger was updated
        assert mock_ledger["data"]["cash"] == 100000.0
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    def test_sync_uses_force_flag(self, mock_account, mock_enabled, mock_ledger):
        """Test that force=True is passed to get_account_cached."""
        mock_enabled.return_value = True
        mock_account.return_value = {"cash": 50000.0}
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        # Verify force=True was passed
        mock_account.assert_called_once_with(force=True)
        assert result["status"] == "ok"
        assert result["cash_after"] == 50000.0
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    def test_sync_without_force_flag(self, mock_account, mock_enabled, mock_ledger):
        """Test that force=False uses cached account data."""
        mock_enabled.return_value = True
        mock_account.return_value = {"cash": 75000.0}
        
        result = broker_api.sync_account_to_ledger(force=False)
        
        # Verify force=False was passed
        mock_account.assert_called_once_with(force=False)
        assert result["status"] == "ok"
        assert result["cash_after"] == 75000.0
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    def test_sync_handles_partial_account_data(self, mock_account, mock_enabled, mock_ledger):
        """Test that sync extracts cash from account with missing fields."""
        mock_enabled.return_value = True
        # Account with only cash field
        mock_account.return_value = {"cash": 90000.0}
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        assert result["status"] == "ok"
        assert result["cash_after"] == 90000.0
        assert result["broker_cash"] == 90000.0


class TestSyncIntegrationWithBrokerAPI:
    """Test sync_account_to_ledger through BrokerAPI wrapper."""
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    def test_broker_api_wrapper_exposes_sync(self, mock_account, mock_enabled, mock_ledger):
        """Test that BrokerAPI class exposes sync_account_to_ledger."""
        mock_enabled.return_value = True
        mock_account.return_value = {"cash": 100000.0}
        
        api = broker_api.BrokerAPI()
        result = api.sync_account_to_ledger(force=True)
        
        assert result["status"] == "ok"
        assert result["synced"] is True
        assert result["cash_after"] == 100000.0


class TestSyncLogging:
    """Test that sync operations are properly logged."""
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    @patch('dt_backend.engines.broker_api.log')
    def test_sync_logs_success(self, mock_log, mock_account, mock_enabled, mock_ledger):
        """Test that successful sync is logged."""
        mock_enabled.return_value = True
        mock_account.return_value = {"cash": 100000.0}
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        # Find the success log
        success_logs = [
            call for call in mock_log.call_args_list
            if "Synced cash" in str(call) and "✅" in str(call)
        ]
        
        assert len(success_logs) > 0
        log_msg = str(success_logs[0])
        assert "$7333.55" in log_msg
        assert "$100000.00" in log_msg
    
    @patch('dt_backend.engines.broker_api._alpaca_enabled')
    @patch('dt_backend.engines.broker_api.get_account_cached')
    @patch('dt_backend.engines.broker_api.log')
    def test_sync_logs_empty_account(self, mock_log, mock_account, mock_enabled, mock_ledger):
        """Test that empty account is logged."""
        mock_enabled.return_value = True
        mock_account.return_value = {}
        
        result = broker_api.sync_account_to_ledger(force=True)
        
        # Find the warning log
        warning_logs = [
            call for call in mock_log.call_args_list
            if "empty" in str(call).lower() and "⚠️" in str(call)
        ]
        
        assert len(warning_logs) > 0
