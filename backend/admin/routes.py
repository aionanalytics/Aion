from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any, Dict

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.admin.auth import login_admin, issue_token, require_admin
from backend.historical_replay_swing.job_manager import (
    start_replay_legacy,
    get_replay_status,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _extract_token(req: Request) -> str:
    auth = (req.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    if auth:
        return auth
    return (req.headers.get("x-admin-token") or "").strip()


def _require_admin(req: Request) -> None:
    token = _extract_token(req)
    if not token or not require_admin(token):
        raise HTTPException(status_code=403, detail="forbidden")


# -------------------------------------------------
# Admin UI (RESTORE GET ROUTES)
# -------------------------------------------------
# If you have a separate admin frontend, set:
#   AION_ADMIN_UI_URL="http://127.0.0.1:5173/admin"
# and /admin will redirect there.
AION_ADMIN_UI_URL = os.getenv("AION_ADMIN_UI_URL", "").strip()


def _admin_login_html() -> str:
    # Minimal built-in login page (no frameworks, no dependencies)
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AION Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px; }
    .wrap { max-width: 520px; margin: 0 auto; }
    .card { border: 1px solid #eee; border-radius: 12px; padding: 18px; }
    input { width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid #ddd; }
    button { padding: 10px 12px; border-radius: 10px; border: 0; cursor: pointer; }
    button.primary { width: 100%; margin-top: 12px; }
    code { background: #f5f5f5; padding: 2px 6px; border-radius: 6px; }
    .row { margin-top: 10px; }
    .muted { color: #666; font-size: 13px; }
    .ok { color: #0a7; }
    .err { color: #c00; white-space: pre-wrap; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>AION Admin</h1>
    <div class="card">
      <div class="row">
        <label class="muted">Admin password</label>
        <input id="pw" type="password" placeholder="Enter password" autocomplete="current-password" />
      </div>
      <button class="primary" onclick="doLogin()">Login</button>

      <div class="row muted">
        This will call <code>POST /admin/login</code> and store the returned token in <code>localStorage</code>
        as <code>aion_admin_token</code>.
      </div>

      <div id="out" class="row"></div>
    </div>

    <p class="muted" style="margin-top: 16px;">
      API docs: <a href="/docs">/docs</a>
    </p>
  </div>

<script>
async function doLogin() {
  const out = document.getElementById('out');
  out.className = 'row muted';
  out.textContent = 'Logging in...';

  const password = (document.getElementById('pw').value || '').trim();
  if (!password) {
    out.className = 'row err';
    out.textContent = 'missing_password';
    return;
  }

  try {
    const res = await fetch('/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password })
    });

    const text = await res.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }

    if (!res.ok) {
      out.className = 'row err';
      out.textContent = 'Login failed: ' + (data.detail || data.raw || res.status);
      return;
    }

    const token = data.token;
    if (!token) {
      out.className = 'row err';
      out.textContent = 'No token returned.';
      return;
    }

    localStorage.setItem('aion_admin_token', token);
    out.className = 'row ok';
    out.innerHTML = '✅ Logged in. Token saved as <code>aion_admin_token</code>.';

  } catch (e) {
    out.className = 'row err';
    out.textContent = String(e);
  }
}
</script>
</body>
</html>
"""


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def admin_root() -> HTMLResponse:
    # If you have a separate admin UI, redirect to it.
    if AION_ADMIN_UI_URL:
        return RedirectResponse(url=AION_ADMIN_UI_URL)
    # Otherwise serve minimal built-in login UI
    return HTMLResponse(_admin_login_html(), status_code=200)


@router.get("/login", include_in_schema=False)
async def admin_login_page() -> HTMLResponse:
    # Same behavior as /admin
    if AION_ADMIN_UI_URL:
        # If UI expects /admin/login specifically, tack it on
        # (won't double-slash because we strip in env var)
        url = AION_ADMIN_UI_URL.rstrip("/") + "/login"
        return RedirectResponse(url=url)
    return HTMLResponse(_admin_login_html(), status_code=200)


# --- Auth API ---
@router.post("/login")
async def admin_login(req: Request) -> Dict[str, Any]:
    try:
        data = await req.json()
    except Exception:
        data = {}

    password = str((data or {}).get("password", "")).strip()
    if not password:
        raise HTTPException(status_code=400, detail="missing_password")

    if not login_admin(password):
        raise HTTPException(status_code=403, detail="invalid_password")

    return {"token": issue_token()}


# --- Replay control ---
@router.post("/replay/start")
async def replay_start(req: Request) -> Dict[str, Any]:
    _require_admin(req)

    try:
        payload = await req.json()
    except Exception:
        payload = {}

    weeks = payload.get("weeks", 4)
    version = payload.get("version", "v1")

    return start_replay_legacy(weeks=int(weeks), version=str(version))


@router.get("/replay/status")
async def replay_status(req: Request) -> Dict[str, Any]:
    _require_admin(req)
    return get_replay_status()


# --- Restart services ---
@router.post("/system/restart")
async def restart_services(req: Request) -> Dict[str, Any]:
    # IMPORTANT: use the same header-based token check as other admin routes
    _require_admin(req)

    def delayed_exit():
        time.sleep(1.5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=delayed_exit, daemon=True).start()
    return {"status": "ok", "message": "Restarting services"}
