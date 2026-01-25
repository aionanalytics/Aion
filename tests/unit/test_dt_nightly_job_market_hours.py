"""Unit tests for DT nightly job market closed detection."""

import pytest
from unittest.mock import patch, MagicMock
from dt_backend.jobs.dt_nightly_job import run_dt_nightly_job


class TestNightlyJobMarketClosed:
    """Test DT nightly job behavior when market is closed."""
    
    @patch('dt_backend.core.market_hours.is_market_open')
    def test_weekend_returns_skipped(self, mock_is_market_open):
        """Test that nightly job returns skipped status on weekends."""
        mock_is_market_open.return_value = False
        
        result = run_dt_nightly_job(session_date="2024-01-06")  # Saturday
        
        assert result["status"] == "ok"
        assert result["trades"] == 0
        assert result["win_rate"] is None
        assert result["realized_pnl"] == 0.0
        assert result["continuous_learning"] == "skipped"
        assert result["knob_tuner"]["status"] == "skipped"
        assert "Market closed" in result["note"]
    
    @patch('dt_backend.core.market_hours.is_market_open')
    def test_holiday_returns_skipped(self, mock_is_market_open):
        """Test that nightly job returns skipped status on holidays."""
        mock_is_market_open.return_value = False
        
        result = run_dt_nightly_job(session_date="2024-01-15")  # MLK Day
        
        assert result["status"] == "ok"
        assert result["trades"] == 0
        assert result["win_rate"] is None
        assert result["realized_pnl"] == 0.0
        assert result["continuous_learning"] == "skipped"
        assert result["knob_tuner"]["status"] == "skipped"
        assert "Market closed" in result["note"]
    
    @patch('dt_backend.core.market_hours.is_market_open')
    def test_after_hours_returns_skipped(self, mock_is_market_open):
        """Test that nightly job returns skipped status after hours."""
        mock_is_market_open.return_value = False
        
        result = run_dt_nightly_job(session_date="2024-01-08")  # Regular day, but after hours
        
        assert result["status"] == "ok"
        assert result["trades"] == 0
        assert result["continuous_learning"] == "skipped"
        assert result["knob_tuner"]["status"] == "skipped"


class TestNightlyJobMarketOpen:
    """Test DT nightly job behavior when market is open."""
    
    @patch('dt_backend.core.market_hours.is_market_open')
    @patch('dt_backend.jobs.dt_nightly_job._brokers_dir')
    @patch('dt_backend.jobs.dt_nightly_job._write_metrics')
    @patch('dt_backend.jobs.dt_nightly_job._stamp_brain')
    def test_trading_day_processes_normally(self, mock_stamp_brain, mock_write_metrics, 
                                           mock_brokers_dir, mock_is_market_open, tmp_path):
        """Test that nightly job processes normally on trading days."""
        mock_is_market_open.return_value = True
        mock_brokers_dir.return_value = tmp_path
        mock_write_metrics.return_value = tmp_path / "metrics.json"
        
        # Create empty bot files
        (tmp_path / "bot_1.json").write_text('{"fills": []}')
        
        result = run_dt_nightly_job(session_date="2024-01-08")  # Regular Monday
        
        assert result["status"] == "ok"
        assert "trades" in result
        assert "win_rate" in result
        assert "realized_pnl" in result
        assert "continuous_learning" in result
        assert "knob_tuner" in result
    
    @patch('dt_backend.core.market_hours.is_market_open')
    @patch('dt_backend.jobs.dt_nightly_job._brokers_dir')
    @patch('dt_backend.jobs.dt_nightly_job._write_metrics')
    @patch('dt_backend.jobs.dt_nightly_job._stamp_brain')
    @patch('dt_backend.jobs.dt_nightly_job.run_dt_knob_tuner')
    def test_knob_tuner_enabled_on_trading_day(self, mock_run_tuner, mock_stamp_brain, 
                                                mock_write_metrics, mock_brokers_dir, 
                                                mock_is_market_open, tmp_path):
        """Test that knob tuner is enabled with 'ok' status on trading days."""
        mock_is_market_open.return_value = True
        mock_brokers_dir.return_value = tmp_path
        mock_write_metrics.return_value = tmp_path / "metrics.json"
        mock_run_tuner.return_value = {"status": "success", "adjustments": 0}
        
        # Create empty bot files
        (tmp_path / "bot_1.json").write_text('{"fills": []}')
        
        result = run_dt_nightly_job(session_date="2024-01-08")
        
        # Knob tuner should be called and status should be "ok"
        assert "knob_tuner" in result
        assert result["knob_tuner"]["status"] == "ok"
    
    @patch('dt_backend.core.market_hours.is_market_open')
    @patch('dt_backend.jobs.dt_nightly_job._brokers_dir')
    @patch('dt_backend.jobs.dt_nightly_job._write_metrics')
    @patch('dt_backend.jobs.dt_nightly_job._stamp_brain')
    @patch('dt_backend.jobs.dt_nightly_job.run_dt_knob_tuner', None)
    def test_knob_tuner_fallback_when_missing(self, mock_stamp_brain, mock_write_metrics, 
                                              mock_brokers_dir, mock_is_market_open, tmp_path):
        """Test that knob tuner falls back to 'ok' status when function is missing."""
        mock_is_market_open.return_value = True
        mock_brokers_dir.return_value = tmp_path
        mock_write_metrics.return_value = tmp_path / "metrics.json"
        
        # Create empty bot files
        (tmp_path / "bot_1.json").write_text('{"fills": []}')
        
        result = run_dt_nightly_job(session_date="2024-01-08")
        
        # Even when run_dt_knob_tuner is None, status should be "ok" with default values
        assert "knob_tuner" in result
        assert result["knob_tuner"]["status"] == "ok"
        assert "tuned_at" in result["knob_tuner"]
        assert "version" in result["knob_tuner"]


class TestNightlyJobBotLedgerProcessing:
    """Test bot ledger processing with market checks."""
    
    @patch('dt_backend.core.market_hours.is_market_open')
    @patch('dt_backend.jobs.dt_nightly_job._brokers_dir')
    @patch('dt_backend.jobs.dt_nightly_job._write_metrics')
    @patch('dt_backend.jobs.dt_nightly_job._stamp_brain')
    def test_no_stale_data_on_weekend(self, mock_stamp_brain, mock_write_metrics,
                                      mock_brokers_dir, mock_is_market_open, tmp_path):
        """Test that stale bot ledger data is not processed on weekends."""
        mock_is_market_open.return_value = False
        mock_brokers_dir.return_value = tmp_path
        
        # Create bot files with Friday's data
        bot_ledger = {
            "fills": [
                {"side": "SELL", "realized_pnl": 10.0},
                {"side": "SELL", "realized_pnl": -5.0}
            ]
        }
        (tmp_path / "bot_1.json").write_text(str(bot_ledger))
        
        result = run_dt_nightly_job(session_date="2024-01-06")  # Saturday
        
        # Should skip without processing ledgers
        assert result["trades"] == 0
        assert result["realized_pnl"] == 0.0
        assert result["continuous_learning"] == "skipped"
        # write_metrics should not be called
        mock_write_metrics.assert_not_called()
