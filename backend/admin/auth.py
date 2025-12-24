from __future__ import annotations

import os
import time
import secrets
import hmac
import hashlib
from typing import Any

# ---------------------------------------------------------------------
# Load environment variables (.env support)
# ---------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # dotenv is optional; env vars may already be injected by the server
    pass


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

# Store SHA256(password) in env as ADMIN_PASSWORD_HASH
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
ADMIN_TOKEN_TTL = int(os.getenv("ADMIN_TOKEN_TTL_SECONDS", "3600"))

# In-memory tokens (single-admin, backend-only)
_ADMIN_TOKENS: dict[str, float] = {}


# ---------------------------------------------------------------------
# Startup diagnostics (VERY IMPORTANT)
# ---------------------------------------------------------------------

print(
    "[admin.auth] ADMIN_PASSWORD_HASH loaded:",
    "SET" if ADMIN_PASSWORD_HASH else "MISSING",
    f"(len={len(ADMIN_PASSWORD_HASH)})",
)
print(
    "[admin.auth] ADMIN_TOKEN_TTL_SECONDS:",
    ADMIN_TOKEN_TTL,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _consteq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a, b)
    except Exception:
        return a == b


# ---------------------------------------------------------------------
# Public API (used by routes)
# ---------------------------------------------------------------------

def login_admin(password: str) -> bool:
    """
    Validate admin password against stored hash.
    """
    if not ADMIN_PASSWORD_HASH:
        return False

    computed = _hash_password(password)
    return _consteq(computed, ADMIN_PASSWORD_HASH)


def issue_token() -> str:
    """
    Issue a short-lived admin token.
    """
    token = secrets.token_hex(32)
    _ADMIN_TOKENS[token] = time.time() + ADMIN_TOKEN_TTL
    return token


def validate_token(token: str) -> bool:
    """
    Validate token existence + expiry.
    """
    exp = _ADMIN_TOKENS.get(token)
    if not exp:
        return False

    if time.time() > exp:
        _ADMIN_TOKENS.pop(token, None)
        return False

    return True


def require_admin(token_or_request: Any) -> bool:
    """
    FastAPI-compatible admin guard.

    Accepts:
      • token string
      • FastAPI Request (or any object with .headers)

    Returns:
      bool (NO decorators, NO Flask globals)
    """
    # Raw token case
    if isinstance(token_or_request, str):
        return validate_token(token_or_request)

    # Request-like case
    headers = getattr(token_or_request, "headers", None)
    if headers:
        token = (
            headers.get("x-admin-token")
            or headers.get("X-Admin-Token")
            or ""
        ).strip()

        if token:
            return validate_token(token)

    return False
