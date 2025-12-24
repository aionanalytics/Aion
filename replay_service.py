"""replay_service.py

Standalone admin service for Swing historical replay.

Run (example):
  python -m uvicorn replay_service:app --host 0.0.0.0 --port 8020

This keeps the replay endpoints separate from the main backend service.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.routers.swing_replay_router import router as swing_replay_router


app = FastAPI(title="AION Replay Service", version="0.1")


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(swing_replay_router)
