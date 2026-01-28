# Configuration Reference

This document provides a complete reference for all configuration sources in the Aion Analytics platform.

## Configuration Hierarchy

```
Priority (highest to lowest):
1. Runtime UI overrides (stored in JSON)
2. Auto-tuned knob overrides (from tuner)
3. Environment files (.env, knobs.env, dt_knobs.env)
4. Default values in settings.py
5. Hardcoded constants in config.py
```

## 1. Environment Variables (.env)

**Location:** `/home/runner/work/Aion/Aion/.env`

### Trading API Credentials
```bash
# Alpaca Trading API
ALPACA_API_KEY_ID="your_api_key"
ALPACA_SECRET_KEY="your_secret_key"
ALPACA_BASE_URL="https://paper-api.alpaca.markets"  # or "https://api.alpaca.markets" for live
```

### Cloud & External Services
```bash
# Supabase (Cloud Cache)
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_KEY="your_supabase_anon_key"

# OpenAI (Sentiment Analysis)
OPENAI_API_KEY="sk-..."

# FRED API (Macro Data)
FRED_API_KEY="your_fred_api_key"
```

### Admin Authentication
```bash
# Admin Password Hash (SHA256)
ADMIN_PASSWORD_HASH="5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"  # "password"
ADMIN_TOKEN_TTL_SECONDS="3600"  # 1 hour
```

### Database (Optional)
```bash
# PostgreSQL Connection (optional - not required for file-based storage)
DATABASE_URL="postgresql://user:password@localhost:5432/aion"
```

### System Settings
```bash
# Timezone (default: America/Denver)
AION_TZ="America/Denver"

# Environment (dev/prod)
ENVIRONMENT="development"

# Logging Level
LOG_LEVEL="INFO"
```

### Loaded by:
- `admin_keys.py` (root level)
- `backend/core/config.py` (re-exports)
- `dt_backend/core/config_dt.py` (DT engine)

## 2. Swing Bot Knobs (knobs.env)

**Location:** `/home/runner/work/Aion/Aion/knobs.env`

### Bot Behavior
```bash
# Enable/disable bots
SWING_ENABLED="true"

# Aggression level (0.0 - 1.0)
AGGRESSION="0.50"

# Max allocation per position (USD)
MAX_ALLOC="1000.0"

# Max concurrent positions
MAX_POSITIONS="10"

# Stop loss percentage
STOP_LOSS="3.0"

# Take profit percentage
TAKE_PROFIT="6.0"

# Minimum confidence threshold
MIN_CONFIDENCE="0.55"

# Allow ETFs
ALLOW_ETFS="true"

# Penny stock only mode
PENNY_ONLY="false"
```

### Loaded by:
- `settings.py` (merged into BOT_KNOBS_DEFAULTS)
- `backend/bots/base_swing_bot.py`

## 3. Intraday Bot Knobs (dt_knobs.env)

**Location:** `/home/runner/work/Aion/Aion/dt_knobs.env`

### DT Bot Behavior
```bash
# Enable/disable DT bots
DT_ENABLED="true"

# Aggression level (0.0 - 1.0)
DT_AGGRESSION="0.50"

# Max allocation per position (USD)
DT_MAX_ALLOC="500.0"

# Max concurrent positions
DT_MAX_POSITIONS="5"

# Stop loss percentage
DT_STOP_LOSS="0.8"

# Take profit percentage
DT_TAKE_PROFIT="1.5"

# Minimum confidence threshold
DT_MIN_CONFIDENCE="0.55"

# Max daily trades
DT_MAX_DAILY_TRADES="12"

# Allow ETFs
DT_ALLOW_ETFS="true"

# Penny stock only mode
DT_PENNY_ONLY="false"
```

### Loaded by:
- `settings.py` (merged into BOT_KNOBS_DEFAULTS["intraday"])
- `dt_backend/core/config_dt.py`
- `dt_backend/bots/dt_bot.py`

## 4. Root Settings (settings.py)

**Location:** `/home/runner/work/Aion/Aion/settings.py`

### Timezone Configuration
```python
import pytz
import os

TIMEZONE = pytz.timezone(os.getenv("AION_TZ", "America/Denver"))
```

### Bot Knobs Defaults
```python
BOT_KNOBS_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "swing": {
        "enabled": True,
        "aggression": 0.50,
        "max_alloc": 1000.0,          # dollars
        "max_positions": 10,
        "stop_loss": 3.0,             # percent
        "take_profit": 6.0,           # percent
        "min_confidence": 0.55,
        "allow_etfs": True,
        "penny_only": False,
    },
    "intraday": {
        "enabled": True,
        "aggression": 0.50,
        "max_alloc": 500.0,           # dollars
        "max_positions": 5,
        "stop_loss": 0.8,             # percent
        "take_profit": 1.5,           # percent
        "min_confidence": 0.55,
        "penny_only": False,
        "allow_etfs": True,
        "max_daily_trades": 12,
    },
}
```

### Schema Validation Hints
```python
BOT_KNOBS_SCHEMA: Dict[str, Dict[str, Any]] = {
    "aggression": {"min": 0.0, "max": 1.0, "step": 0.05},
    "max_alloc": {"min": 0.0, "max": 1_000_000.0, "step": 50.0},
    "max_positions": {"min": 1, "max": 200, "step": 1},
    "stop_loss": {"min": 0.0, "max": 50.0, "step": 0.1},
    "take_profit": {"min": 0.0, "max": 200.0, "step": 0.1},
    "min_confidence": {"min": 0.0, "max": 1.0, "step": 0.01},
    "max_daily_trades": {"min": 0, "max": 500, "step": 1},
}
```

## 5. Root Config (config.py)

**Location:** `/home/runner/work/Aion/Aion/config.py`

### Path Constants
```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DATA_ROOT = ROOT / "data"
ML_ROOT = ROOT / "ml_data"
BRAINS_ROOT = ROOT / "da_brains"
LOGS_ROOT = ROOT / "logs"
```

### PATHS Dictionary (Swing/EOD)
```python
PATHS: Dict[str, Path] = {
    "root": ROOT,
    
    # Raw data
    "raw_daily": DATA_ROOT / "raw" / "daily_bars",
    "raw_intraday": DATA_ROOT / "raw" / "intraday_bars",
    "raw_news": DATA_ROOT / "raw" / "news",
    "raw_fundamentals": DATA_ROOT / "raw" / "fundamentals",
    
    # Universe
    "universe": DATA_ROOT / "universe",
    "universe_master_file": DATA_ROOT / "universe" / "master_universe.json",
    "universe_swing_file": DATA_ROOT / "universe" / "swing_universe.json",
    
    # Cache
    "stock_cache": DATA_ROOT / "stock_cache",
    "stock_cache_master": DATA_ROOT / "stock_cache" / "master",
    
    # Brains
    "brains_root": BRAINS_ROOT,
    "rolling_brain": BRAINS_ROOT / "core" / "rolling_brain.json.gz",
    "aion_brain": BRAINS_ROOT / "core" / "aion_brain.json.gz",
    
    # ML
    "ml_data": ML_ROOT,
    "ml_models": ML_ROOT / "models",
    "ml_datasets": ML_ROOT / "datasets",
    "ml_predictions": ML_ROOT / "predictions",
    
    # Logs
    "logs": LOGS_ROOT,
    "backend_logs": LOGS_ROOT / "backend",
    "nightly_logs": LOGS_ROOT / "nightly",
    "scheduler_logs": LOGS_ROOT / "scheduler",
    
    # Insights
    "insights": ROOT / "insights",
    "dashboard_cache": DATA_ROOT / "dashboard_cache",
    
    # News
    "news_cache": DATA_ROOT / "news_cache",
    "news_dashboard_json": DATA_ROOT / "news_cache" / "news_dashboard_latest.json",
    "sentiment_map": DATA_ROOT / "news_cache" / "sentiment_map_latest.json",
    
    # Bot configs
    "bots_config": ML_ROOT / "config" / "bots_config.json",
    "bots_ui_overrides": ML_ROOT / "config" / "bots_ui_overrides.json",
    "swing_knob_overrides": ML_ROOT / "config" / "swing_knob_overrides.json",
    "swing_tuning_log": ML_ROOT / "config" / "swing_tuning_log.jsonl",
    
    # Model settings
    "max_train_rows": 800000,
    "train_batch_rows": 100000,
    "max_features": 180,
}
```

### DT_PATHS Dictionary (Intraday)
```python
DT_PATHS: Dict[str, Path] = {
    "root": ROOT,
    "dt_backend": ROOT / "dt_backend",
    "data_dt": ROOT / "data_dt",
    
    # Universe
    "universe_dir": DATA_ROOT / "universe",
    "universe_file": DATA_ROOT / "universe" / "dt_universe.json",
    
    # Bars
    "bars_intraday_dir": ROOT / "ml_data_dt" / "bars" / "intraday",
    "bars_daily_dir": ROOT / "ml_data_dt" / "bars" / "daily",
    
    # Rolling
    "rolling": BRAINS_ROOT / "intraday" / "rolling_intraday.json.gz",
    "rolling_intraday_file": BRAINS_ROOT / "intraday" / "rolling_intraday.json.gz",
    "rolling_market_intraday_file": BRAINS_ROOT / "intraday" / "rolling_intraday_market.json.gz",
    
    # Brain
    "dt_brain_file": BRAINS_ROOT / "core" / "dt_brain.json.gz",
    
    # Models
    "models_root": ROOT / "dt_backend" / "models",
    "models_lgbm_intraday_dir": ROOT / "dt_backend" / "models" / "lightgbm_intraday",
    
    # State
    "dt_state_file": BRAINS_ROOT / "intraday" / "dt_state.json",
    "dt_trades_file": BRAINS_ROOT / "intraday" / "dt_trades.jsonl",
    "dt_metrics_file": BRAINS_ROOT / "intraday" / "dt_metrics.json",
    
    # Locks
    "dt_cycle_lock_file": BRAINS_ROOT / "intraday" / ".dt_cycle.lock",
    "dt_scheduler_lock_file": BRAINS_ROOT / "intraday" / ".dt_scheduler.lock",
    "dt_bars_fetch_lock_file": BRAINS_ROOT / "intraday" / ".dt_bars_fetch.lock",
    
    # Configs
    "intraday_ui_store": ROOT / "ml_data_dt" / "config" / "intraday_bots_ui.json",
    "dt_knob_overrides": ROOT / "ml_data_dt" / "config" / "dt_knob_overrides.json",
    "dt_tuning_log": ROOT / "ml_data_dt" / "config" / "dt_tuning_log.jsonl",
}
```

## 6. Runtime Configuration Files (JSON)

### Swing Bot Config
**Location:** `ml_data/config/bots_config.json`

```json
{
  "swing_1w": {
    "enabled": true,
    "aggression": 0.50,
    "max_alloc": 1000.0,
    "max_positions": 10,
    "stop_loss": 3.0,
    "take_profit": 6.0,
    "min_confidence": 0.55,
    "allow_etfs": true,
    "penny_only": false
  },
  "swing_2w": {
    "enabled": true,
    "aggression": 0.45,
    "max_positions": 8
  },
  "swing_4w": {
    "enabled": true,
    "aggression": 0.40,
    "max_positions": 6
  }
}
```

**Updated by:** `backend/routers/bots_router.py` (POST /api/bots/config)

### Swing Knob Overrides (Auto-Tuning)
**Location:** `ml_data/config/swing_knob_overrides.json`

```json
{
  "swing_1w": {
    "aggression": 0.65,
    "stop_loss": 2.5,
    "applied_at": "2024-01-15T10:30:00Z",
    "reason": "Performance tuning - increased win rate",
    "metrics": {
      "win_rate": 0.72,
      "avg_return": 4.2,
      "sharpe_ratio": 1.8
    }
  }
}
```

**Updated by:** `backend/services/swing_knob_tuner.py`

### DT Bot Config
**Location:** `ml_data_dt/config/intraday_bots_ui.json`

```json
{
  "bots": {
    "dt_scalper": {
      "enabled": true,
      "aggression": 0.50,
      "max_alloc": 500.0,
      "max_positions": 5,
      "stop_loss": 0.8,
      "take_profit": 1.5,
      "max_daily_trades": 12
    }
  }
}
```

**Updated by:** `dt_backend/routers/dt_router.py`

### DT Knob Overrides (Auto-Tuning)
**Location:** `ml_data_dt/config/dt_knob_overrides.json`

```json
{
  "dt_scalper": {
    "aggression": 0.60,
    "stop_loss": 0.7,
    "applied_at": "2024-01-15T14:20:00Z",
    "metrics": {
      "win_rate": 0.68,
      "avg_return": 1.2
    }
  }
}
```

**Updated by:** `dt_backend/services/dt_knob_tuner.py`

### UI Overrides
**Location:** `ml_data/config/bots_ui_overrides.json`

```json
{
  "theme": "dark",
  "chart_height": 400,
  "refresh_interval": 30000,
  "show_pending_signals": true,
  "show_trade_history": true,
  "max_history_rows": 100
}
```

**Updated by:** `frontend/app/bots/config/page.tsx`

## 7. Configuration Loading Order

### Backend Startup:
```python
1. Load .env → os.environ
2. Load admin_keys.py → API keys from .env
3. Load settings.py → TIMEZONE, BOT_KNOBS_DEFAULTS (merges knobs.env)
4. Load config.py → PATHS, DT_PATHS (uses settings.TIMEZONE)
5. Load backend/core/config.py → Re-exports all above
6. Services import from backend/core/config.py
```

### Bot Execution:
```python
1. Load BOT_KNOBS_DEFAULTS from settings.py (base defaults)
2. Load bots_config.json (user configs)
3. Load swing_knob_overrides.json (auto-tuning)
4. Merge: overrides > config > defaults
5. Apply merged config to bot instance
```

### Frontend Config:
```typescript
1. Load .env.local → process.env (Next.js)
2. Fetch /api/backend/bots/config → Backend returns merged config
3. Apply config to UI state
4. User edits → POST /api/backend/bots/config → Update bots_config.json
```

## 8. Configuration Access Patterns

### Backend (Python):
```python
# Import PATHS
from backend.core.config import PATHS, ROOT, TIMEZONE

# Access path
rolling_path = PATHS["rolling_brain"]

# Access setting
from settings import BOT_KNOBS_DEFAULTS
default_aggression = BOT_KNOBS_DEFAULTS["swing"]["aggression"]

# Access secret
from admin_keys import ALPACA_API_KEY_ID
```

### DT Backend (Python):
```python
# Import DT_PATHS
from dt_backend.core.config_dt import DT_PATHS, ROOT

# Access path
rolling_path = DT_PATHS["rolling"]

# Access setting
from settings import BOT_KNOBS_DEFAULTS
default_aggression = BOT_KNOBS_DEFAULTS["intraday"]["aggression"]
```

### Frontend (TypeScript):
```typescript
// Access from environment
const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'

// Fetch config from backend
const config = await fetch('/api/backend/bots/config').then(r => r.json())

// Use config
const aggression = config.swing_1w.aggression
```

## 9. Configuration Validation

### Environment Variable Validation:
```python
# In admin_keys.py
required_vars = [
    "ALPACA_API_KEY_ID",
    "ALPACA_SECRET_KEY",
]

missing = [v for v in required_vars if not os.getenv(v)]
if missing:
    raise ValueError(f"Missing required env vars: {missing}")
```

### Knob Validation:
```python
# In bots_router.py
from settings import BOT_KNOBS_SCHEMA

def validate_knob(key: str, value: Any) -> bool:
    if key in BOT_KNOBS_SCHEMA:
        schema = BOT_KNOBS_SCHEMA[key]
        if value < schema["min"] or value > schema["max"]:
            raise ValueError(f"{key} must be between {schema['min']} and {schema['max']}")
    return True
```

### Path Validation:
```python
# In config.py
def ensure_project_structure() -> None:
    """Create all required directories."""
    for key, path in PATHS.items():
        if isinstance(path, Path) and path.suffix == "":
            path.mkdir(parents=True, exist_ok=True)
```

## 10. Configuration Defaults Summary

### Trading Defaults:
- **Swing Aggression:** 0.50
- **Swing Max Alloc:** $1,000 per position
- **Swing Max Positions:** 10
- **Swing Stop Loss:** 3.0%
- **Swing Take Profit:** 6.0%
- **DT Aggression:** 0.50
- **DT Max Alloc:** $500 per position
- **DT Max Positions:** 5
- **DT Stop Loss:** 0.8%
- **DT Take Profit:** 1.5%
- **DT Max Daily Trades:** 12

### System Defaults:
- **Timezone:** America/Denver
- **Admin Token TTL:** 3600 seconds (1 hour)
- **Log Level:** INFO
- **Max Train Rows:** 800,000
- **Max Features:** 180
- **Nightly Job Time:** 02:00 (2am local)
- **Intraday Cycle:** Every 15 minutes

### Model Defaults:
- **Min Confidence:** 0.55
- **LightGBM Trees:** 100 (per model)
- **Learning Rate:** 0.1
- **Max Depth:** 5

## Summary

**Configuration Sources:**
1. ✅ `.env` - Environment variables (secrets, API keys)
2. ✅ `knobs.env` - Swing bot parameters
3. ✅ `dt_knobs.env` - Intraday bot parameters
4. ✅ `settings.py` - Default knobs and timezone
5. ✅ `config.py` - Paths and constants
6. ✅ `bots_config.json` - Runtime bot configs
7. ✅ `swing_knob_overrides.json` - Auto-tuned knobs
8. ✅ `dt_knob_overrides.json` - DT auto-tuned knobs
9. ✅ `bots_ui_overrides.json` - UI preferences

**Configuration Priority:**
1. Runtime overrides (highest)
2. Auto-tuned knobs
3. User configs (JSON)
4. Environment files (.env)
5. Default values (settings.py)
6. Hardcoded constants (lowest)

**Critical for Startup:**
- ✅ `.env` must exist with ALPACA credentials
- ✅ `config.py` must load successfully
- ✅ `settings.py` must define TIMEZONE
- ✅ All PATHS directories auto-created on startup
