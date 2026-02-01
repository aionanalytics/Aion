"""
Test that /api/auth/admin-login is a public route.
"""
import pytest
from unittest.mock import Mock, patch
from fastapi import Request
from starlette.datastructures import Headers

from backend.middleware.auth_middleware import AuthMiddleware, PUBLIC_ROUTES


class TestAdminLoginPublicRoute:
    """Test suite for admin-login public route."""
    
    def test_admin_login_in_public_routes(self):
        """Test that /api/auth/admin-login is in PUBLIC_ROUTES."""
        assert "/api/auth/admin-login" in PUBLIC_ROUTES
    
    @pytest.mark.asyncio
    async def test_admin_login_bypasses_auth(self):
        """Test that /api/auth/admin-login bypasses authentication."""
        # Create middleware instance
        middleware = AuthMiddleware(app=Mock())
        
        # Create request for admin-login
        request = Mock(spec=Request)
        request.url.path = "/api/auth/admin-login"
        request.headers = Headers({})
        
        # Create mock call_next
        async def call_next(request):
            return Mock(status_code=200)
        
        # Should not raise HTTPException for missing auth header
        with patch("backend.middleware.auth_middleware.logger"):
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_regular_login_also_public(self):
        """Test that /api/auth/login is also public (existing behavior)."""
        # Create middleware instance
        middleware = AuthMiddleware(app=Mock())
        
        # Create request for regular login
        request = Mock(spec=Request)
        request.url.path = "/api/auth/login"
        request.headers = Headers({})
        
        # Create mock call_next
        async def call_next(request):
            return Mock(status_code=200)
        
        # Should not raise HTTPException for missing auth header
        with patch("backend.middleware.auth_middleware.logger"):
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
