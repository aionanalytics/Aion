from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dt_backend.jobs.daytrading_job import run_daytrading_cycle
from dt_backend.jobs.rank_fetch_scheduler import run_rank_scheduler
from dt_backend.jobs.backfill_intraday_full import backfill_intraday_full
from dt_backend.jobs.live_market_data_loop import fetch_live_bars_once

router = APIRouter()

class DaytradingRunRequest(BaseModel):
    # Keep flexible: dt_backend internals can evolve.
    universe: Optional[list[str]] = Field(default=None, description="Optional list of symbols to run.")
    dry_run: bool = Field(default=False, description="If true, run without writing outputs where supported.")

@router.post("/daytrading/run")
def run_daytrading(req: DaytradingRunRequest) -> Dict[str, Any]:
    # dt_backend function signature currently accepts no args.
    # We keep request fields for forward compatibility; unused fields are ignored safely.
    out = run_daytrading_cycle()
    return {"ok": True, "result": out}

@router.post("/rank_scheduler/run")
def run_rank_fetch() -> Dict[str, Any]:
    # IMPORTANT: run_rank_scheduler() is an infinite loop by design.
    # The API endpoint must never hang, so we run a single cycle.
    out = run_rank_scheduler(once=True)
    return {"ok": True, "result": out}


class LiveBarsRequest(BaseModel):
    symbols: Optional[list[str]] = Field(default=None, description="Optional symbol list; if omitted, uses universe")
    max_symbols: Optional[int] = Field(default=None, description="Cap symbols (useful for testing)")
    fetch_1m: bool = Field(default=True)
    fetch_5m: bool = Field(default=True)


@router.post("/live_bars/fetch")
def fetch_live_bars(req: LiveBarsRequest) -> Dict[str, Any]:
    out = fetch_live_bars_once(
        symbols=req.symbols,
        max_symbols=req.max_symbols,
        fetch_1m=req.fetch_1m,
        fetch_5m=req.fetch_5m,
    )
    return {"ok": True, "result": out}

class BackfillRequest(BaseModel):
    symbols: Optional[list[str]] = Field(default=None, description="Optional symbol list; if omitted, uses universe.")
    days: int = Field(default=5, ge=1, le=60, description="How many days of intraday bars to backfill.")
    interval: str = Field(default="1m", description="Bar interval, e.g., '1m', '5m'.")
    force: bool = Field(default=False, description="If true, force re-download/backfill if supported.")

@router.post("/backfill/run")
def run_backfill(req: BackfillRequest) -> Dict[str, Any]:
    # backfill_intraday_full signature varies across versions; call defensively.
    kwargs: Dict[str, Any] = {}
    # Only pass args if function supports them.
    import inspect
    sig = inspect.signature(backfill_intraday_full)
    if "symbols" in sig.parameters:
        kwargs["symbols"] = req.symbols
    if "days" in sig.parameters:
        kwargs["days"] = req.days
    if "interval" in sig.parameters:
        kwargs["interval"] = req.interval
    if "force" in sig.parameters:
        kwargs["force"] = req.force

    out = backfill_intraday_full(**kwargs)
    return {"ok": True, "result": out}
