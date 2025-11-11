# nightly_job.py — AION Analytics (Rolling-native v3.2)
# Full production pipeline: Heal → Backfill → Metrics → Aggregations → Enrichment → ML → Train → Drift → Insights → Sync

from pathlib import Path
import os, sys, json, time, traceback
from datetime import datetime
from backend.data_pipeline import log
from .config import PATHS
from .data_pipeline import (
    log,
    _read_rolling,
    apply_context_enrichment,
)

# optional cloud sync import
try:
    from .cloud_sync import sync_with_supabase
except ImportError:
    def sync_with_supabase(*args, **kwargs):
        log("☁️ Supabase sync unavailable (cloud_sync missing). Skipping.")
        return False

try:
    from .data_pipeline import (
        compress_to_weekly_from_rolling,
        compress_to_monthly_from_rolling,
    )
except Exception:
    def compress_to_weekly_from_rolling() -> str:
        log("ℹ️ compress_to_weekly_from_rolling not found — skipping weekly aggregation.")
        return "skipped"
    def compress_to_monthly_from_rolling() -> str:
        log("ℹ️ compress_to_monthly_from_rolling not found — skipping monthly aggregation.")
        return "skipped"

from .backfill_history import backfill_symbols
from .metrics_fetcher import build_latest_metrics
from . import ml_data_builder
from .train_lightgbm import train_all_models
from .insights_builder import build_daily_insights

try:
    from .ops_helpers import run_drift_report, _read_brain, save_brain
    _DRIFT_AVAILABLE = True
except Exception:
    _DRIFT_AVAILABLE = False


# ---------------------------------------
# Job lock (blocking, local-only; no config.py dependency)
# ---------------------------------------
ROOT = Path(__file__).resolve().parents[1]
JOB_LOCK_PATH = ROOT / "data" / "stock_cache" / "master" / "nightly_job.lock"
os.makedirs(JOB_LOCK_PATH.parent, exist_ok=True)

def _ensure_not_directory(p: Path):
    if p.exists() and p.is_dir():
        try:
            # remove empty dir; if not empty, raise
            os.rmdir(p)
            log(f"⚠️ Found directory instead of file at {p}, removing...")
        except Exception as e:
            log(f"⚠️ Could not remove unexpected lock directory {p}: {e}")

def _blocking_acquire_lock(lock_path: Path, poll_secs: float = 0.5) -> bool:
    """Blocking file-lock via O_EXCL create; waits until holder releases."""
    _ensure_not_directory(lock_path)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"pid={os.getpid()} ts={datetime.utcnow().isoformat()}")
            return True
        except FileExistsError:
            time.sleep(poll_secs)
        except PermissionError as e:
            log(f"⚠️ Permission error creating job lock: {e}")
            time.sleep(poll_secs)
        except Exception as e:
            log(f"⚠️ job lock create failed: {e}")
            time.sleep(poll_secs)

def _release_lock(lock_path: Path):
    try:
        if lock_path.exists() and lock_path.is_file():
            os.remove(lock_path)
    except Exception as e:
        log(f"⚠️ Could not remove job lock: {e}")

def _job_lock_acquire() -> bool:
    return _blocking_acquire_lock(JOB_LOCK_PATH)

def _job_lock_release() -> None:
    _release_lock(JOB_LOCK_PATH)


# ---------------------------------------
# Helpers
# ---------------------------------------
def _format_eta(seconds: float) -> str:
    if seconds < 0: return "?"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s" if m else f"{s}s"

def _progress_bar(current: int, total: int, start_time: float, prefix: str = ""):
    elapsed = time.time() - start_time
    rate = current / elapsed if elapsed > 0 else 0
    remaining = max(0, total - current)
    eta = remaining / max(rate, 1e-6)
    pct = current / total if total else 0
    bar_len = 30
    filled = int(bar_len * pct)
    bar = "█" * filled + "-" * (bar_len - filled)
    sys.stdout.write(f"\r{prefix} |{bar}| {current}/{total} ({pct*100:5.1f}%) ETA {_format_eta(eta)}  {rate:5.1f}/s ")
    sys.stdout.flush()

def _phase(title: str):
    log(f"——— {title} ———")


# ---------------------------------------
# Main Orchestration
# ---------------------------------------
def run() -> dict:
    summary = {
        "heal": None,
        "backfill": None,
        "metrics": None,
        "weekly_file": None,
        "monthly_file": None,
        "ml_daily_rows": 0,
        "ml_weekly_rows": 0,
        "training": None,
        "brain": None,
        "insights": None,
        "synced": False,
    }

    if not _job_lock_acquire():
        return {"status": "skipped_locked"}

    log("🛠️  Nightly Job — AION Analytics (Rolling-Native v3.2)")
    t0 = time.time()

    try:
        rolling = _read_rolling() or {}
        num_syms = len(rolling)
        log(f"📦 Rolling present — {num_syms} tickers loaded.")

        # Heal rolling cache
        _phase("Heal rolling cache (batch + self-heal)")
        try:
            syms = list(rolling.keys())
            total = len(syms)
            if total:
                start = time.time()
                for i, _ in enumerate(syms, 1):
                    if i % 50 == 0 or i == total:
                        _progress_bar(i, total, start, prefix="Healing")
                sys.stdout.write("\n")
                heal_start = time.time()
                syms_for_heal = list((_read_rolling() or {}).keys())
                backfill_symbols(syms_for_heal, min_days=180, max_workers=12)
                log(f"✅ Heal complete in {time.time()-heal_start:.1f}s.")
            summary["heal"] = "ok"
        except Exception as e:
            log(f"⚠️ heal_rolling_backfill failed: {e}")
            summary["heal"] = f"error: {e}"

        # Backfill today's bars
        _phase("Backfill (today's bars)")
        try:
            syms = list((_read_rolling() or {}).keys())
            total = len(syms)
            if total:
                start = time.time()
                for i, _ in enumerate(syms, 1):
                    if i % 50 == 0 or i == total:
                        _progress_bar(i, total, start, prefix="Backfill")
                sys.stdout.write("\n")
                backfill_symbols(syms, min_days=180, max_workers=12)
                log(f"✅ Backfill complete in {time.time()-start:.1f}s for {total} tickers.")
                summary["backfill"] = f"{total} tickers"
        except Exception as e:
            log(f"⚠️ backfill failed: {e}")
            summary["backfill"] = f"error: {e}"

        # Metrics refresh
        _phase("Metrics refresh (StockAnalysis)")
        try:
            build_latest_metrics()
            summary["metrics"] = "ok"
        except Exception as e:
            log(f"⚠️ metrics refresh failed: {e}")
            summary["metrics"] = f"error: {e}"

        # Aggregations
        _phase("Weekly aggregate")
        try:
            summary["weekly_file"] = compress_to_weekly_from_rolling()
        except Exception as e:
            log(f"ℹ️ weekly compression skipped: {e}")

        _phase("Monthly aggregate")
        try:
            summary["monthly_file"] = compress_to_monthly_from_rolling()
        except Exception as e:
            log(f"ℹ️ monthly compression skipped: {e}")

        # Context enrichment
        _phase("Context enrichment")
        try:
            apply_context_enrichment()
        except Exception as e:
            log(f"ℹ️ context enrichment skipped: {e}")

        # More human like
        from . import context_state, regime_detector, policy_engine, supervisor_agent
        context_state.update()
        regime_detector.run()
        rolling = _read_rolling() or {}
        rolling = policy_engine.apply(rolling)
        save_rolling(rolling)

# ---------------- News Intelligence ----------------
        _phase("News Intelligence")
        try:
            import subprocess
            log("🚀 Running News Intelligence module...")
            result = subprocess.run(
                ["python", "-m", "backend.news_intel"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore"
            )
            if result.returncode == 0:
                log("✅ News Intelligence completed successfully.")
            else:
                log(f"⚠️ News Intelligence exited with code {result.returncode}")
                log(result.stderr)
        except Exception as e:
            log(f"⚠️ News Intelligence failed: {e}")

        # Build ML datasets
        _phase("Build ML datasets (rolling-native)")
        try:
            ddf = ml_data_builder.build_ml_dataset("daily")
            summary["ml_daily_rows"] = int(getattr(ddf, "shape", [0])[0]) if ddf is not None else 0
        except Exception as e:
            log(f"⚠️ daily ML build failed: {e}")
        try:
            wdf = ml_data_builder.build_ml_dataset("weekly")
            summary["ml_weekly_rows"] = int(getattr(wdf, "shape", [0])[0]) if wdf is not None else 0
        except Exception as e:
            log(f"ℹ️ weekly ML build skipped: {e}")

        # Model training
        _phase("Model training")
        try:
            summary["training"] = train_all_models()
        except Exception as e:
            log(f"⚠️ training failed: {e}")
            summary["training"] = f"error: {e}"

        # ---------------- AI Predictions ----------------
        _phase("AI model predictions → Rolling")
        try:
            from .ai_model import score_all_tickers
            from .ops_helpers import log_predictions
            from .continuous_learning import train_incremental
            from backend.data_pipeline import save_rolling

            preds = score_all_tickers()
            rolling = _read_rolling() or {}

            pred_records = []
            for sym, res in preds.items():
                node = rolling.get(sym, {})
                node["predictions"] = res.get("predictions", {})
                rolling[sym] = node

                pmap = res.get("predictions", {})
                for horizon, pvals in pmap.items():
                    pred_records.append({
                        "symbol": sym,
                        "horizon": horizon,
                        "currentPrice": pvals.get("currentPrice"),
                        "predictedPrice": pvals.get("predictedPrice"),
                        "expectedReturnPct": pvals.get("expectedReturnPct"),
                        "proba": pvals.get("confidence"),
                        "score": pvals.get("score"),
                        "rankingScore": pvals.get("rankingScore"),
                    })

            save_rolling(rolling)
            log(f"🤖 Predictions updated for {len(preds)} symbols in Rolling.")

            model_version = datetime.utcnow().strftime("v%Y%m%d_%H%M%S")
            log_path = log_predictions(pred_records, model_version, feature_names=[])
            log(f"🧾 Predictions logged → {log_path}")

            inc_status = train_incremental()
            log(f"🔁 Incremental training completed → {inc_status.get('status')} ({inc_status.get('model_version')})")

            summary["predictions"] = len(preds)
            summary["incremental"] = inc_status

        except Exception as e:
            log(f"⚠️ AI model prediction phase failed: {e}")
            summary["predictions"] = f"error: {e}"

        _phase("News Intelligence")
        from backend.news_intel import run_news_intel
        run_news_intel()

        _phase("Event Outcome Harvest")
        from backend.event_outcome_harvester import run_event_harvest
        run_event_harvest()

        # Supervisor agent (risk audit)
        _phase("Supervisor agent (risk audit)")
        try:
            import json
            from . import supervisor_agent
            global_js = {}
            try:
                global_js = json.load(
                    open(PATHS["ml_data"] / "market_state.json", "r", encoding="utf-8")
                )
            except Exception:
                pass

            sup_metrics = {
                "drawdown_7d": 0.0,
                "regime": global_js.get("market_state", "neutral"),
                "regime_conf": float(global_js.get("macro_vol", 0.5) or 0.6),
            }

            supervisor_agent.step(sup_metrics)
        except Exception as e:
            log(f"⚠️ Supervisor agent phase failed: {e}")

        # Drift, Calibration & Rolling Brain update
        _phase("Drift monitoring & Rolling Brain update")
        try:
            from .ops_helpers import _read_brain, save_brain
            brain = _read_brain() or {}

            # NOTE: left exactly as-is (still references PATHS if you had it)
            if _DRIFT_AVAILABLE:
                ref = PATHS["training_data_weekly"]  # ✅ unified path
                cur = PATHS["training_data_daily"]  # ✅ unified path
                drift_ok = run_drift_report(ref, cur)
                if drift_ok:
                    brain["last_drift_path"] = str(PATHS["ml_data"] / "drift_report.html")  # ✅ unified
                summary["drift"] = "ok" if drift_ok else "skipped"
            else:
                log("ℹ️ Drift utilities not found — skipping.")
                summary["drift"] = "skipped"

            brain["updated_at"] = datetime.utcnow().isoformat()
            calib = (summary.get("incremental") or {}).get("calibration") or {}
            if calib:
                brain["calibration"] = calib
            save_brain(brain)

            log(
                f"🧠 Rolling Brain updated — "
                f"confidence×{calib.get('confidence_multiplier')}, "
                f"hit={calib.get('hit_rate')}, "
                f"brier={calib.get('brier')}"
            )
            summary["brain"] = "ok"

        except Exception as e:
            log(f"⚠️ Drift/Brain update failed: {e}")
            summary["brain"] = f"error: {e}"

        # Insights
        _phase("Insights generation (Top 50)")
        try:
            summary["insights"] = build_daily_insights(limit=50)
        except Exception as e:
            log(f"⚠️ insights build failed: {e}")
            summary["insights"] = f"error: {e}"

        # Cloud Sync
        _phase("Cloud sync (Supabase)")
        try:
            sync_with_supabase()
            summary["synced"] = True
        except Exception as e:
            log(f"ℹ️ supabase sync skipped: {e}")
            summary["synced"] = False

        log(f"\n✅ Nightly job complete in {time.time()-t0:.1f}s.")
        return {"status": "ok", **summary}

    except Exception as e:
        log(f"❌ Nightly job fatal error: {e}")
        traceback.print_exc()
        return {"status": "error", "error": str(e), **summary}
    finally:
        _job_lock_release()


if __name__ == "__main__":
    out = run()
    print(out)
