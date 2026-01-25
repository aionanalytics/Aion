"""Unit tests for market hours detection."""

import pytest
from datetime import date, datetime
from dt_backend.core.market_hours import is_market_open, get_market_status, _get_us_market_holidays


class TestMarketHolidays:
    """Test US market holiday detection."""
    
    def test_new_years_day_2024(self):
        """Test New Year's Day 2024 (Monday, Jan 1)."""
        holidays = _get_us_market_holidays(2024)
        assert date(2024, 1, 1) in holidays
    
    def test_mlk_day_2024(self):
        """Test MLK Day 2024 (3rd Monday in January)."""
        holidays = _get_us_market_holidays(2024)
        # January 2024: 1st is Monday, so 3rd Monday is 15th
        assert date(2024, 1, 15) in holidays
    
    def test_presidents_day_2024(self):
        """Test Presidents Day 2024 (3rd Monday in February)."""
        holidays = _get_us_market_holidays(2024)
        # February 2024: 1st is Thursday, so 3rd Monday is 19th
        assert date(2024, 2, 19) in holidays
    
    def test_good_friday_2024(self):
        """Test Good Friday 2024."""
        holidays = _get_us_market_holidays(2024)
        # Easter 2024 is March 31, Good Friday is March 29
        assert date(2024, 3, 29) in holidays
    
    def test_memorial_day_2024(self):
        """Test Memorial Day 2024 (last Monday in May)."""
        holidays = _get_us_market_holidays(2024)
        assert date(2024, 5, 27) in holidays
    
    def test_independence_day_2024(self):
        """Test Independence Day 2024 (Thursday, July 4)."""
        holidays = _get_us_market_holidays(2024)
        assert date(2024, 7, 4) in holidays
    
    def test_labor_day_2024(self):
        """Test Labor Day 2024 (1st Monday in September)."""
        holidays = _get_us_market_holidays(2024)
        # September 2024: 1st is Sunday, so 1st Monday is 2nd
        assert date(2024, 9, 2) in holidays
    
    def test_thanksgiving_2024(self):
        """Test Thanksgiving 2024 (4th Thursday in November)."""
        holidays = _get_us_market_holidays(2024)
        # November 2024: 1st is Friday, so 4th Thursday is 28th
        assert date(2024, 11, 28) in holidays
    
    def test_christmas_2024(self):
        """Test Christmas 2024 (Wednesday, Dec 25)."""
        holidays = _get_us_market_holidays(2024)
        assert date(2024, 12, 25) in holidays
    
    def test_observed_holidays_when_weekend(self):
        """Test that holidays falling on weekends are observed on adjacent weekdays."""
        # Independence Day 2026 falls on Saturday
        holidays = _get_us_market_holidays(2026)
        # Should be observed on Friday July 3
        assert date(2026, 7, 3) in holidays
        # Saturday itself should not be in the list (it's a weekend anyway)
        # but the observed day (Friday) should be


class TestWeekendDetection:
    """Test weekend detection."""
    
    def test_saturday_is_closed(self):
        """Test that Saturday is detected as market closed."""
        # January 6, 2024 is a Saturday
        assert not is_market_open("2024-01-06")
    
    def test_sunday_is_closed(self):
        """Test that Sunday is detected as market closed."""
        # January 7, 2024 is a Sunday
        assert not is_market_open("2024-01-07")
    
    def test_monday_is_open(self):
        """Test that a regular Monday (non-holiday) is detected as open."""
        # January 8, 2024 is a Monday (not a holiday)
        assert is_market_open("2024-01-08")
    
    def test_friday_is_open(self):
        """Test that a regular Friday (non-holiday) is detected as open."""
        # January 5, 2024 is a Friday (not a holiday)
        assert is_market_open("2024-01-05")


class TestHolidayDetection:
    """Test specific holiday detection."""
    
    def test_mlk_day_is_closed(self):
        """Test that MLK Day is detected as market closed."""
        # January 15, 2024 is MLK Day
        assert not is_market_open("2024-01-15")
    
    def test_good_friday_is_closed(self):
        """Test that Good Friday is detected as market closed."""
        # March 29, 2024 is Good Friday
        assert not is_market_open("2024-03-29")
    
    def test_independence_day_is_closed(self):
        """Test that Independence Day is detected as market closed."""
        # July 4, 2024 is Independence Day (Thursday)
        assert not is_market_open("2024-07-04")
    
    def test_christmas_is_closed(self):
        """Test that Christmas is detected as market closed."""
        # December 25, 2024 is Christmas (Wednesday)
        assert not is_market_open("2024-12-25")


class TestMarketStatus:
    """Test get_market_status function."""
    
    def test_market_status_weekend(self):
        """Test market status returns correct info for weekend."""
        status = get_market_status("2024-01-06")  # Saturday
        assert status["is_weekend"] is True
        assert status["is_open"] is False
        assert status["date"] == "2024-01-06"
    
    def test_market_status_holiday(self):
        """Test market status returns correct info for holiday."""
        status = get_market_status("2024-01-15")  # MLK Day 2024
        assert status["is_holiday"] is True
        assert status["is_open"] is False
        assert status["date"] == "2024-01-15"
    
    def test_market_status_regular_trading_day(self):
        """Test market status returns correct info for regular trading day."""
        status = get_market_status("2024-01-08")  # Regular Monday
        assert status["is_weekend"] is False
        assert status["is_holiday"] is False
        assert status["date"] == "2024-01-08"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_none_date_uses_current(self):
        """Test that None date uses current NY time."""
        # This should not raise an error
        result = is_market_open(None)
        assert isinstance(result, bool)
    
    def test_invalid_date_format(self):
        """Test that invalid date format falls back gracefully."""
        # Should not raise an error, should use current date
        result = is_market_open("not-a-date")
        assert isinstance(result, bool)
    
    def test_different_years(self):
        """Test that holidays work across different years."""
        # MLK Day 2025 (3rd Monday in January)
        holidays_2025 = _get_us_market_holidays(2025)
        assert date(2025, 1, 20) in holidays_2025
        
        # MLK Day 2026
        holidays_2026 = _get_us_market_holidays(2026)
        assert date(2026, 1, 19) in holidays_2026
