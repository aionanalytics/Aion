from __future__ import annotations

import os
import time
import secrets
import hmac
import hashlib
from typing import Any

from fastapi import HTTPException

# ---------------------------------------------------------------------
# Load environment variables (.env support)
# ---------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
ADMIN_TOKEN_TTL = int(os.getenv("ADMIN_TOKEN_TTL_SECONDS", "3600"))

# In-memory token store (single-admin backend model)
_ADMIN_TOKENS: dict[str, float] = {}


# ---------------------------------------------------------------------
# Startup diagnostics
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


def _validate_token(token: str) -> bool:
    exp = _ADMIN_TOKENS.get(token)
    if not exp:
        return False

    if time.time() > exp:
        _ADMIN_TOKENS.pop(token, None)
        return False

    return True


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def login_admin(password: str) -> bool:
    if not ADMIN_PASSWORD_HASH:
        return False

    return _consteq(_hash_password(password), ADMIN_PASSWORD_HASH)


def issue_token() -> str:
    token = secrets.token_hex(32)
    _ADMIN_TOKENS[token] = time.time() + ADMIN_TOKEN_TTL
    return token


def require_admin(token_or_request: Any) -> str:
    """
    Admin validator.

    Accepts:
      • raw token string
      • FastAPI Request (with headers)

    Returns:
      • token string if valid
    Raises:
      • HTTPException(403) if invalid
    """

    token = ""

    # Case 1: raw token string
    if isinstance(token_or_request, str):
        token = token_or_request.strip()

    # Case 2: FastAPI Request-like object
    else:
        headers = getattr(token_or_request, "headers", None)
        if headers:
            auth = headers.get("authorization", "") or ""
            if auth.lower().startswith("bearer "):
                token = auth.split(" ", 1)[1].strip()
            else:
                token = auth.strip()

            if not token:
                token = (
                    headers.get("x-admin-token")
                    or headers.get("X-Admin-Token")
                    or ""
                ).strip()

    if not token or not _validate_token(token):
        raise HTTPException(status_code=403, detail="forbidden")

    return token
