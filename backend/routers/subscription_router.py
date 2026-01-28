"""
Subscription API router.
Handles subscription management endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import Optional

from backend.database.connection import get_db
from backend.models.subscription import SubscriptionResponse, SubscriptionUpdate
from backend.core.subscription_service import (
    get_subscription_by_user,
    update_subscription,
    calculate_subscription_price,
    cancel_subscription,
)
from backend.core.auth_service import verify_token

router = APIRouter(prefix="/api/subscription", tags=["subscription"])


async def get_current_user_id(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
) -> str:
    """
    Dependency to get current user ID from JWT token.
    
    Args:
        authorization: Authorization header with Bearer token
        db: Database session
        
    Returns:
        User ID
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header"
        )
    
    token = authorization.replace("Bearer ", "")
    is_valid, reason, user = verify_token(db, token)
    
    if not is_valid or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    return str(user.id)


@router.get("/status", response_model=SubscriptionResponse)
async def get_status(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    """
    Get current subscription details.
    
    Args:
        user_id: Current user ID from JWT
        db: Database session
        
    Returns:
        Subscription details
    """
    subscription = get_subscription_by_user(db, user_id)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found"
        )
    
    return subscription


@router.put("/update-payment")
async def update_payment(
    payment_method_id: str,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    """
    Update payment method.
    
    Args:
        payment_method_id: Stripe payment method ID
        user_id: Current user ID from JWT
        db: Database session
        
    Returns:
        Success message
    """
    subscription = get_subscription_by_user(db, user_id)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found"
        )
    
    # Update payment method in Stripe
    if subscription.stripe_customer_id:
        try:
            from backend.core.stripe_service import update_payment_method
            update_payment_method(subscription.stripe_customer_id, payment_method_id)
            
            # Update subscription status if it was past_due
            if subscription.status == "past_due":
                subscription.status = "active"
                db.commit()
            
            return {"message": "Payment method updated successfully"}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to update payment method: {str(e)}"
            )
    
    return {"message": "Payment method updated successfully"}


@router.post("/upgrade", response_model=SubscriptionResponse)
async def upgrade(
    update_data: SubscriptionUpdate,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    """
    Change subscription plan.
    
    Args:
        update_data: Subscription update data
        user_id: Current user ID from JWT
        db: Database session
        
    Returns:
        Updated subscription
    """
    subscription = get_subscription_by_user(db, user_id)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found"
        )
    
    try:
        updated_subscription = update_subscription(db, str(subscription.id), update_data)
        return updated_subscription
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/cancel")
async def cancel(
    immediate: bool = False,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    """
    Cancel subscription.
    
    Args:
        immediate: If True, cancel immediately; otherwise at period end
        user_id: Current user ID from JWT
        db: Database session
        
    Returns:
        Success message
    """
    subscription = get_subscription_by_user(db, user_id)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription found"
        )
    
    try:
        cancel_subscription(db, str(subscription.id), immediate)
        return {
            "message": "Subscription canceled" if immediate else "Subscription will cancel at period end"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/pricing")
async def get_pricing(
    subscription_type: str,
    addons: Optional[list[str]] = None,
    billing_frequency: str = "monthly",
    early_adopter: bool = False
):
    """
    Calculate subscription pricing.
    
    Args:
        subscription_type: Type of subscription (swing, day, both)
        addons: List of add-ons (analytics, backup)
        billing_frequency: Billing frequency (monthly, annual)
        early_adopter: Whether early adopter discount applies
        
    Returns:
        Price breakdown
    """
    if addons is None:
        addons = []
    
    total, breakdown = calculate_subscription_price(
        subscription_type,
        addons,
        billing_frequency,
        early_adopter
    )
    
    return {
        "total": total,
        "breakdown": breakdown
    }
