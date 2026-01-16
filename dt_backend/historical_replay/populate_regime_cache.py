#!/usr/bin/env python3
"""
dt_backend/historical_replay/populate_regime_cache.py

CLI tool to pre-populate regime cache for historical replay.

Usage:
    python -m dt_backend.historical_replay.populate_regime_cache --start 2025-01-01 --end 2025-01-15
    python -m dt_backend.historical_replay.populate_regime_cache --date 2025-01-10
    python -m dt_backend.historical_replay.populate_regime_cache --list
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dt_backend.core.regime_cache import (
        populate_regime_cache,
        list_cached_dates,
        clear_regime_cache,
    )
    from dt_backend.core.data_pipeline_dt import log
except Exception as e:
    print(f"Error importing modules: {e}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Populate regime cache for historical replay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD)",
    )
    
    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD)",
    )
    
    parser.add_argument(
        "--date",
        type=str,
        help="Single date to populate (YYYY-MM-DD)",
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recompute even if cache exists",
    )
    
    parser.add_argument(
        "--list",
        action="store_true",
        help="List cached dates",
    )
    
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear cache for specified date range",
    )
    
    args = parser.parse_args()
    
    # List cached dates
    if args.list:
        log("[populate_regime_cache] 📋 Listing cached dates...")
        cached_dates = list_cached_dates(
            start_date=args.start,
            end_date=args.end,
        )
        
        if cached_dates:
            log(f"[populate_regime_cache] Found {len(cached_dates)} cached dates:")
            for date in cached_dates:
                print(f"  • {date}")
        else:
            log("[populate_regime_cache] No cached dates found")
        
        return 0
    
    # Clear cache
    if args.clear:
        log("[populate_regime_cache] 🗑️ Clearing cache...")
        deleted_count = clear_regime_cache(
            start_date=args.start,
            end_date=args.end,
        )
        log(f"[populate_regime_cache] ✅ Deleted {deleted_count} cache files")
        return 0
    
    # Populate cache
    if args.date:
        # Single date
        start_date = args.date
        end_date = args.date
    elif args.start and args.end:
        # Date range
        start_date = args.start
        end_date = args.end
    else:
        # Default: last 30 days
        end = datetime.now().date()
        start = end - timedelta(days=30)
        start_date = start.isoformat()
        end_date = end.isoformat()
    
    log(f"[populate_regime_cache] 🚀 Populating cache from {start_date} to {end_date}")
    log(f"[populate_regime_cache] Force recompute: {args.force}")
    
    stats = populate_regime_cache(
        start_date=start_date,
        end_date=end_date,
        force_recompute=args.force,
    )
    
    log(f"[populate_regime_cache] ✅ Completed:")
    log(f"  Total days: {stats.get('total_days', 0)}")
    log(f"  Cached: {stats.get('cached', 0)}")
    log(f"  Skipped: {stats.get('skipped', 0)}")
    log(f"  Failed: {stats.get('failed', 0)}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
