"""
dt_backend/core/config_dt.py

AION DT (Day-Trading) — Central Configuration Module
-----------------------------------------------------

Rules (aligned with backend):
    ✔ No hard-coded secrets (ALL keys come from environment / .env)
    ✔ No hard-coded paths (ALL paths derive from PROJECT ROOT)
    ✔ Safe on Windows
    ✔ dt_backend and backend share the SAME .env key names
    ✔ dt_backend may have different logic, but contracts + key names align

"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict
import pytz

# ============================================================
#  KEYS (env-driven, UI-editable via shared .env)
#  MUST be defined BEFORE any aliases
# ============================================================

KEYS: Dict[str, str] = {
    # --- Supabase ---
    "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),
    "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
    "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY", ""),
    "SUPABASE_BUCKET": os.getenv("SUPABASE_BUCKET", ""),

    # --- Trading / Market Data ---
    "ALPACA_API_KEY_ID": os.getenv("ALPACA_API_KEY_ID", ""),
    "ALPACA_API_SECRET_KEY": os.getenv("ALPACA_API_SECRET_KEY", ""),
    "ALPACA_PAPER_BASE_URL": os.getenv("ALPACA_PAPER_BASE_URL", ""),

    "PERIGON_KEY": os.getenv("PERIGON_KEY", ""),
    "MARKETAUX_API_KEY": os.getenv("MARKETAUX_API_KEY", ""),
    "FINNHUB_API_KEY": os.getenv("FINNHUB_API_KEY", ""),
    "NEWSAPI_KEY": os.getenv("NEWSAPI_KEY", ""),
    "RSS2JSON_KEY": os.getenv("RSS2JSON_KEY", ""),
    "TWITTER_BEARER": os.getenv("TWITTER_BEARER", ""),

    # --- Reddit ---
    "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID", ""),
    "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET", ""),
    "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT", ""),

    # --- Massive / Flatfiles ---
    "MASSIVE_API": os.getenv("MASSIVE_API", ""),
    "MASSIVE_S3API": os.getenv("MASSIVE_S3API", ""),
    "MASSIVE_S3API_SECRET": os.getenv("MASSIVE_S3API_SECRET", ""),
    "MASSIVE_S3_ENDPOINT": os.getenv("MASSIVE_S3_ENDPOINT", ""),
    "MASSIVE_S3_BUCKET": os.getenv("MASSIVE_S3_BUCKET", ""),
}

# ============================================================
#  Module-level aliases (contract adapters)
#  Expose KEYS entries as importable constants (mirrors backend)
# ============================================================

# --- Supabase ---
SUPABASE_URL = KEYS["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = KEYS["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_ANON_KEY = KEYS["SUPABASE_ANON_KEY"]
SUPABASE_BUCKET = KEYS["SUPABASE_BUCKET"]

# --- Trading / Market Data ---
ALPACA_API_KEY_ID = KEYS["ALPACA_API_KEY_ID"]
ALPACA_KEY = KEYS["ALPACA_API_KEY_ID"]  # legacy alias
ALPACA_API_SECRET_KEY = KEYS["ALPACA_API_SECRET_KEY"]
ALPACA_SECRET = KEYS["ALPACA_API_SECRET_KEY"]  # legacy alias
ALPACA_PAPER_BASE_URL = KEYS["ALPACA_PAPER_BASE_URL"]

PERIGON_KEY = KEYS["PERIGON_KEY"]
MARKETAUX_API_KEY = KEYS["MARKETAUX_API_KEY"]
MARKETAUX_KEY = KEYS["MARKETAUX_API_KEY"]
FINNHUB_API_KEY = KEYS["FINNHUB_API_KEY"]
NEWSAPI_KEY = KEYS["NEWSAPI_KEY"]
RSS2JSON_KEY = KEYS["RSS2JSON_KEY"]
TWITTER_BEARER = KEYS["TWITTER_BEARER"]

# --- Reddit ---
REDDIT_CLIENT_ID = KEYS["REDDIT_CLIENT_ID"]
REDDIT_CLIENT_SECRET = KEYS["REDDIT_CLIENT_SECRET"]
REDDIT_USER_AGENT = KEYS["REDDIT_USER_AGENT"]

# --- Massive / Flatfiles ---
MASSIVE_API = KEYS["MASSIVE_API"]
MASSIVE_S3API = KEYS["MASSIVE_S3API"]
MASSIVE_S3API_SECRET = KEYS["MASSIVE_S3API_SECRET"]
MASSIVE_S3_ENDPOINT = KEYS["MASSIVE_S3_ENDPOINT"]
MASSIVE_S3_BUCKET = KEYS["MASSIVE_S3_BUCKET"]

# ============================================================
#  PROJECT ROOT
# ============================================================
# dt_backend/core/config_dt.py → dt_backend/core/ → dt_backend/ → <ROOT>/
ROOT: Path = Path(__file__).resolve().parents[2]

# ============================================================
#  TIMEZONE
# ============================================================
TIMEZONE = pytz.timezone(os.getenv("AION_TZ", "America/Denver"))

# ============================================================
#  DATA ROOTS (DT)
# ============================================================
DT_BACKEND: Path = ROOT / "dt_backend"
ML_DATA_DT: Path = ROOT / "ml_data_dt"
LOGS_DT: Path = ROOT / "logs" / "dt_backend"
DA_BRAINS: Path = ROOT / "da_brains"
DATA_DT: Path = ROOT / "data_dt"
# ============================================================
#  PATHS DICTIONARY (DT)
# ============================================================

DT_PATHS: Dict[str, Path] = {
    "root": ROOT,
    "dt_backend": DT_BACKEND,
    "da_brains": DA_BRAINS,
    "data_dt": DATA_DT,
    "core": DT_BACKEND / "core",

    "universe_dir": DT_BACKEND / "universe",
    "universe_file": DT_BACKEND / "universe" / "symbol_universe.json",
    "exchanges_file": DT_BACKEND / "universe" / "exchanges.json",

    "bars_intraday_dir": ML_DATA_DT / "bars" / "intraday",
    "bars_daily_dir": ML_DATA_DT / "bars" / "daily",

    "rolling_intraday_dir": DA_BRAINS / "intraday",
    # DT engine rolling (context/features/predictions/policy/execution)
    "rolling": DA_BRAINS / "intraday" / "rolling_intraday.json.gz",
    "rolling_intraday_file": DA_BRAINS / "intraday" / "rolling_intraday.json.gz",
    # Live market bars rolling (written by live_market_data_loop only)
    "rolling_market_intraday_file": DA_BRAINS / "intraday" / "rolling_intraday_market.json.gz",
    # Lock file used when dt jobs write rolling_intraday_file on Windows
    "rolling_dt_lock_file": DA_BRAINS / "intraday" / ".rolling_intraday_dt.lock",
    "rolling_longterm_dir": DT_BACKEND / "rolling" / "longterm",

    # DT brain (durable learning / performance memory)
    "dt_brain_file": DA_BRAINS / "core" / "dt_brain.json.gz",

    "signals_intraday_dir": ML_DATA_DT / "signals" / "intraday",
    "signals_intraday_predictions_dir": DATA_DT / "signals" / "intraday" / "predictions",
    "signals_intraday_ranks_dir": ML_DATA_DT / "signals" / "intraday" / "ranks",
    "signals_intraday_boards_dir": ML_DATA_DT / "signals" / "intraday" / "boards",

    "signals_longterm_dir": ML_DATA_DT / "signals" / "longterm",
    "signals_longterm_predictions_dir": ML_DATA_DT / "signals" / "longterm" / "predictions",
    "signals_longterm_boards_dir": ML_DATA_DT / "signals" / "longterm" / "boards",

    "historical_replay_root": DATA_DT / "historical_replay",
    "historical_replay_raw": DATA_DT / "historical_replay" / "raw",
    "historical_replay_processed": DATA_DT / "historical_replay" / "processed",
    "historical_replay_meta": DATA_DT / "historical_replay" / "metadata.json",

    "ml_data_dt": ML_DATA_DT,
    "dtml_data": ML_DATA_DT,
    "dtml_intraday_dataset": ML_DATA_DT / "training_data_intraday.parquet",
    "dtmodels": ML_DATA_DT / "models",

    "models_root": DT_BACKEND / "models",
    "models_lgbm_intraday_dir": DT_BACKEND / "models" / "lightgbm_intraday",
    "models_lstm_intraday_dir": DT_BACKEND / "models" / "lstm_intraday",
    "models_transformer_intraday_dir": DT_BACKEND / "models" / "transformer_intraday",
    "models_ensemble_dir": DT_BACKEND / "models" / "ensemble",

    "logs_dt": LOGS_DT,
}


def ensure_dt_dirs() -> None:
    """Best-effort directory creation. Never raises."""
    for _, path in DT_PATHS.items():
        try:
            target = path if path.suffix == "" else path.parent
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue


ensure_dt_dirs()


def get_dt_path(key: str) -> Path:
    """Mirror backend.get_path()."""
    return DT_PATHS.get(key)
