"""
Test auth middleware logging functionality.
Ensures logging statements are present and don't expose sensitive data.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi import Request, HTTPException
from starlette.datastructures import Headers

from backend.middleware.auth_middleware import AuthMiddleware


class TestAuthMiddlewareLogging:
    """Test suite for auth middleware logging."""
    
    @pytest.fixture
    def middleware(self):
        """Create middleware instance."""
        return AuthMiddleware(app=Mock())
    
    @pytest.fixture
    def mock_request(self):
        """Create a mock request."""
        request = Mock(spec=Request)
        request.url.path = "/api/test"
        request.headers = Headers({})
        request.state = Mock()
        return request
    
    @pytest.fixture
    def mock_call_next(self):
        """Create a mock call_next function."""
        async def call_next(request):
            return Mock()
        return call_next
    
    @pytest.mark.asyncio
    async def test_public_route_logging(self, middleware, mock_call_next):
        """Test that public routes are logged as skipped."""
        request = Mock(spec=Request)
        request.url.path = "/api/auth/login"
        request.headers = Headers({})
        
        with patch("backend.middleware.auth_middleware.logger") as mock_logger:
            await middleware.dispatch(request, mock_call_next)
            
            # Verify debug logs were called
            assert mock_logger.debug.call_count >= 2
            
            # Check that it logged processing the request
            calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("processing request" in str(call).lower() for call in calls)
            
            # Check that it logged skipping authentication
            assert any("skipping authentication" in str(call).lower() for call in calls)
    
    @pytest.mark.asyncio
    async def test_missing_auth_header_logging(self, middleware, mock_call_next):
        """Test that missing auth headers are logged."""
        request = Mock(spec=Request)
        request.url.path = "/api/protected"
        request.headers = Headers({})
        
        with patch("backend.middleware.auth_middleware.logger") as mock_logger:
            with pytest.raises(HTTPException) as exc_info:
                await middleware.dispatch(request, mock_call_next)
            
            assert exc_info.value.status_code == 401
            
            # Verify warning was logged
            assert mock_logger.warning.called
            warn_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("missing or invalid" in str(call).lower() for call in warn_calls)
    
    @pytest.mark.asyncio
    async def test_auth_header_presence_logged_sanitized(self, middleware, mock_call_next):
        """Test that auth header presence is logged without exposing token."""
        request = Mock(spec=Request)
        request.url.path = "/api/protected"
        request.headers = Headers({"authorization": "Bearer secret_token_12345"})
        
        with patch("backend.middleware.auth_middleware.logger") as mock_logger, \
             patch("backend.middleware.auth_middleware.SessionLocal") as mock_session, \
             patch("backend.middleware.auth_middleware.verify_token") as mock_verify:
            
            # Setup mock DB and successful verification
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_verify.return_value = (True, None, Mock())
            
            await middleware.dispatch(request, mock_call_next)
            
            # Verify debug logs were called
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            
            # Check that token presence was logged
            assert any("authorization header: present" in str(call).lower() for call in debug_calls)
            
            # Ensure the actual token is NOT in any log call
            all_calls = debug_calls + [str(call) for call in mock_logger.warning.call_args_list]
            assert not any("secret_token_12345" in str(call) for call in all_calls)
            assert not any("Bearer secret_token_12345" in str(call) for call in all_calls)
    
    @pytest.mark.asyncio
    async def test_admin_route_logging(self, middleware, mock_call_next):
        """Test that admin routes are logged appropriately."""
        request = Mock(spec=Request)
        request.url.path = "/api/admin/users"
        request.headers = Headers({"authorization": "Bearer admin_token"})
        
        with patch("backend.middleware.auth_middleware.logger") as mock_logger, \
             patch("backend.middleware.auth_middleware.SessionLocal") as mock_session, \
             patch("backend.middleware.auth_middleware.verify_admin_token") as mock_verify_admin:
            
            # Setup mock DB and successful verification
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_verify_admin.return_value = (True, None)
            
            await middleware.dispatch(request, mock_call_next)
            
            # Verify admin route was logged
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("admin authentication" in str(call).lower() for call in debug_calls)
    
    @pytest.mark.asyncio
    async def test_user_route_logging(self, middleware, mock_call_next):
        """Test that user routes are logged appropriately."""
        request = Mock(spec=Request)
        request.url.path = "/api/user/profile"
        request.headers = Headers({"authorization": "Bearer user_token"})
        
        with patch("backend.middleware.auth_middleware.logger") as mock_logger, \
             patch("backend.middleware.auth_middleware.SessionLocal") as mock_session, \
             patch("backend.middleware.auth_middleware.verify_token") as mock_verify:
            
            # Setup mock DB and successful verification
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_verify.return_value = (True, None, Mock())
            
            await middleware.dispatch(request, mock_call_next)
            
            # Verify user route was logged
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("user authentication" in str(call).lower() for call in debug_calls)
    
    @pytest.mark.asyncio
    async def test_authentication_failure_logging(self, middleware, mock_call_next):
        """Test that authentication failures are logged with reason."""
        request = Mock(spec=Request)
        request.url.path = "/api/protected"
        request.headers = Headers({"authorization": "Bearer invalid_token"})
        
        with patch("backend.middleware.auth_middleware.logger") as mock_logger, \
             patch("backend.middleware.auth_middleware.SessionLocal") as mock_session, \
             patch("backend.middleware.auth_middleware.verify_token") as mock_verify:
            
            # Setup mock DB and failed verification
            mock_db = Mock()
            mock_session.return_value = mock_db
            mock_verify.return_value = (False, "token_expired", None)
            
            with pytest.raises(HTTPException) as exc_info:
                await middleware.dispatch(request, mock_call_next)
            
            assert exc_info.value.status_code == 401
            
            # Verify failure was logged
            warn_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("authentication failed" in str(call).lower() for call in warn_calls)
            assert any("token_expired" in str(call) for call in warn_calls)
