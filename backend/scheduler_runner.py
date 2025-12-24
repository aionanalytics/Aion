"""
scheduler_runner.py

Runs automated backend + dt_backend tasks based on scheduler_config.py.

Key guarantees:
- Correct project root
- Correct PYTHONPATH
- Live stdout printing
- Exit codes logged
- No silent failures

Fixes (Windows stability):
- Forces UTF-8 decoding for child process output to avoid 'charmap' crashes
- Logs written as UTF-8 with safe replacement for any weird bytes
- Injects PYTHONUTF8=1 into child processes for consistent stdout encoding
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import pytz

from .scheduler_config import ENABLE, TIMEZONE, SCHEDULE
from backend.core.config import PATHS

# ---------------------------------------------------------------------
# Resolve project root (CRITICAL, robust)
# ---------------------------------------------------------------------

def _find_project_root() -> Path:
    # Prefer explicit config if provided
    root = PATHS.get("project_root")
    if root:
        return Path(root).resolve()

    # Walk upwards until we find both backend/ and dt_backend/
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / "backend").exists() and (p / "dt_backend").exists():
            return p.resolve()

    # Final fallback: repo-local assumption
    return here.parents[2].resolve()


PROJECT_ROOT = _find_project_root()

os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

LOG_DIR: Path = PATHS.get(
    "scheduler_logs",
    PATHS.get("logs", Path("logs")) / "scheduler",
)
LOG_FILE: Path = LOG_DIR / "scheduler_runner.log"


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    _ensure_log_dir()
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[scheduler] {ts} {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------

def _build_cmd(job: Dict[str, Any]) -> list[str]:
    python = job.get("python") or sys.executable
    module = job["module"]
    args = job.get("args", [])
    return [python, "-u", "-m", module, *args]


def _job_log_path(job: Dict[str, Any]) -> Path:
    date_tag = datetime.utcnow().strftime("%Y%m%d")
    return LOG_DIR / f"{job['name']}_{date_tag}.log"


def run_job(job: Dict[str, Any]) -> None:
    name = job["name"]
    desc = job.get("description", "")
    cmd = _build_cmd(job)
    cwd = job.get("cwd") or PROJECT_ROOT

    job_log = _job_log_path(job)

    log(f"▶ START {name}: {desc}")
    log(f"  CMD={cmd}")
    log(f"  CWD={cwd}")

    # Ensure children are nudged toward UTF-8 output (helps avoid mojibake)
    child_env = dict(os.environ)
    child_env.setdefault("PYTHONUTF8", "1")

    with job_log.open("ab") as f:
        f.write(f"\n===== JOB START {datetime.utcnow().isoformat()} =====\n".encode("utf-8", errors="replace"))

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )

        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    # line is already decoded safely as UTF-8 with replacement
                    print(f"[{name}] {line}", end="", flush=True)
                    f.write(line.encode("utf-8", errors="replace"))
            else:
                msg = "[scheduler] ⚠️ No stdout pipe from subprocess.\n"
                print(msg, flush=True)
                f.write(msg.encode("utf-8", errors="replace"))
        except Exception as e:
            # Never let decoding/logging kill the scheduler
            err_line = f"[scheduler] ⚠️ Exception while streaming output for {name}: {e}\n"
            print(err_line, flush=True)
            f.write(err_line.encode("utf-8", errors="replace"))

        proc.wait()

        f.write(
            f"\n===== JOB END {datetime.utcnow().isoformat()} exit_code={proc.returncode} =====\n"
            .encode("utf-8", errors="replace")
        )

    if proc.returncode == 0:
        log(f"✔ DONE {name} (exit=0)")
    else:
        log(f"❌ FAIL {name} (exit={proc.returncode})")


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------

def main(loop_forever: bool = True) -> None:
    if not ENABLE:
        log("Scheduler disabled via ENABLE=False.")
        return

    tz = pytz.timezone(TIMEZONE)
    log(f"Scheduler started — TZ={TIMEZONE}, jobs={len(SCHEDULE)}")
    log(f"Resolved PROJECT_ROOT={PROJECT_ROOT}")

    last_run: Dict[str, str] = {}

    while True:
        now = datetime.now(tz)
        minute = now.strftime("%H:%M")

        for job in SCHEDULE:
            name = job["name"]
            if job["time"] == minute and last_run.get(name) != minute:
                run_job(job)
                last_run[name] = minute

        if not loop_forever:
            break

        time.sleep(15)


if __name__ == "__main__":
    main(loop_forever="--once" not in sys.argv)
