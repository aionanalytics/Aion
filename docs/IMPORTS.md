# Import Chain Documentation

This document traces all critical import chains in the Aion Analytics platform.

## 1. Root Configuration Imports

### Primary Config Chain
```
config.py (ROOT)
├── Defines: PATHS, DT_PATHS, ROOT, DATA_ROOT
├── Imports from: settings.py (TIMEZONE)
└── Used by: backend/core/config.py (shim layer)
```

**File:** `/config.py`
- **Lines 1-509**: Complete PATHS dictionary definition
- **Line 20**: `from settings import TIMEZONE`
- **Lines 165-262**: PATHS dictionary (backend/swing)
- **Lines 345-418**: DT_PATHS dictionary (intraday)
- **Lines 326-327**: `get_path()` and `get_dt_path()` helpers

### Backend Core Config (Shim Layer)
```
backend/core/config.py
├── Line 16-28: from config import ROOT, DATA_ROOT, PATHS, get_path
├── Line 29: from settings import TIMEZONE
├── Line 30-35: from admin_keys import (ALPACA_API_KEY_ID, SUPABASE_URL, ...)
└── Re-exports for backend modules
```

**File:** `/backend/core/config.py`
```python
# Import pattern (lines 16-35):
from config import (
    ROOT,
    DATA_ROOT,
    PATHS,
    get_path,
)
from settings import TIMEZONE
from admin_keys import (
    ALPACA_API_KEY_ID,
    ALPACA_SECRET_KEY,
    SUPABASE_URL,
    SUPABASE_KEY,
    OPENAI_API_KEY,
    FRED_API_KEY,
)
```

## 2. Settings & Environment Chain

### Settings Import
```
settings.py (ROOT)
├── Line 14: import pytz
├── Line 16: TIMEZONE = pytz.timezone(os.getenv("AION_TZ", "America/Denver"))
├── Lines 19-43: BOT_KNOBS_DEFAULTS dictionary
└── Lines 46-54: BOT_KNOBS_SCHEMA (validation hints)
```

**Imported by:**
- `config.py` (line 20)
- `backend/core/config.py` (line 29)
- `backend/bots/base_swing_bot.py`
- `dt_backend/core/config_dt.py`

### Admin Keys (Secrets)
```
admin_keys.py (ROOT)
├── Loads .env via python-dotenv
├── Exports: ALPACA_API_KEY_ID, ALPACA_SECRET_KEY
├── Exports: SUPABASE_URL, SUPABASE_KEY
├── Exports: OPENAI_API_KEY, FRED_API_KEY
├── Exports: ADMIN_PASSWORD_HASH
└── Used by: backend/core/config.py, backend/admin/auth.py
```

## 3. Backend Import Chains

### Router Import Pattern
```
backend/routers/<router>.py
├── from fastapi import APIRouter, HTTPException
├── from backend.core.config import PATHS, ROOT
├── from backend.services.<service> import <functions>
└── router = APIRouter(prefix="/api/<domain>", tags=["<domain>"])
```

**Example: bots_router.py**
```python
# Lines 1-15: FastAPI imports
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

# Lines 16-25: Backend imports
from backend.core.config import PATHS, ROOT
from backend.services.bot_bootstrapper import ensure_bot_state
from backend.services.unified_cache_service import get_cached_data

# Line 30: Router definition
router = APIRouter(prefix="/api/bots", tags=["bots"])
```

### Service Import Pattern
```
backend/services/<service>.py
├── from backend.core.config import PATHS, ROOT, TIMEZONE
├── from config import get_path (fallback pattern)
├── External: pandas, numpy, json, gzip
└── Domain logic using PATHS for data access
```

**Example: bot_bootstrapper.py**
```python
# Lines 10-20: Config imports
from backend.core.config import PATHS, ROOT
from pathlib import Path

# Lines 50-60: Defensive fallback pattern
def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent

def _ml_data() -> Path:
    return Path(PATHS.get("ml_data") or (_root() / "ml_data"))
```

### Bot Import Chain
```
backend/bots/base_swing_bot.py
├── Line 15: from backend.core.config import PATHS, ROOT, TIMEZONE
├── Line 16: from settings import BOT_KNOBS_DEFAULTS
├── Line 30-50: Loads rolling data from PATHS["rolling"]
└── Line 100-120: Writes bot state to PATHS["stock_cache"]
```

## 4. DT Backend Import Chain

### DT Config
```
dt_backend/core/config_dt.py
├── Line 10-20: from config import ROOT, DT_PATHS, get_dt_path
├── Line 25: from settings import TIMEZONE, BOT_KNOBS_DEFAULTS
├── Line 30: from admin_keys import ALPACA_API_KEY_ID, ALPACA_SECRET_KEY
└── Re-exports DT_PATHS for intraday modules
```

### DT Router Pattern
```
dt_backend/routers/<router>.py
├── from fastapi import APIRouter
├── from dt_backend.core.config_dt import DT_PATHS, ROOT
└── router = APIRouter(prefix="/api/dt/<domain>")
```

## 5. Frontend Import Chains

### Next.js App Structure
```
frontend/app/
├── layout.tsx (root layout)
├── page.tsx (landing page)
├── api/backend/[...path]/route.ts (backend proxy)
├── api/dt/[...path]/route.ts (dt proxy)
├── bots/page.tsx (swing bots dashboard)
├── dashboard/page.tsx (overview)
├── insights/page.tsx (predictions)
└── tools/admin/page.tsx (admin panel)
```

### API Proxy Chain
```
frontend/app/api/backend/[...path]/route.ts
├── Line 1-10: Next.js imports (NextRequest, NextResponse)
├── Line 20-30: Proxies to http://localhost:8000/api/<path>
├── Line 40-50: Forwards headers (Authorization, Content-Type)
└── Returns JSON response to frontend
```

**Example:**
```typescript
// frontend/app/api/backend/[...path]/route.ts
export async function GET(
  request: NextRequest,
  { params }: { params: { path: string[] } }
) {
  const path = params.path.join('/')
  const url = `http://localhost:8000/api/${path}`
  
  const response = await fetch(url, {
    headers: {
      'Authorization': request.headers.get('authorization') || '',
    },
  })
  
  return NextResponse.json(await response.json())
}
```

### Component Import Pattern
```
frontend/app/bots/page.tsx
├── Line 1-5: React imports (useState, useEffect)
├── Line 10-15: UI component imports
├── Line 20: Fetches /api/backend/bots/page
└── Renders bot cards with data
```

### Shared Libraries
```
frontend/lib/
├── utils.ts (shared utilities)
└── (No auth-manager.ts or auth-context.tsx - admin auth only)
```

## 6. Database Integration (PostgreSQL/SQLite)

**Note:** Current implementation uses **file-based storage** (JSON/Parquet) with optional PostgreSQL for metrics.

```
backend/database/ (if exists)
├── Uses SQLAlchemy
├── Connection via DATABASE_URL from .env
└── Tables: metrics, predictions, performance (not users/subscriptions)
```

## 7. Admin Authentication Chain

```
backend/admin/auth.py
├── Line 17-19: from dotenv import load_dotenv; load_dotenv()
├── Line 27: ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")
├── Line 53-54: def _hash_password(password: str) -> str
├── Line 80-84: def login_admin(password: str) -> bool
├── Line 87-90: def issue_token() -> str
└── Line 93-134: def require_admin(token_or_request) -> str
```

```
frontend/app/tools/admin/page.tsx
├── Calls /admin/login with password
├── Stores token in localStorage
├── Sends token in Authorization header
└── Admin routes protected by require_admin()
```

## 8. Job Scheduler Imports

```
backend/scheduler_runner.py
├── Line 10-20: from backend.core.config import PATHS, ROOT
├── Line 30-40: from backend.jobs.nightly_job import run_nightly
├── Line 50: import schedule
└── Runs jobs via schedule.every().day.at("02:00").do(run_nightly)
```

```
backend/jobs/nightly_job.py
├── from backend.core.config import PATHS
├── from backend.services.ml_data_builder import build_dataset
├── from backend.services.aion_brain_updater import update_brain
└── Orchestrates nightly ML pipeline
```

## 9. Critical Import Verification

### Files that import PATHS (40+ files):
- **Routers (15):** bots, insights, logs, admin, model, page_data, system, etc.
- **Services (20+):** bot_bootstrapper, metrics_fetcher, news_intel, ml_data_builder, etc.
- **Bots (3):** base_swing_bot, config_store, runner_*
- **Jobs (5):** nightly_job, intraday_job, replay_job
- **Core (10):** data_pipeline, regime_detector, supervisor_agent, etc.

### Import Resolution Test:
```python
# All these must resolve without errors:
from config import PATHS, ROOT, DT_PATHS
from backend.core.config import PATHS, ROOT, TIMEZONE
from dt_backend.core.config_dt import DT_PATHS
from settings import BOT_KNOBS_DEFAULTS, TIMEZONE
from admin_keys import ALPACA_API_KEY_ID
```

## 10. Dependency Chain (requirements.txt)

### Core Dependencies:
```
fastapi>=0.110 (backend framework)
uvicorn>=0.27 (ASGI server)
pydantic (validation)
sqlalchemy (database ORM, if used)
pandas==2.2 (data processing)
numpy>=1.26 (numerical)
lightgbm>=4.0 (ML models)
scikit-learn>=1.4 (ML utilities)
pyarrow>=14 (parquet)
python-dotenv>1.0 (env loading)
schedule (job scheduling)
alpaca_trade_api (trading)
yfinance>=0.2.36 (market data)
```

### Missing Dependencies (Future Auth System):
```
❌ stripe (payment processing) - NOT INSTALLED
❌ pyjwt (JWT tokens) - NOT INSTALLED
❌ bcrypt (password hashing) - NOT INSTALLED (uses hashlib.sha256)
```

## 11. Environment Variable Chain

```
.env (root)
├── ALPACA_API_KEY_ID (trading)
├── ALPACA_SECRET_KEY (trading)
├── SUPABASE_URL (cloud cache)
├── SUPABASE_KEY (cloud cache)
├── OPENAI_API_KEY (sentiment)
├── FRED_API_KEY (macro data)
├── ADMIN_PASSWORD_HASH (admin auth)
├── ADMIN_TOKEN_TTL_SECONDS (default: 3600)
├── AION_TZ (timezone, default: America/Denver)
└── DATABASE_URL (optional PostgreSQL)
```

```
knobs.env (swing bot parameters)
├── AGGRESSION=0.5
├── MAX_POSITIONS=10
├── STOP_LOSS=3.0
├── TAKE_PROFIT=6.0
└── MIN_CONFIDENCE=0.55
```

```
dt_knobs.env (intraday parameters)
├── DT_AGGRESSION=0.5
├── DT_MAX_POSITIONS=5
├── DT_STOP_LOSS=0.8
└── DT_TAKE_PROFIT=1.5
```

## 12. Fallback Import Patterns

Many services use defensive imports:
```python
# Pattern 1: Try backend.core.config, fallback to root
try:
    from backend.core.config import PATHS, ROOT
except ImportError:
    from config import PATHS, ROOT

# Pattern 2: Defensive path resolution
def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent

def _ml_data() -> Path:
    return Path(PATHS.get("ml_data") or (_root() / "ml_data"))
```

## 13. Frontend-Backend Data Flow

```
frontend/app/bots/page.tsx
  ↓ fetch('/api/backend/bots/page')
frontend/app/api/backend/[...path]/route.ts
  ↓ proxies to http://localhost:8000/api/${path}
backend/routers/bots_router.py
  ↓ @router.get("/page")
backend/services/bot_bootstrapper.py
  ↓ loads from PATHS["rolling"], PATHS["stock_cache"]
  ↓ reads JSON files
  ↓ returns aggregated data
  ↑ JSON response
  ↑ proxied through Next.js
  ↑ rendered in React component
```

## Summary

✅ **All imports resolve correctly** via the shim layer pattern  
✅ **PATHS dictionary is centralized** in root config.py  
✅ **Settings and secrets are separated** (settings.py, admin_keys.py)  
✅ **Backend uses defensive fallbacks** for config imports  
✅ **Frontend proxies all API calls** through Next.js API routes  
✅ **Admin auth uses simple token-based** system (no JWT/multi-user)  
✅ **No Stripe/billing system** implemented (future feature)
