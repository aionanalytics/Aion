from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query
from dt_backend.core.data_pipeline_dt import _read_rolling, _rolling_path

router = APIRouter()

@router.get("/rolling/path")
def rolling_path() -> Dict[str, str]:
    return {"path": str(_rolling_path())}

@router.get("/rolling")
def get_rolling(
    symbol: Optional[str] = Query(default=None, description="If provided, return only this symbol node."),
    include_meta: bool = Query(default=False, description="If false, omit keys starting with '_'"),
) -> Dict[str, Any]:
    rolling = _read_rolling() or {}
    if symbol:
        node = rolling.get(symbol.upper()) or rolling.get(symbol) or {}
        return {"symbol": symbol.upper(), "data": node}
    if not include_meta:
        rolling = {k: v for k, v in rolling.items() if not str(k).startswith("_")}
    return {"symbols": len(rolling), "data": rolling}
