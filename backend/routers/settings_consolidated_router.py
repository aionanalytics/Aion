# backend/routers/settings_consolidated_router.py
"""
Settings Consolidated Router — AION Analytics

Consolidated router for all settings and configuration management.
Replaces fragmented settings endpoints with unified interface.

Endpoints:
- GET /api/settings/{name} → get knobs (knobs/dt-knobs/keys)
- POST /api/settings/{name} → save knobs
- GET /api/settings/keys/status → key status
- POST /api/settings/keys/test → test keys
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Body

try:
    from backend.core.config import PATHS, TIMEZONE, ROOT
except ImportError:
    from backend.config import PATHS, TIMEZONE, ROOT  # type: ignore

router = APIRouter(prefix="/api/settings", tags=["settings"])


# -------------------------
# Helper Functions
# -------------------------

def _error_response(error: str, details: Optional[str] = None) -> Dict[str, Any]:
    """Create error response dict."""
    return {
        "error": error,
        "details": details,
        "timestamp": datetime.now(TIMEZONE).isoformat(),
    }


def _load_env_file(filename: str) -> Dict[str, str]:
    """Load environment file into dict."""
    env_path = ROOT / filename
    if not env_path.exists():
        return {}
    
    env_vars = {}
    try:
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    except Exception:
        pass
    
    return env_vars


def _save_env_file(filename: str, env_vars: Dict[str, str]) -> bool:
    """Save dict to environment file."""
    env_path = ROOT / filename
    try:
        lines = []
        for key, value in env_vars.items():
            lines.append(f"{key}={value}\n")
        
        env_path.write_text("".join(lines), encoding="utf-8")
        return True
    except Exception:
        return False


# -------------------------
# Get Settings Endpoint
# -------------------------

@router.get("/{name}")
async def get_settings(name: str) -> Dict[str, Any]:
    """
    Get settings by name.
    
    Args:
        name: Settings name (knobs, dt-knobs, keys)
    
    Returns:
        {
            "name": "knobs",
            "settings": {...},
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "name": name,
            "settings": {},
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Map names to files
        settings_map = {
            "knobs": "knobs.env",
            "dt-knobs": "dt_knobs.env",
            "keys": ".env",
        }
        
        filename = settings_map.get(name)
        if not filename:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown settings name: {name}"
            )
        
        # Load settings
        result["settings"] = _load_env_file(filename)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(
            f"Failed to get settings: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Save Settings Endpoint
# -------------------------

@router.post("/{name}")
async def save_settings(
    name: str,
    settings: Dict[str, str] = Body(...)
) -> Dict[str, Any]:
    """
    Save settings by name.
    
    Args:
        name: Settings name (knobs, dt-knobs, keys)
        settings: Settings dict to save
    
    Returns:
        {
            "status": "ok" | "error",
            "name": "knobs",
            "message": "...",
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "status": "ok",
            "name": name,
            "message": "",
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Map names to files
        settings_map = {
            "knobs": "knobs.env",
            "dt-knobs": "dt_knobs.env",
            "keys": ".env",
        }
        
        filename = settings_map.get(name)
        if not filename:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown settings name: {name}"
            )
        
        # Save settings
        if _save_env_file(filename, settings):
            result["message"] = f"Settings '{name}' saved successfully"
        else:
            result["status"] = "error"
            result["message"] = f"Failed to save settings '{name}'"
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(
            f"Failed to save settings: {type(e).__name__}",
            str(e)
        )


# -------------------------
# API Keys Status Endpoint
# -------------------------

@router.get("/keys/status")
async def get_keys_status() -> Dict[str, Any]:
    """
    Get API keys status (without exposing actual keys).
    
    Returns:
        {
            "keys": {
                "alpaca": {"configured": bool, "valid": bool},
                "supabase": {"configured": bool, "valid": bool},
                ...
            },
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "keys": {},
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Check key configuration
        try:
            from admin_keys import (
                ALPACA_API_KEY_ID,
                ALPACA_API_SECRET_KEY,
                SUPABASE_URL,
                SUPABASE_SERVICE_ROLE_KEY,
            )
            
            result["keys"]["alpaca"] = {
                "configured": bool(ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY),
                "valid": None,  # Would need to test with API call
            }
            
            result["keys"]["supabase"] = {
                "configured": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY),
                "valid": None,  # Would need to test with API call
            }
            
        except Exception as e:
            result["error"] = f"Failed to check keys: {e}"
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to get keys status: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Test API Keys Endpoint
# -------------------------

@router.post("/keys/test")
async def test_api_keys(
    key_name: str = Body(..., embed=True)
) -> Dict[str, Any]:
    """
    Test API key by making a test request.
    
    Args:
        key_name: Name of key to test (alpaca, supabase)
    
    Returns:
        {
            "status": "ok" | "error",
            "key_name": "alpaca",
            "message": "...",
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "status": "ok",
            "key_name": key_name,
            "message": "",
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        if key_name == "alpaca":
            try:
                from admin_keys import ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY
                
                if not ALPACA_API_KEY_ID or not ALPACA_API_SECRET_KEY:
                    result["status"] = "error"
                    result["message"] = "Alpaca keys not configured"
                else:
                    # TODO: Make actual test API call
                    result["message"] = "Alpaca keys configured (test not implemented)"
                    
            except Exception as e:
                result["status"] = "error"
                result["message"] = f"Failed to test Alpaca keys: {e}"
        
        elif key_name == "supabase":
            try:
                from admin_keys import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
                
                if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
                    result["status"] = "error"
                    result["message"] = "Supabase keys not configured"
                else:
                    # TODO: Make actual test API call
                    result["message"] = "Supabase keys configured (test not implemented)"
                    
            except Exception as e:
                result["status"] = "error"
                result["message"] = f"Failed to test Supabase keys: {e}"
        
        else:
            result["status"] = "error"
            result["message"] = f"Unknown key name: {key_name}"
        
        return result
        
    except Exception as e:
        return _error_response(
            f"Failed to test keys: {type(e).__name__}",
            str(e)
        )


# -------------------------
# Update Settings Values Endpoint
# -------------------------

@router.patch("/{name}/values")
async def update_settings_values(
    name: str,
    updates: Dict[str, str] = Body(...)
) -> Dict[str, Any]:
    """
    Update specific settings values without overwriting entire file.
    
    Args:
        name: Settings name (knobs, dt-knobs, keys)
        updates: Dict of key-value pairs to update
    
    Returns:
        {
            "status": "ok" | "error",
            "name": "knobs",
            "updated": ["key1", "key2"],
            "message": "...",
            "timestamp": "ISO timestamp"
        }
    """
    try:
        result = {
            "status": "ok",
            "name": name,
            "updated": [],
            "message": "",
            "timestamp": datetime.now(TIMEZONE).isoformat(),
        }
        
        # Map names to files
        settings_map = {
            "knobs": "knobs.env",
            "dt-knobs": "dt_knobs.env",
            "keys": ".env",
        }
        
        filename = settings_map.get(name)
        if not filename:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown settings name: {name}"
            )
        
        # Load current settings
        current = _load_env_file(filename)
        
        # Update with new values
        for key, value in updates.items():
            current[key] = value
            result["updated"].append(key)
        
        # Save updated settings
        if _save_env_file(filename, current):
            result["message"] = f"Updated {len(result['updated'])} settings in '{name}'"
        else:
            result["status"] = "error"
            result["message"] = f"Failed to save settings '{name}'"
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        return _error_response(
            f"Failed to update settings: {type(e).__name__}",
            str(e)
        )
