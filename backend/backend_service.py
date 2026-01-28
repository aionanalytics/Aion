"""
backend_service.py — v1.8.2 (Fixed for current routers)
Main FastAPI backend service for AION Analytics. 
Updated to work with current consolidated routers.
"""
from dotenv import load_dotenv
load_dotenv()

import os
import threading
import subprocess
import time
from datetime import datetime
import pytz
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional

# Config paths
from backend.core.config import PATHS, TIMEZONE

# FastAPI app
app = FastAPI(title="AION Analytics Backend", version="2.1.2")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication middleware (disabled by default, enable with AUTH_ENABLED=1)
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "0") == "1"
if AUTH_ENABLED:
    try:
        from backend.middleware.auth_middleware import AuthMiddleware
        app.add_middleware(AuthMiddleware)
        print("[Backend] ✅ Authentication middleware enabled")
    except Exception as e:
        print(f"[Backend] ⚠️  Failed to load auth middleware: {e}")

# ======================================================
# ROUTERS - Consolidated Structure
# ======================================================

# NEW: Consolidated domain routers
from backend.routers.system_router import router as system_router
from backend.routers.logs_router import router as logs_router
from backend.routers.bots_router import router as bots_router
from backend.routers.insights_router_consolidated import router as insights_router
from backend.routers.admin_router_final import router as admin_router_final

# KEEP: Essential standalone routers
from backend.routers.events_router import router as events_router
from backend.routers.unified_cache_router import router as unified_cache_router
from backend.routers.pnl_dashboard_router import router as pnl_dashboard_router

# KEEP: Additional feature routers (not consolidated per requirements)
try:
    from backend.routers.model_router import router as model_router
except ImportError:
    model_router = None

try:
    from backend.routers.testing_router import router as testing_router
except ImportError:
    testing_router = None

try:
    from backend.routers.intraday_router import router as intraday_router
except ImportError:
    intraday_router = None

try:
    from backend.routers.replay_router import router as replay_router
except ImportError:
    replay_router = None

try:
    from backend.routers.page_data_router import router as page_data_router
except ImportError:
    page_data_router = None

try:
    from backend.routers.live_prices_router import router as live_prices_router
except ImportError:
    live_prices_router = None

# NEW: Authentication routers
try:
    from backend.routers.auth_router import router as auth_router
except ImportError:
    auth_router = None

try:
    from backend.routers.subscription_router import router as subscription_router
except ImportError:
    subscription_router = None

try:
    from backend.routers.admin_router_auth import router as admin_auth_router
except ImportError:
    admin_auth_router = None

try:
    from backend.routers.webhook_router import router as webhook_router
except ImportError:
    webhook_router = None

# Include all routers
ROUTERS = [
    # NEW: Consolidated routers (6 domain routers)
    system_router,           # /api/system/ (status, health, diagnostics, actions)
    logs_router,             # /api/logs/ (nightly, intraday, all logs)
    bots_router,             # /api/bots/ (page, status, configs, signals, equity)
    insights_router,         # /api/insights/ (boards, predictions, portfolio, metrics)
    admin_router_final,      # /admin/ (status, settings, replay, tools)
    
    # KEEP: Essential routers
    events_router,           # /api/events/ (SSE streaming)
    unified_cache_router,    # /api/cache/ (unified cache)
    pnl_dashboard_router,    # /api/pnl/ (PnL dashboard)
]

# Add optional routers if available
for router in [model_router, testing_router, intraday_router, replay_router, page_data_router, live_prices_router, auth_router, subscription_router, admin_auth_router, webhook_router]:
    if router is not None:
        ROUTERS.append(router)

for router in ROUTERS:
    app.include_router(router)

# Root endpoint
@app.get("/")
def root():
    return {
        "service": "AION Analytics Backend",
        "version": "2.1.2",
        "status": "online",
        "message": "AION — Predict, Learn, Evolve."
    }

# ======================================================
# Background Threads
# ======================================================

def _backend_heartbeat():
    """Hourly heartbeat"""
    tz = pytz.timezone("America/New_York")
    while True:
        now = datetime.now(tz)
        print(f"[Backend] ❤️ Alive — {now:%H:%M %Z}", flush=True)
        time.sleep(3600)

@app.on_event("startup")
def on_startup():
    print("[Backend] 🚀 Startup sequence...", flush=True)
    
    # Initialize database if auth is enabled
    if AUTH_ENABLED:
        try:
            from backend.database.connection import init_db
            init_db()
            print("[Backend] ✅ Database initialized")
        except Exception as e:
            print(f"[Backend] ⚠️  Database initialization failed: {e}")
    
    threading.Thread(target=_backend_heartbeat, daemon=True).start()
    print("[Backend] ✅ Ready!", flush=True)

if __name__ == "__main__": 
    import uvicorn
    uvicorn.run("backend.backend_service:app", host="0.0.0.0", port=8000, workers=1)
