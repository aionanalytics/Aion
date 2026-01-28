"""
Stripe webhook router.
Handles Stripe webhook events.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.core.stripe_service import verify_webhook_signature, process_webhook_event

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Handle Stripe webhook events.
    
    Args:
        request: FastAPI request
        db: Database session
        
    Returns:
        Success message
    """
    # Get raw payload and signature
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    
    if not signature:
        raise HTTPException(status_code=400, detail="Missing signature")
    
    # Verify webhook signature
    event = verify_webhook_signature(payload, signature)
    
    if not event:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Process event
    success = process_webhook_event(db, event)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to process webhook")
    
    return {"status": "success"}
