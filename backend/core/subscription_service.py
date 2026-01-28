"""
Subscription management service.
Handles subscription creation, updates, and pricing calculations.
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple
from sqlalchemy.orm import Session

from backend.models.subscription import (
    Subscription,
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionType,
    BillingFrequency,
)

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")

# Early adopter configuration
EARLY_ADOPTER_COUNT_LIMIT = 100
EARLY_ADOPTER_DISCOUNT = 50.00  # $50/month discount


# =====================================
# Pricing Configuration
# =====================================

# Base pricing (monthly)
BASE_PRICING = {
    "swing": 199.00,
    "day": 249.00,
    "both": 398.00,  # 20% bundle discount from (199 + 249 = 448)
}

# Annual pricing (includes ~15% annual discount)
ANNUAL_PRICING = {
    "swing": 1990.00,  # 199 * 12 = 2388, discounted to 1990
    "day": 2490.00,    # 249 * 12 = 2988, discounted to 2490
    "both": 3980.00,   # 398 * 12 = 4776, discounted to 3980
}

# Add-on pricing (monthly)
ADDON_PRICING = {
    "analytics": 49.00,
    "backup": 29.00,
}

# Add-on pricing (annual)
ADDON_ANNUAL_PRICING = {
    "analytics": 490.00,  # 49 * 12 = 588, discounted to 490
    "backup": 290.00,     # 29 * 12 = 348, discounted to 290
}


# =====================================
# Pricing Calculations
# =====================================

def calculate_subscription_price(
    subscription_type: str,
    addons: List[str],
    billing_frequency: str,
    early_adopter: bool = False
) -> Tuple[float, Dict[str, float]]:
    """
    Calculate total subscription price.
    
    Args:
        subscription_type: Type of subscription (swing, day, both)
        addons: List of add-ons (analytics, backup)
        billing_frequency: Billing frequency (monthly, annual)
        early_adopter: Whether early adopter discount applies
        
    Returns:
        Tuple of (total_price, breakdown)
    """
    breakdown = {}
    
    # Base price
    if billing_frequency == "annual":
        base_price = ANNUAL_PRICING.get(subscription_type, 0.0)
        breakdown["base"] = base_price
    else:
        base_price = BASE_PRICING.get(subscription_type, 0.0)
        breakdown["base"] = base_price
    
    # Add-ons
    addons_total = 0.0
    for addon in addons:
        if billing_frequency == "annual":
            addon_price = ADDON_ANNUAL_PRICING.get(addon, 0.0)
        else:
            addon_price = ADDON_PRICING.get(addon, 0.0)
        
        addons_total += addon_price
        breakdown[f"addon_{addon}"] = addon_price
    
    breakdown["addons_total"] = addons_total
    
    # Subtotal
    subtotal = base_price + addons_total
    breakdown["subtotal"] = subtotal
    
    # Early adopter discount (monthly only, applied to entire subscription)
    discount = 0.0
    if early_adopter:
        if billing_frequency == "monthly":
            discount = EARLY_ADOPTER_DISCOUNT
        else:
            # Annual early adopter discount: $50/month * 12 = $600/year
            discount = EARLY_ADOPTER_DISCOUNT * 12
        
        breakdown["early_adopter_discount"] = -discount
    
    # Total
    total = subtotal - discount
    breakdown["total"] = total
    
    return total, breakdown


# =====================================
# Subscription Operations
# =====================================

def create_subscription(
    db: Session,
    user_id: str,
    subscription_data: SubscriptionCreate
) -> Subscription:
    """
    Create a new subscription for a user.
    
    Args:
        db: Database session
        user_id: User ID
        subscription_data: Subscription creation data
        
    Returns:
        Created subscription
    """
    # Check if user already has a subscription
    existing = db.query(Subscription).filter(
        Subscription.user_id == user_id
    ).first()
    
    if existing:
        raise ValueError("User already has a subscription")
    
    # Check early adopter eligibility
    early_adopter = subscription_data.early_adopter_discount
    if early_adopter:
        # Count active subscriptions with early adopter discount
        count = db.query(Subscription).filter(
            Subscription.early_adopter_discount == True
        ).count()
        
        if count >= EARLY_ADOPTER_COUNT_LIMIT:
            early_adopter = False
    
    # Create subscription
    subscription = Subscription(
        user_id=user_id,
        subscription_type=subscription_data.subscription_type.value,
        addons=subscription_data.addons,
        billing_frequency=subscription_data.billing_frequency.value,
        early_adopter_discount=early_adopter,
        status="active",  # Will be updated after payment
    )
    
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    
    return subscription


def update_subscription(
    db: Session,
    subscription_id: str,
    update_data: SubscriptionUpdate
) -> Subscription:
    """
    Update an existing subscription.
    
    Args:
        db: Database session
        subscription_id: Subscription ID
        update_data: Subscription update data
        
    Returns:
        Updated subscription
    """
    subscription = db.query(Subscription).filter(
        Subscription.id == subscription_id
    ).first()
    
    if not subscription:
        raise ValueError("Subscription not found")
    
    # Update fields
    if update_data.subscription_type:
        subscription.subscription_type = update_data.subscription_type.value
    
    if update_data.addons is not None:
        subscription.addons = update_data.addons
    
    if update_data.billing_frequency:
        subscription.billing_frequency = update_data.billing_frequency.value
    
    if update_data.cancel_at_period_end is not None:
        subscription.cancel_at_period_end = update_data.cancel_at_period_end
    
    db.commit()
    db.refresh(subscription)
    
    return subscription


def get_subscription_by_user(db: Session, user_id: str) -> Subscription | None:
    """
    Get subscription for a user.
    
    Args:
        db: Database session
        user_id: User ID
        
    Returns:
        Subscription or None
    """
    return db.query(Subscription).filter(
        Subscription.user_id == user_id
    ).first()


def cancel_subscription(db: Session, subscription_id: str, immediate: bool = False) -> Subscription:
    """
    Cancel a subscription.
    
    Args:
        db: Database session
        subscription_id: Subscription ID
        immediate: If True, cancel immediately; otherwise cancel at period end
        
    Returns:
        Updated subscription
    """
    subscription = db.query(Subscription).filter(
        Subscription.id == subscription_id
    ).first()
    
    if not subscription:
        raise ValueError("Subscription not found")
    
    if immediate:
        subscription.status = "canceled"
    else:
        subscription.cancel_at_period_end = True
    
    db.commit()
    db.refresh(subscription)
    
    return subscription
