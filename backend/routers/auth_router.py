"""
Authentication API router.
Handles user authentication endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

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
from backend.core.subscription_service import create_subscription
from backend.models.subscription import SubscriptionCreate

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
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
        email: User email
        password: User password
        subscription_type: Type of subscription (swing, day, both)
        addons: List of add-ons (analytics, backup)
        billing_frequency: Billing frequency (monthly, annual)
        early_adopter: Whether to apply early adopter discount
        payment_method_id: Stripe payment method ID (optional)
        db: Database session
        
    Returns:
        JWT tokens for authentication
    """
    try:
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
                    detail=error or "Payment processing failed"
                )
        
        # Generate tokens
        tokens = generate_tokens(db, user)
        
        return tokens
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create account: {str(e)}"
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: UserLogin,
    db: Session = Depends(get_db)
):
    """
    Login with email and password.
    
    Args:
        credentials: User login credentials
        db: Database session
        
    Returns:
        JWT tokens for authentication
    """
    user, error = authenticate_user(db, credentials.email, credentials.password)
    
    if error == "account_locked":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to too many failed login attempts. Please try again in 15 minutes."
        )
    
    if error == "payment_failed":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Payment failed. Please update your payment method."
        )
    
    if error == "subscription_inactive":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription is inactive. Please contact support."
        )
    
    if error == "invalid_credentials":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed."
        )
    
    # Generate tokens
    tokens = generate_tokens(db, user)
    return tokens


@router.post("/verify", response_model=TokenVerifyResponse)
async def verify(
    request: TokenVerifyRequest,
    db: Session = Depends(get_db)
):
    """
    Verify a JWT token.
    
    Args:
        request: Token verification request
        db: Database session
        
    Returns:
        Token verification response
    """
    is_valid, reason, user = verify_token(db, request.token)
    
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
    request: TokenVerifyRequest,
    db: Session = Depends(get_db)
):
    """
    Logout and revoke token.
    
    Args:
        request: Token to revoke
        db: Database session
        
    Returns:
        Success message
    """
    success = revoke_token(db, request.token)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to logout"
        )
    
    return {"message": "Successfully logged out"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    refresh_token: str,
    db: Session = Depends(get_db)
):
    """
    Refresh an expired access token using a refresh token.
    
    Args:
        refresh_token: Refresh token
        db: Database session
        
    Returns:
        New access and refresh tokens
    """
    # Verify refresh token
    is_valid, reason, user = verify_token(db, refresh_token)
    
    if not is_valid or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )
    
    # Revoke old tokens
    revoke_token(db, refresh_token)
    
    # Generate new tokens
    tokens = generate_tokens(db, user)
    return tokens


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
