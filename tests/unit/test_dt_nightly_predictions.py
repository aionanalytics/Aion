"""Unit tests for DT nightly job predictions attachment."""

import pytest
from unittest.mock import patch, MagicMock
from dt_backend.jobs.dt_nightly_job import run_dt_nightly_job


class TestNightlyJobPredictions:
    """Test DT nightly job predictions attachment."""
    
    @patch('dt_backend.core.market_hours.is_trading_day')
    @patch('dt_backend.jobs.dt_nightly_job._brokers_dir')
    @patch('dt_backend.jobs.dt_nightly_job._write_metrics')
    @patch('dt_backend.jobs.dt_nightly_job._stamp_brain')
    @patch('dt_backend.jobs.dt_nightly_job.attach_intraday_predictions')
    def test_attach_predictions_called_on_trading_day(
        self, mock_attach_preds, mock_stamp_brain, mock_write_metrics, 
        mock_brokers_dir, mock_is_trading_day, tmp_path
    ):
        """Test that attach_intraday_predictions is called on trading days."""
        mock_is_trading_day.return_value = True
        mock_brokers_dir.return_value = tmp_path
        mock_write_metrics.return_value = tmp_path / "metrics.json"
        
        # Mock predictions result
        mock_attach_preds.return_value = {
            "status": "ok",
            "symbols_seen": 10,
            "predicted": 10,
            "missing_features": 0,
            "ts": "2024-01-08T20:00:00Z"
        }
        
        # Create empty bot files
        (tmp_path / "bot_1.json").write_text('{"fills": []}')
        
        result = run_dt_nightly_job(session_date="2024-01-08")
        
        # Verify attach_intraday_predictions was called
        mock_attach_preds.assert_called_once()
        
        # Verify predictions in summary
        assert "predictions" in result
        assert result["predictions"]["status"] == "ok"
        assert result["predictions"]["predicted"] == 10
    
    @patch('dt_backend.core.market_hours.is_trading_day')
    @patch('dt_backend.jobs.dt_nightly_job._brokers_dir')
    @patch('dt_backend.jobs.dt_nightly_job._write_metrics')
    @patch('dt_backend.jobs.dt_nightly_job._stamp_brain')
    @patch('dt_backend.jobs.dt_nightly_job.attach_intraday_predictions')
    def test_attach_predictions_handles_error(
        self, mock_attach_preds, mock_stamp_brain, mock_write_metrics,
        mock_brokers_dir, mock_is_trading_day, tmp_path
    ):
        """Test that prediction errors are handled gracefully."""
        mock_is_trading_day.return_value = True
        mock_brokers_dir.return_value = tmp_path
        mock_write_metrics.return_value = tmp_path / "metrics.json"
        
        # Mock predictions to raise an error
        mock_attach_preds.side_effect = Exception("Test prediction error")
        
        # Create empty bot files
        (tmp_path / "bot_1.json").write_text('{"fills": []}')
        
        result = run_dt_nightly_job(session_date="2024-01-08")
        
        # Job should still complete successfully
        assert result["status"] == "ok"
        
        # Predictions should have error status
        assert "predictions" in result
        assert result["predictions"]["status"] == "error"
        assert "Test prediction error" in result["predictions"]["error"]
    
    @patch('dt_backend.core.market_hours.is_trading_day')
    def test_predictions_skipped_on_non_trading_day(self, mock_is_trading_day):
        """Test that predictions are skipped on non-trading days."""
        mock_is_trading_day.return_value = False
        
        result = run_dt_nightly_job(session_date="2024-01-06")  # Saturday
        
        # Predictions should be skipped
        assert "predictions" in result
        assert result["predictions"]["status"] == "skipped"
    
    @patch('dt_backend.core.market_hours.is_trading_day')
    @patch('dt_backend.jobs.dt_nightly_job._brokers_dir')
    @patch('dt_backend.jobs.dt_nightly_job._write_metrics')
    @patch('dt_backend.jobs.dt_nightly_job._stamp_brain')
    @patch('dt_backend.jobs.dt_nightly_job.attach_intraday_predictions')
    def test_predictions_zero_symbols(
        self, mock_attach_preds, mock_stamp_brain, mock_write_metrics,
        mock_brokers_dir, mock_is_trading_day, tmp_path
    ):
        """Test handling when no symbols have predictions."""
        mock_is_trading_day.return_value = True
        mock_brokers_dir.return_value = tmp_path
        mock_write_metrics.return_value = tmp_path / "metrics.json"
        
        # Mock predictions with zero predictions
        mock_attach_preds.return_value = {
            "status": "no_rows",
            "symbols_seen": 5,
            "predicted": 0,
            "missing_features": 5,
            "ts": "2024-01-08T20:00:00Z"
        }
        
        # Create empty bot files
        (tmp_path / "bot_1.json").write_text('{"fills": []}')
        
        result = run_dt_nightly_job(session_date="2024-01-08")
        
        # Job should still complete successfully
        assert result["status"] == "ok"
        
        # Predictions should reflect zero predictions
        assert "predictions" in result
        assert result["predictions"]["predicted"] == 0
