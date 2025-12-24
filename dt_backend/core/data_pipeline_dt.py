# dt_backend/core/data_pipeline_dt.py — v1.1
"""
Lightweight I/O helpers for dt_backend intraday engine.

Guarantees:
  • never raises in normal use (best-effort)
  • atomic writes for rolling cache
  • stable node schema via ensure_symbol_node

Key API:
  • log(msg)
  • _read_rolling()
  • save_rolling(rolling)
  • load_universe()
  • ensure_symbol_node(rolling, symbol)
"""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .config_dt import DT_PATHS
from .logger_dt import log


# ---------------------------------------------------------------------------
# Rolling cache helpers
# ---------------------------------------------------------------------------

def _rolling_path() -> Path:
    # Allow per-process override. This is how we keep "bars rolling" and
    # "dt engine rolling" separate on Windows (avoids gzip corruption).
    #
    # If DT_ROLLING_PATH is set, it wins.
    override = os.getenv("DT_ROLLING_PATH", "").strip()
    if override:
        return Path(override)
    return Path(DT_PATHS["rolling_intraday_file"])


def _lock_path() -> Path:
    # If DT_LOCK_PATH is set, it wins; otherwise use config default.
    override = os.getenv("DT_LOCK_PATH", "").strip()
    if override:
        return Path(override)
    return Path(DT_PATHS.get("rolling_dt_lock_file") or (Path(DT_PATHS["rolling_intraday_dir"]) / ".rolling_intraday_dt.lock"))


def _should_lock() -> bool:
    return str(os.getenv("DT_USE_LOCK", "0")).strip().lower() in ("1", "true", "yes", "y", "on")


def _acquire_lock(timeout_s: float = 30.0) -> bool:
    """Best-effort lock via exclusive file create.

    Windows-friendly: avoids relying on POSIX-only file locks.
    """
    if not _should_lock():
        return True

    lock = _lock_path()
    deadline = time.time() + max(0.0, float(timeout_s))

    while True:
        try:
            # O_EXCL ensures we fail if it already exists.
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8", errors="ignore"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            if time.time() >= deadline:
                return False
            time.sleep(0.15)
        except Exception:
            # If lock fails for any reason, do not crash.
            return False


def _release_lock() -> None:
    if not _should_lock():
        return
    try:
        _lock_path().unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass


def _read_rolling() -> Dict[str, Any]:
    path = _rolling_path()
    if not path.exists():
        return {}

    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log(f"⚠️ rolling cache at {path} is not a dict, resetting.")
            return {}
        return data
    except Exception as e:
        log(f"⚠️ failed to read rolling cache {path}: {e}")
        return {}


def save_rolling(rolling: Dict[str, Any]) -> None:
    """
    Atomically write intraday rolling cache as JSON.GZ.

    IMPORTANT:
      Path.with_suffix(".tmp") breaks names like *.json.gz.
      We use "<filename>.tmp" next to the target file.
    """
    path = _rolling_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(path.name + ".tmp")

    try:
        if not _acquire_lock(timeout_s=float(os.getenv("DT_LOCK_TIMEOUT", "30"))):
            log(f"⚠️ rolling lock timeout; skipping save: {path}")
            return
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(rolling or {}, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e:
        log(f"⚠️ failed to save rolling cache {path}: {e}")
    finally:
        _release_lock()


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

def _norm_sym(sym: str) -> str:
    return (sym or "").strip().upper()


def load_universe() -> List[str]:
    """
    Load universe schema:
      1) {"symbols": ["AAPL", ...]}
      2) ["AAPL", ...]
    """
    path = Path(DT_PATHS["universe_file"])
    if not path.exists():
        log(f"⚠️ universe file missing at {path} — using empty universe.")
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"⚠️ failed to parse universe file {path}: {e}")
        return []

    if isinstance(raw, dict) and "symbols" in raw:
        items: Iterable[str] = raw.get("symbols", [])
    elif isinstance(raw, list):
        items = raw
    else:
        log(f"⚠️ unexpected universe schema in {path}, expected list or dict['symbols'].")
        return []

    out: List[str] = []
    seen = set()
    for item in items:
        sym = _norm_sym(str(item))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


# ---------------------------------------------------------------------------
# Rolling node helpers
# ---------------------------------------------------------------------------

def ensure_symbol_node(rolling: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """
    Ensure rolling[symbol] exists and has standard sections.

    Standard sections:
      • bars_intraday
      • features_dt
      • predictions_dt
      • context_dt
      • policy_dt
    """
    sym = _norm_sym(symbol)
    node = rolling.get(sym)
    if not isinstance(node, dict):
        node = {}

    node.setdefault("bars_intraday", [])
    node.setdefault("features_dt", {})
    node.setdefault("predictions_dt", {})
    node.setdefault("context_dt", {})
    node.setdefault("policy_dt", {})

    rolling[sym] = node
    return node