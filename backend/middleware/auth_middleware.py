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
from utils.logger import Logger

# Create logger for auth middleware
logger = Logger(name="auth_middleware", source="backend")


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
        
        # Log incoming request
        logger.debug(f"Auth middleware processing request: {path}")
        
        # Skip authentication for public routes
        if any(path.startswith(route) for route in PUBLIC_ROUTES):
            logger.debug(f"Skipping authentication for public route: {path}")
            return await call_next(request)
        
        # Check if route requires admin auth
        is_admin_route = any(path.startswith(route) for route in ADMIN_ROUTES)
        
        if is_admin_route:
            logger.debug(f"Route requires admin authentication: {path}")
        else:
            logger.debug(f"Route requires user authentication: {path}")
        
        # Get authorization header
        auth_header = request.headers.get("authorization")
        
        # Log authorization header presence (sanitized)
        if auth_header:
            if auth_header.startswith("Bearer "):
                logger.debug(f"Authorization header: Present (Bearer token)")
            else:
                logger.debug(f"Authorization header: Present (invalid format)")
        else:
            logger.debug(f"Authorization header: Missing")
        
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning(f"Missing or invalid authorization header for {path}")
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
                logger.debug(f"Verifying admin token for {path}")
                is_valid, reason = verify_admin_token(db, token)
                if not is_valid:
                    logger.warning(f"Admin authentication failed for {path}: {reason}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=f"Admin authentication failed: {reason}"
                    )
                logger.debug(f"Admin authentication successful for {path}")
            else:
                # Verify user token
                logger.debug(f"Verifying user token for {path}")
                is_valid, reason, user = verify_token(db, token)
                if not is_valid:
                    logger.warning(f"User authentication failed for {path}: {reason}")
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
                
                logger.debug(f"User authentication successful for {path}")
                
                # Add user to request state
                if user:
                    request.state.user = user
        
        finally:
            db.close()
        
        # Continue to next handler
        return await call_next(request)
