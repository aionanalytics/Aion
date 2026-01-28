"""
Subscription model and schemas.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, ARRAY, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field
from enum import Enum

from backend.database.connection import Base


# =====================================
# Enums
# =====================================

class SubscriptionType(str, Enum):
    """Subscription types."""
    SWING = "swing"
    DAY = "day"
    BOTH = "both"


class BillingFrequency(str, Enum):
    """Billing frequency options."""
    MONTHLY = "monthly"
    ANNUAL = "annual"


class SubscriptionStatus(str, Enum):
    """Subscription status."""
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    SUSPENDED = "suspended"


# =====================================
# SQLAlchemy ORM Model
# =====================================

class Subscription(Base):
    """Subscription database model."""
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    subscription_type = Column(String(50), nullable=False)  # swing, day, both
    addons = Column(ARRAY(Text), default=[])  # ['analytics', 'backup']
    billing_frequency = Column(String(50), nullable=False)  # monthly, annual
    stripe_customer_id = Column(String(255), unique=True, nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default="active")
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)
    early_adopter_discount = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="subscriptions")


# =====================================
# Pydantic Schemas
# =====================================

class SubscriptionCreate(BaseModel):
    """Schema for creating a subscription."""
    subscription_type: SubscriptionType
    addons: List[str] = Field(default_factory=list)
    billing_frequency: BillingFrequency
    early_adopter_discount: bool = False


class SubscriptionUpdate(BaseModel):
    """Schema for updating a subscription."""
    subscription_type: Optional[SubscriptionType] = None
    addons: Optional[List[str]] = None
    billing_frequency: Optional[BillingFrequency] = None
    cancel_at_period_end: Optional[bool] = None


class SubscriptionResponse(BaseModel):
    """Schema for subscription response."""
    id: uuid.UUID
    user_id: uuid.UUID
    subscription_type: str
    addons: List[str]
    billing_frequency: str
    status: str
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    cancel_at_period_end: bool
    early_adopter_discount: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
