"""
Authentication middleware.
Validates JWT tokens and enforces route protection.
"""
from __future__ import annotations

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable

from backend.database.connection import SessionLocal
from backend.core.auth_service import verify_token
from backend.core.admin_service import verify_admin_token


# Routes that don't require authentication
PUBLIC_ROUTES = [
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/password-reset",
    "/api/auth/verify",
    "/api/subscription/pricing",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/system/status",
    "/api/system/health",
]

# Routes that require admin authentication
ADMIN_ROUTES = [
    "/api/admin/",
    "/admin/",
]


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate JWT tokens on protected routes.
    """
    
    async def dispatch(self, request: Request, call_next: Callable):
        """
        Process request and validate authentication.
        
        Args:
            request: FastAPI request
            call_next: Next middleware/handler
            
        Returns:
            Response from handler
        """
        path = request.url.path
        
        # Skip authentication for public routes
        if any(path.startswith(route) for route in PUBLIC_ROUTES):
            return await call_next(request)
        
        # Check if route requires admin auth
        is_admin_route = any(path.startswith(route) for route in ADMIN_ROUTES)
        
        # Get authorization header
        auth_header = request.headers.get("authorization")
        
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization header"
            )
        
        token = auth_header.replace("Bearer ", "")
        
        # Verify token
        db = SessionLocal()
        try:
            if is_admin_route:
                # Verify admin token
                is_valid, reason = verify_admin_token(db, token)
                if not is_valid:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"Admin authentication failed: {reason}"
                    )
            else:
                # Verify user token
                is_valid, reason, user = verify_token(db, token)
                if not is_valid:
                    if reason == "payment_failed":
                        raise HTTPException(
                            status_code=status.HTTP_402_PAYMENT_REQUIRED,
                            detail="Payment failed. Please update your payment method."
                        )
                    elif reason == "subscription_inactive":
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Subscription is inactive."
                        )
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=f"Authentication failed: {reason}"
                        )
                
                # Add user to request state
                if user:
                    request.state.user = user
        
        finally:
            db.close()
        
        # Continue to next handler
        return await call_next(request)
