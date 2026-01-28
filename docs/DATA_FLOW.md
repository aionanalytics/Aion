# Data Flow Documentation

This document traces complete end-to-end data flows in the Aion Analytics platform.

## Overview

The Aion platform uses **file-based data storage** with JSON/Parquet files as the primary data layer, with optional PostgreSQL for metrics. There is **no multi-user auth/billing system** - only admin authentication.

## 1. Admin Login Flow

```
┌─────────────────────────────────────────────────────────┐
│ ADMIN LOGIN FLOW                                        │
└─────────────────────────────────────────────────────────┘

frontend/app/tools/admin/page.tsx
  │
  ├─ User enters password
  │
  ├─ POST /admin/login
  │   Body: { "password": "..." }
  │
  └─> backend/admin/routes.py
        │
        ├─ @router.post("/login")
        │
        ├─> backend/admin/auth.py::login_admin(password)
        │     │
        │     ├─ SHA256 hash password
        │     ├─ Compare with ADMIN_PASSWORD_HASH from .env
        │     └─ If match: return True
        │
        ├─ If valid:
        │   └─> auth.issue_token()
        │         │
        │         ├─ Generate 64-char hex token
        │         ├─ Store in memory with expiry (TTL: 3600s)
        │         └─ Return token
        │
        └─ Response: { "token": "abc123...", "expires_in": 3600 }

frontend receives token
  │
  ├─ Store in localStorage.setItem("adminToken", token)
  │
  └─ Redirect to /tools/admin dashboard
```

## 2. Protected Admin Route Flow

```
┌─────────────────────────────────────────────────────────┐
│ PROTECTED ADMIN ROUTE FLOW                              │
└─────────────────────────────────────────────────────────┘

frontend/app/tools/admin/page.tsx
  │
  ├─ Loads token from localStorage
  │
  ├─ GET /admin/settings
  │   Headers: { "Authorization": "Bearer <token>" }
  │
  └─> backend/admin/routes.py
        │
        ├─ @router.get("/settings")
        │   def get_settings(request: Request):
        │       auth.require_admin(request)  # ← Protection
        │
        └─> backend/admin/auth.py::require_admin(request)
              │
              ├─ Extract token from Authorization header
              │   or X-Admin-Token header
              │
              ├─ Check token exists in _ADMIN_TOKENS dict
              │
              ├─ Check expiry time < current time
              │
              ├─ If valid: return token
              │
              └─ If invalid: raise HTTPException(403, "forbidden")

If token valid:
  └─ Return admin settings JSON

If token invalid:
  └─ Frontend receives 403
      └─ Redirect to /tools/admin login page
```

## 3. Nightly ML Pipeline Flow

```
┌─────────────────────────────────────────────────────────┐
│ NIGHTLY ML PIPELINE (Scheduled Job)                     │
└─────────────────────────────────────────────────────────┘

backend/scheduler_runner.py
  │
  ├─ schedule.every().day.at("02:00").do(run_nightly)
  │
  └─> backend/jobs/nightly_job.py::run_nightly()
        │
        ├─ Step 1: Data Collection
        │   └─> backend/services/backfill_history.py
        │         │
        │         ├─ Fetch daily bars from yfinance
        │         ├─ Fetch fundamentals from FRED API
        │         ├─ Fetch news from sources
        │         └─ Write to PATHS["raw_daily"]/*.parquet
        │
        ├─ Step 2: Feature Engineering
        │   └─> backend/core/data_pipeline.py::build_features()
        │         │
        │         ├─ Load raw data from PATHS["raw_daily"]
        │         ├─ Calculate technical indicators
        │         ├─ Add sentiment features
        │         └─ Write to PATHS["ml_datasets"]/training_data_daily.parquet
        │
        ├─ Step 3: Model Training
        │   └─> backend/core/ai_model/trainer.py
        │         │
        │         ├─ Load dataset from PATHS["ml_datasets"]
        │         ├─ Train LightGBM models (1w, 2w, 4w horizons)
        │         └─ Save to PATHS["ml_models"]/lgbm_cache/*.pkl
        │
        ├─ Step 4: Generate Predictions
        │   └─> backend/services/ml_data_builder.py::generate_predictions()
        │         │
        │         ├─ Load models from PATHS["ml_models"]
        │         ├─ Load latest data
        │         ├─ Generate predictions for all symbols
        │         └─ Write to PATHS["rolling_brain"] (rolling_brain.json.gz)
        │
        ├─ Step 5: Update Aion Brain
        │   └─> backend/services/aion_brain_updater.py::update_brain()
        │         │
        │         ├─ Load PATHS["rolling_brain"]
        │         ├─ Add regime detection
        │         ├─ Add portfolio optimization
        │         └─ Write to PATHS["aion_brain"] (aion_brain.json.gz)
        │
        └─ Step 6: Log Results
            └─> Write summary to PATHS["nightly_summary"]
                  (logs/nightly/last_nightly_summary.json)
```

## 4. Bot Execution Flow (Swing Bots)

```
┌─────────────────────────────────────────────────────────┐
│ SWING BOT EXECUTION FLOW                                │
└─────────────────────────────────────────────────────────┘

backend/bots/runner_1w.py (manual or scheduled)
  │
  └─> backend/bots/base_swing_bot.py::SwingBot.__init__()
        │
        ├─ Step 1: Load Config
        │   │
        │   ├─ Load BOT_KNOBS_DEFAULTS from settings.py
        │   ├─ Load overrides from PATHS["bots_config"]
        │   │   (ml_data/config/bots_config.json)
        │   └─ Merge configs
        │
        ├─ Step 2: Load Rolling Data
        │   │
        │   ├─ Read PATHS["rolling_brain"]
        │   │   (da_brains/core/rolling_brain.json.gz)
        │   │
        │   └─ Parse predictions, scores, metadata
        │
        ├─ Step 3: Load Bot State
        │   │
        │   ├─ Read bot state from PATHS["stock_cache_master"]
        │   │   (data/stock_cache/master/bot/rolling_1w.json.gz)
        │   │
        │   └─ Load current positions, trade history
        │
        ├─ Step 4: Generate Signals
        │   │
        │   ├─ Filter predictions by min_confidence
        │   ├─ Rank by expected return
        │   ├─ Apply risk constraints (max_positions, max_alloc)
        │   └─ Generate entry/exit signals
        │
        ├─ Step 5: Execute Trades (Simulated or Live)
        │   │
        │   ├─ For each signal:
        │   │   ├─ Calculate position size
        │   │   ├─ Submit order via Alpaca API
        │   │   └─ Log trade details
        │   │
        │   └─ Update positions state
        │
        ├─ Step 6: Write Bot State
        │   │
        │   ├─ Save updated positions
        │   ├─ Save trade history
        │   └─ Write to PATHS["stock_cache_master"]/bot/rolling_1w.json.gz
        │
        └─ Step 7: Write Trade Logs
            │
            └─ Append to PATHS["ml_data"]/bot_logs/1w/trades_YYYYMMDD.jsonl
```

## 5. Dashboard Load Flow (Frontend)

```
┌─────────────────────────────────────────────────────────┐
│ DASHBOARD LOAD FLOW                                     │
└─────────────────────────────────────────────────────────┘

frontend/app/dashboard/page.tsx
  │
  ├─ useEffect(() => { loadDashboard() }, [])
  │
  ├─ Step 1: Fetch Bots Overview
  │   │
  │   ├─ GET /api/backend/bots/overview
  │   │
  │   └─> frontend/app/api/backend/[...path]/route.ts
  │         │
  │         ├─ Proxy to http://localhost:8000/api/bots/overview
  │         │
  │         └─> backend/routers/bots_router.py::get_overview()
  │               │
  │               ├─ Load PATHS["rolling_brain"]
  │               ├─ Load bot states from PATHS["stock_cache_master"]
  │               ├─ Aggregate metrics (positions, PnL, win rate)
  │               └─ Return JSON
  │
  ├─ Step 2: Fetch Insights
  │   │
  │   ├─ GET /api/backend/insights/predictions
  │   │
  │   └─> backend/routers/insights_router_consolidated.py::get_predictions()
  │         │
  │         ├─ Load PATHS["rolling_brain"]
  │         ├─ Filter top 50 predictions by score
  │         └─ Return JSON
  │
  ├─ Step 3: Fetch PnL Dashboard
  │   │
  │   ├─ GET /api/backend/pnl/dashboard
  │   │
  │   └─> backend/routers/pnl_dashboard_router.py::get_pnl_dashboard()
  │         │
  │         ├─ Load bot logs from PATHS["ml_data"]/bot_logs/
  │         ├─ Calculate daily/weekly/monthly PnL
  │         ├─ Calculate cumulative returns
  │         └─ Return JSON
  │
  └─ Step 4: Render Dashboard
      │
      ├─ Display bot cards with metrics
      ├─ Display top predictions table
      └─ Display PnL charts
```

## 6. Bots Page Data Flow

```
┌─────────────────────────────────────────────────────────┐
│ BOTS PAGE DATA FLOW                                     │
└─────────────────────────────────────────────────────────┘

frontend/app/bots/page.tsx
  │
  ├─ useEffect(() => { getBotsPageBundle() }, [])
  │
  ├─ GET /api/backend/bots/page
  │
  └─> frontend/app/api/backend/[...path]/route.ts
        │
        ├─ Proxy to http://localhost:8000/api/bots/page
        │
        └─> backend/routers/bots_router.py::get_bots_page()
              │
              ├─ Step 1: Load Rolling Data
              │   │
              │   ├─ Read PATHS["rolling_brain"]
              │   └─ Extract latest predictions
              │
              ├─ Step 2: Load Bot States (all horizons)
              │   │
              │   ├─ Read PATHS["stock_cache_master"]/bot/rolling_1w.json.gz
              │   ├─ Read PATHS["stock_cache_master"]/bot/rolling_2w.json.gz
              │   ├─ Read PATHS["stock_cache_master"]/bot/rolling_4w.json.gz
              │   └─ Parse positions, PnL, trades
              │
              ├─ Step 3: Load Bot Configs
              │   │
              │   └─ Read PATHS["bots_config"]
              │       (ml_data/config/bots_config.json)
              │
              ├─ Step 4: Aggregate Data
              │   │
              │   ├─ For each bot:
              │   │   ├─ Current positions
              │   │   ├─ Pending signals
              │   │   ├─ Recent trades
              │   │   ├─ Performance metrics
              │   │   └─ Config knobs
              │   │
              │   └─ Compile into bundle
              │
              └─ Return JSON bundle

frontend receives bundle
  │
  └─ Render:
      ├─ Bot status cards (1w, 2w, 4w)
      ├─ Active positions table
      ├─ Pending signals table
      └─ Performance charts
```

## 7. Intraday Bot Flow (DT Backend)

```
┌─────────────────────────────────────────────────────────┐
│ INTRADAY BOT EXECUTION FLOW (DT)                        │
└─────────────────────────────────────────────────────────┘

dt_backend/scheduler_runner.py
  │
  ├─ schedule.every(15).minutes.do(run_intraday_cycle)
  │
  └─> dt_backend/jobs/intraday_job.py::run_intraday_cycle()
        │
        ├─ Step 1: Check Market Hours
        │   │
        │   ├─ Load market calendar
        │   └─ Exit if market closed
        │
        ├─ Step 2: Fetch Latest Bars
        │   │
        │   └─> dt_backend/services/intraday_fetcher.py
        │         │
        │         ├─ Fetch 1-min bars from Alpaca
        │         └─ Write to DT_PATHS["bars_intraday_dir"]
        │
        ├─ Step 3: Update Rolling Intraday
        │   │
        │   └─> dt_backend/services/rolling_updater.py
        │         │
        │         ├─ Load latest bars
        │         ├─ Calculate intraday features
        │         ├─ Generate predictions
        │         └─ Write to DT_PATHS["rolling"]
        │             (da_brains/intraday/rolling_intraday.json.gz)
        │
        ├─ Step 4: Execute DT Bots
        │   │
        │   └─> dt_backend/bots/dt_bot.py
        │         │
        │         ├─ Load DT_PATHS["rolling"]
        │         ├─ Load DT_PATHS["dt_brain_file"]
        │         ├─ Generate entry/exit signals
        │         ├─ Execute trades
        │         ├─ Update bot state
        │         └─ Write to DT_PATHS["dt_state_file"]
        │
        └─ Step 5: Log Trades
            │
            └─ Append to DT_PATHS["dt_trades_file"]
                (da_brains/intraday/dt_trades.jsonl)
```

## 8. Configuration Update Flow

```
┌─────────────────────────────────────────────────────────┐
│ BOT CONFIG UPDATE FLOW                                  │
└─────────────────────────────────────────────────────────┘

frontend/app/bots/config/page.tsx
  │
  ├─ User adjusts knobs (aggression, max_positions, etc.)
  │
  ├─ POST /api/backend/bots/config
  │   Body: {
  │     "bot_type": "swing",
  │     "horizon": "1w",
  │     "config": { "aggression": 0.7, "max_positions": 15 }
  │   }
  │
  └─> backend/routers/bots_router.py::update_config()
        │
        ├─ Validate config (min/max ranges)
        │
        ├─ Load current PATHS["bots_config"]
        │
        ├─ Merge new config
        │
        ├─ Write to PATHS["bots_config"]
        │   (ml_data/config/bots_config.json)
        │
        └─ Return { "success": true }

Next bot run:
  │
  └─> Loads updated config from PATHS["bots_config"]
      └─ Applies new knobs to trading logic
```

## 9. Logs Retrieval Flow

```
┌─────────────────────────────────────────────────────────┐
│ LOGS RETRIEVAL FLOW                                     │
└─────────────────────────────────────────────────────────┘

frontend/app/tools/logs/page.tsx
  │
  ├─ GET /api/backend/logs/nightly?date=2024-01-15
  │
  └─> backend/routers/logs_router.py::get_nightly_logs()
        │
        ├─ Parse date parameter
        │
        ├─ Construct log path:
        │   PATHS["nightly_logs"]/nightly_YYYYMMDD.log
        │
        ├─ Read log file (tail last 1000 lines)
        │
        └─ Return { "logs": [...], "date": "2024-01-15" }

frontend receives logs
  │
  └─ Display in scrollable text area
      └─ Highlight errors/warnings
```

## 10. Real-Time Updates (SSE)

```
┌─────────────────────────────────────────────────────────┐
│ SERVER-SENT EVENTS (SSE) FLOW                           │
└─────────────────────────────────────────────────────────┘

frontend/app/bots/page.tsx
  │
  ├─ useEffect(() => {
  │     const eventSource = new EventSource('/api/backend/events/bots')
  │     eventSource.onmessage = (e) => updateBotData(e.data)
  │   }, [])
  │
  └─> backend/routers/events_router.py::stream_bots()
        │
        ├─ @router.get("/bots")
        │   async def stream_bots():
        │       return EventSourceResponse(bot_event_generator())
        │
        └─> async def bot_event_generator():
              │
              ├─ While True:
              │   │
              │   ├─ Load PATHS["rolling_brain"]
              │   ├─ Load bot states
              │   ├─ Detect changes
              │   ├─ Yield SSE message
              │   └─ await asyncio.sleep(5)
              │
              └─ Client receives updates every 5 seconds
```

## 11. File Write Summary

### Files Written During Operation:

1. **Nightly Job Outputs:**
   - `PATHS["raw_daily"]/*.parquet` (daily bars)
   - `PATHS["ml_datasets"]/training_data_daily.parquet` (features)
   - `PATHS["ml_models"]/lgbm_cache/*.pkl` (trained models)
   - `PATHS["rolling_brain"]` (predictions)
   - `PATHS["aion_brain"]` (optimized brain)
   - `PATHS["nightly_logs"]/nightly_YYYYMMDD.log` (logs)
   - `PATHS["nightly_summary"]` (summary JSON)

2. **Bot Execution Outputs:**
   - `PATHS["stock_cache_master"]/bot/rolling_<horizon>.json.gz` (bot state)
   - `PATHS["ml_data"]/bot_logs/<horizon>/trades_YYYYMMDD.jsonl` (trades)
   - `PATHS["bots_config"]` (config updates)

3. **Intraday Outputs:**
   - `DT_PATHS["bars_intraday_dir"]/*.parquet` (intraday bars)
   - `DT_PATHS["rolling"]` (intraday predictions)
   - `DT_PATHS["dt_state_file"]` (DT bot state)
   - `DT_PATHS["dt_trades_file"]` (DT trades)
   - `DT_PATHS["dt_brain_file"]` (DT brain)

4. **Cache Outputs:**
   - `PATHS["news_cache"]/*.json` (news cache)
   - `PATHS["dashboard_cache"]/*.json` (dashboard cache)
   - `PATHS["cloud_cache"]/*.json` (cloud sync)

## 12. File Read Summary

### Files Read During Operation:

1. **Configuration:**
   - `config.py` (PATHS)
   - `settings.py` (TIMEZONE, knobs)
   - `admin_keys.py` (API keys)
   - `.env` (environment variables)
   - `knobs.env` (swing knobs)
   - `dt_knobs.env` (intraday knobs)

2. **Bot Inputs:**
   - `PATHS["rolling_brain"]` (predictions)
   - `PATHS["aion_brain"]` (optimized brain)
   - `PATHS["bots_config"]` (bot configs)
   - `PATHS["stock_cache_master"]/bot/*.json.gz` (bot states)

3. **Data Sources:**
   - `PATHS["raw_daily"]/*.parquet` (historical data)
   - `PATHS["ml_datasets"]/*.parquet` (training data)
   - `PATHS["ml_models"]/*.pkl` (trained models)

4. **Logs & Metrics:**
   - `PATHS["nightly_logs"]/*.log`
   - `PATHS["ml_data"]/bot_logs/*/*.jsonl`
   - `DT_PATHS["dt_trades_file"]`

## Summary

**Key Data Flows:**
1. ✅ Admin login → token issuance → localStorage → protected routes
2. ✅ Nightly job → data collection → feature engineering → model training → predictions
3. ✅ Bot execution → load predictions → generate signals → execute trades → log results
4. ✅ Dashboard load → proxy API calls → aggregate data → render UI
5. ✅ Config updates → validate → write JSON → next run applies
6. ✅ Real-time updates → SSE streaming → frontend auto-refresh

**Data Storage:**
- Primary: File-based JSON/Parquet (no database required)
- Optional: PostgreSQL for metrics/analytics
- Cache: In-memory (Redis optional)
