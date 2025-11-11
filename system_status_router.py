from fastapi import APIRouter
from pathlib import Path
import re
from datetime import datetime, timedelta
import pytz

router = APIRouter(prefix="/api/system", tags=["system"])

LOG_DIR = Path("ml_data/logs")
CST = pytz.timezone("America/Chicago")

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

def parse_last_runs() -> dict:
    """Read all nightly logs and extract last ✅ success timestamps."""
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


@router.get("/status")
def get_system_status():
    """Return live health for all backend jobs (OK / Running / Overdue)."""
    last_runs = parse_last_runs()
    now = datetime.now(CST)
    data = []

    for name, freq_key, label in JOBS:
        freq = FREQ_HOURS[freq_key]
        last_ts_str = last_runs.get(label)
        status = "error"

        if last_ts_str:
            try:
                last_dt = CST.localize(datetime.strptime(last_ts_str, "%Y-%m-%d %H:%M:%S"))
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

    return {
        "status": "ok",
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S CST"),
        "jobs": data,
    }
