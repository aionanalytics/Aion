"""
Stripe integration service.
Handles payment processing and subscription management with Stripe.
"""
from __future__ import annotations

import os
import stripe
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from backend.models.subscription import Subscription
from backend.core.subscription_service import calculate_subscription_price

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Stripe configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Pricing IDs (these would be created in Stripe dashboard)
# For now, we'll use dynamic pricing
STRIPE_ENABLED = bool(stripe.api_key and stripe.api_key != "")


# =====================================
# Customer Management
# =====================================

def create_stripe_customer(
    email: str,
    payment_method_id: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None
) -> stripe.Customer:
    """
    Create a Stripe customer.
    
    Args:
        email: Customer email
        payment_method_id: Optional payment method ID
        metadata: Optional metadata
        
    Returns:
        Stripe customer object
    """
    customer_data = {
        "email": email,
        "metadata": metadata or {},
    }
    
    if payment_method_id:
        customer_data["payment_method"] = payment_method_id
        customer_data["invoice_settings"] = {
            "default_payment_method": payment_method_id
        }
    
    return stripe.Customer.create(**customer_data)


def update_payment_method(
    customer_id: str,
    payment_method_id: str
) -> stripe.Customer:
    """
    Update customer's default payment method.
    
    Args:
        customer_id: Stripe customer ID
        payment_method_id: New payment method ID
        
    Returns:
        Updated Stripe customer object
    """
    # Attach payment method to customer
    stripe.PaymentMethod.attach(
        payment_method_id,
        customer=customer_id,
    )
    
    # Set as default payment method
    return stripe.Customer.modify(
        customer_id,
        invoice_settings={
            "default_payment_method": payment_method_id
        }
    )


# =====================================
# Subscription Management
# =====================================

def create_stripe_subscription(
    customer_id: str,
    price_amount: float,
    interval: str = "month",
    metadata: Optional[Dict[str, str]] = None
) -> stripe.Subscription:
    """
    Create a Stripe subscription with dynamic pricing.
    
    Args:
        customer_id: Stripe customer ID
        price_amount: Subscription price in dollars
        interval: Billing interval (month, year)
        metadata: Optional metadata
        
    Returns:
        Stripe subscription object
    """
    # Create a price for this subscription
    price = stripe.Price.create(
        unit_amount=int(price_amount * 100),  # Convert to cents
        currency="usd",
        recurring={"interval": interval},
        product_data={
            "name": "AION Analytics Subscription",
        },
    )
    
    # Create subscription
    return stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": price.id}],
        metadata=metadata or {},
        payment_behavior="default_incomplete",
        payment_settings={"save_default_payment_method": "on_subscription"},
        expand=["latest_invoice.payment_intent"],
    )


def update_stripe_subscription(
    subscription_id: str,
    new_price_amount: Optional[float] = None,
    metadata: Optional[Dict[str, str]] = None,
    cancel_at_period_end: Optional[bool] = None
) -> stripe.Subscription:
    """
    Update a Stripe subscription.
    
    Args:
        subscription_id: Stripe subscription ID
        new_price_amount: New price amount (if changing plan)
        metadata: Optional metadata to update
        cancel_at_period_end: Whether to cancel at period end
        
    Returns:
        Updated Stripe subscription object
    """
    update_data = {}
    
    if new_price_amount is not None:
        # Create new price
        subscription = stripe.Subscription.retrieve(subscription_id)
        current_item = subscription["items"]["data"][0]
        
        new_price = stripe.Price.create(
            unit_amount=int(new_price_amount * 100),
            currency="usd",
            recurring={"interval": current_item.price.recurring.interval},
            product_data={"name": "AION Analytics Subscription"},
        )
        
        update_data["items"] = [{
            "id": current_item.id,
            "price": new_price.id,
        }]
    
    if metadata is not None:
        update_data["metadata"] = metadata
    
    if cancel_at_period_end is not None:
        update_data["cancel_at_period_end"] = cancel_at_period_end
    
    return stripe.Subscription.modify(subscription_id, **update_data)


def cancel_stripe_subscription(
    subscription_id: str,
    immediately: bool = False
) -> stripe.Subscription:
    """
    Cancel a Stripe subscription.
    
    Args:
        subscription_id: Stripe subscription ID
        immediately: If True, cancel immediately; otherwise at period end
        
    Returns:
        Canceled/updated Stripe subscription object
    """
    if immediately:
        return stripe.Subscription.delete(subscription_id)
    else:
        return stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=True
        )


# =====================================
# Payment Processing
# =====================================

def process_signup_payment(
    db: Session,
    subscription: Subscription,
    payment_method_id: str,
    user_email: str
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Process payment for a new signup.
    
    Args:
        db: Database session
        subscription: Subscription database object
        payment_method_id: Stripe payment method ID
        user_email: User email
        
    Returns:
        Tuple of (success, error_message, payment_data)
    """
    if not STRIPE_ENABLED:
        # Stripe not configured, skip payment
        return True, None, {"status": "skipped", "reason": "stripe_not_configured"}
    
    try:
        # Calculate price
        total, breakdown = calculate_subscription_price(
            subscription.subscription_type,
            subscription.addons,
            subscription.billing_frequency,
            subscription.early_adopter_discount
        )
        
        # Create Stripe customer
        customer = create_stripe_customer(
            email=user_email,
            payment_method_id=payment_method_id,
            metadata={
                "user_id": str(subscription.user_id),
                "subscription_id": str(subscription.id),
            }
        )
        
        # Create Stripe subscription
        interval = "month" if subscription.billing_frequency == "monthly" else "year"
        stripe_subscription = create_stripe_subscription(
            customer_id=customer.id,
            price_amount=total,
            interval=interval,
            metadata={
                "subscription_type": subscription.subscription_type,
                "addons": ",".join(subscription.addons) if subscription.addons else "none",
                "early_adopter": str(subscription.early_adopter_discount),
            }
        )
        
        # Update database with Stripe IDs
        subscription.stripe_customer_id = customer.id
        subscription.stripe_subscription_id = stripe_subscription.id
        subscription.status = stripe_subscription.status
        subscription.current_period_start = datetime.fromtimestamp(stripe_subscription.current_period_start)
        subscription.current_period_end = datetime.fromtimestamp(stripe_subscription.current_period_end)
        
        db.commit()
        
        payment_data = {
            "customer_id": customer.id,
            "subscription_id": stripe_subscription.id,
            "status": stripe_subscription.status,
            "client_secret": stripe_subscription.latest_invoice.payment_intent.client_secret
        }
        
        return True, None, payment_data
        
    except stripe.error.CardError as e:
        return False, f"Card error: {e.user_message}", None
    except stripe.error.StripeError as e:
        return False, f"Payment error: {str(e)}", None
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def handle_payment_failure(
    db: Session,
    subscription_id: str
) -> bool:
    """
    Handle payment failure for a subscription.
    
    Args:
        db: Database session
        subscription_id: Subscription database ID
        
    Returns:
        True if handled successfully
    """
    try:
        subscription = db.query(Subscription).filter(
            Subscription.id == subscription_id
        ).first()
        
        if not subscription:
            return False
        
        # Update subscription status
        subscription.status = "past_due"
        db.commit()
        
        return True
        
    except Exception:
        return False


# =====================================
# Webhook Processing
# =====================================

def verify_webhook_signature(
    payload: bytes,
    signature: str
) -> Optional[Dict[str, Any]]:
    """
    Verify Stripe webhook signature.
    
    Args:
        payload: Raw request payload
        signature: Stripe signature header
        
    Returns:
        Event dict if valid, None otherwise
    """
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, STRIPE_WEBHOOK_SECRET
        )
        return event
    except ValueError:
        # Invalid payload
        return None
    except stripe.error.SignatureVerificationError:
        # Invalid signature
        return None


def process_webhook_event(
    db: Session,
    event: Dict[str, Any]
) -> bool:
    """
    Process a Stripe webhook event.
    
    Args:
        db: Database session
        event: Stripe event dict
        
    Returns:
        True if processed successfully
    """
    event_type = event.get("type")
    data = event.get("data", {}).get("object", {})
    
    # Handle different event types
    if event_type == "invoice.payment_failed":
        # Payment failed
        subscription_id = data.get("subscription")
        if subscription_id:
            subscription = db.query(Subscription).filter(
                Subscription.stripe_subscription_id == subscription_id
            ).first()
            if subscription:
                subscription.status = "past_due"
                db.commit()
                return True
    
    elif event_type == "invoice.payment_succeeded":
        # Payment succeeded
        subscription_id = data.get("subscription")
        if subscription_id:
            subscription = db.query(Subscription).filter(
                Subscription.stripe_subscription_id == subscription_id
            ).first()
            if subscription:
                subscription.status = "active"
                subscription.current_period_start = datetime.fromtimestamp(data.get("period_start", 0))
                subscription.current_period_end = datetime.fromtimestamp(data.get("period_end", 0))
                db.commit()
                return True
    
    elif event_type == "customer.subscription.deleted":
        # Subscription canceled
        subscription_id = data.get("id")
        if subscription_id:
            subscription = db.query(Subscription).filter(
                Subscription.stripe_subscription_id == subscription_id
            ).first()
            if subscription:
                subscription.status = "canceled"
                db.commit()
                return True
    
    elif event_type == "customer.subscription.updated":
        # Subscription updated
        subscription_id = data.get("id")
        if subscription_id:
            subscription = db.query(Subscription).filter(
                Subscription.stripe_subscription_id == subscription_id
            ).first()
            if subscription:
                subscription.status = data.get("status", subscription.status)
                subscription.cancel_at_period_end = data.get("cancel_at_period_end", False)
                db.commit()
                return True
    
    # Return True for events we don't handle
    return True
