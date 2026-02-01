"""
Core authentication service.
Handles user signup, login, token generation, and validation.
"""
from __future__ import annotations

import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from passlib.context import CryptContext
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from backend.models.user import User, UserCreate, UserResponse
from backend.models.token import Token, TokenResponse
from backend.models.subscription import Subscription
from utils.logger import Logger

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Create logger for auth service
logger = Logger(name="auth_service", source="backend")

# Configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "default_secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 30

# Password hashing context with bcrypt rounds configured
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12  # 12 rounds for security
)

# Account lockout configuration
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15
_login_attempts: dict[str, list[datetime]] = {}  # email -> list of attempt timestamps


# =====================================
# Password Hashing
# =====================================

def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt.
    
    Args:
        password: Plain text password
        
    Returns:
        Hashed password
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.
    
    Args:
        plain_password: Plain text password
        hashed_password: Hashed password from database
        
    Returns:
        True if password matches
    """
    return pwd_context.verify(plain_password, hashed_password)


# =====================================
# Token Generation
# =====================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.
    
    Args:
        data: Payload data to encode
        expires_delta: Optional expiration time delta
        
    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    
    # Log token creation (safely, without exposing sensitive data)
    logger.debug(
        f"Creating access token - "
        f"subject: {to_encode.get('sub', 'unknown')[:8]}..., "
        f"type: access, "
        f"expires: {expire.isoformat()}"
    )
    
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """
    Create a JWT refresh token.
    
    Args:
        data: Payload data to encode
        
    Returns:
        Encoded JWT refresh token
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    
    # Log token creation (safely, without exposing sensitive data)
    logger.debug(
        f"Creating refresh token - "
        f"subject: {to_encode.get('sub', 'unknown')[:8]}..., "
        f"type: refresh, "
        f"expires: {expire.isoformat()}"
    )
    
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def hash_token(token: str) -> str:
    """
    Hash a token for storage in database.
    
    Args:
        token: JWT token
        
    Returns:
        SHA-256 hash of token
    """
    return hashlib.sha256(token.encode()).hexdigest()


# =====================================
# Account Lockout
# =====================================

def record_login_attempt(email: str) -> bool:
    """
    Record a failed login attempt and check if account should be locked.
    
    Args:
        email: User email
        
    Returns:
        True if account is locked
    """
    now = datetime.utcnow()
    
    # Initialize or get attempts list
    if email not in _login_attempts:
        _login_attempts[email] = []
    
    # Remove old attempts (older than lockout duration)
    cutoff = now - timedelta(minutes=LOCKOUT_DURATION_MINUTES)
    _login_attempts[email] = [
        attempt for attempt in _login_attempts[email]
        if attempt > cutoff
    ]
    
    # Add new attempt
    _login_attempts[email].append(now)
    
    # Check if locked
    return len(_login_attempts[email]) >= MAX_LOGIN_ATTEMPTS


def clear_login_attempts(email: str):
    """Clear login attempts for an email (on successful login)."""
    _login_attempts.pop(email, None)


def is_account_locked(email: str) -> bool:
    """Check if account is currently locked."""
    if email not in _login_attempts:
        return False
    
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=LOCKOUT_DURATION_MINUTES)
    
    # Remove old attempts
    _login_attempts[email] = [
        attempt for attempt in _login_attempts[email]
        if attempt > cutoff
    ]
    
    return len(_login_attempts[email]) >= MAX_LOGIN_ATTEMPTS


# =====================================
# User Operations
# =====================================

def create_user(db: Session, user_data: UserCreate) -> User:
    """
    Create a new user.
    
    Args:
        db: Database session
        user_data: User creation data
        
    Returns:
        Created user
        
    Raises:
        ValueError: If email already exists
    """
    # Check if email exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise ValueError("Email already registered")
    
    # Hash password
    password_hash = hash_password(user_data.password)
    
    # Create user
    user = User(
        email=user_data.email,
        password_hash=password_hash,
    )
    
    db.add(user)
    db.commit()
    db.refresh(user)
    
    return user


def authenticate_user(db: Session, email: str, password: str) -> Tuple[Optional[User], str]:
    """
    Authenticate a user with email and password.
    
    Args:
        db: Database session
        email: User email
        password: Plain text password
        
    Returns:
        Tuple of (User object if successful, error message)
    """
    # Check if account is locked
    if is_account_locked(email):
        return None, "account_locked"
    
    # Get user
    user = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
    if not user:
        record_login_attempt(email)
        return None, "invalid_credentials"
    
    # Verify password
    if not verify_password(password, user.password_hash):
        record_login_attempt(email)
        return None, "invalid_credentials"
    
    # Check subscription status
    subscription = db.query(Subscription).filter(
        Subscription.user_id == user.id
    ).first()
    
    if subscription and subscription.status == "past_due":
        return None, "payment_failed"
    
    if subscription and subscription.status in ["canceled", "suspended"]:
        return None, "subscription_inactive"
    
    # Clear login attempts on successful login
    clear_login_attempts(email)
    
    return user, "success"


def generate_tokens(db: Session, user: User) -> TokenResponse:
    """
    Generate access and refresh tokens for a user.
    
    Args:
        db: Database session
        user: User object
        
    Returns:
        Token response with access and refresh tokens
    """
    # Create token payload
    payload = {
        "sub": str(user.id),
        "email": user.email,
    }
    
    # Generate tokens
    access_token = create_access_token(payload)
    refresh_token = create_refresh_token(payload)
    
    # Store token hashes in database
    token_record = Token(
        user_id=user.id,
        token_hash=hash_token(access_token),
        refresh_token_hash=hash_token(refresh_token),
        expires_at=datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    
    db.add(token_record)
    db.commit()
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # Convert to seconds
    )


def verify_token(db: Session, token: str) -> Tuple[bool, Optional[str], Optional[User]]:
    """
    Verify a JWT token.
    
    Args:
        db: Database session
        token: JWT token to verify
        
    Returns:
        Tuple of (is_valid, error_reason, user)
    """
    try:
        # Decode token
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        
        logger.debug(
            f"Token decoded successfully - "
            f"subject: {user_id[:8] if user_id else 'unknown'}..., "
            f"type: {payload.get('type', 'unknown')}"
        )
        
        if user_id is None:
            logger.warning("Token validation failed: missing subject (sub) in payload")
            return False, "invalid_token", None
        
        # Check if token is in database and not revoked
        token_hash = hash_token(token)
        token_record = db.query(Token).filter(
            Token.token_hash == token_hash,
            Token.revoked.is_(False)
        ).first()
        
        if not token_record:
            logger.warning(
                f"Token validation failed: token not found or revoked - "
                f"subject: {user_id[:8]}..."
            )
            return False, "token_revoked", None
        
        # Check if token is expired
        if token_record.expires_at < datetime.utcnow():
            logger.warning(
                f"Token validation failed: token expired - "
                f"subject: {user_id[:8]}..., "
                f"expired_at: {token_record.expires_at.isoformat()}"
            )
            return False, "token_expired", None
        
        # Get user
        user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
        if not user:
            logger.warning(
                f"Token validation failed: user not found or deleted - "
                f"user_id: {user_id[:8]}..."
            )
            return False, "user_not_found", None
        
        # Check subscription status
        subscription = db.query(Subscription).filter(
            Subscription.user_id == user.id
        ).first()
        
        if subscription and subscription.status == "past_due":
            logger.warning(
                f"Token validation failed: payment failed - "
                f"user_id: {user_id[:8]}..., "
                f"subscription_status: {subscription.status}"
            )
            return False, "payment_failed", user
        
        if subscription and subscription.status in ["canceled", "suspended"]:
            logger.warning(
                f"Token validation failed: subscription inactive - "
                f"user_id: {user_id[:8]}..., "
                f"subscription_status: {subscription.status}"
            )
            return False, "subscription_inactive", user
        
        logger.debug(f"Token validation successful - user_id: {user_id[:8]}...")
        return True, None, user
        
    except JWTError as e:
        logger.warning(
            f"Token validation failed: invalid token signature or format - "
            f"error: {type(e).__name__}"
        )
        return False, "invalid_token", None


def revoke_token(db: Session, token: str) -> bool:
    """
    Revoke a token (logout).
    
    Args:
        db: Database session
        token: JWT token to revoke
        
    Returns:
        True if successfully revoked
    """
    try:
        token_hash = hash_token(token)
        token_record = db.query(Token).filter(Token.token_hash == token_hash).first()
        
        if token_record:
            token_record.revoked = True
            db.commit()
            return True
        
        return False
    except Exception:
        return False
