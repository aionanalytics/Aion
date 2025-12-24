# utils/json_tools.py
"""
Robust JSON helpers supporting:
- read_json()
- write_json()
- read_json_gz()
- write_json_gz()
with full robustness and atomic writes.
"""

from __future__ import annotations

import json
import gzip
from pathlib import Path
from typing import Any, Optional
from utils.logger import warn, error


def read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        error(f"[json] Failed to read {path}", e)
        return None


def write_json(path: Path, obj: Any, pretty: bool = True) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")

        with tmp.open("w", encoding="utf-8") as f:
            if pretty:
                json.dump(obj, f, indent=2, ensure_ascii=False)
            else:
                json.dump(obj, f)

        tmp.replace(path)
        return True
    except Exception as e:
        error(f"[json] Failed to write {path}", e)
        return False


def read_json_gz(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        error(f"[json] Failed to read gz {path}", e)
        return None


def write_json_gz(path: Path, obj: Any, pretty: bool = False) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")

        mode = "wt"
        with gzip.open(tmp, mode, encoding="utf-8") as f:
            if pretty:
                json.dump(obj, f, indent=2, ensure_ascii=False)
            else:
                json.dump(obj, f, ensure_ascii=False)

        tmp.replace(path)
        return True
    except Exception as e:
        error(f"[json] Failed to write gz {path}", e)
        return False
