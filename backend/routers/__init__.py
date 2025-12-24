"""
AION Analytics — Backend Routers Registry
"""

from .system_status_router import router as system_status_router
from .insights_router import router as insights_router
from .live_prices_router import router as live_prices_router
from .intraday_router import router as intraday_router
from .model_router import router as model_router
from .metrics_router import router as metrics_router
from .settings_router import router as settings_router
from .eod_bots_router import router as eod_bots_router
from .replay_router import router as replay_router
from .intraday_logs_router import router as intraday_logs_router
from .intraday_stream_router import router as intraday_stream_router

__all__ = [
    "system_status_router",
    "insights_router",
    "live_prices_router",
    "intraday_router",
    "model_router",
    "metrics_router",
    "settings_router",
    "eod_bots_router",
    "replay_router",
    "intraday_logs_router",
    "intraday_stream_router",
]
