"""
User model and schemas.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pydantic import BaseModel, EmailStr, Field

from backend.database.connection import Base


# =====================================
# SQLAlchemy ORM Model
# =====================================

class User(Base):
    """User database model."""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    deleted_at = Column(DateTime, nullable=True)  # Soft delete

    # Relationships
    subscriptions = relationship("Subscription", back_populates="user")
    tokens = relationship("Token", back_populates="user")
    password_resets = relationship("PasswordReset", back_populates="user")


# =====================================
# Pydantic Schemas
# =====================================

class UserBase(BaseModel):
    """Base user schema."""
    email: EmailStr


class UserCreate(UserBase):
    """Schema for creating a new user."""
    password: str = Field(..., min_length=8, max_length=100)


class UserLogin(BaseModel):
    """Schema for user login."""
    email: EmailStr
    password: str


class UserResponse(UserBase):
    """Schema for user response (no password)."""
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserInDB(UserResponse):
    """Schema for user in database (with password hash)."""
    password_hash: str

    class Config:
        from_attributes = True
