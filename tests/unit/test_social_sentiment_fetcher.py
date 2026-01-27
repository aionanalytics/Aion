"""Unit tests for social_sentiment_fetcher._safe_float function."""

import pytest
from backend.services.social_sentiment_fetcher import _safe_float


class TestSafeFloat:
    """Test the _safe_float helper function."""

    def test_safe_float_with_valid_float(self):
        """Test _safe_float with a valid float value."""
        assert _safe_float(3.14) == 3.14
        assert _safe_float(0.0) == 0.0
        assert _safe_float(-2.5) == -2.5

    def test_safe_float_with_valid_int(self):
        """Test _safe_float with a valid integer value."""
        assert _safe_float(42) == 42.0
        assert _safe_float(0) == 0.0
        assert _safe_float(-10) == -10.0

    def test_safe_float_with_string_number(self):
        """Test _safe_float with a string that can be converted to float."""
        assert _safe_float("3.14") == 3.14
        assert _safe_float("42") == 42.0
        assert _safe_float("-2.5") == -2.5

    def test_safe_float_with_none(self):
        """Test _safe_float with None value."""
        assert _safe_float(None) == 0.0
        assert _safe_float(None, 5.0) == 5.0

    def test_safe_float_with_invalid_string(self):
        """Test _safe_float with invalid string."""
        assert _safe_float("invalid") == 0.0
        assert _safe_float("abc", 10.0) == 10.0

    def test_safe_float_with_custom_default(self):
        """Test _safe_float with custom default value."""
        assert _safe_float(None, 99.9) == 99.9
        assert _safe_float("invalid", -1.0) == -1.0
        assert _safe_float("", 100.0) == 100.0

    def test_safe_float_with_empty_string(self):
        """Test _safe_float with empty string."""
        assert _safe_float("") == 0.0
        assert _safe_float("", 7.5) == 7.5

    def test_safe_float_with_dict(self):
        """Test _safe_float with dict (should return default)."""
        assert _safe_float({}) == 0.0
        assert _safe_float({"key": "value"}, 3.0) == 3.0

    def test_safe_float_with_list(self):
        """Test _safe_float with list (should return default)."""
        assert _safe_float([]) == 0.0
        assert _safe_float([1, 2, 3], 2.5) == 2.5
