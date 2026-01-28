# Troubleshooting Guide

This document provides solutions to common issues in the Aion Analytics platform.

## Table of Contents

1. [Configuration Issues](#1-configuration-issues)
2. [Import Errors](#2-import-errors)
3. [Path Not Found Errors](#3-path-not-found-errors)
4. [Authentication Issues](#4-authentication-issues)
5. [Backend Startup Failures](#5-backend-startup-failures)
6. [Frontend Build Failures](#6-frontend-build-failures)
7. [Bot Execution Errors](#7-bot-execution-errors)
8. [Data Loading Issues](#8-data-loading-issues)
9. [API Connection Errors](#9-api-connection-errors)
10. [Lock File Issues](#10-lock-file-issues)
11. [Performance Issues](#11-performance-issues)
12. [Database Issues](#12-database-issues)

---

## 1. Configuration Issues

### Problem: Missing .env file

**Symptoms:**
```
ModuleNotFoundError: No module named 'admin_keys'
KeyError: 'ALPACA_API_KEY_ID'
```

**Solution:**
```bash
# Copy example .env
cp .env.example .env

# Edit with your credentials
nano .env

# Set required variables
ALPACA_API_KEY_ID=your_key
ALPACA_SECRET_KEY=your_secret
ADMIN_PASSWORD_HASH=$(echo -n "your_password" | sha256sum | cut -d' ' -f1)
```

**Validate:**
```bash
python3 -c "from admin_keys import ALPACA_API_KEY_ID; print('✅ Config OK')"
```

---

### Problem: ADMIN_PASSWORD_HASH not set

**Symptoms:**
```
Admin login fails
[admin.auth] ADMIN_PASSWORD_HASH loaded: MISSING
```

**Solution:**
```bash
# Generate password hash
PASSWORD="your_secure_password"
HASH=$(echo -n "$PASSWORD" | sha256sum | cut -d' ' -f1)

# Add to .env
echo "ADMIN_PASSWORD_HASH=$HASH" >> .env

# Restart backend
pkill -f "python3 run_backend.py"
python3 run_backend.py &
```

**Validate:**
```bash
curl -X POST http://localhost:8000/admin/login \
  -H "Content-Type: application/json" \
  -d '{"password":"your_secure_password"}'
# Should return token
```

---

### Problem: Timezone not set

**Symptoms:**
```
Trades executed at wrong time
Nightly job runs at wrong hour
```

**Solution:**
```bash
# Set timezone in .env
echo "AION_TZ=America/New_York" >> .env

# Or edit settings.py
# TIMEZONE = pytz.timezone("America/New_York")
```

**Available Timezones:**
```python
import pytz
print(pytz.all_timezones)
# Common: America/New_York, America/Chicago, America/Denver, America/Los_Angeles, UTC
```

---

## 2. Import Errors

### Problem: Cannot import from config

**Symptoms:**
```
ModuleNotFoundError: No module named 'config'
ImportError: cannot import name 'PATHS' from 'config'
```

**Solution:**
```bash
# Ensure you're in project root
cd /home/runner/work/Aion/Aion

# Check config.py exists
ls -la config.py

# Add project root to PYTHONPATH
export PYTHONPATH=/home/runner/work/Aion/Aion:$PYTHONPATH

# Or run with -m flag
python3 -m backend.jobs.nightly_job
```

**Permanent Fix (add to ~/.bashrc):**
```bash
export PYTHONPATH=/home/runner/work/Aion/Aion:$PYTHONPATH
```

---

### Problem: Circular import error

**Symptoms:**
```
ImportError: cannot import name 'PATHS' from partially initialized module 'config'
```

**Solution:**
```python
# Use late import
def get_paths():
    from config import PATHS
    return PATHS

# Or use fallback import
try:
    from backend.core.config import PATHS
except ImportError:
    from config import PATHS
```

---

## 3. Path Not Found Errors

### Problem: PATHS key not found

**Symptoms:**
```
KeyError: 'rolling_brain'
TypeError: 'NoneType' object is not subscriptable
```

**Solution:**
```python
# Use defensive access
from config import PATHS

# Option 1: .get() with fallback
rolling_path = PATHS.get("rolling_brain", Path("da_brains/core/rolling_brain.json.gz"))

# Option 2: Check existence
if "rolling_brain" in PATHS:
    rolling_path = PATHS["rolling_brain"]
else:
    raise ValueError("rolling_brain path not configured")
```

---

### Problem: Directory does not exist

**Symptoms:**
```
FileNotFoundError: [Errno 2] No such file or directory: 'da_brains/core/rolling_brain.json.gz'
```

**Solution:**
```bash
# Create missing directories
python3 -c "from config import ensure_project_structure; ensure_project_structure()"

# Or manually
mkdir -p da_brains/core
mkdir -p ml_data/config
mkdir -p data/stock_cache/master/bot
mkdir -p logs/nightly
```

**Auto-create on startup:**
```python
# In your script
from config import ensure_project_structure
ensure_project_structure()
```

---

## 4. Authentication Issues

### Problem: Admin token expired

**Symptoms:**
```
HTTP 403: Forbidden
{"detail": "forbidden"}
```

**Solution:**
```bash
# Login again to get new token
curl -X POST http://localhost:8000/admin/login \
  -H "Content-Type: application/json" \
  -d '{"password":"your_password"}' | jq -r '.token'

# Update frontend localStorage
# In browser console:
localStorage.setItem('adminToken', 'new_token_here')
```

**Adjust TTL (in .env):**
```bash
ADMIN_TOKEN_TTL_SECONDS=7200  # 2 hours
```

---

### Problem: Cannot login (wrong password)

**Symptoms:**
```
POST /admin/login returns 401
```

**Solution:**
```bash
# Regenerate password hash
NEW_PASSWORD="new_secure_password"
NEW_HASH=$(echo -n "$NEW_PASSWORD" | sha256sum | cut -d' ' -f1)

# Update .env
sed -i "s/ADMIN_PASSWORD_HASH=.*/ADMIN_PASSWORD_HASH=$NEW_HASH/" .env

# Restart backend
pkill -f "run_backend.py"
python3 run_backend.py &
```

---

## 5. Backend Startup Failures

### Problem: Port already in use

**Symptoms:**
```
ERROR:    [Errno 48] error while attempting to bind on address ('0.0.0.0', 8000): address already in use
```

**Solution:**
```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or use a different port
uvicorn backend_service:app --port 8001
```

---

### Problem: Missing dependencies

**Symptoms:**
```
ModuleNotFoundError: No module named 'fastapi'
ModuleNotFoundError: No module named 'lightgbm'
```

**Solution:**
```bash
# Install all dependencies
pip install -r requirements.txt

# Or specific package
pip install fastapi uvicorn lightgbm

# Check installed packages
pip list | grep -E "(fastapi|lightgbm|pandas)"
```

---

### Problem: Import errors on startup

**Symptoms:**
```
TypeError: 'PathLike' object is not subscriptable
AttributeError: module 'config' has no attribute 'PATHS'
```

**Solution:**
```bash
# Clear Python cache
find . -type d -name "__pycache__" -exec rm -r {} +
find . -type f -name "*.pyc" -delete

# Restart backend
python3 run_backend.py
```

---

## 6. Frontend Build Failures

### Problem: npm install fails

**Symptoms:**
```
npm ERR! code ERESOLVE
npm ERR! ERESOLVE unable to resolve dependency tree
```

**Solution:**
```bash
cd frontend

# Clear cache
rm -rf node_modules package-lock.json

# Install with legacy peer deps
npm install --legacy-peer-deps

# Or use specific Node version
nvm use 18
npm install
```

---

### Problem: TypeScript compilation errors

**Symptoms:**
```
Type error: Cannot find module 'next'
Type error: Property 'useState' does not exist on type 'React'
```

**Solution:**
```bash
cd frontend

# Reinstall types
npm install --save-dev @types/react @types/node

# Check tsconfig.json
cat tsconfig.json

# Build with verbose output
npm run build -- --debug
```

---

### Problem: Environment variables not loaded

**Symptoms:**
```
process.env.NEXT_PUBLIC_BACKEND_URL is undefined
```

**Solution:**
```bash
cd frontend

# Create .env.local
cat > .env.local << EOF
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
EOF

# Restart dev server
npm run dev
```

---

## 7. Bot Execution Errors

### Problem: Rolling brain not found

**Symptoms:**
```
FileNotFoundError: da_brains/core/rolling_brain.json.gz
```

**Solution:**
```bash
# Run nightly job to generate brain
python3 -c "from backend.jobs.nightly_job import run_nightly; run_nightly()"

# Or create empty brain
mkdir -p da_brains/core
echo '{}' | gzip > da_brains/core/rolling_brain.json.gz
```

---

### Problem: Bot config not loading

**Symptoms:**
```
KeyError: 'swing_1w'
FileNotFoundError: ml_data/config/bots_config.json
```

**Solution:**
```bash
# Create default config
mkdir -p ml_data/config
cat > ml_data/config/bots_config.json << 'EOF'
{
  "swing_1w": {
    "enabled": true,
    "aggression": 0.50,
    "max_positions": 10
  }
}
EOF
```

---

### Problem: Alpaca API errors

**Symptoms:**
```
alpaca_trade_api.rest.APIError: 401 Unauthorized
```

**Solution:**
```bash
# Verify credentials
python3 -c "
from admin_keys import ALPACA_API_KEY_ID, ALPACA_SECRET_KEY
print(f'Key ID: {ALPACA_API_KEY_ID[:10]}...')
print(f'Secret: {ALPACA_SECRET_KEY[:10]}...')
"

# Test connection
python3 -c "
from alpaca_trade_api import REST
from admin_keys import ALPACA_API_KEY_ID, ALPACA_SECRET_KEY
api = REST(ALPACA_API_KEY_ID, ALPACA_SECRET_KEY, base_url='https://paper-api.alpaca.markets')
account = api.get_account()
print(f'Account: {account.account_number}')
"
```

---

## 8. Data Loading Issues

### Problem: Empty predictions

**Symptoms:**
```
Dashboard shows no predictions
GET /api/insights/predictions returns []
```

**Solution:**
```bash
# Check rolling brain
python3 -c "
import gzip, json
from config import PATHS
with gzip.open(PATHS['rolling_brain'], 'rt') as f:
    data = json.load(f)
print(f'Brain has {len(data)} entries')
"

# Regenerate if empty
python3 -c "from backend.jobs.nightly_job import run_nightly; run_nightly()"
```

---

### Problem: Stale data

**Symptoms:**
```
Dashboard shows yesterday's data
Predictions not updated
```

**Solution:**
```bash
# Check last nightly job
cat logs/nightly/last_nightly_summary.json | jq '.completed_at'

# Run nightly manually
python3 -c "from backend.jobs.nightly_job import run_nightly; run_nightly()"

# Check scheduler is running
ps aux | grep scheduler_runner.py
```

---

### Problem: Corrupted files

**Symptoms:**
```
json.decoder.JSONDecodeError: Expecting value
gzip.BadGzipFile: Not a gzipped file
```

**Solution:**
```bash
# Backup corrupted file
cp da_brains/core/rolling_brain.json.gz da_brains/core/rolling_brain.json.gz.backup

# Restore from backup
cp da_brains/core/rolling_brain.json.gz.backup.YYYYMMDD da_brains/core/rolling_brain.json.gz

# Or regenerate
python3 -c "from backend.jobs.nightly_job import run_nightly; run_nightly()"
```

---

## 9. API Connection Errors

### Problem: Backend not responding

**Symptoms:**
```
fetch failed: connect ECONNREFUSED 127.0.0.1:8000
```

**Solution:**
```bash
# Check backend is running
curl http://localhost:8000/api/system/status

# If not running, start it
python3 run_backend.py &

# Check logs
tail -f logs/backend/backend_*.log
```

---

### Problem: CORS errors

**Symptoms:**
```
Access to fetch has been blocked by CORS policy
```

**Solution:**
```python
# In backend_service.py, add CORS middleware
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

### Problem: Proxy timeout

**Symptoms:**
```
504 Gateway Timeout
Request timeout
```

**Solution:**
```typescript
// In frontend/app/api/backend/[...path]/route.ts
const response = await fetch(url, {
  headers: headers,
  signal: AbortSignal.timeout(30000),  // 30 second timeout
})
```

---

## 10. Lock File Issues

### Problem: Stale lock file

**Symptoms:**
```
LockError: Another process is running (lock file exists)
Process hangs indefinitely
```

**Solution:**
```bash
# Check for stale locks
find . -name "*.lock" -mmin +60  # Older than 60 minutes

# Remove stale locks
rm -f da_brains/nightly_job.lock
rm -f da_brains/intraday/.dt_cycle.lock
rm -f da_brains/intraday/.dt_scheduler.lock

# Verify no process is running
ps aux | grep -E "(nightly_job|scheduler)"
```

---

### Problem: Lock file not released

**Symptoms:**
```
Lock file persists after process exit
```

**Solution:**
```python
# Use context manager in code
from pathlib import Path
import fcntl

class FileLock:
    def __init__(self, path):
        self.path = path
        self.fd = None
    
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.path, 'w')
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self
    
    def __exit__(self, *args):
        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
        self.path.unlink(missing_ok=True)

# Usage
with FileLock(PATHS["nightly_lock"]):
    run_nightly_job()
```

---

## 11. Performance Issues

### Problem: Slow dashboard loading

**Symptoms:**
```
Dashboard takes 10+ seconds to load
```

**Solution:**
```bash
# Enable cache
# In backend/routers/bots_router.py
from backend.services.unified_cache_service import get_cached_data, set_cached_data

@router.get("/page")
def get_bots_page():
    cached = get_cached_data("bots_page", max_age=60)
    if cached:
        return cached
    
    data = load_bots_page_data()
    set_cached_data("bots_page", data)
    return data
```

---

### Problem: Large log files

**Symptoms:**
```
logs/nightly/ uses 10GB+ disk space
```

**Solution:**
```bash
# Rotate old logs
find logs/nightly -name "*.log" -mtime +30 -delete

# Compress old logs
find logs/nightly -name "*.log" -mtime +7 -exec gzip {} \;

# Add log rotation (logrotate)
cat > /etc/logrotate.d/aion << EOF
/home/runner/work/Aion/Aion/logs/*/*.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
}
EOF
```

---

## 12. Database Issues

### Problem: Database connection fails (if using PostgreSQL)

**Symptoms:**
```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Solution:**
```bash
# Check DATABASE_URL
echo $DATABASE_URL

# Test connection
psql "$DATABASE_URL"

# Or use Python
python3 -c "
from sqlalchemy import create_engine
from os import getenv
engine = create_engine(getenv('DATABASE_URL'))
with engine.connect() as conn:
    print('✅ Connection OK')
"
```

---

### Problem: Missing tables

**Symptoms:**
```
sqlalchemy.exc.ProgrammingError: relation "users" does not exist
```

**Solution:**
```python
# Create tables
from backend.database.models import Base
from sqlalchemy import create_engine
from os import getenv

engine = create_engine(getenv('DATABASE_URL'))
Base.metadata.create_all(engine)
print('✅ Tables created')
```

---

## Emergency Recovery

### Nuclear Option: Full Reset

**Use only if all else fails:**

```bash
# 1. Backup data
mkdir -p /tmp/aion_backup
cp -r da_brains /tmp/aion_backup/
cp -r ml_data /tmp/aion_backup/
cp -r data /tmp/aion_backup/

# 2. Clean all generated files
rm -rf da_brains/*
rm -rf ml_data/config/*
rm -rf ml_data/models/*
rm -rf data/stock_cache/*
rm -rf logs/*

# 3. Recreate structure
python3 -c "from config import ensure_project_structure; ensure_project_structure()"

# 4. Run nightly job
python3 -c "from backend.jobs.nightly_job import run_nightly; run_nightly()"

# 5. Restart all services
pkill -f "run_backend.py"
pkill -f "scheduler_runner.py"
python3 run_backend.py &
python3 backend/scheduler_runner.py &
```

---

## Getting Help

If you're still stuck:

1. **Check logs:** `tail -f logs/backend/backend_*.log`
2. **Check GitHub Issues:** Search for similar problems
3. **Enable debug logging:**
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```
4. **Run diagnostic script:** `python3 scripts/diagnose.py` (if exists)
5. **Create GitHub Issue** with:
   - Error message
   - Steps to reproduce
   - Relevant logs
   - System info (`uname -a`, `python3 --version`)

---

## Quick Diagnostic Commands

```bash
# Check all services
ps aux | grep -E "(run_backend|scheduler|dt_backend)"

# Check ports
lsof -i :8000
lsof -i :3000

# Check disk space
df -h

# Check recent errors
grep -r "ERROR" logs/ | tail -20

# Check config validity
python3 -c "
from backend.core.config import PATHS, ROOT, TIMEZONE
from settings import BOT_KNOBS_DEFAULTS
from admin_keys import ALPACA_API_KEY_ID
print('✅ All configs load successfully')
"

# Check imports
python3 -c "
import backend.routers.bots_router
import backend.services.bot_bootstrapper
import backend.bots.base_swing_bot
print('✅ All imports resolve')
"

# Check paths exist
python3 -c "
from config import PATHS
critical = ['rolling_brain', 'bots_config', 'ml_data', 'logs']
for key in critical:
    path = PATHS[key]
    status = '✅' if path.exists() else '❌'
    print(f'{status} {key}: {path}')
"
```

## Summary

**Most Common Issues:**
1. ✅ Missing .env file → Copy .env.example
2. ✅ Import errors → Set PYTHONPATH
3. ✅ Missing directories → Run ensure_project_structure()
4. ✅ Expired tokens → Login again
5. ✅ Port conflicts → Use different port or kill process
6. ✅ Stale locks → Remove .lock files
7. ✅ Empty data → Run nightly job
8. ✅ Slow performance → Enable caching

**Quick Fixes:**
```bash
# Reset and restart everything
./scripts/reset_and_restart.sh  # If exists

# Or manually
pkill -f "python3"
rm -f da_brains/**/*.lock
python3 run_backend.py &
cd frontend && npm run dev &
```
