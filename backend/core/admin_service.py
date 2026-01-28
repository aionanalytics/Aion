"""
Admin authentication service.
Handles admin login and token management.
"""
from __future__ import annotations

import os
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from backend.models.token import AdminToken

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
ADMIN_TOKEN_EXPIRE_HOURS = 24


# =====================================
# Password Verification
# =====================================

def verify_admin_password(password: str) -> bool:
    """
    Verify admin password.
    
    Args:
        password: Plain text password
        
    Returns:
        True if password matches
    """
    # Support both plain password and hashed password in env
    if ADMIN_PASSWORD and password == ADMIN_PASSWORD:
        return True
    
    if ADMIN_PASSWORD_HASH:
        # Simple SHA-256 hash comparison for admin password
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        return password_hash == ADMIN_PASSWORD_HASH
    
    return False


# =====================================
# Token Generation
# =====================================

def create_admin_token(db: Session) -> str:
    """
    Create an admin JWT token.
    
    Args:
        db: Database session
        
    Returns:
        Encoded JWT token
    """
    # Create token payload
    payload = {
        "sub": "admin",
        "type": "admin",
        "exp": datetime.utcnow() + timedelta(hours=ADMIN_TOKEN_EXPIRE_HOURS),
    }
    
    # Generate token
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    
    # Store token hash in database
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    admin_token = AdminToken(
        token_hash=token_hash,
        expires_at=datetime.utcnow() + timedelta(hours=ADMIN_TOKEN_EXPIRE_HOURS),
    )
    
    db.add(admin_token)
    db.commit()
    
    return token


def verify_admin_token(db: Session, token: str) -> Tuple[bool, Optional[str]]:
    """
    Verify an admin JWT token.
    
    Args:
        db: Database session
        token: JWT token to verify
        
    Returns:
        Tuple of (is_valid, error_reason)
    """
    try:
        # Decode token
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        
        if payload.get("type") != "admin":
            return False, "invalid_token_type"
        
        # Check if token is in database and not revoked
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        token_record = db.query(AdminToken).filter(
            AdminToken.token_hash == token_hash,
            AdminToken.revoked == False
        ).first()
        
        if not token_record:
            return False, "token_revoked"
        
        # Check if token is expired
        if token_record.expires_at < datetime.utcnow():
            return False, "token_expired"
        
        return True, None
        
    except JWTError:
        return False, "invalid_token"


def revoke_admin_token(db: Session, token: str) -> bool:
    """
    Revoke an admin token (logout).
    
    Args:
        db: Database session
        token: JWT token to revoke
        
    Returns:
        True if successfully revoked
    """
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        token_record = db.query(AdminToken).filter(
            AdminToken.token_hash == token_hash
        ).first()
        
        if token_record:
            token_record.revoked = True
            db.commit()
            return True
        
        return False
    except Exception:
        return False


def authenticate_admin(db: Session, password: str) -> Tuple[bool, Optional[str]]:
    """
    Authenticate admin with password.
    
    Args:
        db: Database session
        password: Plain text password
        
    Returns:
        Tuple of (token if successful, error message)
    """
    if not verify_admin_password(password):
        return None, "invalid_password"
    
    # Generate token
    token = create_admin_token(db)
    return token, None
