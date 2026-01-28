# Issues Found During System Audit

**Generated:** 2026-01-28  
**Audit Scope:** Complete end-to-end system audit of Aion

---

## Executive Summary

**Total Issues Found:** 12  
**Critical:** 2  
**High:** 3  
**Medium:** 5  
**Low:** 2

**Overall Status:** ⚠️ **Action Required** - Critical and high-priority issues must be addressed

---

## Critical Issues

### Issue ID: 012
- **Type:** Architecture - Circular Dependency
- **Location:** `backend.historical_replay_swing.snapshot_manager` ↔ `backend.services.backfill_history` ↔ `backend.services.replay_data_pipeline`
- **Issue:** Circular import dependency detected
- **Current:** 
  ```
  snapshot_manager → backfill_history → replay_data_pipeline → snapshot_manager
  ```
- **Expected:** Should break the cycle by extracting shared functionality
- **Severity:** Critical
- **Status:** ❌ Fail
- **Impact:** Can cause import errors, makes code hard to maintain, prevents proper testing
- **Fix:**
  1. Extract shared types/interfaces to a separate module (e.g., `backend.historical_replay_swing.types`)
  2. Have all three modules import from the types module
  3. Remove direct imports between the three modules
  ```python
  # backend/historical_replay_swing/types.py (new file)
  from dataclasses import dataclass
  
  @dataclass
  class SnapshotData:
      # shared data structures
      pass
  
  # Then in each module:
  from backend.historical_replay_swing.types import SnapshotData
  ```
- **Priority:** P0 - Must fix immediately

---

### Issue ID: 005
- **Type:** Security - Authentication
- **Location:** `backend/routers/system_router.py:POST /action`
- **Issue:** No authentication on dangerous system actions
- **Current:** Any request can trigger nightly job, model training, system tasks
- **Expected:** Should require admin authentication token
- **Severity:** Critical
- **Status:** ❌ Fail
- **Impact:** Malicious actors could trigger resource-intensive operations, DoS attack vector
- **Fix:**
  ```python
  @router.post("/action")
  async def execute_action(
      task: str,
      current_user: User = Depends(get_admin_user)  # Add this
  ):
      # ... rest of code
  ```
- **Priority:** P0 - Must fix immediately

---

## High Priority Issues

### Issue ID: 001
- **Type:** File I/O - Error Handling
- **Location:** `backend/core/ai_model/feature_pipeline.py:60`
- **Issue:** No error handling for parquet read failures
- **Current:** Uncaught exception crashes nightly job
  ```python
  df = pd.read_parquet(dataset_path)  # No try/except
  ```
- **Expected:** Should validate file and handle corruption gracefully
- **Severity:** High
- **Status:** ❌ Fail
- **Impact:** Nightly ML pipeline fails completely if dataset corrupted
- **Fix:**
  ```python
  try:
      df = pd.read_parquet(dataset_path)
  except Exception as e:
      logger.error(f"Failed to read parquet: {e}")
      return pd.DataFrame()  # Return empty DataFrame
  ```
- **Priority:** P1 - Fix within 1 week

---

### Issue ID: 002
- **Type:** File I/O - Resource Check
- **Location:** `backend/services/prediction_logger.py:35`
- **Issue:** No disk space check before write
- **Current:** May fail silently or crash if disk full
  ```python
  with open(ledger_path, "a") as f:
      f.write(json.dumps(entry) + "\n")
  ```
- **Expected:** Check disk space, log error if insufficient
- **Severity:** High
- **Status:** ❌ Fail
- **Impact:** Silent data loss when disk full, difficult to diagnose
- **Fix:**
  ```python
  import shutil
  
  def check_disk_space(path: Path, required_mb: int = 100) -> bool:
      stat = shutil.disk_usage(path.parent)
      available_mb = stat.free / (1024 * 1024)
      return available_mb >= required_mb
  
  if not check_disk_space(ledger_path):
      logger.error("Insufficient disk space")
      raise IOError("Disk full")
  
  with open(ledger_path, "a") as f:
      f.write(json.dumps(entry) + "\n")
  ```
- **Priority:** P1 - Fix within 1 week

---

### Issue ID: 009
- **Type:** Data Validation - Input Validation
- **Location:** `backend/routers/eod_bots_router.py:POST /configs`
- **Issue:** No validation of config values (can set negative cash, invalid percentages)
- **Current:** Accepts any JSON dict as config
  ```python
  @router.post("/configs")
  async def update_config(data: dict):  # No validation
      config_store.save_bot_config(data["bot_key"], data["config"])
  ```
- **Expected:** Pydantic model validation with constraints
- **Severity:** High
- **Status:** ❌ Fail
- **Impact:** Invalid bot configurations can cause trading errors, negative cash balances
- **Fix:**
  ```python
  from pydantic import BaseModel, Field, validator
  
  class BotConfigUpdate(BaseModel):
      bot_key: str
      config: Dict[str, Any]
      
      @validator('config')
      def validate_config(cls, v):
          if 'cash_allocation' in v and v['cash_allocation'] < 0:
              raise ValueError("cash_allocation must be positive")
          if 'risk_per_trade' in v:
              if not 0 <= v['risk_per_trade'] <= 1:
                  raise ValueError("risk_per_trade must be between 0 and 1")
          return v
  
  @router.post("/configs")
  async def update_config(data: BotConfigUpdate):
      config_store.save_bot_config(data.bot_key, data.config)
  ```
- **Priority:** P1 - Fix within 1 week

---

## Medium Priority Issues

### Issue ID: 003
- **Type:** Error Handling - Missing 404
- **Location:** `backend/routers/eod_bots_router.py:75`
- **Issue:** Missing bot state file silently returns empty, no 404
- **Current:** Returns empty dict for missing files
  ```python
  bot_state = _read_bot_state(bot_key)  # Returns {} if missing
  return {"bot": bot_state}
  ```
- **Expected:** Should return 404 if bot doesn't exist
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Impact:** Client can't distinguish between empty state and non-existent bot
- **Fix:**
  ```python
  bot_state = _read_bot_state(bot_key)
  if not bot_state:
      raise HTTPException(404, f"Bot {bot_key} not found")
  return {"bot": bot_state}
  ```
- **Priority:** P2 - Fix within 2 weeks

---

### Issue ID: 004
- **Type:** Data Accuracy - Misleading Defaults
- **Location:** `backend/routers/dashboard_router.py:95`
- **Issue:** Defaults to 0.5 if accuracy file missing, misleading
- **Current:** Returns 0.5 (50%) as placeholder
  ```python
  accuracy = 0.5  # Default
  try:
      data = json.loads(accuracy_path.read_text())
      accuracy = data.get("accuracy", 0.5)
  except:
      pass
  return {"accuracy": accuracy}
  ```
- **Expected:** Should return null or indicate "Not Available"
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Impact:** Users see false 50% accuracy, think system is working when it's not
- **Fix:**
  ```python
  accuracy = None
  try:
      data = json.loads(accuracy_path.read_text())
      accuracy = data.get("accuracy")
  except:
      pass
  return {
      "accuracy": accuracy,
      "status": "available" if accuracy is not None else "calculating"
  }
  ```
- **Priority:** P2 - Fix within 2 weeks

---

### Issue ID: 006
- **Type:** Consistency - Error Response Format
- **Location:** Multiple routers
- **Issue:** Inconsistent error response formats (HTTPException vs dict vs None)
- **Current:** Each router uses different error pattern
  - Some: `raise HTTPException(500, detail="error")`
  - Some: `return {"error": "message"}`
  - Some: `return None`
- **Expected:** Standardized error response across all routers
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Impact:** Frontend must handle multiple error formats, inconsistent UX
- **Fix:** Create unified error handler middleware
  ```python
  # backend/middleware/error_handler.py
  from fastapi import Request, status
  from fastapi.responses import JSONResponse
  
  @app.exception_handler(Exception)
  async def global_exception_handler(request: Request, exc: Exception):
      return JSONResponse(
          status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
          content={
              "error": {
                  "type": type(exc).__name__,
                  "message": str(exc),
                  "timestamp": datetime.utcnow().isoformat()
              }
          }
      )
  ```
- **Priority:** P2 - Fix within 2 weeks

---

### Issue ID: 007
- **Type:** Performance - No Caching
- **Location:** `backend/routers/eod_bots_router.py:GET /status`
- **Issue:** Loads all bot state files on every request (no caching)
- **Current:** Reads 5-10 gzip files per request (~500ms)
- **Expected:** Cache bot states with 60s TTL
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Impact:** High latency on dashboard load, excessive disk I/O
- **Fix:**
  ```python
  from functools import lru_cache
  from time import time
  
  _cache = {}
  _cache_ttl = 60  # seconds
  
  def get_bot_states():
      now = time()
      if "bot_states" in _cache:
          cached_time, cached_data = _cache["bot_states"]
          if now - cached_time < _cache_ttl:
              return cached_data
      
      # Load from disk
      data = _load_all_bot_states()
      _cache["bot_states"] = (now, data)
      return data
  ```
- **Priority:** P2 - Fix within 2 weeks

---

### Issue ID: 008
- **Type:** Performance - Redundant Computation
- **Location:** `backend/routers/dashboard_router.py:GET /metrics`
- **Issue:** Recomputes accuracy on every request
- **Current:** Reads accuracy file + bot states + calculates (~300ms)
- **Expected:** Cache computed metrics with 5min TTL
- **Severity:** Medium
- **Status:** ⚠️ Warning
- **Impact:** Unnecessary computation, slow dashboard loads
- **Fix:** Same caching pattern as Issue ID: 007
- **Priority:** P2 - Fix within 2 weeks

---

## Low Priority Issues

### Issue ID: 010
- **Type:** Missing Feature - Pagination
- **Location:** All routers
- **Issue:** No pagination on large result sets
- **Current:** Returns all results (can be 1000s of items)
- **Expected:** Implement cursor-based pagination
- **Severity:** Low
- **Status:** ⚠️ Warning
- **Impact:** Large payloads, slow network transfers
- **Fix:**
  ```python
  @router.get("/predictions")
  async def get_predictions(
      limit: int = 50,
      offset: int = 0
  ):
      all_predictions = load_predictions()
      total = len(all_predictions)
      page = all_predictions[offset:offset + limit]
      
      return {
          "data": page,
          "pagination": {
              "total": total,
              "limit": limit,
              "offset": offset,
              "has_more": offset + limit < total
          }
      }
  ```
- **Priority:** P3 - Fix when convenient

---

### Issue ID: 011
- **Type:** Missing Feature - Rate Limiting
- **Location:** All routers
- **Issue:** No rate limiting
- **Current:** Unlimited requests per user/IP
- **Expected:** Rate limiting (e.g., 100 req/min per IP)
- **Severity:** Low
- **Status:** ⚠️ Warning
- **Impact:** Vulnerable to DoS attacks, API abuse
- **Fix:**
  ```python
  from slowapi import Limiter
  from slowapi.util import get_remote_address
  
  limiter = Limiter(key_func=get_remote_address)
  app.state.limiter = limiter
  
  @app.get("/api/dashboard")
  @limiter.limit("100/minute")
  async def dashboard(request: Request):
      # ... endpoint code
  ```
- **Priority:** P3 - Fix when convenient

---

## Issue Summary by Category

### Architecture (1 issue)
- Critical: Issue ID 012 (Circular dependency)

### Security (1 issue)
- Critical: Issue ID 005 (Authentication on system actions)

### File I/O (2 issues)
- High: Issue ID 001 (Parquet read error handling)
- High: Issue ID 002 (Disk space check)

### Data Validation (1 issue)
- High: Issue ID 009 (Config validation)

### Error Handling (2 issues)
- Medium: Issue ID 003 (Missing 404)
- Medium: Issue ID 006 (Inconsistent error format)

### Performance (2 issues)
- Medium: Issue ID 007 (No caching - bot states)
- Medium: Issue ID 008 (No caching - metrics)

### Data Accuracy (1 issue)
- Medium: Issue ID 004 (Misleading defaults)

### Missing Features (2 issues)
- Low: Issue ID 010 (Pagination)
- Low: Issue ID 011 (Rate limiting)

---

## Recommended Action Plan

### Immediate (Within 24 hours)
1. **Issue ID 012:** Break circular dependency in historical replay modules
2. **Issue ID 005:** Add authentication to system action endpoint

### Week 1
2. **Issue ID 001:** Add error handling to feature_pipeline.py
3. **Issue ID 002:** Add disk space checks to file writes
4. **Issue ID 009:** Add Pydantic validation to bot configs

### Week 2
5. **Issue ID 003-004, 006-008:** Address medium priority issues
   - Standardize error responses
   - Add caching for bot states and metrics
   - Fix misleading defaults

### Week 3-4
6. **Issue ID 010-011:** Implement pagination and rate limiting

---

## Testing Recommendations

After fixes:
1. Run `python3 scripts/audit/full_system_audit.py`
2. Test each endpoint with invalid inputs
3. Test file I/O error scenarios (corrupted files, disk full)
4. Load test dashboard endpoints
5. Security test system action endpoint

---

**End of Issues Report**
