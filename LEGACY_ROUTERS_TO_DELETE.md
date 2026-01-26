# Legacy Router Files - Consolidation Status

## Overview

This document tracks the router consolidation completed in v2.0.0. The backend has been refactored from 20+ fragmented routers into **5 consolidated domain routers** + **9 standalone feature routers**.

## Status: CONSOLIDATION COMPLETED (v2.0.0)

**Completion Date:** 2026-01-26

## Consolidated Router Mapping

### 1. SYSTEM Router (`backend/routers/system_router.py`)

**Replaces:**
- ✅ `backend/routers/system_status_router.py` - `/api/system/status` endpoint
- ✅ `backend/routers/health_router.py` - `/health` endpoints  
- ✅ `backend/routers/system_run_router.py` - `/api/system/run/{task}` endpoints
- ✅ `backend/routers/diagnostics_router.py` - `/api/diagnostics` endpoint

**New Endpoints:**
- `GET /api/system/status` - Job monitor + supervisor verdict
- `GET /api/system/health` - Component health checks
- `GET /api/system/diagnostics` - File stats and path verification
- `POST /api/system/action` - System actions (replaces /run/{task})

### 2. LOGS Router (`backend/routers/logs_router.py`)

**Replaces:**
- ✅ `backend/routers/nightly_logs_router.py` - `/api/logs` endpoints
- ⚠️ `backend/routers/intraday_logs_router.py` - Log endpoints only (bot endpoints kept)

**New Endpoints:**
- `GET /api/logs/list?scope={scope}` - Unified log listing
- `GET /api/logs/{id}` - Read any log file
- `GET /api/logs/nightly/recent` - Recent nightly logs
- `GET /api/logs/intraday/recent` - Recent intraday logs

### 3. BOTS Router (`backend/routers/bots_router.py`)

**Aggregates (via delegation):**
- ✅ `backend/routers/bots_page_router.py` - `/api/bots/page` endpoint
- ✅ `backend/routers/bots_hub_router.py` - `/api/bots/overview` endpoint
- ⚠️ `backend/routers/eod_bots_router.py` - Kept for detailed EOD operations

**New Endpoints:**
- `GET /api/bots/page` - Unified bot data bundle
- `GET /api/bots/overview` - Aggregated status
- `GET /api/bots/status` - All bot statuses
- `GET /api/bots/configs` - All bot configurations
- `GET /api/bots/signals` - Latest signals
- `GET /api/bots/equity` - Portfolio equity

### 4. INSIGHTS Router (`backend/routers/insights_router_consolidated.py`)

**Aggregates (via delegation):**
- ⚠️ `backend/routers/insights_router.py` - Kept for core functionality
- ⚠️ `backend/routers/metrics_router.py` - Kept for core functionality
- ⚠️ `backend/routers/portfolio_router.py` - Kept for core functionality

**New Endpoints:**
- `GET /api/insights/boards/{board}` - Insight boards
- `GET /api/insights/top-predictions` - Top predictions
- `GET /api/insights/portfolio` - Portfolio holdings
- `GET /api/insights/metrics` - Performance metrics

### 5. ADMIN Router (`backend/routers/admin_router_final.py`)

**Aggregates (via delegation):**
- ✅ `backend/routers/admin_consolidated_router.py` - Admin operations
- ⚠️ `backend/admin/routes.py` - Kept for auth and core admin
- ⚠️ `backend/admin/admin_tools_router.py` - Kept for tools
- ⚠️ `backend/routers/settings_router.py` - Kept for settings management
- ⚠️ `backend/routers/swing_replay_router.py` - Kept for replay control

**New Endpoints:**
- `GET /admin/status` - System health
- `GET /admin/logs` - Live logs
- `POST /admin/settings/update` - Update settings
- `POST /admin/replay/start` - Start replay
- `POST /admin/login` - Authentication

## Files Safe to Delete (After Verification)

**Note:** These files can be deleted once v2.0.0 consolidation is verified stable. Most functionality has been consolidated into new routers with delegation patterns for backward compatibility.

### Safe to Delete (No longer imported in backend_service.py)

**SYSTEM domain:**
- `backend/routers/system_status_router.py` - Replaced by `system_router.py`
- `backend/routers/health_router.py` - Replaced by `system_router.py`  
- `backend/routers/system_run_router.py` - Replaced by `system_router.py`
- `backend/routers/diagnostics_router.py` - Replaced by `system_router.py`

**LOGS domain:**
- `backend/routers/nightly_logs_router.py` - Replaced by `logs_router.py`

**ADMIN domain:**
- `backend/routers/admin_consolidated_router.py` - Replaced by `admin_router_final.py`

### Keep for Delegation (Used by consolidated routers)

These files are kept because the consolidated routers delegate to them:

**Bot operations:**
- `backend/routers/bots_page_router.py` - Used by `bots_router.py`
- `backend/routers/bots_hub_router.py` - Used by `bots_router.py`
- `backend/routers/eod_bots_router.py` - Used by `bots_router.py`
- `backend/routers/intraday_logs_router.py` - Used by both `bots_router.py` and `logs_router.py`

**Insights operations:**
- `backend/routers/insights_router.py` - Used by `insights_router_consolidated.py`
- `backend/routers/metrics_router.py` - Used by `insights_router_consolidated.py`
- `backend/routers/portfolio_router.py` - Used by `insights_router_consolidated.py`

**Admin operations:**
- `backend/admin/routes.py` - Used by `admin_router_final.py`
- `backend/admin/admin_tools_router.py` - Used by `admin_router_final.py`
- `backend/routers/settings_router.py` - Used by `admin_router_final.py`
- `backend/routers/swing_replay_router.py` - Used by `admin_router_final.py`

## Files to KEEP

These files are active in v2.0.0:

### NEW Consolidated Routers (v2.0.0)
- ✅ `backend/routers/system_router.py` - SYSTEM domain consolidation
- ✅ `backend/routers/logs_router.py` - LOGS domain consolidation
- ✅ `backend/routers/bots_router.py` - BOTS domain aggregation
- ✅ `backend/routers/insights_router_consolidated.py` - INSIGHTS domain aggregation
- ✅ `backend/routers/admin_router_final.py` - ADMIN domain aggregation
- ✅ `backend/routers/registry.py` - Router documentation

### Essential Standalone Routers
- ✅ `backend/routers/events_router.py` - SSE streaming
- ✅ `backend/routers/unified_cache_router.py` - Unified cache
- ✅ `backend/routers/testing_router.py` - Testing endpoints
- ✅ `backend/routers/page_data_router.py` - Page bundles
- ✅ `backend/routers/pnl_dashboard_router.py` - PnL dashboard

### Optional Feature Routers
- ✅ `backend/routers/model_router.py` - ML operations
- ✅ `backend/routers/live_prices_router.py` - Market data
- ✅ `backend/routers/intraday_router.py` - DT operations
- ✅ `backend/routers/replay_router.py` - Historical replay

### Delegation Support Files (see above)
- ✅ `backend/admin/admin_tools_router.py` - Admin tools

## Deletion Command (Run After Phase 3 Verification)

```bash
# Navigate to repository root
cd /path/to/SAP---StockAnalyzerPro

# Delete legacy router files (run this ONLY after verifying Phase 3 is stable)
rm -f backend/routers/bots_page_router.py
rm -f backend/routers/bots_hub_router.py
rm -f backend/routers/dashboard_router.py
rm -f backend/routers/portfolio_router.py
rm -f backend/routers/system_status_router.py
rm -f backend/routers/diagnostics_router.py
rm -f backend/routers/insights_router.py
rm -f backend/routers/live_prices_router.py
rm -f backend/routers/intraday_router.py
rm -f backend/routers/model_router.py
rm -f backend/routers/metrics_router.py
rm -f backend/routers/settings_router.py
rm -f backend/routers/nightly_logs_router.py
rm -f backend/routers/replay_router.py
rm -f backend/routers/swing_replay_router.py
rm -f backend/routers/intraday_logs_router.py
rm -f backend/routers/intraday_stream_router.py
rm -f backend/routers/intraday_tape_router.py
rm -f backend/routers/eod_bots_router.py

# Commit the deletion
git add -A
git commit -m "Phase 3: Delete legacy router files"
git push
```

## Verification Before Deletion

Before deleting these files, verify:

1. ✅ All frontend pages load correctly
2. ✅ Bots page shows data from `/api/page/bots`
3. ✅ Admin pages show data from `/api/admin/*`
4. ✅ Settings pages work with `/api/settings/*`
5. ✅ No console errors about missing endpoints
6. ✅ No backend errors in logs
7. ✅ Production has been stable for 1-2 weeks

## Impact Analysis

**Total files to delete:** 19 router files
**Total size saved:** ~200 KB of code
**Maintenance reduction:** 19 fewer files to maintain
**Code complexity reduction:** Significant (from 25+ routers to 10)

## Rollback Plan

If issues arise after deletion:
1. Restore files from git history: `git checkout HEAD~1 backend/routers/`
2. Re-add imports to `backend_service.py`
3. Add routers to ROUTERS list
4. Restart backend

## Timeline

- **Phase 3 Completion:** [Current Date]
- **Verification Period:** 1-2 weeks
- **Estimated Deletion Date:** [Date + 2 weeks]
