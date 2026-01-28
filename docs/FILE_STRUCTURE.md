# File Structure and I/O Operations

This document provides a comprehensive map of all files that read and write data in the Aion Analytics platform.

## Directory Structure Overview

```
/home/runner/work/Aion/Aion/
├── config.py                  # Root config (PATHS, DT_PATHS)
├── settings.py                # Knobs and timezone
├── admin_keys.py              # API secrets (from .env)
├── requirements.txt           # Python dependencies
├── .env                       # Environment variables
├── knobs.env                  # Swing bot parameters
├── dt_knobs.env              # Intraday parameters
│
├── backend/                   # EOD/Swing trading engine
│   ├── core/                  # Core modules
│   │   ├── config.py         # Shim layer (imports root config)
│   │   ├── data_pipeline.py  # Feature engineering
│   │   ├── regime_detector.py
│   │   └── ai_model/         # ML models
│   ├── routers/              # FastAPI routes (27 files)
│   ├── services/             # Business logic (31 files)
│   ├── bots/                 # Swing bot strategies
│   ├── jobs/                 # Scheduled jobs
│   └── admin/                # Admin auth & routes
│
├── dt_backend/               # Intraday trading engine
│   ├── core/
│   │   └── config_dt.py     # DT config (imports root)
│   ├── routers/             # DT API routes
│   ├── services/            # DT services
│   └── bots/                # Intraday bots
│
├── frontend/                 # Next.js UI
│   ├── app/                 # Pages
│   │   ├── api/backend/[...path]/route.ts  # Backend proxy
│   │   ├── api/dt/[...path]/route.ts       # DT proxy
│   │   ├── bots/page.tsx                   # Bots dashboard
│   │   ├── dashboard/page.tsx              # Main dashboard
│   │   ├── insights/page.tsx               # Predictions
│   │   └── tools/admin/page.tsx            # Admin panel
│   ├── components/          # React components
│   ├── lib/                 # Shared utilities
│   └── hooks/               # React hooks
│
├── data/                     # Data storage root
│   ├── raw/                 # Raw market data
│   │   ├── daily_bars/      # EOD OHLCV (Parquet)
│   │   ├── intraday_bars/   # 1-min bars (Parquet)
│   │   ├── news/            # News articles
│   │   └── fundamentals/    # Fundamentals
│   ├── universe/            # Symbol universes
│   ├── stock_cache/         # Bot states & cache
│   │   └── master/
│   │       └── bot/         # Bot state files
│   ├── news_cache/          # News cache
│   ├── dashboard_cache/     # Dashboard cache
│   └── replay/              # Replay engine data
│       └── swing/
│           ├── replay_state.json
│           └── snapshots/
│
├── da_brains/                # Predictions & brains
│   ├── core/
│   │   ├── rolling_brain.json.gz   # Latest predictions (EOD)
│   │   ├── aion_brain.json.gz      # Optimized brain
│   │   └── dt_brain.json.gz        # DT brain
│   ├── intraday/
│   │   ├── rolling_intraday.json.gz       # DT predictions
│   │   ├── rolling_intraday_market.json.gz # Live bars
│   │   ├── dt_state.json                   # DT runtime state
│   │   ├── dt_trades.jsonl                 # DT trade log
│   │   └── dt_metrics.json                 # DT metrics
│   └── news_n_buzz_brain/
│       ├── news_brain_rolling.json.gz
│       └── news_brain_intraday.json.gz
│
├── ml_data/                  # ML artifacts (EOD)
│   ├── config/              # Bot configs
│   │   ├── bots_config.json         # Bot settings
│   │   ├── bots_ui_overrides.json   # UI overrides
│   │   ├── swing_knob_overrides.json # Auto-tuning
│   │   └── swing_tuning_log.jsonl   # Tuning history
│   ├── models/              # Trained models
│   │   └── lgbm_cache/      # LightGBM models
│   ├── datasets/            # Training datasets
│   │   └── training_data_daily.parquet
│   ├── predictions/         # Prediction outputs
│   └── bot_logs/            # Bot trade logs
│       ├── 1w/              # 1-week horizon
│       ├── 2w/              # 2-week horizon
│       └── 4w/              # 4-week horizon
│
├── ml_data_dt/              # ML artifacts (Intraday)
│   ├── intraday/
│   │   ├── dataset/         # Intraday training data
│   │   ├── predictions/     # Intraday predictions
│   │   └── replay/          # Intraday replay
│   └── config/              # DT configs
│       ├── intraday_bots_ui.json
│       ├── dt_knob_overrides.json
│       └── dt_tuning_log.jsonl
│
├── logs/                     # Application logs
│   ├── backend/             # Backend logs
│   ├── nightly/             # Nightly job logs
│   │   ├── nightly_YYYYMMDD.log
│   │   └── last_nightly_summary.json
│   ├── scheduler/           # Scheduler logs
│   └── intraday/            # Intraday logs
│
└── insights/                 # Generated insights
    └── reports/
```

## Files That WRITE Data

### 1. Configuration Files (Written by Users/Admin)

| File | Writer | Purpose | Format |
|------|--------|---------|--------|
| `.env` | Admin | Environment variables | ENV |
| `knobs.env` | Admin/UI | Swing bot parameters | ENV |
| `dt_knobs.env` | Admin/UI | Intraday parameters | ENV |
| `ml_data/config/bots_config.json` | `bots_router.py` | Bot runtime configs | JSON |
| `ml_data/config/bots_ui_overrides.json` | `bots_router.py` | UI overrides | JSON |
| `ml_data_dt/config/intraday_bots_ui.json` | `dt_router.py` | DT UI configs | JSON |

### 2. Raw Data Collection (Written by Services)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `data/raw/daily_bars/*.parquet` | `backfill_history.py` | Daily (2am) | Parquet |
| `data/raw/intraday_bars/*.parquet` | `intraday_fetcher.py` | Every 15min | Parquet |
| `data/raw/news/*.json` | `news_intel.py` | Hourly | JSON |
| `data/raw/fundamentals/*.json` | `backfill_history.py` | Daily | JSON |
| `data/universe/master_universe.json` | `universe_builder.py` | Daily | JSON |
| `data/universe/swing_universe.json` | `universe_builder.py` | Daily | JSON |
| `data/universe/dt_universe.json` | `dt_universe_builder.py` | Daily | JSON |

### 3. ML Datasets & Models (Written by ML Pipeline)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `ml_data/datasets/training_data_daily.parquet` | `ml_data_builder.py` | Daily (2am) | Parquet |
| `ml_data/models/lgbm_cache/*.pkl` | `ai_model/trainer.py` | Daily (2am) | Pickle |
| `ml_data_dt/intraday/dataset/training_data_intraday.parquet` | `dt_ml_builder.py` | Daily | Parquet |
| `dt_backend/models/lightgbm_intraday/*.pkl` | `dt_trainer.py` | Daily | Pickle |

### 4. Predictions & Brains (Written by ML Services)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `da_brains/core/rolling_brain.json.gz` | `ml_data_builder.py` | Daily (2am) | JSON.GZ |
| `da_brains/core/aion_brain.json.gz` | `aion_brain_updater.py` | Daily (2am) | JSON.GZ |
| `da_brains/core/dt_brain.json.gz` | `dt_brain_updater.py` | Daily | JSON.GZ |
| `da_brains/intraday/rolling_intraday.json.gz` | `rolling_updater.py` | Every 15min | JSON.GZ |
| `da_brains/intraday/rolling_intraday_market.json.gz` | `live_market_data_loop` | Real-time | JSON.GZ |
| `da_brains/news_n_buzz_brain/news_brain_rolling.json.gz` | `news_brain_builder.py` | Daily | JSON.GZ |
| `da_brains/news_n_buzz_brain/news_brain_intraday.json.gz` | `news_brain_builder.py` | Hourly | JSON.GZ |

### 5. Bot States & Trade Logs (Written by Bots)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `data/stock_cache/master/bot/rolling_1w.json.gz` | `base_swing_bot.py` | On trade | JSON.GZ |
| `data/stock_cache/master/bot/rolling_2w.json.gz` | `base_swing_bot.py` | On trade | JSON.GZ |
| `data/stock_cache/master/bot/rolling_4w.json.gz` | `base_swing_bot.py` | On trade | JSON.GZ |
| `ml_data/bot_logs/1w/trades_YYYYMMDD.jsonl` | `base_swing_bot.py` | On trade | JSONL |
| `ml_data/bot_logs/2w/trades_YYYYMMDD.jsonl` | `base_swing_bot.py` | On trade | JSONL |
| `ml_data/bot_logs/4w/trades_YYYYMMDD.jsonl` | `base_swing_bot.py` | On trade | JSONL |
| `da_brains/intraday/dt_state.json` | `dt_bot.py` | Every cycle | JSON |
| `da_brains/intraday/dt_trades.jsonl` | `dt_bot.py` | On trade | JSONL |
| `da_brains/intraday/dt_metrics.json` | `dt_bot.py` | Every cycle | JSON |

### 6. Cache Files (Written by Services)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `data/news_cache/news_dashboard_latest.json` | `news_cache.py` | Hourly | JSON |
| `data/news_cache/sentiment_map_latest.json` | `sentiment_engine.py` | Hourly | JSON |
| `data/dashboard_cache/*.json` | `unified_cache_service.py` | On request | JSON |
| `data/cloud_cache/*.json` | `cloud_sync.py` | Periodic | JSON |

### 7. Replay & Snapshots (Written by Replay Engine)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `data/replay/swing/replay_state.json` | `replay_service.py` | On step | JSON |
| `data/replay/swing/snapshots/*.json` | `replay_service.py` | On snapshot | JSON |
| `ml_data_dt/intraday/replay/replay_results/*.json` | `dt_replay.py` | On replay | JSON |

### 8. Application Logs (Written by Jobs/Services)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `logs/nightly/nightly_YYYYMMDD.log` | `nightly_job.py` | Daily (2am) | Text |
| `logs/nightly/last_nightly_summary.json` | `nightly_job.py` | Daily (2am) | JSON |
| `logs/backend/backend_YYYYMMDD.log` | `backend_service.py` | Continuous | Text |
| `logs/scheduler/scheduler_YYYYMMDD.log` | `scheduler_runner.py` | Continuous | Text |
| `logs/intraday/intraday_YYYYMMDD.log` | `intraday_runner.py` | Continuous | Text |

### 9. Auto-Tuning Files (Written by Tuner)

| File | Writer | Frequency | Format |
|------|--------|-----------|--------|
| `ml_data/config/swing_knob_overrides.json` | `swing_knob_tuner.py` | After tuning | JSON |
| `ml_data/config/swing_tuning_log.jsonl` | `swing_knob_tuner.py` | After tuning | JSONL |
| `ml_data/config/swing_tuner_state.json` | `swing_knob_tuner.py` | After tuning | JSON |
| `ml_data/config/exploration_budget.json` | `swing_knob_tuner.py` | After tuning | JSON |
| `ml_data_dt/config/dt_knob_overrides.json` | `dt_knob_tuner.py` | After tuning | JSON |
| `ml_data_dt/config/dt_tuning_log.jsonl` | `dt_knob_tuner.py` | After tuning | JSONL |

## Files That READ Data

### 1. Configuration Files (Read on Startup/Request)

| File | Readers | Purpose |
|------|---------|---------|
| `config.py` | All modules | PATHS dictionary |
| `settings.py` | All modules | Timezone, knob defaults |
| `admin_keys.py` | `backend/core/config.py` | API secrets |
| `.env` | `admin_keys.py`, `backend_service.py` | Environment vars |
| `knobs.env` | `settings.py`, bots | Swing parameters |
| `dt_knobs.env` | `dt_backend/core/config_dt.py` | Intraday parameters |

### 2. Bot Configuration (Read Before Each Run)

| File | Readers | Purpose |
|------|---------|---------|
| `ml_data/config/bots_config.json` | `base_swing_bot.py`, `bots_router.py` | Bot runtime configs |
| `ml_data/config/bots_ui_overrides.json` | `bots_router.py` | UI-specific overrides |
| `ml_data/config/swing_knob_overrides.json` | `base_swing_bot.py` | Auto-tuned knobs |
| `ml_data_dt/config/intraday_bots_ui.json` | `dt_router.py` | DT UI configs |
| `ml_data_dt/config/dt_knob_overrides.json` | `dt_bot.py` | DT auto-tuned knobs |

### 3. Predictions & Brains (Read Every Cycle)

| File | Readers | Purpose |
|------|---------|---------|
| `da_brains/core/rolling_brain.json.gz` | `base_swing_bot.py`, `bots_router.py`, `insights_router.py` | EOD predictions |
| `da_brains/core/aion_brain.json.gz` | `page_data_router.py`, `insights_router.py` | Optimized predictions |
| `da_brains/core/dt_brain.json.gz` | `dt_bot.py`, `dt_router.py` | DT learning state |
| `da_brains/intraday/rolling_intraday.json.gz` | `dt_bot.py`, `dt_router.py` | Intraday predictions |
| `da_brains/intraday/rolling_intraday_market.json.gz` | `dt_router.py` | Live market data |
| `da_brains/news_n_buzz_brain/news_brain_rolling.json.gz` | `news_intel.py`, `insights_router.py` | News sentiment |

### 4. Bot States (Read on Startup & Every Cycle)

| File | Readers | Purpose |
|------|---------|---------|
| `data/stock_cache/master/bot/rolling_1w.json.gz` | `base_swing_bot.py`, `bots_router.py` | 1w bot state |
| `data/stock_cache/master/bot/rolling_2w.json.gz` | `base_swing_bot.py`, `bots_router.py` | 2w bot state |
| `data/stock_cache/master/bot/rolling_4w.json.gz` | `base_swing_bot.py`, `bots_router.py` | 4w bot state |
| `da_brains/intraday/dt_state.json` | `dt_bot.py`, `dt_router.py` | DT runtime state |

### 5. ML Models & Datasets (Read by Training/Inference)

| File | Readers | Purpose |
|------|---------|---------|
| `ml_data/models/lgbm_cache/*.pkl` | `ai_model/predictor.py` | Trained models |
| `ml_data/datasets/training_data_daily.parquet` | `ai_model/trainer.py` | Training data |
| `dt_backend/models/lightgbm_intraday/*.pkl` | `dt_predictor.py` | DT models |
| `ml_data_dt/intraday/dataset/training_data_intraday.parquet` | `dt_trainer.py` | DT training data |

### 6. Raw Market Data (Read by Data Pipeline)

| File | Readers | Purpose |
|------|---------|---------|
| `data/raw/daily_bars/*.parquet` | `data_pipeline.py`, `backfill_history.py` | Historical OHLCV |
| `data/raw/intraday_bars/*.parquet` | `dt_data_pipeline.py`, `intraday_fetcher.py` | Intraday bars |
| `data/raw/news/*.json` | `news_intel.py`, `sentiment_engine.py` | News articles |
| `data/raw/fundamentals/*.json` | `data_pipeline.py` | Fundamentals |

### 7. Universe Files (Read by Bots & Services)

| File | Readers | Purpose |
|------|---------|---------|
| `data/universe/master_universe.json` | All services | Master symbol list |
| `data/universe/swing_universe.json` | `base_swing_bot.py` | Swing tradeable symbols |
| `data/universe/dt_universe.json` | `dt_bot.py` | Intraday tradeable symbols |

### 8. Trade Logs (Read by Analytics)

| File | Readers | Purpose |
|------|---------|---------|
| `ml_data/bot_logs/1w/trades_*.jsonl` | `pnl_dashboard_router.py`, `analytics/pnl.py` | 1w trade history |
| `ml_data/bot_logs/2w/trades_*.jsonl` | `pnl_dashboard_router.py`, `analytics/pnl.py` | 2w trade history |
| `ml_data/bot_logs/4w/trades_*.jsonl` | `pnl_dashboard_router.py`, `analytics/pnl.py` | 4w trade history |
| `da_brains/intraday/dt_trades.jsonl` | `dt_router.py`, `dt_analytics.py` | DT trade history |
| `da_brains/intraday/dt_metrics.json` | `dt_router.py` | DT performance metrics |

### 9. Cache Files (Read on Demand)

| File | Readers | Purpose |
|------|---------|---------|
| `data/news_cache/news_dashboard_latest.json` | `news_router.py`, `dashboard_router.py` | Latest news |
| `data/news_cache/sentiment_map_latest.json` | `insights_router.py` | Sentiment scores |
| `data/dashboard_cache/*.json` | `dashboard_router.py` | Cached dashboard data |

### 10. Application Logs (Read by UI)

| File | Readers | Purpose |
|------|---------|---------|
| `logs/nightly/nightly_*.log` | `logs_router.py` | Nightly job logs |
| `logs/nightly/last_nightly_summary.json` | `system_router.py`, `dashboard_router.py` | Last job summary |
| `logs/backend/backend_*.log` | `logs_router.py` | Backend logs |
| `logs/intraday/intraday_*.log` | `logs_router.py` | Intraday logs |

## Critical Path Dependencies

### Startup Sequence:
1. `config.py` loads (defines PATHS)
2. `settings.py` loads (defines TIMEZONE, knobs)
3. `admin_keys.py` loads (reads .env)
4. `backend/core/config.py` imports from all above
5. All routers/services import from `backend/core/config.py`

### Nightly Job Sequence:
1. Read: `data/raw/daily_bars/*.parquet`
2. Write: `ml_data/datasets/training_data_daily.parquet`
3. Write: `ml_data/models/lgbm_cache/*.pkl`
4. Write: `da_brains/core/rolling_brain.json.gz`
5. Write: `da_brains/core/aion_brain.json.gz`
6. Write: `logs/nightly/nightly_YYYYMMDD.log`
7. Write: `logs/nightly/last_nightly_summary.json`

### Bot Execution Sequence:
1. Read: `ml_data/config/bots_config.json`
2. Read: `ml_data/config/swing_knob_overrides.json`
3. Read: `da_brains/core/rolling_brain.json.gz`
4. Read: `data/stock_cache/master/bot/rolling_<horizon>.json.gz`
5. Execute trades
6. Write: `data/stock_cache/master/bot/rolling_<horizon>.json.gz`
7. Write: `ml_data/bot_logs/<horizon>/trades_YYYYMMDD.jsonl`

### Dashboard Load Sequence:
1. Read: `da_brains/core/rolling_brain.json.gz`
2. Read: `data/stock_cache/master/bot/*.json.gz` (all bots)
3. Read: `ml_data/bot_logs/*/*.jsonl` (recent trades)
4. Read: `logs/nightly/last_nightly_summary.json`
5. Return aggregated JSON to frontend

## Lock Files

| File | Purpose | Created By |
|------|---------|------------|
| `da_brains/nightly_job.lock` | Prevent concurrent nightly runs | `nightly_job.py` |
| `data/replay/locks/swing.lock` | Prevent concurrent replay runs | `replay_service.py` |
| `da_brains/intraday/.rolling_intraday_dt.lock` | Prevent concurrent rolling writes | `rolling_updater.py` |
| `da_brains/intraday/.dt_cycle.lock` | Prevent concurrent DT cycles | `dt_bot.py` |
| `da_brains/intraday/.dt_scheduler.lock` | Prevent multiple schedulers | `dt_scheduler_runner.py` |
| `da_brains/intraday/.dt_bars_fetch.lock` | Prevent multi-process fetches | `intraday_fetcher.py` |

## Summary

**Total Files Tracked:** 100+ files across categories
- **Config:** 10 files (read: all modules, write: admin/UI)
- **Raw Data:** 20+ files (read: pipelines, write: fetchers)
- **ML Artifacts:** 15+ files (read: bots, write: trainers)
- **Predictions/Brains:** 10 files (read: bots/UI, write: ML services)
- **Bot States:** 7 files (read: bots/UI, write: bots)
- **Logs:** 20+ files (read: UI, write: jobs/services)
- **Cache:** 10+ files (read: routers, write: services)

**Critical Files (Must Exist):**
1. `config.py` - Foundation for all PATHS
2. `settings.py` - Timezone and defaults
3. `admin_keys.py` - API credentials
4. `.env` - Environment variables
5. `da_brains/core/rolling_brain.json.gz` - Latest predictions
6. `data/stock_cache/master/bot/*.json.gz` - Bot states

**Auto-Created on Startup:**
- All directories in PATHS dictionary
- Empty JSON configs if missing (`bots_config.json`, etc.)
- Lock file directories
