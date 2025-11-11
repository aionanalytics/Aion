"""
AION Analytics — Global Configuration Map
Centralized path and environment configuration for backend + ML pipeline.

Usage:
    from backend.config import PATHS, SETTINGS
"""

from pathlib import Path
import pytz

# ==============================================================
# ML data paths (used by helpers and bots)
# ==============================================================
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

# Ensure consistent reference for ML data dir
ML_DATA_DIR = BASE_DIR / "ml_data"

# ---------------------------------------------------------------------
# Root Resolution (sap_env/)
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]  # → sap_env/

# ---------------------------------------------------------------------
# Path Map
# ---------------------------------------------------------------------
PATHS = {
    # ----- News, Events, Sentiment Intelligence -----
    "news": ROOT / "data" / "news_cache",
    "news_raw": ROOT / "data" / "news_cache" / "news_raw_*.json",
    "news_sentiment": ROOT / "data" / "news_cache" / "news_sentiment.json",
    "news_events": ROOT / "data" / "news_cache" / "news_events_*.parquet",
    "news_dashboard_html": ROOT / "dashboard" / "news_dashboard_latest.html",
    "news_dashboard_json": ROOT / "data" / "news_cache" / "news_dashboard_latest.json",
    "sentiment_map": ROOT / "data" / "news_cache" / "sentiment_map_*.json",

    # ----- Core Data & Rolling System -----
    "rolling": ROOT / "data" / "stock_cache" / "master" / "rolling.json.gz",
    "rolling_brain": ROOT / "data" / "stock_cache" / "master" / "rolling_brain.json.gz",
    "rolling_backups": ROOT / "data" / "stock_cache" / "master" / "backups",
    "stock_cache": ROOT / "data" / "stock_cache",
    "master": ROOT / "data" / "stock_cache" / "master",

    # ----- Machine Learning / Training Data -----
    "ml_data": ROOT / "ml_data",
    "ml_models": ROOT / "ml_data" / "models",
    "training_data_daily": ROOT / "ml_data" / "training" / "training_data_daily.parquet",
    "training_data_weekly": ROOT / "ml_data" / "training" / "training_data_weekly.parquet",
    "training_data_monthly": ROOT / "ml_data" / "training" / "training_data_monthly.parquet",
    "prediction_logs": ROOT / "ml_data" / "prediction_logs",
    "prediction_outcomes": ROOT / "ml_data" / "prediction_outcomes",
    "model_registry": ROOT / "ml_data" / "model_registry.jsonl",
    "rank_history": ROOT / "ml_data" / "rank_history.json",
    "insights": ROOT / "ml_data" / "insights_history",
    "context_snapshots": ROOT / "ml_data" / "context_enriched" / "context_enriched_sentiment_*.json",
    "models": ROOT / "ml_data" / "ai_models",
    "ml_data": ML_DATA_DIR,
    # ----- Brain & Intelligence -----
    "brain": ROOT / "data" / "stock_cache" / "master" / "rolling_brain.json.gz",
    "brain_backup": ROOT / "data" / "stock_cache" / "master" / "backups",

    # ----- Macro / Fundamentals / Metrics -----
    "macro": ROOT / "data" / "macro_cache",
    "macro_features": ROOT / "data" / "macro_cache" / "macro_features.parquet",
    "fundamentals": ROOT / "data" / "metrics_cache" / "fundamentals.json",
    "metrics": ROOT / "data" / "metrics_cache",
    "metrics_bundle": ROOT / "data" / "metrics_cache" / "bundle",
    "latest_metrics": ROOT / "data" / "metrics_cache" / "latest_metrics.json",

    # ----- Dashboards & Reports -----
    "dashboard": ROOT / "data" / "dashboard_cache",
    "dashboard_metrics": ROOT / "data" / "dashboard_cache" / "metrics.json",
    "dashboard_top": ROOT / "data" / "dashboard_cache" / "top_performers.json",
    "dashboard_html": ROOT / "data" / "dashboard" / "news_dashboard_latest.html",

    # ----- Logs, Scheduling & System -----
    "logs": ROOT / "logs",
    "system_logs": ROOT / "logs" / "ml_data" / "logs",
    "scheduler_logs": ROOT / "logs" / "scheduler",
    "nightly_logs": ROOT / "logs" / "nightly_jobs",

    # ----- Cloud & Sync -----
    "cloud_cache": ROOT / "data" / "cloud_cache",
    "supabase_bucket": "aion-cache",

    # ----- Executables / Scripts -----
    "backend_service": ROOT / "backend" / "backend_service.py",
    "run_backend": ROOT / "run_backend.py",
    "nightly_job": ROOT / "backend" / "nightly_job.py",
    "scheduler_runner": ROOT / "backend" / "scheduler_runner.py",

    # ----- Frontend (React app) -----
    "frontend": ROOT / "frontend",
    "frontend_assets": ROOT / "frontend" / "public" / "assets",
}

# ---------------------------------------------------------------------
# System Settings
# ---------------------------------------------------------------------
SETTINGS = {
    "timezone": pytz.timezone("America/New_York"),
    "max_history_days": 180,
    "max_rolling_backups": 8,
    "cloud_sync_enabled": True,
    "drift_threshold": 0.25,
    "ic_floor": 0.02,
}

# ---------------------------------------------------------------------
# Directory auto-creation
# ---------------------------------------------------------------------
for key, path in PATHS.items():
    try:
        if isinstance(path, Path) and not any(str(path).endswith(x) for x in (".gz", ".json", ".jsonl", ".parquet", "*.json", "*.log")):
            path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def get_path(key: str) -> Path:
    return PATHS.get(key)
