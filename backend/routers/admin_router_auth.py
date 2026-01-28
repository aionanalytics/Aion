"""
Admin API router.
Handles admin-only endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from backend.database.connection import get_db
from backend.models.user import UserResponse, User
from backend.models.subscription import SubscriptionResponse, Subscription
from backend.models.token import TokenResponse
from backend.core.admin_service import authenticate_admin, verify_admin_token

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminLoginRequest(BaseModel):
    """Admin login request."""
    password: str


async def verify_admin(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    Dependency to verify admin authentication.
    
    Args:
        authorization: Authorization header with Bearer token
        db: Database session
        
    Raises:
        HTTPException: If not authenticated as admin
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header"
        )
    
    token = authorization.replace("Bearer ", "")
    is_valid, reason = verify_admin_token(db, token)
    
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Admin authentication failed: {reason}"
        )


@router.post("/login")
async def admin_login(
    request: AdminLoginRequest,
    db: Session = Depends(get_db)
):
    """
    Admin login with password only.
    
    Args:
        request: Admin login request
        db: Database session
        
    Returns:
        JWT token for admin authentication
    """
    token, error = authenticate_admin(db, request.password)
    
    if error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin password"
        )
    
    return {
        "access_token": token,
        "token_type": "bearer"
    }


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    _: None = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    List all users (admin only).
    
    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        _: Admin verification
        db: Database session
        
    Returns:
        List of users
    """
    users = db.query(User).filter(
        User.deleted_at.is_(None)
    ).offset(skip).limit(limit).all()
    
    return users


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    _: None = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    Get user by ID (admin only).
    
    Args:
        user_id: User ID
        _: Admin verification
        db: Database session
        
    Returns:
        User details
    """
    user = db.query(User).filter(
        User.id == user_id,
        User.deleted_at.is_(None)
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return user


@router.get("/subscriptions", response_model=List[SubscriptionResponse])
async def list_subscriptions(
    skip: int = 0,
    limit: int = 100,
    status_filter: str = None,
    _: None = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    List all subscriptions (admin only).
    
    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        status_filter: Optional status filter
        _: Admin verification
        db: Database session
        
    Returns:
        List of subscriptions
    """
    query = db.query(Subscription)
    
    if status_filter:
        query = query.filter(Subscription.status == status_filter)
    
    subscriptions = query.offset(skip).limit(limit).all()
    
    return subscriptions


@router.get("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: str,
    _: None = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    Get subscription by ID (admin only).
    
    Args:
        subscription_id: Subscription ID
        _: Admin verification
        db: Database session
        
    Returns:
        Subscription details
    """
    subscription = db.query(Subscription).filter(
        Subscription.id == subscription_id
    ).first()
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )
    
    return subscription


@router.get("/stats")
async def get_stats(
    _: None = Depends(verify_admin),
    db: Session = Depends(get_db)
):
    """
    Get system statistics (admin only).
    
    Args:
        _: Admin verification
        db: Database session
        
    Returns:
        System statistics
    """
    total_users = db.query(User).filter(User.deleted_at.is_(None)).count()
    total_subscriptions = db.query(Subscription).count()
    active_subscriptions = db.query(Subscription).filter(
        Subscription.status == "active"
    ).count()
    past_due_subscriptions = db.query(Subscription).filter(
        Subscription.status == "past_due"
    ).count()
    early_adopters = db.query(Subscription).filter(
        Subscription.early_adopter_discount == True
    ).count()
    
    return {
        "total_users": total_users,
        "total_subscriptions": total_subscriptions,
        "active_subscriptions": active_subscriptions,
        "past_due_subscriptions": past_due_subscriptions,
        "early_adopters": early_adopters,
    }
