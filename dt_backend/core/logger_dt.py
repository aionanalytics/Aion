"""dt_backend/core/logger_dt.py

DT-backend local logging (kept separate from backend's logger).

Goals
-----
* Timestamped logs everywhere (UTC).
* Always writes to a dt_backend-specific logfile directory.
* Safe console output (never crash on Unicode).
* Minimal dependencies; standard library only.

This intentionally does **not** import backend modules so dt_backend can
run as a mostly-separate program.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Log destination
# ---------------------------------------------------------------------------

def _resolve_log_dir() -> Path:
    """Prefer dt_backend's configured log directory; fall back safely."""
    try:
        # Local import to avoid import cycles.
        from dt_backend.core.config_dt import DT_PATHS  # type: ignore

        p = DT_PATHS.get("logs_dt")
        if isinstance(p, Path):
            return p
    except Exception:
        pass

    # Fallback: project-root-ish logs/dt_backend
    try:
        here = Path(__file__).resolve()
        root = here.parents[2]
        return root / "logs" / "dt_backend"
    except Exception:
        return Path("logs") / "dt_backend"


LOG_DIR: Path = _resolve_log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Formatting / I/O
# ---------------------------------------------------------------------------

def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _logfile_path() -> Path:
    # daily rotation: dt_backend_YYYY-MM-DD.log
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"dt_backend_{day}.log"


def _safe_print(msg: str, *, stderr: bool = False) -> None:
    stream = sys.stderr if stderr else sys.stdout
    try:
        # Bypass Windows' cp1252 by writing UTF-8 bytes.
        stream.buffer.write((msg + "\n").encode("utf-8"))
        stream.flush()
    except Exception:
        # Absolute fallback: strip non-ASCII.
        safe = msg.encode("ascii", "ignore").decode("ascii", "ignore")
        try:
            print(safe, file=stream, flush=True)
        except Exception:
            pass


def _write_file(line: str) -> None:
    try:
        fp = _logfile_path()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Logging must never take the system down.
        pass


def _fmt(level: str, message: str) -> str:
    pid = os.getpid()
    return f"[{_utc_ts()}] [{level}] [pid={pid}] {message}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def info(message: str) -> None:
    line = _fmt("INFO", message)
    _safe_print(line)
    _write_file(line)


def warn(message: str) -> None:
    line = _fmt("WARN", message)
    _safe_print(line)
    _write_file(line)


def error(message: str, exc: Optional[BaseException] = None) -> None:
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        message = f"{message}\n{tb}".rstrip()
    line = _fmt("ERROR", message)
    _safe_print(line, stderr=True)
    _write_file(line)


# Convenience alias used across dt_backend (mirrors earlier core.data_pipeline_dt.log)
def log(message: str) -> None:
    info(message)
