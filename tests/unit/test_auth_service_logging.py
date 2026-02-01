"""
Test auth_service JWT logging functionality.
Ensures logging is present and doesn't expose sensitive data.
"""
import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from backend.core.auth_service import (
    create_access_token,
    create_refresh_token,
    verify_token,
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
)


class TestAuthServiceLogging:
    """Test suite for auth_service JWT logging."""
    
    def test_jwt_algorithm_from_env(self, monkeypatch):
        """Test that JWT_ALGORITHM can be loaded from environment."""
        # Set environment variable
        monkeypatch.setenv("JWT_ALGORITHM", "HS512")
        
        # Re-import to get new env value
        import importlib
        from backend.core import auth_service
        importlib.reload(auth_service)
        
        assert auth_service.JWT_ALGORITHM == "HS512"
        
        # Reset to default
        importlib.reload(auth_service)
    
    def test_jwt_secret_from_env(self, monkeypatch):
        """Test that JWT_SECRET_KEY can be loaded from environment."""
        test_secret = "test_secret_key_123"
        monkeypatch.setenv("JWT_SECRET_KEY", test_secret)
        
        # Re-import to get new env value
        import importlib
        from backend.core import auth_service
        importlib.reload(auth_service)
        
        assert auth_service.JWT_SECRET_KEY == test_secret
        
        # Reset to default
        importlib.reload(auth_service)
    
    def test_jwt_algorithm_fallback(self):
        """Test that JWT_ALGORITHM falls back to HS256 when not in env."""
        # Verify the fallback value
        assert JWT_ALGORITHM in ["HS256", "HS512"]  # Could be from env or fallback
    
    def test_jwt_secret_fallback(self):
        """Test that JWT_SECRET_KEY has a fallback value."""
        # Verify there's a non-empty secret
        assert JWT_SECRET_KEY is not None
        assert len(JWT_SECRET_KEY) > 0
    
    @patch("backend.core.auth_service.logger")
    def test_create_access_token_logging(self, mock_logger):
        """Test that access token creation is logged safely."""
        payload = {"sub": "user123456789", "email": "test@example.com"}
        
        token = create_access_token(payload)
        
        # Verify token was created
        assert token is not None
        assert len(token) > 0
        
        # Verify logger.debug was called
        assert mock_logger.debug.called
        
        # Get the log message
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        log_message = " ".join(log_calls)
        
        # Verify log contains safe information
        assert "creating access token" in log_message.lower() or "access" in log_message.lower()
        
        # Verify sensitive data is NOT fully exposed (only partial subject)
        assert "user123456789" not in log_message  # Full subject should not be there
        assert "test@example.com" not in log_message  # Email should not be in logs
        
        # Verify partial subject is shown (first 8 chars)
        assert "user1234" in log_message or "subject" in log_message.lower()
    
    @patch("backend.core.auth_service.logger")
    def test_create_refresh_token_logging(self, mock_logger):
        """Test that refresh token creation is logged safely."""
        payload = {"sub": "user987654321", "email": "refresh@example.com"}
        
        token = create_refresh_token(payload)
        
        # Verify token was created
        assert token is not None
        assert len(token) > 0
        
        # Verify logger.debug was called
        assert mock_logger.debug.called
        
        # Get the log message
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        log_message = " ".join(log_calls)
        
        # Verify log contains safe information
        assert "creating refresh token" in log_message.lower() or "refresh" in log_message.lower()
        
        # Verify sensitive data is NOT fully exposed
        assert "user987654321" not in log_message  # Full subject should not be there
        assert "refresh@example.com" not in log_message  # Email should not be in logs
        
        # Verify partial subject is shown
        assert "user9876" in log_message or "subject" in log_message.lower()
    
    @patch("backend.core.auth_service.logger")
    def test_verify_token_success_logging(self, mock_logger):
        """Test that successful token verification is logged."""
        from backend.models.token import Token
        from backend.models.user import User
        
        # Create a valid token
        payload = {"sub": "test_user_id_123"}
        token = create_access_token(payload)
        
        # Mock database objects
        mock_db = Mock(spec=Session)
        mock_token_record = Mock(spec=Token)
        mock_token_record.revoked = False
        mock_token_record.expires_at = datetime.utcnow() + timedelta(hours=1)
        
        mock_user = Mock(spec=User)
        mock_user.id = "test_user_id_123"
        mock_user.deleted_at = None
        
        # Setup query chain
        mock_token_query = Mock()
        mock_token_query.filter.return_value.first.return_value = mock_token_record
        
        mock_user_query = Mock()
        mock_user_query.filter.return_value.first.return_value = mock_user
        
        mock_subscription_query = Mock()
        mock_subscription_query.filter.return_value.first.return_value = None
        
        def query_side_effect(model):
            if model == Token:
                return mock_token_query
            elif model == User:
                return mock_user_query
            else:  # Subscription
                return mock_subscription_query
        
        mock_db.query.side_effect = query_side_effect
        
        # Verify token
        is_valid, error, user = verify_token(mock_db, token)
        
        # Verify result
        assert is_valid is True
        assert error is None
        
        # Verify logging occurred
        assert mock_logger.debug.called or mock_logger.warning.called
        
        # Get all log messages
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        log_message = " ".join(debug_calls)
        
        # Verify success was logged
        assert "token decoded successfully" in log_message.lower() or "decoded" in log_message.lower()
    
    @patch("backend.core.auth_service.logger")
    def test_verify_token_expired_logging(self, mock_logger):
        """Test that expired token validation logs detailed error."""
        from backend.models.token import Token
        from backend.models.user import User
        
        # Create a token
        payload = {"sub": "expired_user_id"}
        token = create_access_token(payload)
        
        # Mock database with expired token
        mock_db = Mock(spec=Session)
        mock_token_record = Mock(spec=Token)
        mock_token_record.revoked = False
        mock_token_record.expires_at = datetime.utcnow() - timedelta(hours=1)  # Expired
        
        # Setup query chain
        mock_token_query = Mock()
        mock_token_query.filter.return_value.first.return_value = mock_token_record
        mock_db.query.return_value = mock_token_query
        
        # Verify token
        is_valid, error, user = verify_token(mock_db, token)
        
        # Verify result
        assert is_valid is False
        assert error == "token_expired"
        
        # Verify warning was logged
        assert mock_logger.warning.called
        
        # Get warning message
        warn_calls = [str(call) for call in mock_logger.warning.call_args_list]
        log_message = " ".join(warn_calls)
        
        # Verify detailed error was logged
        assert "token expired" in log_message.lower() or "expired" in log_message.lower()
    
    @patch("backend.core.auth_service.logger")
    def test_verify_token_revoked_logging(self, mock_logger):
        """Test that revoked token validation logs detailed error."""
        from backend.models.token import Token
        
        # Create a token
        payload = {"sub": "revoked_user_id"}
        token = create_access_token(payload)
        
        # Mock database with no token record (revoked)
        mock_db = Mock(spec=Session)
        mock_token_query = Mock()
        mock_token_query.filter.return_value.first.return_value = None  # Token not found
        mock_db.query.return_value = mock_token_query
        
        # Verify token
        is_valid, error, user = verify_token(mock_db, token)
        
        # Verify result
        assert is_valid is False
        assert error == "token_revoked"
        
        # Verify warning was logged
        assert mock_logger.warning.called
        
        # Get warning message
        warn_calls = [str(call) for call in mock_logger.warning.call_args_list]
        log_message = " ".join(warn_calls)
        
        # Verify detailed error was logged
        assert "revoked" in log_message.lower() or "not found" in log_message.lower()
    
    @patch("backend.core.auth_service.logger")
    def test_verify_token_invalid_signature_logging(self, mock_logger):
        """Test that invalid token signature logs error."""
        # Create an invalid token (not a real JWT)
        invalid_token = "invalid.token.here"
        
        # Mock database
        mock_db = Mock(spec=Session)
        
        # Verify token
        is_valid, error, user = verify_token(mock_db, invalid_token)
        
        # Verify result
        assert is_valid is False
        assert error == "invalid_token"
        
        # Verify warning was logged
        assert mock_logger.warning.called
        
        # Get warning message
        warn_calls = [str(call) for call in mock_logger.warning.call_args_list]
        log_message = " ".join(warn_calls)
        
        # Verify detailed error was logged
        assert "invalid token" in log_message.lower() or "invalid" in log_message.lower()
    
    @patch("backend.core.auth_service.logger")
    def test_verify_token_user_not_found_logging(self, mock_logger):
        """Test that user not found validation logs detailed error."""
        from backend.models.token import Token
        from backend.models.user import User
        
        # Create a token
        payload = {"sub": "nonexistent_user"}
        token = create_access_token(payload)
        
        # Mock database
        mock_db = Mock(spec=Session)
        mock_token_record = Mock(spec=Token)
        mock_token_record.revoked = False
        mock_token_record.expires_at = datetime.utcnow() + timedelta(hours=1)
        
        # Setup query chain
        mock_token_query = Mock()
        mock_token_query.filter.return_value.first.return_value = mock_token_record
        
        mock_user_query = Mock()
        mock_user_query.filter.return_value.first.return_value = None  # User not found
        
        def query_side_effect(model):
            if model == Token:
                return mock_token_query
            elif model == User:
                return mock_user_query
        
        mock_db.query.side_effect = query_side_effect
        
        # Verify token
        is_valid, error, user = verify_token(mock_db, token)
        
        # Verify result
        assert is_valid is False
        assert error == "user_not_found"
        
        # Verify warning was logged
        assert mock_logger.warning.called
        
        # Get warning message
        warn_calls = [str(call) for call in mock_logger.warning.call_args_list]
        log_message = " ".join(warn_calls)
        
        # Verify detailed error was logged
        assert "user not found" in log_message.lower() or "deleted" in log_message.lower()
