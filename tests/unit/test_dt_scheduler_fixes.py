"""Unit tests for DT scheduler bug fixes.

Tests for:
1. Infinite loop prevention when scheduler starts after market close
2. Weekend/holiday detection to skip DT nightly on non-trading days
"""

import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone
from dt_backend.jobs.dt_scheduler import _is_market_open_on_date


class TestIsMarketOpenOnDate:
    """Test _is_market_open_on_date helper function."""
    
    @patch('dt_backend.core.market_hours.is_market_open')
    def test_trading_day_returns_true(self, mock_is_market_open):
        """Test that a regular trading day returns True."""
        mock_is_market_open.return_value = True
        
        result = _is_market_open_on_date("2024-01-08")  # Monday
        
        assert result is True
        mock_is_market_open.assert_called_once_with("2024-01-08")
    
    @patch('dt_backend.core.market_hours.is_market_open')
    def test_weekend_returns_false(self, mock_is_market_open):
        """Test that weekend returns False."""
        mock_is_market_open.return_value = False
        
        result = _is_market_open_on_date("2024-01-06")  # Saturday
        
        assert result is False
        mock_is_market_open.assert_called_once_with("2024-01-06")
    
    @patch('dt_backend.core.market_hours.is_market_open')
    def test_holiday_returns_false(self, mock_is_market_open):
        """Test that holiday returns False."""
        mock_is_market_open.return_value = False
        
        result = _is_market_open_on_date("2024-01-15")  # MLK Day
        
        assert result is False
        mock_is_market_open.assert_called_once_with("2024-01-15")
    
    def test_fallback_weekday_check(self):
        """Test fallback to simple weekday check when market_hours is unavailable."""
        # Patch to raise exception to test fallback
        with patch('dt_backend.jobs.dt_scheduler.is_market_open', side_effect=ImportError):
            # Monday should return True (weekday < 5)
            result = _is_market_open_on_date("2024-01-08")
            assert result is True
            
            # Saturday should return False (weekday >= 5)
            result = _is_market_open_on_date("2024-01-06")
            assert result is False
            
            # Sunday should return False (weekday >= 5)
            result = _is_market_open_on_date("2024-01-07")
            assert result is False
    
    def test_invalid_date_returns_false(self):
        """Test that invalid date string returns False."""
        with patch('dt_backend.jobs.dt_scheduler.is_market_open', side_effect=ImportError):
            result = _is_market_open_on_date("not-a-date")
            assert result is False


class TestSchedulerInfiniteLoopFix:
    """Test that scheduler doesn't enter infinite loop when started after market close."""
    
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_fallback')
    @patch('dt_backend.jobs.dt_scheduler._now_ny')
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_on_date')
    @patch('dt_backend.jobs.dt_scheduler.last_dt_nightly_session_date')
    @patch('dt_backend.jobs.dt_scheduler.run_dt_nightly_job')
    @patch('dt_backend.jobs.dt_scheduler.acquire_scheduler_lock')
    @patch('dt_backend.jobs.dt_scheduler.release_lock_file')
    @patch('dt_backend.jobs.dt_scheduler.time.sleep')
    def test_late_start_weekend_skips_nightly(self, mock_sleep, mock_release, mock_acquire,
                                               mock_run_nightly, mock_last_nightly, 
                                               mock_is_market_open_date, mock_now_ny,
                                               mock_is_open_fallback):
        """Test that late-start on weekend skips DT nightly and doesn't loop."""
        # Setup: Scheduler starts Sunday 3:26 AM (after close)
        mock_acquire.return_value = MagicMock()
        mock_is_open_fallback.return_value = False  # Market closed
        
        # Sunday Jan 7, 2024 at 3:26 AM
        mock_now_ny.return_value = datetime(2024, 1, 7, 3, 26, 0)
        
        # Session date will be Saturday (yesterday from the 16:00:01 calculation)
        mock_is_market_open_date.return_value = False  # Weekend
        mock_last_nightly.return_value = None
        
        # Run for only 2 iterations then stop
        iteration_count = [0]
        def sleep_side_effect(seconds):
            iteration_count[0] += 1
            if iteration_count[0] >= 2:
                raise KeyboardInterrupt("Stop test")
        
        mock_sleep.side_effect = sleep_side_effect
        
        from dt_backend.jobs.dt_scheduler import run_dt_scheduler
        
        with pytest.raises(KeyboardInterrupt):
            run_dt_scheduler()
        
        # DT nightly should NOT be called (weekend)
        mock_run_nightly.assert_not_called()
    
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_fallback')
    @patch('dt_backend.jobs.dt_scheduler._now_ny')
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_on_date')
    @patch('dt_backend.jobs.dt_scheduler.last_dt_nightly_session_date')
    @patch('dt_backend.jobs.dt_scheduler.run_dt_nightly_job')
    @patch('dt_backend.jobs.dt_scheduler.acquire_scheduler_lock')
    @patch('dt_backend.jobs.dt_scheduler.release_lock_file')
    @patch('dt_backend.jobs.dt_scheduler.time.sleep')
    def test_late_start_trading_day_runs_once(self, mock_sleep, mock_release, mock_acquire,
                                               mock_run_nightly, mock_last_nightly,
                                               mock_is_market_open_date, mock_now_ny,
                                               mock_is_open_fallback):
        """Test that late-start on trading day runs DT nightly ONCE only."""
        # Setup: Scheduler starts Friday 5:00 PM (after close)
        mock_acquire.return_value = MagicMock()
        mock_is_open_fallback.return_value = False  # Market closed
        
        # Friday Jan 5, 2024 at 5:00 PM
        mock_now_ny.return_value = datetime(2024, 1, 5, 17, 0, 0)
        
        # Session date will be Friday (today from the 16:00:01 calculation)
        mock_is_market_open_date.return_value = True  # Trading day
        mock_last_nightly.return_value = None
        mock_run_nightly.return_value = {"status": "ok"}
        
        # Run for only 3 iterations then stop
        iteration_count = [0]
        def sleep_side_effect(seconds):
            iteration_count[0] += 1
            if iteration_count[0] >= 3:
                raise KeyboardInterrupt("Stop test")
        
        mock_sleep.side_effect = sleep_side_effect
        
        from dt_backend.jobs.dt_scheduler import run_dt_scheduler
        
        with pytest.raises(KeyboardInterrupt):
            run_dt_scheduler()
        
        # DT nightly should be called EXACTLY ONCE (not in a loop)
        assert mock_run_nightly.call_count == 1
    
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_fallback')
    @patch('dt_backend.jobs.dt_scheduler._now_ny')
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_on_date')
    @patch('dt_backend.jobs.dt_scheduler.last_dt_nightly_session_date')
    @patch('dt_backend.jobs.dt_scheduler.run_dt_nightly_job')
    @patch('dt_backend.jobs.dt_scheduler.acquire_scheduler_lock')
    @patch('dt_backend.jobs.dt_scheduler.release_lock_file')
    @patch('dt_backend.jobs.dt_scheduler.time.sleep')
    def test_late_start_already_done_skips(self, mock_sleep, mock_release, mock_acquire,
                                            mock_run_nightly, mock_last_nightly,
                                            mock_is_market_open_date, mock_now_ny,
                                            mock_is_open_fallback):
        """Test that late-start skips if nightly already done for the session."""
        # Setup: Scheduler starts after close, but nightly already ran
        mock_acquire.return_value = MagicMock()
        mock_is_open_fallback.return_value = False
        
        # Friday Jan 5, 2024 at 5:00 PM
        mock_now_ny.return_value = datetime(2024, 1, 5, 17, 0, 0)
        
        mock_is_market_open_date.return_value = True
        # Last nightly was already done for this session
        mock_last_nightly.return_value = "2024-01-05"
        
        # Run for only 2 iterations
        iteration_count = [0]
        def sleep_side_effect(seconds):
            iteration_count[0] += 1
            if iteration_count[0] >= 2:
                raise KeyboardInterrupt("Stop test")
        
        mock_sleep.side_effect = sleep_side_effect
        
        from dt_backend.jobs.dt_scheduler import run_dt_scheduler
        
        with pytest.raises(KeyboardInterrupt):
            run_dt_scheduler()
        
        # DT nightly should NOT be called (already done)
        mock_run_nightly.assert_not_called()


class TestSchedulerWeekendHolidaySkip:
    """Test that scheduler properly skips weekends and holidays."""
    
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_fallback')
    @patch('dt_backend.jobs.dt_scheduler._now_ny')
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_on_date')
    @patch('dt_backend.jobs.dt_scheduler.last_dt_nightly_session_date')
    @patch('dt_backend.jobs.dt_scheduler.run_dt_nightly_job')
    @patch('dt_backend.jobs.dt_scheduler.acquire_scheduler_lock')
    @patch('dt_backend.jobs.dt_scheduler.release_lock_file')
    @patch('dt_backend.jobs.dt_scheduler.time.sleep')
    @patch('dt_backend.jobs.dt_scheduler.log')
    def test_weekend_logs_skip_message(self, mock_log, mock_sleep, mock_release, mock_acquire,
                                        mock_run_nightly, mock_last_nightly,
                                        mock_is_market_open_date, mock_now_ny,
                                        mock_is_open_fallback):
        """Test that weekend skip is logged correctly."""
        mock_acquire.return_value = MagicMock()
        mock_is_open_fallback.return_value = False
        
        # Sunday Jan 7, 2024 at 5:00 PM (AFTER 16:00:01 close)
        mock_now_ny.return_value = datetime(2024, 1, 7, 17, 0, 0)
        
        mock_is_market_open_date.return_value = False  # Weekend
        mock_last_nightly.return_value = None
        
        # Run for only 2 iterations (first logs skip, second doesn't log due to tracking)
        iteration_count = [0]
        def sleep_side_effect(seconds):
            iteration_count[0] += 1
            if iteration_count[0] >= 2:
                raise KeyboardInterrupt("Stop test")
        
        mock_sleep.side_effect = sleep_side_effect
        
        from dt_backend.jobs.dt_scheduler import run_dt_scheduler
        
        with pytest.raises(KeyboardInterrupt):
            run_dt_scheduler()
        
        # Debug: Print all log calls
        print(f"Log calls: {[str(call) for call in mock_log.call_args_list]}")
        
        # Check that skip message was logged
        skip_logged = any(
            'skipping DT nightly' in str(call[0]) and 'weekend/holiday' in str(call[0])
            for call in mock_log.call_args_list
        )
        assert skip_logged, f"Expected weekend skip message to be logged. Got: {mock_log.call_args_list}"
        
        # DT nightly should NOT be called
        mock_run_nightly.assert_not_called()
    
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_fallback')
    @patch('dt_backend.jobs.dt_scheduler._now_ny')
    @patch('dt_backend.jobs.dt_scheduler._is_market_open_on_date')
    @patch('dt_backend.jobs.dt_scheduler.last_dt_nightly_session_date')
    @patch('dt_backend.jobs.dt_scheduler.run_dt_nightly_job')
    @patch('dt_backend.jobs.dt_scheduler.acquire_scheduler_lock')
    @patch('dt_backend.jobs.dt_scheduler.release_lock_file')
    @patch('dt_backend.jobs.dt_scheduler.time.sleep')
    @patch('dt_backend.jobs.dt_scheduler.log')
    def test_holiday_logs_skip_message(self, mock_log, mock_sleep, mock_release, mock_acquire,
                                        mock_run_nightly, mock_last_nightly,
                                        mock_is_market_open_date, mock_now_ny,
                                        mock_is_open_fallback):
        """Test that holiday skip is logged correctly."""
        mock_acquire.return_value = MagicMock()
        mock_is_open_fallback.return_value = False
        
        # MLK Day 2024 (Monday Jan 15) at 5:00 PM (AFTER 16:00:01 close)
        mock_now_ny.return_value = datetime(2024, 1, 15, 17, 0, 0)
        
        mock_is_market_open_date.return_value = False  # Holiday
        mock_last_nightly.return_value = None
        
        # Run for only 2 iterations (first logs skip, second doesn't log due to tracking)
        iteration_count = [0]
        def sleep_side_effect(seconds):
            iteration_count[0] += 1
            if iteration_count[0] >= 2:
                raise KeyboardInterrupt("Stop test")
        
        mock_sleep.side_effect = sleep_side_effect
        
        from dt_backend.jobs.dt_scheduler import run_dt_scheduler
        
        with pytest.raises(KeyboardInterrupt):
            run_dt_scheduler()
        
        # Check that skip message was logged
        skip_logged = any(
            'skipping DT nightly' in str(call[0]) and 'weekend/holiday' in str(call[0])
            for call in mock_log.call_args_list
        )
        assert skip_logged, "Expected holiday skip message to be logged"
        
        # DT nightly should NOT be called
        mock_run_nightly.assert_not_called()
