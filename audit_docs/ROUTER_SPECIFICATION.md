# Router Endpoint Specification

**Generated:** 2026-01-28  
**Scope:** All backend router endpoints (31 router files, 100+ endpoints)

---

## Executive Summary

**Total Routers:** 31 files  
**Total Endpoints:** 100+ REST API endpoints  
**Primary Methods:** GET (70%), POST (20%), PUT/PATCH/DELETE (10%)  
**Data Format:** JSON (98%), Binary (2% - log files)  
**Error Handling:** Mixed (HTTPException vs graceful degradation)  
**Authentication:** JWT-based for protected routes

---

## Table of Contents
1. [Bots & Trading Endpoints](#1-bots--trading-endpoints)
2. [Dashboard & Metrics](#2-dashboard--metrics)
3. [Insights & Predictions](#3-insights--predictions)
4. [System & Health](#4-system--health)
5. [Admin & Auth](#5-admin--auth)
6. [Logs & Debugging](#6-logs--debugging)
7. [Configuration](#7-configuration)
8. [File I/O Summary](#8-file-io-summary)
9. [Error Codes Reference](#9-error-codes-reference)
10. [Issues & Recommendations](#10-issues--recommendations)

---

## 1. Bots & Trading Endpoints

### 1.1 bots_page_router.py
**Base Path:** `/api/bots`

#### GET /page
**Purpose:** Unified data bundle for Bots page UI (swing + intraday)

**Request:**
- Method: GET
- Parameters: None
- Headers: Standard

**Response Schema:**
```json
{
  "swing": {
    "status": {...},
    "configs": {...},
    "logs": {...}
  },
  "intraday": {
    "status": {...},
    "configs": {...},
    "logs": {...},
    "signals": [...],
    "fills": [...]
  },
  "as_of": "2026-01-28T12:00:00Z"
}
```

**Files Read:**
- `PATHS["ml_data_dt"]/rolling.json.gz` - Latest prices
- `DT_PATHS["signals_intraday_dir"]` - Signal files
- `ml_data_dt/sim_logs/` - Execution logs

**Error Handling:**
- ✅ Best-effort: Sub-component failures become error objects
- ✅ Never throws 500, returns partial data
- ✅ Error details limited to 2000 chars

**Performance:**
- Reads: 5-10 files
- Response time: 200-1000ms
- Response size: 50-500KB

---

### 1.2 eod_bots_router.py
**Base Path:** `/api/eod`

#### GET /status
**Purpose:** Current swing bot states

**Response:**
```json
{
  "timestamp": "2026-01-28T12:00:00Z",
  "bots": {
    "eod_1w_momentum": {
      "bot_key": "eod_1w_momentum",
      "cash": 50000.0,
      "equity": 75000.0,
      "total_value": 125000.0,
      "positions": [
        {
          "symbol": "AAPL",
          "quantity": 100,
          "entry_price": 150.0,
          "current_price": 155.0,
          "market_value": 15500.0,
          "unrealized_pnl": 500.0,
          "unrealized_pnl_pct": 3.33
        }
      ],
      "equity_curve": [
        {"date": "2026-01-20", "equity": 125000.0},
        {"date": "2026-01-21", "equity": 126000.0}
      ]
    }
  }
}
```

**Files Read:**
- `stock_cache/master/bot/rolling_{botkey}.json(.gz)` - Bot state files
- `PATHS["rolling"]` - Current prices for position valuation

**Error Handling:**
- ❌ No explicit error for missing bot state file (returns empty)
- ✅ Graceful handling of missing prices (uses last known)

**Issue ID: 003**
- **Type:** Error Handling
- **Location:** `backend/routers/eod_bots_router.py:75`
- **Issue:** Missing bot state file silently returns empty, no 404
- **Current:** Returns empty dict for missing files
- **Expected:** Should return 404 if bot doesn't exist
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Fix:** Check bot existence, return 404 if not found

---

#### GET /logs/days
**Purpose:** List of trading days with activity logs

**Response:**
```json
{
  "count": 30,
  "days": ["2026-01-27", "2026-01-26", ...]
}
```

**Files Read:**
- `ml_data/bot_logs/{horizon}/bot_activity_*.json`

**Error Handling:**
- ✅ Returns empty list if no logs

---

#### GET /logs/{day}
**Purpose:** All bot activity for specific trading day

**Path Parameters:**
- `day`: YYYY-MM-DD format

**Response:**
```json
{
  "date": "2026-01-27",
  "horizons": {
    "1w": {
      "bots": {
        "momentum": {
          "actions": [
            {
              "type": "buy",
              "symbol": "AAPL",
              "quantity": 10,
              "price": 150.0,
              "timestamp": "2026-01-27T09:35:00Z"
            }
          ]
        }
      }
    }
  }
}
```

**Files Read:**
- `ml_data/bot_logs/{horizon}/bot_activity_{day}.json`

**Error Handling:**
- ✅ 404 if day not found
- ✅ 500 if log file corrupted (with error detail)

---

#### GET /configs
**Purpose:** All bot configurations

**Response:**
```json
{
  "configs": {
    "eod_1w_momentum": {
      "enabled": true,
      "cash_allocation": 100000.0,
      "max_positions": 10,
      "risk_per_trade": 0.02
    }
  }
}
```

**Files Read:**
- Lazy-loads from `backend.bots.config_store`

**Error Handling:**
- ✅ 500 if config file missing/corrupted

---

#### POST /configs
**Purpose:** Update bot configuration

**Request Body:**
```json
{
  "bot_key": "eod_1w_momentum",
  "config": {
    "enabled": true,
    "cash_allocation": 100000.0
  }
}
```

**Response:**
```json
{
  "bot_key": "eod_1w_momentum",
  "config": {...},
  "updated_at": "2026-01-28T12:00:00Z"
}
```

**Files Written:**
- `ml_data/config/bots_config.json`
- `ml_data/config/bots_ui_overrides.json`

**Error Handling:**
- ✅ 404 if bot_key unknown
- ✅ 500 if write fails

---

### 1.3 intraday_router.py
**Base Path:** `/api/intraday`

#### GET /snapshot
**Purpose:** Quick snapshot of top buy/sell signals

**Query Parameters:**
- `limit`: int (default 50, max 200)

**Response:**
```json
{
  "timestamp": "2026-01-28T12:00:00Z",
  "top_buy": [
    {
      "symbol": "AAPL",
      "confidence": 0.85,
      "predicted_return": 0.02,
      "current_price": 150.0
    }
  ],
  "top_sell": [...]
}
```

**Files Read:**
- Calls `backend.intraday_service.get_snapshot()`
- Reads from `DT_PATHS["rolling_intraday_file"]`

**Error Handling:**
- ✅ Graceful degradation if service unavailable

---

#### GET /symbol/{symbol}
**Purpose:** Full intraday view for specific symbol

**Path Parameters:**
- `symbol`: Stock ticker (e.g., "AAPL")

**Response:**
```json
{
  "symbol": "AAPL",
  "current_price": 150.0,
  "prediction": {
    "confidence": 0.85,
    "direction": "BUY",
    "expected_return": 0.02
  },
  "features": {...},
  "bars_1m": [...]
}
```

**Error Handling:**
- ✅ 404 if symbol not in universe

---

## 2. Dashboard & Metrics

### 2.1 dashboard_router.py
**Base Path:** `/dashboard`

#### GET /metrics
**Purpose:** Overall system performance metrics

**Response:**
```json
{
  "accuracy_30d": 0.67,
  "model_accuracy": 0.65,
  "execution_accuracy": 0.68,
  "updated_at": "2026-01-28T12:00:00Z"
}
```

**Calculation:**
```python
model_acc = weighted_mean(accuracy_latest.json)
exec_acc = win_rate(last_30_days_positions)
accuracy_30d = (model_acc * 0.4) + (exec_acc * 0.6)
```

**Files Read:**
- `ml_data/metrics/accuracy/accuracy_latest.json`
- `stock_cache/master/bot/rolling_*.json(.gz)` - For execution accuracy

**Error Handling:**
- ✅ Returns defaults if files missing (0.5 accuracy)

**Issue ID: 004**
- **Type:** Data Accuracy
- **Location:** `backend/routers/dashboard_router.py:95`
- **Issue:** Defaults to 0.5 if accuracy file missing, misleading
- **Current:** Returns 0.5 (50%) as placeholder
- **Expected:** Should return null or indicate "Not Available"
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Fix:** Return `null` or `{"status": "calculating"}` instead of false 50%

---

#### GET /top/{horizon}
**Purpose:** Top predicted performers for horizon

**Path Parameters:**
- `horizon`: "1w", "2w", "4w", "52w"

**Query Parameters:**
- `limit`: int (default 50, max 200)

**Response:**
```json
{
  "horizon": "1w",
  "count": 50,
  "results": [
    {
      "symbol": "AAPL",
      "predicted_return": 0.05,
      "confidence": 0.85,
      "current_price": 150.0
    }
  ]
}
```

**Files Read:**
- `logs/nightly/predictions/latest_predictions.json`

**Error Handling:**
- ✅ Empty list if no predictions

---

### 2.2 metrics_router.py
**Base Path:** `/api/metrics`

#### GET /accuracy
**Purpose:** Global accuracy badge

**Response:**
```json
{
  "global_accuracy": 0.67,
  "by_horizon": {
    "1d": 0.65,
    "1w": 0.68,
    "2w": 0.66,
    "4w": 0.67
  },
  "updated_at": "2026-01-28T12:00:00Z"
}
```

---

#### GET /drift
**Purpose:** Model drift diagnostics

**Response:**
```json
{
  "overall_drift": 0.15,
  "drift_by_feature": {...},
  "drift_by_sector": {...},
  "recommendation": "retrain_recommended"
}
```

**Files Read:**
- `ml_data/metrics/drift/drift_report.json`

---

## 3. Insights & Predictions

### 3.1 insights_router.py
**Base Path:** `/api/insights`

#### GET /
**Purpose:** Insight boards (top 50 by various criteria)

**Query Parameters:**
- `board`: "1w", "2w", "4w", "52w", "social", "news"
- `limit`: int (default 50)
- `sector`: Optional sector filter
- `minConfidence`: float (0.0-1.0)

**Response:**
```json
{
  "timestamp": "2026-01-28T12:00:00Z",
  "board": "1w",
  "count": 50,
  "items": [
    {
      "symbol": "AAPL",
      "score": 0.85,
      "predicted_return": 0.05,
      "confidence": 0.80,
      "sector": "Technology"
    }
  ]
}
```

**Boards (Prebuilt JSON files):**
- `insights/top50_1w.json`
- `insights/top50_2w.json`
- `insights/top50_4w.json`
- `insights/top50_52w.json`
- `insights/top50_social_heat.json`
- `insights/top50_news_novelty.json`

**Fallback Strategy:**
1. Try load prebuilt board JSON
2. If missing → rebuild via `build_daily_insights()`
3. Still missing → fallback to `rolling.json` (1d ML scores)

**Error Handling:**
- ✅ Never throws 500, always returns response
- ✅ Rebuilds missing boards on-the-fly

---

#### GET /predictions/latest
**Purpose:** UI-optimized prediction feed

**Response:**
```json
{
  "timestamp": "2026-01-28T12:00:00Z",
  "predictions": [
    {
      "symbol": "AAPL",
      "horizons": {
        "1d": {"return": 0.01, "confidence": 0.75},
        "1w": {"return": 0.05, "confidence": 0.80}
      }
    }
  ]
}
```

**Files Read:**
- `logs/nightly/predictions/latest_predictions.json`

---

### 3.2 portfolio_router.py
**Base Path:** `/api/portfolio`

#### GET /holdings/top/{horizon}
**Purpose:** Top holdings by PnL

**Path Parameters:**
- `horizon`: "1w", "1m", "4w"

**Query Parameters:**
- `limit`: int (default 3)

**Response:**
```json
{
  "horizon": "1w",
  "count": 3,
  "holdings": [
    {
      "ticker": "AAPL",
      "pnl_dollars": 500.0,
      "pnl_percent": 3.33,
      "entry_price": 150.0,
      "current_price": 155.0,
      "quantity": 100,
      "days_held": 5
    }
  ]
}
```

**Calculation:**
1. Load all positions from bot states
2. Get current prices from `rolling.json`
3. Calculate PnL: `(current_price - entry_price) * quantity`
4. Filter by min holding days for horizon
5. Sort by `pnl_percent` descending

**Files Read:**
- `stock_cache/master/bot/rolling_*.json(.gz)`
- `PATHS["rolling"]`

**Error Handling:**
- ✅ 500 if exception (with traceback)

---

## 4. System & Health

### 4.1 system_router.py
**Base Path:** `/api/system`

#### GET /status
**Purpose:** System job monitor & coverage

**Response:**
```json
{
  "timestamp": "2026-01-28T12:00:00Z",
  "job_monitor": {
    "nightly": {
      "last_run": "2026-01-28T02:00:00Z",
      "status": "success",
      "duration_seconds": 450
    }
  },
  "supervisor_verdict": {
    "overall_status": "healthy",
    "recommendations": []
  },
  "rolling_coverage": {
    "total_symbols": 5000,
    "with_predictions": 4950,
    "coverage_pct": 99.0
  }
}
```

**Dependencies:**
- Calls `backend.core.supervisor_agent.supervisor_verdict()`

---

#### GET /health
**Purpose:** Component health check

**Response:**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "components": {
    "broker": "connected",
    "data_pipeline": "ok",
    "ml_models": "loaded"
  }
}
```

---

#### POST /action
**Purpose:** Execute system actions (admin)

**Request Body:**
```json
{
  "task": "nightly",  // or "train", "insights", etc.
  "params": {}
}
```

**Actions (TASKS):**
- `nightly`: Run nightly job
- `train`: Train LightGBM models
- `insights`: Build daily insights
- `metrics`: Build latest metrics
- `fundamentals`: Update fundamentals
- `news`: Fetch news
- `verify`: Verify paths & counts
- `dashboard`: Compute dashboard cache

**Response:**
```json
{
  "task": "nightly",
  "status": "queued",  // or "running", "completed"
  "result": {...}
}
```

**Error Handling:**
- ✅ 404 if unknown action
- ✅ 503 if scheduler unreachable

**Issue ID: 005**
- **Type:** Security
- **Location:** `backend/routers/system_router.py:POST /action`
- **Issue:** No authentication/authorization check on dangerous actions
- **Current:** Any request can trigger nightly job, model training
- **Expected:** Should require admin token or API key
- **Severity:** Critical
- **Status:** ❌ Fail
- **Fix:** Add `Depends(get_admin_user)` to endpoint

---

## 5. Admin & Auth

### 5.1 auth_router.py
**Base Path:** `/api/auth`

Endpoints:
- POST `/login` - User login
- POST `/register` - User registration
- POST `/refresh` - Refresh JWT token
- GET `/me` - Current user info
- POST `/logout` - User logout
- POST `/forgot-password` - Password reset

**JWT Token Schema:**
```json
{
  "sub": "user_id",
  "email": "user@example.com",
  "exp": 1706400000
}
```

---

### 5.2 admin_router_final.py
**Base Path:** `/api/admin`

16 admin endpoints for system management (details omitted for brevity)

---

## 6. Logs & Debugging

### 6.1 logs_router.py
**Base Path:** `/api/logs`

#### GET /list
**Purpose:** List available log files

**Query Parameters:**
- `scope`: "nightly", "intraday", "scheduler", "backend", "all"

**Response:**
```json
{
  "scope": "nightly",
  "count": 30,
  "files": [
    {
      "id": "base64_encoded_path",
      "name": "nightly_20260128.log",
      "size_bytes": 50000,
      "modified": "2026-01-28T02:30:00Z"
    }
  ]
}
```

**Security:**
- ✅ Base64 encoding of file paths
- ✅ Allowed-roots validation prevents path traversal

---

#### GET /{id}
**Purpose:** Retrieve log file content

**Path Parameters:**
- `id`: Base64-encoded file path

**Response:**
- Content-Type: text/plain
- Body: Log file content

**Error Handling:**
- ✅ 404 if file not found
- ✅ 403 if path outside allowed roots

---

### 6.2 intraday_logs_router.py
**Base Path:** `/api/intraday`

Endpoints for intraday bot logs (see section 1.2)

---

## 7. Configuration

### 7.1 settings_router.py
**Base Path:** `/api/settings`

8 endpoints for system settings management

---

### 7.2 swing_tuning_router.py
**Base Path:** `/api/tuning/swing`

8 endpoints for swing bot parameter tuning

---

## 8. File I/O Summary

### 8.1 Input Files (Read Operations)

| Path | Format | Used By | Purpose | Size |
|------|--------|---------|---------|------|
| `stock_cache/master/bot/rolling_*.json(.gz)` | JSON/GZ | eod_bots, dashboard, portfolio | Bot states | 50-500KB each |
| `ml_data/rolling.json.gz` | JSON.GZ | Multiple routers | Latest prices | 5-10MB |
| `ml_data/bot_logs/{horizon}/bot_activity_*.json` | JSON | eod_bots, page_data | Daily bot logs | 10-100KB |
| `ml_data/metrics/accuracy/accuracy_latest.json` | JSON | dashboard, metrics | Accuracy metrics | 5-20KB |
| `logs/nightly/predictions/latest_predictions.json` | JSON | insights, page_data | UI predictions | 1-5MB |
| `da_brains/rolling_optimized.json.gz` | JSON.GZ | page_data | Optimized predictions | 5-10MB |
| `ml_data_dt/sim_logs/*.json` | JSON | intraday_logs, bots_page | Intraday logs | 10-50KB |
| `ml_data/insights/top50_*.json` | JSON | insights | Prebuilt boards | 50-200KB |

### 8.2 Output Files (Write Operations)

| Path | Format | Written By | Purpose |
|------|--------|-----------|---------|
| `ml_data/config/bots_config.json` | JSON | eod_bots | Bot runtime configs |
| `ml_data/config/bots_ui_overrides.json` | JSON | eod_bots | UI-only overrides |
| `ml_data_dt/config/intraday_bots_ui.json` | JSON | intraday_logs | Intraday UI configs |

---

## 9. Error Codes Reference

### 9.1 Standard HTTP Status Codes

| Code | Meaning | Usage | Routers |
|------|---------|-------|---------|
| 200 | OK | Successful response (default) | All |
| 400 | Bad Request | Invalid parameters, validation errors | intraday, live_prices |
| 401 | Unauthorized | Missing/invalid JWT token | auth, admin |
| 403 | Forbidden | Valid token but insufficient permissions | admin, logs |
| 404 | Not Found | Resource doesn't exist (day, bot, symbol) | eod_bots, portfolio, intraday |
| 500 | Internal Server Error | Unhandled exception, file corruption | eod_bots, dashboard, portfolio |
| 503 | Service Unavailable | External service down (scheduler) | system |

### 9.2 Custom Error Response Format

**Pattern 1: HTTPException (FastAPI)**
```json
{
  "detail": "Bot state file not found for eod_1w_momentum"
}
```

**Pattern 2: Error Object (Custom)**
```json
{
  "error": "ValueError: Invalid horizon",
  "trace": "Traceback (most recent call last):\n..."
}
```

**Pattern 3: Graceful Degradation (Best-effort)**
```json
{
  "data": {...},
  "warnings": ["Could not load accuracy metrics"]
}
```

---

## 10. Issues & Recommendations

### 10.1 Critical Issues

**Issue ID: 005** (Duplicate from Section 4)
- **Type:** Security
- **Location:** `backend/routers/system_router.py:POST /action`
- **Issue:** No authentication on dangerous system actions
- **Severity:** Critical
- **Status:** ❌ Fail
- **Fix:** Add admin authentication dependency

**Issue ID: 006**
- **Type:** Error Handling Inconsistency
- **Location:** Multiple routers
- **Issue:** Inconsistent error response formats (HTTPException vs dict vs None)
- **Current:** Each router uses different error pattern
- **Expected:** Standardized error response across all routers
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Fix:** Create unified error handler middleware

---

### 10.2 Performance Issues

**Issue ID: 007**
- **Type:** Performance
- **Location:** `backend/routers/eod_bots_router.py:GET /status`
- **Issue:** Loads all bot state files on every request (no caching)
- **Current:** Reads 5-10 gzip files per request (~500ms)
- **Expected:** Cache bot states with 60s TTL
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Fix:** Implement in-memory cache with TTL

**Issue ID: 008**
- **Type:** Performance
- **Location:** `backend/routers/dashboard_router.py:GET /metrics`
- **Issue:** Recomputes accuracy on every request
- **Current:** Reads accuracy file + bot states + calculates (~300ms)
- **Expected:** Cache computed metrics with 5min TTL
- **Severity:** Low
- **Status:** ⚠️ Warning
- **Fix:** Implement Redis or in-memory cache

---

### 10.3 Data Integrity Issues

**Issue ID: 004** (Duplicate from Section 2)
- Default accuracy of 0.5 is misleading

**Issue ID: 009**
- **Type:** Data Validation
- **Location:** `backend/routers/eod_bots_router.py:POST /configs`
- **Issue:** No validation of config values (can set negative cash, invalid percentages)
- **Current:** Accepts any JSON dict as config
- **Expected:** Pydantic model validation with constraints
- **Severity:** High
- **Status:** ❌ Fail
- **Fix:** Create BotConfigUpdate Pydantic model with validators

---

### 10.4 Missing Features

**Issue ID: 010**
- **Type:** Missing Feature
- **Location:** All routers
- **Issue:** No pagination on large result sets
- **Current:** Returns all results (can be 1000s of items)
- **Expected:** Implement cursor-based pagination
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Fix:** Add `limit` and `offset` query params, return pagination metadata

**Issue ID: 011**
- **Type:** Missing Feature
- **Location:** All routers
- **Issue:** No rate limiting
- **Current:** Unlimited requests per user/IP
- **Expected:** Rate limiting (e.g., 100 req/min per IP)
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Fix:** Add FastAPI rate limiting middleware

---

### 10.5 Recommendations Summary

1. **Standardize error responses** - Use FastAPI exception handlers
2. **Add input validation** - Pydantic models for all request bodies
3. **Implement caching** - Redis or in-memory for frequently accessed data
4. **Add pagination** - Limit large result sets
5. **Add rate limiting** - Prevent abuse
6. **Add authentication** - Protect admin endpoints
7. **Add logging** - Request/response logging for debugging
8. **Add monitoring** - Prometheus metrics for each endpoint
9. **Add documentation** - OpenAPI/Swagger for all endpoints
10. **Add tests** - Unit and integration tests for each router

---

**End of Router Endpoint Specification**
