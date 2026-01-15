# Testing Guide: API Proxy Routes and Configuration Editor

This document outlines how to test the newly implemented API proxy routes and configuration editor.

## Prerequisites

1. **Backend Services Running:**
   - Main backend (EOD/Nightly) on port 8001
   - DT backend (Intraday) on port 8010

2. **Environment Configuration:**
   ```bash
   cd frontend
   cp .env.example .env.local
   # Edit .env.local with your backend URLs
   ```

3. **Frontend Running:**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

## Test 1: Backend Proxy Route - Basic Routes

### Test 1.1: Bots Page Endpoint
**Endpoint:** `GET /api/backend/bots/page`  
**Expected:** Should forward to `http://backend:8001/api/bots/page`

```bash
# Using curl
curl -v http://localhost:3000/api/backend/bots/page

# Expected Response: 200 OK with bot configuration data
```

**Browser Test:**
- Navigate to `http://localhost:3000/bots`
- Open browser DevTools Network tab
- Look for requests to `/api/backend/bots/page`
- Verify 200 OK response (not 404 or 502)

### Test 1.2: Settings Knobs Endpoint
**Endpoint:** `GET /api/backend/settings/knobs`  
**Expected:** Should forward to `http://backend:8001/api/settings/knobs`

```bash
curl -v http://localhost:3000/api/backend/settings/knobs

# Expected Response: 200 OK with { "content": "..." }
```

## Test 2: Backend Proxy Route - Dashboard Routes (No /api Prefix)

### Test 2.1: Dashboard Metrics
**Endpoint:** `GET /api/backend/dashboard/metrics`  
**Expected:** Should forward to `http://backend:8001/dashboard/metrics` (NO /api prefix)

```bash
curl -v http://localhost:3000/api/backend/dashboard/metrics

# Expected: 200 OK with dashboard metrics
```

**Verify in logs:**
Check Next.js console output for:
```
[Backend Proxy] GET /api/backend/dashboard/metrics → http://backend:8001/dashboard/metrics
```
Note: NO `/api` between `:8001` and `/dashboard`

## Test 3: Backend Proxy Route - Admin Routes (No /api Prefix)

### Test 3.1: Admin Login
**Endpoint:** `POST /api/backend/admin/login`  
**Expected:** Should forward to `http://backend:8001/admin/login` (NO /api prefix)

```bash
curl -v -X POST http://localhost:3000/api/backend/admin/login \
  -H "Content-Type: application/json" \
  -d '{"password":"test"}'

# Expected: Response from admin login endpoint
```

**Browser Test:**
- Navigate to `http://localhost:3000/tools/admin`
- Try logging in with admin password
- Check DevTools Network tab for `/api/backend/admin/login`
- Verify request reaches backend

## Test 4: DT Backend Proxy Route

### Test 4.1: Health Check
**Endpoint:** `GET /api/dt/health`  
**Expected:** Should forward to `http://dt-backend:8010/health`

```bash
curl -v http://localhost:3000/api/dt/health

# Expected: 200 OK with health status
```

### Test 4.2: Jobs Status
**Endpoint:** `GET /api/dt/jobs/status`  
**Expected:** Should forward to `http://dt-backend:8010/jobs/status`

```bash
curl -v http://localhost:3000/api/dt/jobs/status

# Expected: 200 OK with job status data
```

**Verify in logs:**
Check Next.js console output for:
```
[DT Proxy] GET /api/dt/health → http://dt-backend:8010/health
[DT Proxy] GET /api/dt/jobs/status → http://dt-backend:8010/jobs/status
```

## Test 5: Configuration Editor Page

### Test 5.1: Page Load
**URL:** `http://localhost:3000/bots/config`

**Expected:**
1. Page loads without errors
2. Two tabs visible: "knobs.env" and "dt_knobs.env"
3. Loading state displays briefly
4. Content from both files loads into editor

**Checklist:**
- [ ] Page renders correctly
- [ ] Tab switching works
- [ ] Files load without errors
- [ ] Textarea shows file content
- [ ] UI matches existing app styling (dark theme, cards)

### Test 5.2: Load knobs.env
**Browser Test:**
1. Navigate to `http://localhost:3000/bots/config`
2. Verify "knobs.env" tab is active
3. Check DevTools Network tab for:
   - Request to `/api/backend/settings/knobs`
   - Response 200 OK
   - Response body contains `{ "content": "..." }`
4. Verify content appears in textarea

### Test 5.3: Load dt_knobs.env
**Browser Test:**
1. Click "dt_knobs.env" tab
2. Check DevTools Network tab for:
   - Request to `/api/backend/settings/dt-knobs`
   - Response 200 OK
   - Response body contains `{ "content": "..." }`
3. Verify content appears in textarea

### Test 5.4: Save Changes
**Browser Test:**
1. Edit content in textarea (add a comment or modify value)
2. Click "Save Changes" button
3. Verify:
   - Button shows "Saving..." state
   - Request sent to POST `/api/backend/settings/knobs` (or dt-knobs)
   - Request body: `{ "content": "...modified content..." }`
   - Success message displays (green border)
   - Message disappears after 5 seconds
4. Check backend filesystem to verify file was actually saved

### Test 5.5: Reload Changes
**Browser Test:**
1. Click "Reload" button
2. Verify:
   - Fresh GET request sent
   - Content reloads from server
   - Any unsaved edits are replaced with server version
   - Success message shows "reloaded"

### Test 5.6: Error Handling
**Browser Test:**
1. Stop backend service temporarily
2. Try to load or save a file
3. Verify:
   - Error message displays (red border)
   - Message explains the failure
   - User can dismiss error message

## Test 6: HTTP Methods Support

### Test 6.1: POST Method
```bash
curl -X POST http://localhost:3000/api/backend/settings/knobs \
  -H "Content-Type: application/json" \
  -d '{"content":"TEST_VAR=123\n"}'

# Expected: 200 OK, file saved
```

### Test 6.2: PUT Method (if backend supports)
```bash
curl -X PUT http://localhost:3000/api/backend/some/endpoint \
  -H "Content-Type: application/json" \
  -d '{"test":"data"}'

# Expected: Request forwarded to backend
```

## Test 7: Query String Preservation

```bash
curl -v "http://localhost:3000/api/backend/bots/page?refresh=true&_ts=12345"

# Expected: Query string preserved in backend request
# Verify in logs: → http://backend:8001/api/bots/page?refresh=true&_ts=12345
```

## Test 8: Error Scenarios

### Test 8.1: Backend Unreachable
**Setup:** Stop backend service

```bash
curl -v http://localhost:3000/api/backend/bots/page

# Expected: 502 Bad Gateway
# Response body: { "error": "Backend request failed", "message": "..." }
```

### Test 8.2: Environment Not Configured
**Setup:** Remove BACKEND_URL from .env.local

```bash
# Restart Next.js dev server
curl -v http://localhost:3000/api/backend/bots/page

# Expected: 502 Bad Gateway
# Response body: { "error": "Backend URL not configured" }
```

## Test 9: Console Logs Verification

While running tests, check Next.js console for proper logging:

```
✅ Good Logs:
[Backend Proxy] GET /api/backend/bots/page → http://209.126.82.160:8001/api/bots/page
[Backend Proxy] GET /api/backend/dashboard/metrics → http://209.126.82.160:8001/dashboard/metrics
[DT Proxy] GET /api/dt/health → http://209.126.82.160:8010/health

❌ Bad Logs (indicates bugs):
[Backend Proxy] GET /api/backend/dashboard/metrics → http://209.126.82.160:8001/api/dashboard/metrics
                                                                                  ^^^^ Should NOT have /api prefix
```

## Success Criteria

All tests pass when:

- [x] Backend proxy routes return 200 OK (not 404)
- [x] Dashboard/admin routes forward WITHOUT /api prefix
- [x] Other routes forward WITH /api prefix
- [x] DT proxy routes forward correctly
- [x] Query strings are preserved
- [x] POST/PUT/PATCH requests forward with body
- [x] Configuration editor page loads and displays content
- [x] Tab switching works correctly
- [x] Save functionality persists changes
- [x] Reload functionality refreshes content
- [x] Error messages display appropriately
- [x] No 502 Bad Gateway errors in normal operation
- [x] Console logs show correct target URLs

## Troubleshooting

### 404 Not Found
- Verify environment variables are set correctly
- Check Next.js server is running on port 3000
- Verify backend services are running

### 502 Bad Gateway
- Check backend services are accessible
- Verify BACKEND_URL and DT_BACKEND_URL are correct
- Check firewall/network settings

### Files Not Loading in Config Editor
- Verify backend `/api/settings/knobs` endpoints exist
- Check backend logs for errors
- Verify knobs.env files exist in backend root directory

### TypeScript Errors
- Run `npm install` to ensure all dependencies installed
- Restart Next.js dev server
- Check tsconfig.json is properly configured
