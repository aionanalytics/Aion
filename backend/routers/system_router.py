# backend/routers/system_router.py
"""
Consolidated System Router — AION Analytics

Consolidates system-related endpoints from:
  - system_status_router.py (deleted)
  - health_router.py (deleted)
  - system_run_router.py (deleted)
  - diagnostics_router.py (deleted)

Endpoints:
  - GET  /api/system/status       (job monitor + supervisor verdict)
  - GET  /api/system/health       (component health: broker, data, models)
  - GET  /api/system/diagnostics  (file stats, path verification)
  - POST /api/system/action       (system actions: restart, manual tasks)
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException

# Core imports
from backend.core.config import PATHS
from backend.core.data_pipeline import _read_rolling, _read_brain, log
from backend.core.supervisor_agent import supervisor_verdict
from config import ROOT, DT_PATHS
from settings import TIMEZONE

router = APIRouter(prefix="/api/system", tags=["system"])

# Track service start time for uptime calculation
_START_TIME = time.time()

# Timezone
TZ = TIMEZONE

# Frequency windows (in hours)
FREQ_HOURS = {
    "6h": 6,
    "daily": 24,
    "weekly": 24 * 7,
    "monthly": 24 * 30,
}

# Registered backend modules to monitor
JOBS = [
    ("Data Pipeline", "6h", "data_pipeline.update_daily"),
    ("ML Data Builder", "6h", "ml_data_builder.build_ml_dataset"),
    ("Train LightGBM", "daily", "train_lightgbm.train_lightgbm_models"),
    ("Prediction Logger", "6h", "prediction_logger.log_predictions"),
    ("Online Trainer", "6h", "online_trainer.online_train"),
    ("Drift Monitor", "daily", "drift_monitor.generate_drift_report"),
    ("Macro Fetcher", "daily", "macro_fetcher.build_macro_features"),
    ("Fundamentals Fetcher", "monthly", "fundamentals_fetcher.update_fundamentals"),
    ("Ticker Fetcher", "weekly", "ticker_fetcher.update_universe"),
    ("Insights Builder", "6h", "insights_builder.build_insights"),
]


# =========================================================================
# HELPERS
# =========================================================================

def parse_last_runs() -> dict:
    """Read all nightly logs and extract last successful timestamps."""
    LOG_DIR = PATHS.get("nightly_logs", Path(PATHS.get("logs", ROOT / "logs")) / "nightly")
    results = {}
    if not LOG_DIR.exists():
        return results

    for log_file in sorted(LOG_DIR.glob("nightly_*.log"), reverse=True):
        try:
            for line in open(log_file, encoding="utf-8"):
                match = re.match(r"\[(.*?)\]\s+✅\s+(.*?):", line)
                if match:
                    ts_str, label = match.groups()
                    label = label.strip()
                    if label not in results:
                        results[label] = ts_str
        except Exception:
            continue
    return results


def check_broker_connection() -> Dict[str, Any]:
    """Check if broker API is accessible."""
    try:
        api_key = os.getenv("ALPACA_API_KEY_ID")
        api_secret = os.getenv("ALPACA_API_SECRET_KEY")
        
        if not api_key or not api_secret:
            return {"status": "degraded", "message": "Missing Alpaca credentials"}
        
        return {"status": "healthy", "message": "Credentials configured"}
    except Exception as e:
        return {"status": "degraded", "message": f"Error: {str(e)}"}


def check_data_availability() -> Dict[str, Any]:
    """Check if core data files are available."""
    try:
        data_root = os.getenv("DATA_ROOT", "data")
        data_dir = Path(data_root)
        
        if not data_dir.exists():
            return {"status": "degraded", "message": "Data directory not found"}
        
        return {"status": "healthy", "message": "Data directory available"}
    except Exception as e:
        return {"status": "degraded", "message": f"Error: {str(e)}"}


def check_ml_models() -> Dict[str, Any]:
    """Check if ML models are available."""
    try:
        ml_models_root = os.getenv("ML_MODELS_ROOT", "ml_data/nightly/models")
        models_dir = Path(ml_models_root)
        
        if not models_dir.exists():
            return {"status": "degraded", "message": "Models directory not found"}
        
        model_files = list(models_dir.glob("**/*.txt")) + list(models_dir.glob("**/*.pkl"))
        
        if not model_files:
            return {"status": "degraded", "message": "No models found"}
        
        return {
            "status": "healthy",
            "message": f"{len(model_files)} model(s) available"
        }
    except Exception as e:
        return {"status": "degraded", "message": f"Error: {str(e)}"}


def get_uptime() -> float:
    """Get service uptime in seconds."""
    return time.time() - _START_TIME


def get_last_nightly_run() -> str:
    """Get timestamp of last nightly job run."""
    try:
        summary_file = Path("last_nightly_summary.json")
        
        if not summary_file.exists():
            return "unknown"
        
        with open(summary_file, "r", encoding="utf-8") as f:
            summary = json.load(f)
            return summary.get("timestamp", "unknown")
    except Exception:
        return "unknown"


def _stat(path: Path) -> Dict[str, Any]:
    try:
        st = path.stat()
        return {
            "exists": True,
            "size_bytes": int(st.st_size),
            "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        }
    except FileNotFoundError:
        return {"exists": False, "size_bytes": None, "mtime_iso": None}
    except Exception as e:
        return {"exists": False, "size_bytes": None, "mtime_iso": None, "error": str(e)}


def _read_json_ts(path: Path, keys: List[str]) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v:
            return v
    return None


# =========================================================================
# SYSTEM ACTION HELPERS
# =========================================================================

def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name, "") or "").strip().lower()
    if v == "":
        return default
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def _scheduler_enabled() -> bool:
    return _env_bool("ENABLE_SCHEDULER", default=False)


def _scheduler_base_url() -> str:
    """Where to proxy heavy tasks when running in API/UI mode."""
    url = (os.environ.get("SCHEDULER_URL", "") or "").strip()
    if url:
        return url.rstrip("/")

    host = (os.environ.get("SCHEDULER_HOST", "") or "").strip() or "127.0.0.1"
    port = (os.environ.get("SCHEDULER_PORT", "") or "").strip() or "8001"
    return f"http://{host}:{port}"


def _proxy_to_scheduler(task: str) -> Tuple[bool, Any]:
    """POST the same endpoint to the scheduler instance."""
    base = _scheduler_base_url()
    url = f"{base}/api/system/action"

    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps({"action": task}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(raw) if raw else {"status": "ok"}
            except Exception:
                return True, {"status": "ok", "raw": raw}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return False, {"error": "scheduler_http_error", "code": e.code, "body": body}
    except Exception as e:
        return False, {"error": "scheduler_unreachable", "detail": str(e), "url": url}


def _run_bg(fn: Callable[[], Any], name: str) -> None:
    def _wrapped():
        try:
            log(f"[system_router] 🚀 START action={name}")
            out = fn()
            log(f"[system_router] ✅ DONE action={name} result={out}")
        except Exception as e:
            log(f"[system_router] ❌ FAIL action={name} err={e}")

    threading.Thread(target=_wrapped, daemon=True).start()


# Task implementations
def _task_nightly():
    try:
        from backend.jobs.nightly_job import main as nightly_main  # type: ignore
        return nightly_main()
    except Exception:
        from backend.jobs.nightly_job import run as nightly_run  # type: ignore
        return nightly_run()


def _task_train():
    from backend.core.ai_model.core_training import train_all_models
    return train_all_models(n_trials=100)


def _task_insights():
    from backend.services.insights_builder import build_daily_insights
    return build_daily_insights(limit=50)


def _task_metrics():
    from backend.services.metrics_fetcher import build_latest_metrics
    return build_latest_metrics()


def _task_fundamentals():
    from backend.services.fundamentals_fetcher import enrich_fundamentals
    return enrich_fundamentals()


def _task_news():
    from backend.services.news_fetcher import run_news_fetch
    return run_news_fetch(days_back=2, use_multiprocessing=False)


def _task_verify() -> Dict[str, Any]:
    rolling = _read_rolling() or {}
    brain = _read_brain() or {}

    rolling_syms = [k for k in rolling.keys() if not str(k).startswith("_")]
    brain_syms = [k for k in brain.keys() if not str(k).startswith("_")]

    return {
        "status": "ok",
        "paths": {
            "rolling": str(PATHS.get("rolling")),
            "brain": str(PATHS.get("rolling_brain")),
            "backups": str(PATHS.get("rolling_backups")),
        },
        "counts": {
            "rolling_keys": len(rolling),
            "rolling_symbols": len(rolling_syms),
            "brain_keys": len(brain),
            "brain_symbols": len(brain_syms),
        },
        "scheduler_enabled": _scheduler_enabled(),
    }


def _task_dashboard() -> Dict[str, Any]:
    """Compute and cache dashboard metrics."""
    try:
        from backend.services.unified_cache_service import UnifiedCacheService
        
        cache_service = UnifiedCacheService()
        if cache_service is None:
            log("[system_router] ❌ Failed to initialize UnifiedCacheService")
            return {
                "status": "error",
                "error": "Failed to initialize cache service",
            }
        
        result = cache_service.update_all()
        
        if result is None:
            log("[system_router] ❌ Cache update returned None")
            return {
                "status": "error",
                "error": "Cache update failed",
            }
        
        return {
            "status": "ok",
            "timestamp": result.get("timestamp"),
            "sections_updated": list(result.get("data", {}).keys()),
            "errors": result.get("errors", {}),
        }
    except Exception as e:
        log(f"[system_router] ❌ Dashboard task error: {e}")
        return {
            "status": "error",
            "error": str(e),
        }


TASKS: Dict[str, Callable[[], Any]] = {
    "nightly": _task_nightly,
    "train": _task_train,
    "insights": _task_insights,
    "metrics": _task_metrics,
    "fundamentals": _task_fundamentals,
    "news": _task_news,
    "verify": _task_verify,
    "dashboard": _task_dashboard,
}

HEAVY_TASKS = {"nightly", "train", "insights", "metrics", "fundamentals", "news"}


# =========================================================================
# ENDPOINTS
# =========================================================================

@router.get("/status")
def get_system_status():
    """
    System status endpoint with job monitor and supervisor verdict.
    
    Returns:
      - Core job run status
      - SupervisorAgent v3.0 verdict
      - Rolling coverage stats
    """
    now = datetime.now(TZ)
    last_runs = parse_last_runs()
    data = []

    # Job Monitor
    for name, freq_key, label in JOBS:
        freq = FREQ_HOURS[freq_key]
        last_ts_str = last_runs.get(label)
        status = "error"

        if last_ts_str:
            try:
                last_dt = TZ.localize(datetime.strptime(last_ts_str, "%Y-%m-%d %H:%M:%S"))
                delta = (now - last_dt).total_seconds() / 3600

                if delta <= freq + 1:
                    status = "ok"
                elif delta <= freq * 2:
                    status = "running"
                else:
                    status = "error"
            except Exception:
                status = "error"

        data.append({
            "name": name,
            "frequency": freq_key,
            "lastRun": last_ts_str or "Never",
            "status": status,
        })

    # SupervisorAgent verdict
    supervisor = supervisor_verdict()

    # Rolling coverage summary
    rolling = _read_rolling() or {}
    total = len([s for s in rolling.keys() if not s.startswith("_")])
    missing_preds = sum(1 for _, n in rolling.items()
                        if not n.get("predictions") and not _.startswith("_"))

    coverage = {
        "symbols": total,
        "missing_predictions": missing_preds,
        "predictions_coverage_pct": round(
            100 * (total - missing_preds) / max(total, 1), 2
        ),
    }

    return {
        "status": "ok",
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S TZ"),
        "jobs": data,
        "supervisor": supervisor,
        "coverage": coverage,
    }


@router.get("/health")
def health_check():
    """System health status with component checks."""
    components = {
        "broker": check_broker_connection(),
        "data": check_data_availability(),
        "models": check_ml_models(),
    }
    
    # Determine overall status
    if any(c["status"] == "down" for c in components.values()):
        overall_status = "down"
    elif any(c["status"] == "degraded" for c in components.values()):
        overall_status = "degraded"
    else:
        overall_status = "healthy"
    
    return {
        "status": overall_status,
        "service": "backend",
        "components": components,
        "uptime_seconds": get_uptime(),
        "last_nightly_run": get_last_nightly_run(),
        "version": "v3.3",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/diagnostics")
def diagnostics() -> Dict[str, Any]:
    """System diagnostics with file stats and path verification."""
    check_items: List[Dict[str, Any]] = []

    def add_item(key: str, p: Path):
        s = _stat(p)
        check_items.append(
            {
                "key": key,
                "path": str(p.resolve()),
                **s,
            }
        )

    # Core paths (absolute)
    add_item("root", ROOT)
    for k in [
        "universe",
        "ml_data",
        "ml_models",
        "ml_predictions",
        "ml_datasets",
        "da_brains",
        "rolling_brain",
        "dt_rolling_brain",
        "logs",
        "nightly_logs",
        "intraday_logs",
        "swing_replay_state",
        "nightly_lock",
        "swing_replay_lock",
    ]:
        if k in PATHS:
            add_item(k, Path(PATHS[k]))

    # Universe files
    for k in ["universe_master_file", "universe_swing_file", "universe_dt_file"]:
        p = PATHS.get(k)
        if p is None:
            if k == "universe_master_file":
                p = ROOT / "data" / "universe" / "master_universe.json"
            elif k == "universe_swing_file":
                p = ROOT / "data" / "universe" / "swing_universe.json"
            else:
                p = ROOT / "data" / "universe" / "dt_universe.json"
        add_item(k, Path(p))

    # DT paths (optional)
    for k in ["ml_data_dt", "intraday_ui_store"]:
        if k in DT_PATHS:
            add_item(f"dt:{k}", Path(DT_PATHS[k]))

    missing = [{"key": x["key"], "path": x["path"]} for x in check_items if not x.get("exists")]

    # Last nightly summary
    nightly_summary_path = Path(PATHS.get("logs", ROOT / "logs")) / "nightly" / "last_nightly_summary.json"
    last_nightly_finished = _read_json_ts(nightly_summary_path, ["finished_at", "completed_at", "timestamp"])

    # Replay state
    replay_state_path = Path(PATHS.get("swing_replay_state", ROOT / "data" / "replay" / "swing" / "replay_state.json"))
    last_replay_updated = _read_json_ts(replay_state_path, ["updated_at", "finished_at", "completed_at", "timestamp"])

    # Intraday sim summary
    dt_sim_summary_path = Path(DT_PATHS.get("sim_summary", ROOT / "ml_data_dt" / "sim_logs" / "sim_summary.json"))
    dt_sim_updated = _read_json_ts(dt_sim_summary_path, ["updated_at", "finished_at", "timestamp"])

    return {
        "resolved_root": str(ROOT.resolve()),
        "cwd": os.getcwd(),
        "pythonpath_head": sys.path[:5],
        "paths": check_items,
        "missing": missing,
        "last_nightly": {
            "summary_path": str(nightly_summary_path.resolve()),
            "finished_at": last_nightly_finished,
        },
        "last_replay": {
            "state_path": str(replay_state_path.resolve()),
            "updated_at": last_replay_updated,
        },
        "dt_last_intraday": {
            "sim_summary": str(dt_sim_summary_path.resolve()),
            "updated_at": dt_sim_updated,
        },
    }


@router.post("/action")
def run_action(action: str):
    """
    Execute system action (manual tasks, system operations).
    
    Actions:
      - nightly, train, insights, metrics, fundamentals, news, verify, dashboard
    """
    action_key = (action or "").strip().lower()

    fn = TASKS.get(action_key)
    if not fn:
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Unknown action '{action_key}'",
                "allowed": sorted(TASKS.keys()),
            },
        )

    # Multi-worker safe: proxy heavy jobs to scheduler if not scheduler owner
    if (action_key in HEAVY_TASKS) and (not _scheduler_enabled()):
        ok, payload = _proxy_to_scheduler(action_key)
        if not ok:
            raise HTTPException(status_code=503, detail=payload)
        return {
            "status": "proxied",
            "action": action_key,
            "scheduler": _scheduler_base_url(),
            "scheduler_response": payload,
        }

    # Run locally
    _run_bg(fn, action_key)
    return {"status": "started", "action": action_key, "scheduler_enabled": _scheduler_enabled()}
