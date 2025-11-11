# dt_backend/daytrading_job.py — AION Intraday Trader v1.0
# Lean parallel of nightly_job.py for day trading:
# Live refresh → Intraday ML dataset → Train (fast) → Predict → Signals → Online learn → (optional) Sync

from __future__ import annotations
import os, sys, json, gzip, time, traceback
from datetime import datetime
from pathlib import Path

# --- Import safety: allow dt_backend to import backend utilities ---
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Reuse backend logger (no rolling collisions)
from backend.data_pipeline import log  # type: ignore

# DT config (all paths isolated under *_dt/)
from dt_backend.config_dt import DT_PATHS

# ------------------------------------------------------------
# Job lock (separate from nightly; lives under data_dt/)
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]
JOB_LOCK_PATH = BASE_DIR / "data_dt" / "daytrading_job.lock"
os.makedirs(JOB_LOCK_PATH.parent, exist_ok=True)

def _blocking_acquire_lock(lock_path: Path, poll_secs: float = 0.5) -> bool:
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"pid={os.getpid()} ts={datetime.utcnow().isoformat()}")
            return True
        except FileExistsError:
            time.sleep(poll_secs)
        except Exception as e:
            log(f"⚠️ dt lock create failed: {e}")
            time.sleep(poll_secs)

def _release_lock(lock_path: Path):
    try:
        if lock_path.exists() and lock_path.is_file():
            os.remove(lock_path)
    except Exception as e:
        log(f"⚠️ Could not remove dt job lock: {e}")

def _phase(title: str):
    log(f"——— {title} (DT) ———")

# ------------------------------------------------------------
# Minimal DT rolling I/O (kept separate from nightly rolling)
# ------------------------------------------------------------
_DT_ROLLING = DT_PATHS["dtrolling"]

def _read_dt_rolling() -> dict:
    if not _DT_ROLLING.exists():
        return {}
    try:
        with gzip.open(_DT_ROLLING, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ Failed to read DT rolling: {e}")
        return {}

def _save_dt_rolling(obj: dict) -> None:
    try:
        _DT_ROLLING.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(_DT_ROLLING) + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(obj or {}, f, ensure_ascii=False)
        os.replace(tmp, _DT_ROLLING)
    except Exception as e:
        log(f"⚠️ Failed to save DT rolling: {e}")

# ------------------------------------------------------------
# Optional: cloud sync placeholder (you can wire dt buckets later)
# ------------------------------------------------------------
def _dt_cloud_sync():
    try:
        # Reuse your cloud_sync if/when you add ml_data_dt / data_dt folders there.
        from backend.cloud_sync import sync_all  # type: ignore
        sync_all()  # NOTE: current cloud_sync doesn't include *_dt folders by default
    except Exception:
        log("☁️ DT cloud sync skipped (not configured for *_dt paths).")

# ------------------------------------------------------------
# Main Orchestration
# ------------------------------------------------------------
def run() -> dict:
    summary = {
        "live_refresh": None,
        "dataset_rows": 0,
        "training": None,
        "predictions": 0,
        "signals": None,
        "online_learning": None,
        "synced": False,
        "status": "ok",
    }

    if not _blocking_acquire_lock(JOB_LOCK_PATH):
        return {"status": "skipped_locked", **summary}

def run_daytrading_job(mode: str = "full"):
    """Main function to run the intraday day-trading job."""
    log(f"[DT] ⚡ Day Trading Job — AION Intraday Trader v1.0 (mode={mode})")
    summary = {}

    try:
        # Import all core stages
        from dt_backend.ml_data_builder_intraday import build_intraday_dataset
        from dt_backend.train_lightgbm_intraday import train_intraday_models
        from dt_backend.ai_model_intraday import score_intraday_tickers as generate_intraday_predictions
        from dt_backend.signals_rank_builder import build_intraday_signals

        # 1) Load DT rolling snapshot (independent from nightly)
        dt_rolling = _read_dt_rolling()
        log(f"📦 DT rolling present — {len(dt_rolling):,} tickers loaded.")

        # 2) Live refresh
        _phase("Live price refresh")
        try:
            from backend.live_prices_router import fetch_live_prices
            live_data = fetch_live_prices()
            if live_data and isinstance(live_data, dict):
                dt_rolling.update(live_data)
                refreshed = len(live_data)
                summary["live_refresh"] = refreshed
                log(f"💹 Live prices refreshed for {refreshed} symbols (DT rolling updated).")
            else:
                log("⚠️ No live data returned from live_prices_router.")
        except Exception as e:
            log(f"⚠️ Live refresh failed (DT): {e}")

        # 3) Build intraday ML dataset (to ml_data_dt/training_data_intraday.parquet)
        _phase("Build intraday dataset")
        try:
            from dt_backend.ml_data_builder_intraday import build_intraday_dataset  # type: ignore
            ddf = build_intraday_dataset(dt_rolling=dt_rolling)
            summary["dataset_rows"] = int(getattr(ddf, "shape", [0])[0]) if ddf is not None else 0
            log(f"📊 Intraday dataset → {summary['dataset_rows']} rows.")
        except Exception as e:
            log(f"⚠️ Intraday dataset build failed: {e}")

        # 4) Train intraday model(s) (classifier) → ml_data_dt/models/intraday/
        _phase("Train intraday models")
        try:
            from dt_backend.train_lightgbm_intraday import train_intraday_models  # type: ignore
            summary["training"] = train_intraday_models()
            log(f"🧠 DT training complete: {summary['training']}")
        except Exception as e:
            log(f"⚠️ Intraday training failed: {e}")

        # 5) Predict (short-term) → ml_data_dt/signals/intraday_predictions.json
        _phase("Predict intraday signals")
        try:
            from dt_backend.ai_model_intraday import score_intraday_tickers  # type: ignore
            from dt_backend.signals_rank_builder import build_intraday_signals  # ✅ new import

            preds = score_intraday_tickers() or {}
            summary["predictions"] = len(preds)
            log(f"🤖 DT predictions generated for {summary['predictions']} tickers.")

            # ✅ Build and save ranked signal file for scheduler
            if preds:
                rank_path = build_intraday_signals(preds)
                log(f"📊 Intraday rank file generated → {rank_path}")
            else:
                log("⚠️ No predictions to rank — skipping rank file build.")

        except Exception as e:
            log(f"⚠️ Intraday prediction failed: {e}")

        # 5.5) --- Context & policy (fast mode) ---
        try:
            from backend import context_state, regime_detector
            from dt_backend.policy_engine_dt import apply as apply_dt_policy
            context_state.update()
            regime_detector.run()
            dt_rolling = _read_dt_rolling() or {}
            dt_rolling = apply_dt_policy(dt_rolling)
            _save_dt_rolling(dt_rolling)
        except Exception as e:
            log(f"⚠️ DT context/policy layer skipped: {e}")

        # 6) Build ranked signal board (Top buys/sells)
        _phase("Build signal board")
        try:
            from dt_backend.signals_builder import write_intraday_signals  # type: ignore
            out_path = write_intraday_signals()  # writes to DT_PATHS["dtsignals"]/intraday_predictions.json
            summary["signals"] = str(out_path) if out_path else None
            log(f"🏁 Signals written → {summary['signals']}")
        except Exception as e:
            log(f"⚠️ Signals build failed: {e}")

        # 7) Online learning (use realized outcomes / same-day P&L)
        _phase("Online incremental learning")
        try:
            from dt_backend.continuous_learning_intraday import train_incremental_intraday  # type: ignore
            learn_res = train_incremental_intraday()
            summary["online_learning"] = learn_res
            log(f"🔁 Online learning done: {learn_res}")
        except Exception as e:
            log(f"⚠️ Online learning failed: {e}")

        # 8) Save DT rolling (if we updated anything) + optional sync
        try:
            _save_dt_rolling(dt_rolling)
            _dt_cloud_sync()
            summary["synced"] = True
        except Exception as e:
            log(f"ℹ️ DT cloud sync skipped: {e}")

        dur = time.time() - t0
        log(f"\n✅ Day trading job complete in {dur:.1f}s.")
        return summary

    except Exception as e:
        log(f"❌ DT job fatal error: {e}")
        traceback.print_exc()
        summary["status"] = "error"
        summary["error"] = str(e)
        return summary
    finally:
        _release_lock(JOB_LOCK_PATH)

if __name__ == "__main__":
    from backend.data_pipeline import log
    log("⚡ Starting daytrading_job as standalone module...")
    try:
        from dt_backend.daytrading_job import run_daytrading_job
        run_daytrading_job("full")
    except Exception as e:
        log(f"⚠️ daytrading_job failed: {e}")

