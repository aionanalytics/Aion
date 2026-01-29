"""
Authentication API router.
Handles user authentication endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.database.connection import get_db
from backend.models.user import UserCreate, UserLogin, UserResponse
from backend.models.token import TokenResponse, TokenVerifyRequest, TokenVerifyResponse
from backend.core.auth_service import (
    create_user,
    authenticate_user,
    generate_tokens,
    verify_token,
    revoke_token,
)
from backend.core.subscription_service import create_subscription, calculate_subscription_price
from backend.models.subscription import SubscriptionCreate

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class AdminLoginRequest(BaseModel):
    """Admin login request."""
    password: str


def set_auth_cookies(response: Response, access_token: str, refresh_token: str):
    """Set HTTP-only cookies for authentication."""
    # Access token cookie (24 hour expiry to match JWT)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,  # HTTPS only in production
        samesite="lax",
        max_age=86400,  # 24 hours
    )
    
    # Refresh token cookie (30 day expiry to match JWT)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=2592000,  # 30 days
    )


def clear_auth_cookies(response: Response):
    """Clear authentication cookies."""
    response.delete_cookie(key="access_token")
    response.delete_cookie(key="refresh_token")


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    response: Response,
    email: str,
    password: str,
    subscription_type: str,
    addons: list[str] = [],
    billing_frequency: str = "monthly",
    early_adopter: bool = False,
    payment_method_id: str = None,
    db: Session = Depends(get_db)
):
    """
    Create a new user account with subscription.
    
    Args:
        response: Response object to set cookies
        email: User email
        password: User password
        subscription_type: Type of subscription (swing, day, both)
        addons: List of add-ons (analytics, backup)
        billing_frequency: Billing frequency (monthly, annual)
        early_adopter: Whether to apply early adopter discount
        payment_method_id: Stripe payment method ID (optional)
        db: Database session
        
    Returns:
        JWT tokens for authentication (also sets HTTP-only cookies)
    """
    try:
        # Validate subscription pricing matches frontend expectations
        calculated_price, price_breakdown = calculate_subscription_price(
            subscription_type, addons, billing_frequency, early_adopter
        )
        
        # Create user
        user_data = UserCreate(email=email, password=password)
        user = create_user(db, user_data)
        
        # Create subscription
        subscription_data = SubscriptionCreate(
            subscription_type=subscription_type,
            addons=addons,
            billing_frequency=billing_frequency,
            early_adopter_discount=early_adopter,
        )
        subscription = create_subscription(db, str(user.id), subscription_data)
        
        # Process payment if payment method provided
        if payment_method_id:
            from backend.core.stripe_service import process_signup_payment
            success, error, payment_data = process_signup_payment(
                db, subscription, payment_method_id, email
            )
            
            if not success:
                # Rollback user creation if payment fails
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={"error": error or "Payment processing failed", "code": "payment_failed"}
                )
        
        # Generate tokens
        tokens = generate_tokens(db, user)
        
        # Set HTTP-only cookies
        set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
        
        # Return tokens (for backward compatibility and localStorage fallback)
        return {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": "bearer",
            "subscription_price": calculated_price,
            "price_breakdown": price_breakdown,
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(e), "code": "validation_error"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": f"Failed to create account: {str(e)}", "code": "server_error"}
        )


@router.post("/login")
async def login(
    response: Response,
    credentials: UserLogin,
    db: Session = Depends(get_db)
):
    """
    Login with email and password.
    
    Args:
        response: Response object to set cookies
        credentials: User login credentials
        db: Database session
        
    Returns:
        JWT tokens for authentication (also sets HTTP-only cookies)
    """
    user, error = authenticate_user(db, credentials.email, credentials.password)
    
    if error == "account_locked":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "Account temporarily locked due to too many failed login attempts. Please try again in 15 minutes.", "code": "account_locked"}
        )
    
    if error == "payment_failed":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"error": "Payment failed. Please update your payment method.", "code": "payment_failed"}
        )
    
    if error == "subscription_inactive":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Subscription is inactive. Please contact support.", "code": "subscription_inactive"}
        )
    
    if error == "invalid_credentials":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid email or password.", "code": "invalid_credentials"}
        )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Authentication failed.", "code": "authentication_failed"}
        )
    
    # Generate tokens
    tokens = generate_tokens(db, user)
    
    # Set HTTP-only cookies
    set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    
    # Return tokens (for backward compatibility and localStorage fallback)
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "token_type": "bearer"
    }


@router.post("/verify", response_model=TokenVerifyResponse)
async def verify(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Verify a JWT token from cookie or header.
    
    Args:
        request: Request object to get token
        db: Database session
        
    Returns:
        Token verification response
    """
    # Get token from cookie or Authorization header
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "No authentication token provided", "code": "no_token"}
        )
    
    is_valid, reason, user = verify_token(db, token)
    
    response = TokenVerifyResponse(
        valid=is_valid,
        reason=reason,
    )
    
    if user:
        response.user_id = user.id
        
        # Get subscription status
        from backend.core.subscription_service import get_subscription_by_user
        subscription = get_subscription_by_user(db, str(user.id))
        if subscription:
            response.subscription_status = subscription.status
    
    return response


@router.post("/logout")
async def logout(
    response: Response,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Logout and revoke token.
    
    Args:
        response: Response object to clear cookies
        request: Request object to get token
        db: Database session
        
    Returns:
        Success message
    """
    # Get token from cookie or Authorization header
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
    
    if token:
        revoke_token(db, token)
    
    # Clear cookies
    clear_auth_cookies(response)
    
    return {"message": "Successfully logged out"}


@router.post("/admin-login")
async def admin_login(
    response: Response,
    request: AdminLoginRequest,
    db: Session = Depends(get_db)
):
    """
    Admin login with password only (uses same admin credentials as /tools/admin).
    
    Args:
        response: Response object to set cookies
        request: Admin login request
        db: Database session
        
    Returns:
        JWT token for admin authentication
    """
    from backend.core.admin_service import authenticate_admin
    
    token, error = authenticate_admin(db, request.password)
    
    if error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid admin password", "code": "invalid_admin_password"}
        )
    
    # Set HTTP-only cookie for admin token
    response.set_cookie(
        key="admin_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=3600,  # 1 hour (matches ADMIN_TOKEN_TTL)
    )
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": "admin"
    }


@router.post("/refresh")
async def refresh(
    response: Response,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Refresh an expired access token using a refresh token from cookie or body.
    
    Args:
        response: Response object to set new cookies
        request: Request object to get refresh token
        db: Database session
        
    Returns:
        New access and refresh tokens
    """
    # Get refresh token from cookie or body
    refresh_token = request.cookies.get("refresh_token")
    
    # If not in cookie, try to get from request body
    if not refresh_token:
        try:
            body = await request.json()
            refresh_token = body.get("refresh_token")
        except Exception:
            pass
    
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "No refresh token provided", "code": "no_refresh_token"}
        )
    
    # Verify refresh token
    is_valid, reason, user = verify_token(db, refresh_token)
    
    if not is_valid or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired refresh token", "code": "invalid_refresh_token"}
        )
    
    # Revoke old tokens
    revoke_token(db, refresh_token)
    
    # Generate new tokens
    tokens = generate_tokens(db, user)
    
    # Set HTTP-only cookies
    set_auth_cookies(response, tokens.access_token, tokens.refresh_token)
    
    # Return tokens (for backward compatibility)
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "token_type": "bearer"
    }


@router.post("/password-reset")
async def password_reset(
    email: str,
    db: Session = Depends(get_db)
):
    """
    Request a password reset.
    
    Args:
        email: User email
        db: Database session
        
    Returns:
        Success message
    """
    # TODO: Implement password reset email sending
    # For now, return success (will be implemented with email service)
    return {
        "message": "If an account exists with this email, a password reset link has been sent."
    }
