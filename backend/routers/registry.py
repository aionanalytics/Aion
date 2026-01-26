# backend/routers/registry.py
"""
Router Registry — AION Analytics

This module documents all active routers in the backend service.
It serves as the single source of truth for the API structure.

Last Updated: 2026-01-26
Version: 2.1.0 (Router Consolidation - Cleanup Complete)
"""

from typing import Dict, List, Any

# =========================================================================
# CONSOLIDATED ROUTERS (v2.0.0)
# =========================================================================

CONSOLIDATED_ROUTERS: Dict[str, Dict[str, Any]] = {
    "system_router": {
        "file": "backend/routers/system_router.py",
        "prefix": "/api/system",
        "description": "System status, health, diagnostics, and actions",
        "consolidates": [
            "system_status_router.py (deleted)",
            "health_router.py (deleted)", 
            "system_run_router.py (deleted)",
            "diagnostics_router.py (deleted)"
        ],
        "endpoints": [
            "GET /api/system/status",
            "GET /api/system/health",
            "GET /api/system/diagnostics",
            "POST /api/system/action"
        ]
    },
    
    "logs_router": {
        "file": "backend/routers/logs_router.py",
        "prefix": "/api/logs",
        "description": "All log file access (nightly, intraday, scheduler, backend)",
        "consolidates": [
            "nightly_logs_router.py (deleted)",
            "intraday_logs_router.py (log endpoints only)"
        ],
        "endpoints": [
            "GET /api/logs/list?scope=nightly|intraday|scheduler|backend",
            "GET /api/logs/{id}",
            "GET /api/logs/nightly/recent",
            "GET /api/logs/intraday/recent",
            "GET /api/logs/nightly/runs (backward compat)",
            "GET /api/logs/nightly/run/{run_id} (backward compat)",
            "GET /api/logs/nightly/{day}"
        ]
    },
    
    "bots_router": {
        "file": "backend/routers/bots_router.py",
        "prefix": "/api/bots",
        "description": "All bot-related data (swing + intraday)",
        "consolidates": [
            "bots_page_router.py",
            "bots_hub_router.py",
            "eod_bots_router.py (aggregation only)"
        ],
        "endpoints": [
            "GET /api/bots/page",
            "GET /api/bots/overview",
            "GET /api/bots/status",
            "GET /api/bots/configs",
            "GET /api/bots/signals",
            "GET /api/bots/equity"
        ]
    },
    
    "insights_router": {
        "file": "backend/routers/insights_router_consolidated.py",
        "prefix": "/api/insights",
        "description": "Insights, predictions, portfolio, and metrics",
        "consolidates": [
            "insights_router.py",
            "metrics_router.py",
            "portfolio_router.py"
        ],
        "endpoints": [
            "GET /api/insights/boards/{board}",
            "GET /api/insights/top-predictions",
            "GET /api/insights/portfolio",
            "GET /api/insights/metrics",
            "GET /api/insights/predictions/latest (backward compat)"
        ]
    },
    
    "admin_router": {
        "file": "backend/routers/admin_router_final.py",
        "prefix": "/admin",
        "description": "Admin operations, settings, replay control, tools",
        "consolidates": [
            "admin_consolidated_router.py (deleted)",
            "backend/admin/routes.py",
            "backend/admin/admin_tools_router.py",
            "settings_router.py",
            "swing_replay_router.py",
            "dashboard_router.py"
        ],
        "endpoints": [
            "GET /admin/status",
            "GET /admin/logs",
            "POST /admin/settings/update",
            "GET /admin/settings/current",
            "GET /admin/settings/keys/status",
            "POST /admin/settings/keys/test",
            "GET /admin/replay/status",
            "POST /admin/replay/start",
            "POST /admin/replay/stop",
            "POST /admin/replay/reset",
            "POST /admin/login",
            "GET /admin/tools/logs",
            "POST /admin/tools/clear-locks",
            "POST /admin/tools/git-pull",
            "POST /admin/tools/refresh-universes",
            "POST /admin/system/restart"
        ]
    }
}


# =========================================================================
# STANDALONE ROUTERS (Kept As-Is)
# =========================================================================

STANDALONE_ROUTERS: Dict[str, Dict[str, Any]] = {
    "events_router": {
        "file": "backend/routers/events_router.py",
        "prefix": "/api/events",
        "description": "Server-Sent Events (SSE) streaming",
        "reason_kept": "Specialized SSE implementation",
        "endpoints": [
            "GET /api/events/bots (SSE stream)"
        ]
    },
    
    "unified_cache_router": {
        "file": "backend/routers/unified_cache_router.py",
        "prefix": "/api/cache",
        "description": "Unified cache service for frontend data",
        "reason_kept": "Standalone caching layer",
        "endpoints": [
            "GET /api/cache/unified",
            "POST /api/cache/unified/refresh",
            "GET /api/cache/unified/age"
        ]
    },
    
    "model_router": {
        "file": "backend/routers/model_router.py",
        "prefix": "/api/models",
        "description": "ML model training, tuning, and prediction",
        "reason_kept": "Separate ML operations domain",
        "endpoints": [
            "GET /api/models/list",
            "GET /api/models/{id}",
            "POST /api/models/upload",
            "GET /api/models/{id}/performance"
        ]
    },
    
    "testing_router": {
        "file": "backend/routers/testing_router.py",
        "prefix": "/api/testing",
        "description": "Endpoint verification and testing",
        "reason_kept": "Testing infrastructure",
        "endpoints": [
            "GET /api/testing/health",
            "POST /api/testing/echo"
        ]
    },
    
    "intraday_router": {
        "file": "backend/routers/intraday_router.py",
        "prefix": "/api/intraday",
        "description": "Intraday/DT-specific operations",
        "reason_kept": "DT backend integration",
        "endpoints": [
            "GET /api/intraday/status",
            "POST /api/intraday/refresh"
        ]
    },
    
    "replay_router": {
        "file": "backend/routers/replay_router.py",
        "prefix": "/api/replay",
        "description": "Historical replay operations",
        "reason_kept": "Specialized replay engine",
        "endpoints": [
            "GET /api/replay/jobs",
            "GET /api/replay/days"
        ]
    },
    
    "page_data_router": {
        "file": "backend/routers/page_data_router.py",
        "prefix": "/api/page",
        "description": "Page-specific data bundles",
        "reason_kept": "Frontend page optimization",
        "endpoints": [
            "GET /api/page/bots",
            "GET /api/page/profile",
            "GET /api/page/dashboard"
        ]
    },
    
    "live_prices_router": {
        "file": "backend/routers/live_prices_router.py",
        "prefix": "/api/live-prices",
        "description": "Real-time market data",
        "reason_kept": "Market data streaming",
        "endpoints": [
            "GET /api/live-prices/{symbol}"
        ]
    },
    
    "pnl_dashboard_router": {
        "file": "backend/routers/pnl_dashboard_router.py",
        "prefix": "/api/pnl",
        "description": "PnL dashboard data",
        "reason_kept": "Specialized PnL calculations",
        "endpoints": [
            "GET /api/pnl/dashboard"
        ]
    }
}


# =========================================================================
# DEPRECATED ROUTERS (Deleted - Replaced by Consolidated Routers)
# =========================================================================

DEPRECATED_ROUTERS: List[str] = [
    # Replaced by system_router - DELETED
    "system_status_router.py",
    "health_router.py",
    "system_run_router.py",
    "diagnostics_router.py",
    
    # Replaced by logs_router - DELETED
    "nightly_logs_router.py",
    
    # Replaced by admin_router_final - DELETED
    "admin_consolidated_router.py",
    
    # Unused routers - DELETED in cleanup
    "intraday_stream_router.py",
    "intraday_tape_router.py",
    "settings_consolidated_router.py",
]


# =========================================================================
# ROUTER SUMMARY
# =========================================================================

def get_router_summary() -> Dict[str, Any]:
    """Get a summary of all routers in the system."""
    return {
        "version": "2.1.0",
        "updated": "2026-01-26",
        "summary": {
            "consolidated_routers": len(CONSOLIDATED_ROUTERS),
            "standalone_routers": len(STANDALONE_ROUTERS),
            "total_active_routers": len(CONSOLIDATED_ROUTERS) + len(STANDALONE_ROUTERS),
            "deprecated_routers": len(DEPRECATED_ROUTERS),
            "cleanup_status": "complete"
        },
        "consolidated": CONSOLIDATED_ROUTERS,
        "standalone": STANDALONE_ROUTERS,
        "deprecated": DEPRECATED_ROUTERS
    }


# =========================================================================
# ENDPOINT PREFIX MAPPING
# =========================================================================

ENDPOINT_PREFIXES: Dict[str, str] = {
    "/api/system": "system_router (consolidated)",
    "/api/logs": "logs_router (consolidated)",
    "/api/bots": "bots_router (consolidated)",
    "/api/insights": "insights_router_consolidated (consolidated)",
    "/admin": "admin_router_final (consolidated)",
    "/api/events": "events_router (standalone)",
    "/api/cache": "unified_cache_router (standalone)",
    "/api/models": "model_router (standalone)",
    "/api/testing": "testing_router (standalone)",
    "/api/intraday": "intraday_router (standalone)",
    "/api/replay": "replay_router (standalone)",
    "/api/page": "page_data_router (standalone)",
    "/api/live-prices": "live_prices_router (standalone)",
    "/api/pnl": "pnl_dashboard_router (standalone)",
}


if __name__ == "__main__":
    import json
    summary = get_router_summary()
    print(json.dumps(summary, indent=2))
