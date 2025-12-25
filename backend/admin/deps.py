from fastapi import Request, HTTPException
from backend.admin.auth import require_admin


def admin_required(request: Request) -> None:
    """
    FastAPI dependency that ENFORCES admin auth.
    Raises 403 if invalid.
    """
    if not require_admin(request):
        raise HTTPException(status_code=403, detail="forbidden")
